# 架构

## 分层

```
┌──────────────────────────────────────────────┐
│  GUI 层 (PySide6)   app.py / views / widgets  │
├──────────────────────────────────────────────┤
│  Agent 层   loop(推理->调工具->观察) + tools   │
│   └ LLM 大脑 (OpenAI 兼容云端 API, 可换端点)   │
├──────────────────────────────────────────────┤
│  编排层 (确定性)   alignment / safety / ops    │
├──────────────────────────────────────────────┤
│  引擎层   runner -> subprocess -> 14 个脚本     │
└──────────────────────────────────────────────┘
```

GUI 经 `gui/workers.py` 的 `QRunnable` 把 engine 操作丢进 `QThreadPool`，绝不阻塞 UI。
worker 按函数签名注入 `cancel` 与日志回调（ops 用 `on_output`，runtime 用 `on_stdout`），
子进程 stdout 按行流式回传到日志面板。`start_worker` 把 worker 存入 `_alive_workers`
集合，完成后释放，防止 Python 侧被 GC 导致 signals QObject 先于 emit 被删。

## Agent 层

`agent/` 包是 AI 驱动的核心（无 PySide6 依赖，CLI/GUI 共用）：

- **`llm.py`**：OpenAI 兼容客户端（懒加载 `openai` SDK）+ 纯数据类型 `AssistantMessage`/`ToolCall`。
  `chat_fn` 是协议接口，测试里用 FakeLlm 替换，无需联网。`api_key` 支持 `GISDO_API_KEY` 环境变量。
- **`prompt.py`**：系统提示词，源自原 SKILL.md（对齐门禁、永不删除覆盖、任务路由、比例验证、失败不清理）。
- **`tools.py`**：工具注册表，把 `ops.*` 包装成 OpenAI function-calling 工具（12 只读 + 6 写）。
  写工具的 handler 用循环已确认的 `Alignment`；失败返回 `FailureRecord` 摘要供 LLM 自恢复。
  写工具带 `normalize_args`：输出参数省略时默认补到当前项目的地图输出文件夹（版本化路径），
  对齐块显示补全后的真实路径。
- **`loop.py`**：`Agent` 循环--LLM 返回 `tool_calls` 就执行并回填结果，否则给出最终回答。
  最大迭代数（25）防失控。三档自主：`confirm_writes`（默认，只读自由、写暂停）、
  `autonomous`（全自动，护栏仍强制）、`confirm_every_step`（每步问）。
  真流式：`chat_fn` 支持可选 `on_token` 回调，逐 token 转发；取消经 `cancel` Event 检查抛
  `LlmCancelled(RunCancelled)`，GUI/CLI 停止按钮真正生效。
  历史持久化：`save_history`/`load_history` + `sanitize_history`（修剪孤儿 tool 消息），
  每项目支持多条独立对话，历史存
  `~/.gisdo/projects/<project-id>/conversations/<conversation-id>.json`。
  项目上下文：`inject_project_context` 把当前项目（项目文件夹/地图输出文件夹）幂等注入系统提示词。

**安全由代码强制，不由 LLM 强制**：Agent 调工具 -> `ops.*` -> `safety`/`alignment`/`preflight` 双层校验。
Agent 想越界，工具层拒绝并把错误喂回 LLM 让它改方案。写操作的对齐块经 `on_confirm` 回调
弹出（CLI 终端 y/n；GUI 跨线程握手：worker 发信号后等 `threading.Event`，主线程弹模态对话框
置位 Event 后继续）。

## 三个运行时与派发

`engine/runner.py` 的 `run_script(interpreter, script_name, args, is_py2=...)` 是唯一出口：

- 应用 Python（`sys.executable`）跑 `discover_geoscene.py`、`render_classified_lines.py`、`verify_png.py`
- GeoScene/ArcGIS Pro Python 跑 `inspect_aprx/gdb`、`extract_project_data`、`package_project`、`validate_package`
- ArcMap Python 2.7 跑 `inspect_mxd_legacy`、`inspect_legacy_dataset`、`export_legacy_lines`

PY2 解释器不传 `-X utf8`（遗留脚本自行 `emit()/encode` 处理编码）。
脚本通过 `importlib.resources` 定位，兼容开发安装与 PyInstaller。

**PyInstaller 打包**：打包后 `sys.executable` 指向 exe，无法再当解释器 subprocess 跑
纯 Python 脚本（`discover_geoscene`/`render_classified_lines`/`verify_png`）。`runner.run_script`
检测 frozen 环境后改为主进程内 `runpy` 执行（`_run_script_inplace`），stdout/stderr/JSON/退出码
与 subprocess 路径完全一致。`gisdo.engine.scripts` 为 regular package，spec 用
`collect_data_files(..., include_py_files=True)` 把脚本 .py 作为数据文件打入 exe。
arcpy 工作仍派发到真实 GeoScene/ArcMap 运行时（这些运行时自带解释器）。

## JSON 提取

arcpy 独立运行时会向前面的 stdout 写 GP 消息，破坏整体 `json.loads`。
`engine/jsonutil.py` 先尝试整体解析，失败则从最后一个"独占 `{` 的行"起解析，逐个回退。
退出码约定：`0` 成功、`3` 校验失败（空白/断源/哈希不匹配）、其余运行错误。

## 对齐门禁

`engine/alignment.py` 把 SKILL.md 的 11 字段对齐块固化为 `Alignment` dataclass。
写操作（extract/package/validate/export/render）在 `ops.py` 中先调 `alignment.require_confirmed()`，
未确认即抛 `SafetyError` 阻断。GUI 中对应"生成对齐块 + 勾选确认"两步。

## 安全不变量（defense-in-depth）

`engine/safety.py` 在派发前校验，脚本端各自再校验一次：

| 不变量 | 校验点 |
|---|---|
| 永不覆盖 | `assert_absent` + 脚本 `open("x")` / `FileExistsError` |
| 版本化输出 | `versioning.versioned_path` 生成 `name_v1_YYYYMMDD`，碰撞自增 |
| 拒绝活动锁 | `assert_no_active_locks` + `extract_project_data` 扫 `*.lock` |
| 拒绝断裂源 | `assert_no_broken_sources` + `package_project` 检查 `broken_count` |
| PNG 像素校验 | `render_classified_lines` 内部调 `verify_png`，`ops.render_classified` 后再独立校验 |

## 设置持久化

`config.py` 的 `Settings` 数据类（无 GUI 依赖，CLI/GUI 共用）落盘到 `~/.gisdo/settings.json`：
运行时路径、输出根目录、LLM 配置（`ai_base_url`/`ai_api_key`/`ai_model`/
`ai_thinking_level`）、`autonomy_mode`。思考强度的 `auto` 不覆盖模型默认行为，`disabled`
显式关闭，其他档位由 LLM 客户端按方舟/DeepSeek、百炼/Moonshot 或标准 OpenAI 兼容参数转换。
`gui/state.py` 的 `AppState` import 并 re-export `Settings`，选定运行时后自动回写，
下次启动 `restore_runtimes()` 恢复。新增字段用默认值，旧 settings.json 仍兼容。

**项目 + 对话管理**：`project.py` 的 `ProjectStore`（无 GUI 依赖）落盘 `~/.gisdo/projects.json`，
含项目列表、当前激活项目 id，以及各项目的会话元数据和当前会话 id；消息正文独立存到
`~/.gisdo/projects/<project-id>/conversations/<conversation-id>.json`
（`Agent.save_history`/`load_history`，格式为除 system 外的完整 OpenAI 消息，加载时经
`sanitize_history` 修剪孤儿 tool 消息保证合法性）。旧版单一 `history.json` 首次打开时复制
到默认会话且原文件保留。GUI「项目」页管理项目，Agent 页管理项目内会话；重置只清空当前
会话。写工具 `normalize_args` 把省略的输出参数补到当前项目
地图输出文件夹（`versioned_path`/`versioned_file`），对齐块显示补全后路径。

## 不在范围

- GUI 自动化操控 GeoScene Pro 桌面（仅脚本化 arcpy.mp/arcpy.mapping）
- 企业库/在线服务的真正数据抓取（只记录连接/服务）
- GeoAI 模型训练流程（仅保留路由说明）
- macOS/Linux（arcpy 仅 Windows）
