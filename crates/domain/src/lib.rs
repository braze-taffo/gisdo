use std::path::PathBuf;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeKind {
    Pro,
    Arcmap,
    Legacy,
    Native,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum AutonomyMode {
    #[default]
    ConfirmWrites,
    Autonomous,
    ConfirmEveryStep,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ValidationPolicy {
    None,
    #[default]
    Dataset,
    Png,
    Package,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PlanStep {
    pub id: String,
    #[serde(default)]
    pub stage: Option<String>,
    #[serde(default)]
    pub requirement_refs: Vec<String>,
    pub runtime: RuntimeKind,
    pub tool: String,
    #[serde(default)]
    pub params: Value,
    #[serde(default)]
    pub depends_on: Vec<String>,
    #[serde(default)]
    pub validation: ValidationPolicy,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskPlan {
    pub version: u32,
    pub id: Uuid,
    pub goal: String,
    pub steps: Vec<PlanStep>,
    #[serde(default)]
    pub expected_outputs: Vec<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum PlanOutcome {
    Ready {
        plan: TaskPlan,
    },
    NeedsInput {
        question: String,
        options: Vec<String>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskStatus {
    Queued,
    Planning,
    NeedsInput,
    AwaitingApproval,
    Running,
    Cancelling,
    Completed,
    Failed,
    Cancelled,
    Uncertain,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepStatus {
    Pending,
    AwaitingApproval,
    Running,
    Completed,
    Failed,
    Cancelled,
    Uncertain,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactStatus {
    Expected,
    Verified,
    Partial,
    Uncertain,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Settings {
    pub modern_python: String,
    pub arcmap_python: String,
    pub output_root: String,
    pub ai_enabled: bool,
    pub ai_base_url: String,
    pub ai_credential_ref: Option<String>,
    pub ai_model: String,
    pub ai_thinking_level: String,
    pub autonomy_mode: AutonomyMode,
    pub language: String,
    pub execution_engine: ExecutionEngine,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            modern_python: String::new(),
            arcmap_python: String::new(),
            output_root: String::new(),
            ai_enabled: false,
            ai_base_url: String::new(),
            ai_credential_ref: None,
            ai_model: String::new(),
            ai_thinking_level: "auto".into(),
            autonomy_mode: AutonomyMode::ConfirmWrites,
            language: "zh".into(),
            execution_engine: ExecutionEngine::Worker,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionEngine {
    #[default]
    Worker,
    Legacy,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Project {
    pub id: Uuid,
    pub name: String,
    pub project_dir: PathBuf,
    pub map_output_dir: PathBuf,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Conversation {
    pub id: Uuid,
    pub project_id: Uuid,
    pub title: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Message {
    pub id: Uuid,
    pub conversation_id: Uuid,
    pub role: String,
    pub content: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RuntimeConfig {
    pub kind: RuntimeKind,
    pub python_path: PathBuf,
    pub version: Option<String>,
    pub healthy: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StepResult {
    pub step_id: String,
    pub ok: bool,
    pub summary: Value,
    pub artifacts: Vec<PathBuf>,
    pub duration_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskRecord {
    pub id: Uuid,
    pub conversation_id: Option<Uuid>,
    pub goal: String,
    pub status: TaskStatus,
    pub plan: Option<TaskPlan>,
    pub plan_hash: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl TaskRecord {
    pub fn new(conversation_id: Option<Uuid>, goal: impl Into<String>) -> Self {
        let now = Utc::now();
        Self {
            id: Uuid::new_v4(),
            conversation_id,
            goal: goal.into(),
            status: TaskStatus::Queued,
            plan: None,
            plan_hash: None,
            created_at: now,
            updated_at: now,
        }
    }
}
