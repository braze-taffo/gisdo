# GISdo 0.2.0

GISdo 0.2.0 是对 0.1 Python/PySide 版本的 Rust/Tauri 重写，继续作为同一产品发布。

主要变化：

- 使用 Rust 编排器、SQLite 和常驻 ArcPy Worker，降低重复启动与模型往返开销。
- 自动检查用户提供的文件和目录，根据实际数据、坐标系和工具参数自主规划。
- 支持读取项目路线图、任务书和 Office/PDF/文本资料，生成带需求引用的长程制图计划。
- 规划一次、批量执行、汇报一次，保留固定 510 项工具缓存前缀。
- Windows 安装版采用主程序、Worker、Skill 和工具清单分离的多文件布局。
- 首次启动只读导入 0.1 的 `~/.gisdo` 设置、项目与对话数据。

运行 GIS 任务仍需本机安装 ArcGIS Pro、GeoScene Pro 或 ArcMap。当前发布包未包含商业代码签名，Windows SmartScreen 可能显示未知发布者提示。
