"""引擎与编排层。

- ``scripts/``：现有 11 个脚本原样保留，作为 subprocess 工具集。
- ``runtime``：发现并探测 GeoScene/ArcGIS Pro 与 ArcMap 运行时。
- ``runner``：subprocess 派发，流式输出，解析尾 JSON，可取消。
- ``jsonutil``：从可能夹杂 GP 消息的 stdout 中提取末尾 JSON。
- ``versioning``：``name_v1_YYYYMMDD`` 版本化输出路径。
- ``safety``：覆盖/活动锁/断裂源统一校验。
- ``alignment``：对齐确认块构造与确认门禁。
- ``ops``：面向上层的高层操作封装。
"""
