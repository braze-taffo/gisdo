# GISdo

GISdo 0.2 是原 GISdo 的 Rust 重写版。桌面层使用 Tauri 2 + React/TypeScript，Rust 负责规划、DAG、安全门禁、SQLite、LLM 和进程监督；ArcPy 只存在于常驻的 Pro Python 3 / ArcMap Python 2.7 Worker 中。

0.1 的 `~/.gisdo` JSON 不会被修改。0.2 首次启动只读导入一次，之后数据存放在 `%LOCALAPPDATA%\GISdo\gisdo.db`。开发期使用过 GISdo Next 预览版时，也会在新数据库不存在的前提下复制迁移其数据库，原目录继续保留。

## 启动命令

```powershell
cd C:\Users\nac\Documents\gisdo
npm install
npm run tauri dev
```

只调前端样式、无需启动 Rust/ArcPy 时：

```powershell
npm run dev
```

浏览器开发模式会使用仓库内演示数据，不调用本机文件、SQLite、API Key 或 Worker。正式任务只能从 Tauri 桌面应用发起。

面向普通 Windows 用户生成安装版、中文 MSI 和可解压目录版：

```powershell
npm run package:windows
```

发布文件位于 `target/release/distribution/`，并附带 SHA-256 校验和与发布清单。
安装后的 Worker、Skill 和工具清单是独立资源文件，不会全部塞入主程序 EXE。

## 验证

```powershell
cargo fmt --all -- --check
cargo test --workspace
npm test
npm run build
python -m pytest -q tests\test_worker_protocol.py
```

## 架构原则

- 一次规划、批量执行、一次汇报；正常任务最多两次模型调用。
- 510 项官方工具清单保持固定顺序，作为稳定缓存前缀；模型仍能看到工具和官方必填参数。
- Rust 根据工具注册表推导读写属性，不信任模型声明。
- 输出必须是绝对且不存在的版本化路径；不删除、不覆盖、不移动用户文件。
- 执行器随地理处理步骤自动校验 CRS、count、extent；拒绝单独规划 `GetCount`。
- 每个运行时同时一个 ArcPy 操作，Pro 与 ArcMap 可并行。
- 写前握手失败才允许 legacy 回退；写中崩溃保留部分产物并标记 `uncertain`。
- `document-intake` Skill 会在固定缓存前缀之后读取 PDF、Office 和文本资料，将路线图转换为带阶段与来源引用的长程 GIS 计划。

文档解析由 `crates/document-intake` 在本机完成，Skill 工作流位于 `skills/document-intake`。扫描版 PDF 没有文本层时会明确要求 OCR，不会假装已经读懂。

更详细的模块和状态流见 [docs/architecture.md](docs/architecture.md)。

## 已验证基准

在 Pro Python 3.11.11 / ArcPy 11.5 上对 473,761 个广州建筑执行 `Project → Clip`：常驻 Worker 批量执行 48.9 秒，含首次 ArcPy 冷启动 72.6 秒；输出 3,913 个从化建筑，EPSG:4326、count 与 extent 均和旧版 Golden 一致。结构化结果见 `fixtures/benchmark_20260811.json`。
