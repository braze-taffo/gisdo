//! WorkerStatus 的 serde 契约：前端 worker_status 事件与 UI 状态灯依赖
//! 内部标签枚举的序列化形状（{"status":"ready",...}），此处锁定防止无意漂移。

use gisdo_worker_client::WorkerStatus;
use serde_json::json;
use uuid::Uuid;

#[test]
fn worker_status_is_internally_tagged_with_snake_case() {
    let cases: Vec<(WorkerStatus, serde_json::Value)> = vec![
        (WorkerStatus::Stopped, json!({"status": "stopped"})),
        (WorkerStatus::Starting, json!({"status": "starting"})),
        (
            WorkerStatus::Ready {
                pid: 1234,
                tasks_completed: 2,
            },
            json!({"status": "ready", "pid": 1234, "tasks_completed": 2}),
        ),
        (
            WorkerStatus::Busy {
                pid: 1234,
                task_id: Uuid::nil(),
            },
            json!({"status": "busy", "pid": 1234, "task_id": Uuid::nil()}),
        ),
        (
            WorkerStatus::Recycling {
                reason: "idle".into(),
            },
            json!({"status": "recycling", "reason": "idle"}),
        ),
        (
            WorkerStatus::Failed {
                message: "boom".into(),
            },
            json!({"status": "failed", "message": "boom"}),
        ),
    ];
    for (status, expected) in cases {
        let serialized = serde_json::to_value(&status).unwrap();
        assert_eq!(serialized, expected, "序列化形状漂移：{status:?}");
        assert!(
            serialized.get("status").is_some_and(|tag| tag.is_string()),
            "status 标签必须是字符串，否则前端状态灯解析失败：{serialized}"
        );
    }
}

#[test]
fn bridge_event_payload_keeps_status_flat() {
    // src-tauri 的 bridge_worker_events 会把 detail 序列化后取 "status" 字段；
    // 该函数依赖此处锁定的形状。
    let detail = serde_json::to_value(WorkerStatus::Ready {
        pid: 7,
        tasks_completed: 0,
    })
    .unwrap();
    let payload = json!({
        "runtime": "pro",
        "status": detail.get("status").cloned().unwrap_or_default(),
        "detail": detail,
    });
    assert_eq!(payload["status"], json!("ready"));
    assert_eq!(payload["detail"]["pid"], json!(7));
}
