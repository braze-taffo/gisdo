# GISdo architecture

```mermaid
flowchart LR
    UI["React 工作台"] -->|Tauri Commands| CORE["Rust Orchestrator"]
    CORE --> DOCS["Document Intake\nPDF / Office / Text"]
    DOCS --> SKILL["document-intake Skill\n需求与交付物追踪"]
    SKILL --> PLAN
    CORE --> PLAN["LLM Planner\n固定 510 工具前缀"]
    PLAN --> GATE["计划校验\nDAG / Schema / 路径 / SHA-256"]
    GATE -->|一次确认| DAG["DAG Executor"]
    DAG --> PRO["常驻 Pro Worker\nPython 3.11 + ArcPy"]
    DAG --> MAP["常驻 ArcMap Worker\nPython 2.7 + ArcPy"]
    DAG --> NATIVE["Rust 原生校验"]
    PRO --> RESULT["结构化结果"]
    MAP --> RESULT
    NATIVE --> RESULT
    RESULT --> DB["SQLite / 本地指标"]
    RESULT --> REPORT["LLM 汇报\n失败时确定性回退"]
    REPORT -->|Tauri Events| UI
```

## Task state

```mermaid
stateDiagram-v2
    [*] --> Planning
    Planning --> NeedsInput
    NeedsInput --> Planning
    Planning --> AwaitingApproval
    Planning --> Running: autonomous / read-only
    AwaitingApproval --> Running: hash matches
    Running --> Completed
    Running --> Planning: unknown pre-write failure / once only
    Running --> Uncertain: worker killed during write
    Running --> Failed
    Running --> Cancelling
    Cancelling --> Cancelled
```

## Security boundaries

The frontend never opens SQLite, reads credentials, builds Worker parameters, or accesses the filesystem. Every write is derived and checked twice: first by Rust against the repository tool registry, then by the Python Worker against live ArcPy parameter metadata. `arcpy.env.overwriteOutput` is always false.

Windows Credential Manager holds the LLM API key. SQLite stores only `llm-api-key`. Worker stdout is protocol-only UTF-8 JSON Lines; any non-JSON stdout is a protocol failure.

## Worker lifetime

Workers preheat 200 ms after Tauri setup. A runtime has one in-flight operation and a Rust queue. The supervisor recycles after 20 tasks, 1.5 GB resident memory, protocol damage, or a severe ArcPy exception. A Windows Job Object terminates descendants when the desktop app exits.
