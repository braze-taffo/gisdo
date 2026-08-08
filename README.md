# GISdo · AI Agent 驱动的 GeoScene 工作台

把 `gisdo` skill 做成独立桌面软件：**AI Agent 当司机**，用自然语言下 GIS 任务，自主调用工具完成检查、提取、打包、出图与校验。在 **不直接 import arcpy** 的前提下安全操作 GeoScene Pro / ArcGIS Pro / ArcMap 工程与数据。

> 面向学生/研究者/地理处理从业者的 AI GIS 助手。Agent 大脑接 OpenAI 格式兼容的云端 API（DeepSeek / Qwen / Kimi / OpenAI / 本地 Ollama 通吃），工具层经确定性引擎派发到本机 GeoScene/ArcGIS 运行时执行。安全不变量由代码强制，Agent 无法绕过。

## 核心能力

- **AI Agent 对话**：自然语言下任务，Agent 自主推理 → 调工具 → 看结果 → 下一步，直到完成
- **真流式输出**：模型回复边生成边显示，「停止」随时打断
- **项目 + 对话管理**：每个项目配「项目文件夹」与「地图输出文件夹」，写操作默认落点；每项目独立对话历史，切换项目即切换会话（coding harness 式）
- **三档自主程度**：仅写操作确认 / 全程自主 / 每步都确认，运行时自由切换
- **默认对齐**：用户未明确给出所有必要信息时，Agent 先 `ask_user` 与用户对齐（如跨坐标系时选哪个坐标系），不擅作主张
- **18 个工具**（12 只读 + 6 写）：运行时发现、工程/数据检查、GIS 工具清单实时查询、写操作对齐确认
- **确定性子命令**：`discover / inspect / extract / package / validate / render / verify / preflight`，不依赖 Agent 也能用
- **14 个引擎脚本**：`subprocess` 派发到对应 Python 运行时，解析 JSON stdout

```
你（自然语言）
  └─ Agent 循环（LLM 大脑，OpenAI 兼容，真流式）
       └─ 工具层（12 只读 + 6 写 → ops）
            └─ 引擎（14 个脚本，subprocess 派发到对应 Python 运行时）
                 · GeoScene/ArcGIS Pro Python → APRX/GDB/提取/打包/校验
                 · ArcMap Python 2.7 → MXD/旧数据集/线桥导出
                 · 应用 Python → 渲染/像素校验/运行时发现
```

**安全不变量由代码强制，不由 LLM 强制**：永不覆盖、版本化输出、拒绝活动锁、拒绝断裂源、PNG 必过像素校验。Agent 想越界，工具层直接拒绝并把错误喂回 LLM 让它改方案。

## 安装

需要本机已装 **GeoScene Pro 和/或 ArcMap**（提供 arcpy 运行时；应用进程永不 import arcpy）。

```bash
# 需要 Python 3.10+
pip install -e .            # GUI + 确定性引擎
pip install -e .[ai]        # + Agent（openai SDK）
```

> 国内网络可加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

### 打包（可选）

`packaging/gisdo.spec` 提供 PyInstaller 配置：

```bash
cd packaging && python -m PyInstaller gisdo.spec --noconfirm
```

产物为单文件 `dist/gisdo.exe`（约 200MB，含 PySide6/matplotlib/numpy/openai）。打包后纯 Python 脚本（运行时发现/渲染/校验）改在主进程内 `runpy` 执行，无需额外解释器；arcpy 工作仍派发到真实 GeoScene/ArcMap 运行时。

## 快速开始

### Agent 对话（推荐）

```bash
gisdo chat --base-url https://api.deepseek.com/v1 --api-key sk-xxx --model deepseek-chat
```

或在 GUI 里配好 LLM（「设置」页，支持预设端点）后到「Agent」页用自然语言下任务：

```
你 > 帮我把 D:\proj\foo.aprx 里的数据提取到项目地图输出文件夹
🤖 好的，我先检查工程结构，确认坐标系后再动手…
🔧 inspect_aprx({"project":"D:\\proj\\foo.aprx"})
🤖 工程有两个地图，无断裂源。输出将落在「地图输出文件夹」。开始提取…
```

先到「项目」页新建项目并设置**地图输出文件夹**，写操作输出会自动落到那里。

### 图形界面

```bash
gisdo gui
```

侧边栏：运行时 → 项目 → Agent → 检查 → 提取 → 出图 → 设置。「Agent」页默认打开。

### 确定性子命令

```bash
gisdo discover                  # 发现运行时
gisdo inspect aprx <project.aprx>          # 只读检查 APRX
gisdo extract <project.aprx> [out] --yes   # 提取（先打印对齐块）
gisdo package <project.aprx> [out.ppkx] --yes
gisdo validate <pkg.ppkx> [out] --yes
gisdo render <lines.json> <out.png> --breaks 0,20,40,60,80,100
gisdo verify <map.png>                      # 像素校验
gisdo preflight --project x.aprx --output out_v1   # 写前预检
```

### 项目管理（CLI）

```bash
gisdo project list / new <name> / use <name> / rm <name>
```

## 安全不变量

直接继承自 SKILL.md，由 `engine/safety.py` + 脚本端双层强制（Agent 无法绕过）：

1. 永不删除/截断/重命名/移动用户文件
2. 永不覆盖既有输出（`overwriteOutput = False`）
3. 每个输出路径开工前必须不存在；每次尝试用新版本化目录 `name_v1_YYYYMMDD`
4. 拒绝快照含 `.lock` 的活动文件 GDB
5. 断裂源未解决前拒绝打包（除非显式 `--allow-broken`）
6. PNG 导出必须过 `verify_png` 像素校验（文件存在/大小/返回码不算数）
7. 失败时保留日志与部分输出，不清理，建议新版本号重试

## 项目结构

```
src/gisdo/
  cli.py                 命令行（含 gisdo chat / gisdo project）
  config.py              设置数据类（无 GUI 依赖，CLI/GUI 共用）
  project.py             项目注册表 + 对话历史路径（无 GUI 依赖）
  agent/                 AI Agent 核心（无 PySide6 依赖）
    llm.py               OpenAI 兼容客户端 + 消息类型 + 真流式
    prompt.py            系统提示词（源自 SKILL.md）
    tools.py             工具注册表（ops 包装为 function-calling）
    loop.py              Agent 循环 + 三档自主 + 项目上下文 + 历史持久化
  engine/                确定性引擎 + 编排层
    scripts/             14 个引擎脚本（subprocess 工具集）
    runtime.py / runner.py / jsonutil.py / versioning.py
    safety.py / alignment.py / preflight.py / failure.py / reports.py
    ops.py               高层操作封装（工具层调它）
  gui/                   PySide6 外壳
    app.py / workers.py / state.py / widgets.py
    views/  chat / project / runtime / inspect / extract / render / settings
packaging/               PyInstaller 打包配置（entry_gui.py + gisdo.spec）
tests/                   215 个单元 + 集成测试（无需 arcpy，fake LLM 驱动）
```

## 测试

```bash
python -m pytest tests/
```

Agent 核心用 FakeLlm（脚本化 tool_calls）驱动，断言多步调用、三档自主、写确认门禁、最大迭代保护、真流式与取消，无需联网或 API key。

## 许可

[MIT](LICENSE)。ArcPy 与 GeoScene/ArcGIS 归 Esri/中地数码所有，本软件不分发它们。
