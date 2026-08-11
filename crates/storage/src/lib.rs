use std::fs;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use gisdo_domain::{
    ArtifactStatus, AutonomyMode, Conversation, ExecutionEngine, Message, Project, Settings,
    TaskRecord, TaskStatus,
};
use gisdo_safety::sha256_file;
use rusqlite::{Connection, OptionalExtension, Transaction, params};
use serde::Deserialize;
use serde_json::Value;
use thiserror::Error;
use uuid::Uuid;

const SCHEMA_VERSION: i64 = 1;
const KEYRING_SERVICE: &str = "GISdo";
const PREVIEW_KEYRING_SERVICE: &str = "GISdo Next";
const LLM_KEY_ACCOUNT: &str = "llm-api-key";

#[derive(Debug, Error)]
pub enum StorageError {
    #[error(transparent)]
    Sqlite(#[from] rusqlite::Error),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("系统凭据存储失败：{0}")]
    Credential(String),
    #[error("无法确定本地应用数据目录")]
    NoLocalDataDirectory,
}

pub trait CredentialStore: Send + Sync {
    fn set(&self, reference: &str, secret: &str) -> Result<(), StorageError>;
    fn get(&self, reference: &str) -> Result<Option<String>, StorageError>;
    fn delete(&self, reference: &str) -> Result<(), StorageError>;
}

#[derive(Debug, Clone, Copy, Default)]
pub struct WindowsCredentialStore;

impl CredentialStore for WindowsCredentialStore {
    fn set(&self, reference: &str, secret: &str) -> Result<(), StorageError> {
        keyring::Entry::new(KEYRING_SERVICE, reference)
            .and_then(|entry| entry.set_password(secret))
            .map_err(|error| StorageError::Credential(error.to_string()))
    }

    fn get(&self, reference: &str) -> Result<Option<String>, StorageError> {
        match keyring::Entry::new(KEYRING_SERVICE, reference).and_then(|entry| entry.get_password())
        {
            Ok(secret) => Ok(Some(secret)),
            Err(keyring::Error::NoEntry) => {
                match keyring::Entry::new(PREVIEW_KEYRING_SERVICE, reference)
                    .and_then(|entry| entry.get_password())
                {
                    Ok(secret) => {
                        self.set(reference, &secret)?;
                        Ok(Some(secret))
                    }
                    Err(keyring::Error::NoEntry) => Ok(None),
                    Err(error) => Err(StorageError::Credential(error.to_string())),
                }
            }
            Err(error) => Err(StorageError::Credential(error.to_string())),
        }
    }

    fn delete(&self, reference: &str) -> Result<(), StorageError> {
        match keyring::Entry::new(KEYRING_SERVICE, reference)
            .and_then(|entry| entry.delete_credential())
        {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(StorageError::Credential(error.to_string())),
        }
    }
}

pub fn database_path() -> Result<PathBuf, StorageError> {
    if let Some(root) = std::env::var_os("LOCALAPPDATA") {
        return Ok(PathBuf::from(root).join("GISdo").join("gisdo.db"));
    }
    Err(StorageError::NoLocalDataDirectory)
}

fn preview_database_path() -> Result<PathBuf, StorageError> {
    if let Some(root) = std::env::var_os("LOCALAPPDATA") {
        return Ok(PathBuf::from(root).join("GISdo Next").join("gisdo.db"));
    }
    Err(StorageError::NoLocalDataDirectory)
}

fn migrate_preview_database(source: &Path, destination: &Path) -> Result<bool, StorageError> {
    if destination.exists() || !source.is_file() {
        return Ok(false);
    }
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    let source_connection = Connection::open_with_flags(
        source,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    source_connection.execute("VACUUM INTO ?1", [destination.to_string_lossy().as_ref()])?;
    Ok(true)
}

pub struct Database {
    connection: Connection,
}

#[derive(Debug, Clone, Default)]
pub struct PerformanceMetric {
    pub task_id: Option<Uuid>,
    pub phase: String,
    pub duration_ms: u64,
    pub first_token_ms: Option<u64>,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub cached_tokens: Option<u64>,
}

impl Database {
    pub fn open_default() -> Result<Self, StorageError> {
        let path = database_path()?;
        migrate_preview_database(&preview_database_path()?, &path)?;
        Self::open(path)
    }

    pub fn open(path: impl AsRef<Path>) -> Result<Self, StorageError> {
        let path = path.as_ref();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let connection = Connection::open(path)?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.busy_timeout(std::time::Duration::from_secs(5))?;
        let mut database = Self { connection };
        database.migrate()?;
        database.mark_interrupted_tasks_uncertain()?;
        Ok(database)
    }

    pub fn open_memory() -> Result<Self, StorageError> {
        let connection = Connection::open_in_memory()?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        let mut database = Self { connection };
        database.migrate()?;
        Ok(database)
    }

    fn migrate(&mut self) -> Result<(), StorageError> {
        let transaction = self.connection.transaction()?;
        transaction.execute_batch(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id = 1), body_json TEXT NOT NULL, updated_at TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS runtimes (kind TEXT PRIMARY KEY, python_path TEXT NOT NULL, version TEXT, healthy INTEGER NOT NULL DEFAULT 0, probed_at TEXT);
             CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, project_dir TEXT NOT NULL, map_output_dir TEXT NOT NULL, created_at TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, conversation_id TEXT, goal TEXT NOT NULL, status TEXT NOT NULL, plan_json TEXT, plan_hash TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS steps (task_id TEXT NOT NULL REFERENCES tasks(id), step_id TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, started_at TEXT, completed_at TEXT, PRIMARY KEY(task_id, step_id));
             CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), step_id TEXT, path TEXT NOT NULL, status TEXT NOT NULL, validation_json TEXT, created_at TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS performance_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, phase TEXT NOT NULL, duration_ms INTEGER NOT NULL, first_token_ms INTEGER, input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER, cache_hit_ratio REAL, created_at TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS import_sources (path TEXT NOT NULL, sha256 TEXT NOT NULL, imported_at TEXT NOT NULL, PRIMARY KEY(path, sha256));
             CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations(project_id, updated_at DESC);
             CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
             CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, updated_at DESC);"
        )?;
        transaction.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?1, ?2)",
            params![SCHEMA_VERSION, Utc::now().to_rfc3339()],
        )?;
        transaction.commit()?;
        Ok(())
    }

    pub fn schema_version(&self) -> Result<i64, StorageError> {
        Ok(self
            .connection
            .query_row("SELECT MAX(version) FROM schema_migrations", [], |row| {
                row.get::<_, Option<i64>>(0)
            })?
            .unwrap_or(0))
    }

    pub fn load_settings(&self) -> Result<Settings, StorageError> {
        let body: Option<String> = self
            .connection
            .query_row("SELECT body_json FROM settings WHERE id=1", [], |row| {
                row.get(0)
            })
            .optional()?;
        body.map(|json| serde_json::from_str(&json).map_err(StorageError::from))
            .unwrap_or_else(|| Ok(Settings::default()))
    }

    pub fn save_settings(&self, settings: &Settings) -> Result<(), StorageError> {
        self.connection.execute(
            "INSERT INTO settings(id, body_json, updated_at) VALUES(1, ?1, ?2)
             ON CONFLICT(id) DO UPDATE SET body_json=excluded.body_json, updated_at=excluded.updated_at",
            params![serde_json::to_string(settings)?, Utc::now().to_rfc3339()],
        )?;
        Ok(())
    }

    pub fn list_projects(&self) -> Result<Vec<Project>, StorageError> {
        let mut statement = self.connection.prepare("SELECT id,name,project_dir,map_output_dir,created_at FROM projects ORDER BY created_at")?;
        let rows = statement.query_map([], |row| {
            Ok(Project {
                id: parse_uuid(row.get::<_, String>(0)?),
                name: row.get(1)?,
                project_dir: PathBuf::from(row.get::<_, String>(2)?),
                map_output_dir: PathBuf::from(row.get::<_, String>(3)?),
                created_at: parse_time(row.get::<_, String>(4)?),
            })
        })?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn upsert_project(&self, project: &Project) -> Result<(), StorageError> {
        self.connection.execute(
            "INSERT INTO projects(id,name,project_dir,map_output_dir,created_at) VALUES(?1,?2,?3,?4,?5)
             ON CONFLICT(id) DO UPDATE SET name=excluded.name, project_dir=excluded.project_dir, map_output_dir=excluded.map_output_dir",
            params![project.id.to_string(), project.name, project.project_dir.to_string_lossy(), project.map_output_dir.to_string_lossy(), project.created_at.to_rfc3339()],
        )?;
        Ok(())
    }

    pub fn list_conversations(&self, project_id: Uuid) -> Result<Vec<Conversation>, StorageError> {
        let mut statement = self.connection.prepare("SELECT id,title,created_at,updated_at FROM conversations WHERE project_id=?1 ORDER BY updated_at DESC")?;
        let rows = statement.query_map([project_id.to_string()], |row| {
            Ok(Conversation {
                id: parse_uuid(row.get::<_, String>(0)?),
                project_id,
                title: row.get(1)?,
                created_at: parse_time(row.get::<_, String>(2)?),
                updated_at: parse_time(row.get::<_, String>(3)?),
            })
        })?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn upsert_conversation(&self, conversation: &Conversation) -> Result<(), StorageError> {
        self.connection.execute(
            "INSERT INTO conversations(id,project_id,title,created_at,updated_at) VALUES(?1,?2,?3,?4,?5)
             ON CONFLICT(id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at",
            params![conversation.id.to_string(), conversation.project_id.to_string(), conversation.title, conversation.created_at.to_rfc3339(), conversation.updated_at.to_rfc3339()],
        )?;
        Ok(())
    }

    pub fn append_message(&self, message: &Message) -> Result<(), StorageError> {
        self.connection.execute("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?1,?2,?3,?4,?5)",
            params![message.id.to_string(), message.conversation_id.to_string(), message.role, message.content, message.created_at.to_rfc3339()])?;
        Ok(())
    }

    pub fn list_messages(&self, conversation_id: Uuid) -> Result<Vec<Message>, StorageError> {
        let mut statement = self.connection.prepare("SELECT id,role,content,created_at FROM messages WHERE conversation_id=?1 ORDER BY created_at")?;
        let rows = statement.query_map([conversation_id.to_string()], |row| {
            Ok(Message {
                id: parse_uuid(row.get::<_, String>(0)?),
                conversation_id,
                role: row.get(1)?,
                content: row.get(2)?,
                created_at: parse_time(row.get::<_, String>(3)?),
            })
        })?;
        Ok(rows.collect::<Result<Vec<_>, _>>()?)
    }

    pub fn save_task(&self, task: &TaskRecord) -> Result<(), StorageError> {
        self.connection.execute(
            "INSERT INTO tasks(id,conversation_id,goal,status,plan_json,plan_hash,created_at,updated_at) VALUES(?1,?2,?3,?4,?5,?6,?7,?8)
             ON CONFLICT(id) DO UPDATE SET status=excluded.status, plan_json=excluded.plan_json, plan_hash=excluded.plan_hash, updated_at=excluded.updated_at",
            params![task.id.to_string(), task.conversation_id.map(|id| id.to_string()), task.goal, enum_json(&task.status),
                task.plan.as_ref().map(serde_json::to_string).transpose()?, task.plan_hash, task.created_at.to_rfc3339(), task.updated_at.to_rfc3339()],
        )?;
        Ok(())
    }

    pub fn save_step_result(
        &self,
        task_id: Uuid,
        result: &gisdo_domain::StepResult,
    ) -> Result<(), StorageError> {
        let now = Utc::now().to_rfc3339();
        self.connection.execute(
            "INSERT INTO steps(task_id,step_id,status,result_json,started_at,completed_at) VALUES(?1,?2,'completed',?3,NULL,?4)
             ON CONFLICT(task_id,step_id) DO UPDATE SET status='completed', result_json=excluded.result_json, completed_at=excluded.completed_at",
            params![task_id.to_string(), result.step_id, serde_json::to_string(result)?, now],
        )?;
        self.record_metric(&PerformanceMetric {
            task_id: Some(task_id),
            phase: "worker_step".into(),
            duration_ms: result.duration_ms,
            ..PerformanceMetric::default()
        })?;
        for artifact in &result.artifacts {
            self.mark_artifact(
                task_id,
                Some(&result.step_id),
                artifact,
                ArtifactStatus::Verified,
                Some(&result.summary),
            )?;
        }
        Ok(())
    }

    pub fn mark_artifact(
        &self,
        task_id: Uuid,
        step_id: Option<&str>,
        path: &Path,
        status: ArtifactStatus,
        validation: Option<&Value>,
    ) -> Result<(), StorageError> {
        self.connection.execute("INSERT INTO artifacts(id,task_id,step_id,path,status,validation_json,created_at) VALUES(?1,?2,?3,?4,?5,?6,?7)",
            params![Uuid::new_v4().to_string(), task_id.to_string(), step_id, path.to_string_lossy(), enum_json(&status), validation.map(serde_json::to_string).transpose()?, Utc::now().to_rfc3339()])?;
        Ok(())
    }

    pub fn record_metric(&self, metric: &PerformanceMetric) -> Result<(), StorageError> {
        let ratio = match (metric.input_tokens, metric.cached_tokens) {
            (Some(input), Some(cached)) if input > 0 => Some(cached as f64 / input as f64),
            _ => None,
        };
        self.connection.execute("INSERT INTO performance_metrics(task_id,phase,duration_ms,first_token_ms,input_tokens,output_tokens,cached_tokens,cache_hit_ratio,created_at) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            params![metric.task_id.map(|id| id.to_string()), metric.phase, metric.duration_ms, metric.first_token_ms, metric.input_tokens, metric.output_tokens, metric.cached_tokens, ratio, Utc::now().to_rfc3339()])?;
        Ok(())
    }

    fn mark_interrupted_tasks_uncertain(&self) -> Result<usize, StorageError> {
        let statuses = [TaskStatus::Running, TaskStatus::Cancelling];
        let affected = self.connection.execute(
            "UPDATE tasks SET status=?1, updated_at=?2 WHERE status IN (?3,?4)",
            params![
                enum_json(&TaskStatus::Uncertain),
                Utc::now().to_rfc3339(),
                enum_json(&statuses[0]),
                enum_json(&statuses[1])
            ],
        )?;
        Ok(affected)
    }

    pub fn import_legacy(
        &mut self,
        legacy_root: &Path,
        credentials: &dyn CredentialStore,
    ) -> Result<ImportReport, StorageError> {
        let mut report = ImportReport::default();
        let settings_path = legacy_root.join("settings.json");
        if settings_path.is_file() {
            self.import_settings_file(&settings_path, credentials, &mut report)?;
        }
        let projects_path = legacy_root.join("projects.json");
        if projects_path.is_file() {
            self.import_projects_file(legacy_root, &projects_path, &mut report)?;
        }
        Ok(report)
    }

    fn already_imported(
        transaction: &Transaction<'_>,
        path: &Path,
        sha: &str,
    ) -> Result<bool, rusqlite::Error> {
        transaction
            .query_row(
                "SELECT 1 FROM import_sources WHERE path=?1 AND sha256=?2",
                params![path.to_string_lossy(), sha],
                |_| Ok(()),
            )
            .optional()
            .map(|value| value.is_some())
    }

    fn record_import(
        transaction: &Transaction<'_>,
        path: &Path,
        sha: &str,
    ) -> Result<(), rusqlite::Error> {
        transaction.execute(
            "INSERT INTO import_sources(path,sha256,imported_at) VALUES(?1,?2,?3)",
            params![path.to_string_lossy(), sha, Utc::now().to_rfc3339()],
        )?;
        Ok(())
    }

    fn import_settings_file(
        &mut self,
        path: &Path,
        credentials: &dyn CredentialStore,
        report: &mut ImportReport,
    ) -> Result<(), StorageError> {
        let sha = sha256_file(path)?;
        let transaction = self.connection.transaction()?;
        if Self::already_imported(&transaction, path, &sha)? {
            report.skipped += 1;
            return Ok(());
        }
        let legacy: LegacySettings = serde_json::from_slice(&fs::read(path)?)?;
        let credential_ref = if legacy.ai_api_key.is_empty() {
            None
        } else {
            credentials.set(LLM_KEY_ACCOUNT, &legacy.ai_api_key)?;
            Some(LLM_KEY_ACCOUNT.to_owned())
        };
        let settings = Settings {
            modern_python: legacy.modern_python,
            arcmap_python: legacy.arcmap_python,
            output_root: legacy.output_root,
            ai_enabled: legacy.ai_enabled,
            ai_base_url: legacy.ai_base_url,
            ai_credential_ref: credential_ref,
            ai_model: legacy.ai_model,
            ai_thinking_level: legacy.ai_thinking_level,
            autonomy_mode: parse_autonomy(&legacy.autonomy_mode),
            language: legacy.language,
            execution_engine: ExecutionEngine::Worker,
        };
        transaction.execute("INSERT INTO settings(id,body_json,updated_at) VALUES(1,?1,?2) ON CONFLICT(id) DO NOTHING",
            params![serde_json::to_string(&settings)?, Utc::now().to_rfc3339()])?;
        Self::record_import(&transaction, path, &sha)?;
        transaction.commit()?;
        report.imported += 1;
        Ok(())
    }

    fn import_projects_file(
        &mut self,
        legacy_root: &Path,
        path: &Path,
        report: &mut ImportReport,
    ) -> Result<(), StorageError> {
        let sha = sha256_file(path)?;
        let transaction = self.connection.transaction()?;
        if Self::already_imported(&transaction, path, &sha)? {
            report.skipped += 1;
            return Ok(());
        }
        let root: LegacyProjects = serde_json::from_slice(&fs::read(path)?)?;
        for legacy_project in root.projects {
            let project_id = parse_uuid_or_v5(&legacy_project.id);
            let created_at = parse_time(legacy_project.created_at.clone());
            let project_storage_dir = legacy_root.join("projects").join(&legacy_project.id);
            let has_conversations = !legacy_project.conversations.is_empty();
            transaction.execute("INSERT OR IGNORE INTO projects(id,name,project_dir,map_output_dir,created_at) VALUES(?1,?2,?3,?4,?5)",
                params![project_id.to_string(), legacy_project.name, legacy_project.project_dir, legacy_project.map_output_dir, created_at.to_rfc3339()])?;
            for legacy_conversation in legacy_project.conversations {
                let conversation_id = parse_uuid_or_v5(&legacy_conversation.id);
                transaction.execute("INSERT OR IGNORE INTO conversations(id,project_id,title,created_at,updated_at) VALUES(?1,?2,?3,?4,?5)",
                    params![conversation_id.to_string(), project_id.to_string(), legacy_conversation.title,
                        parse_time(legacy_conversation.created_at).to_rfc3339(), parse_time(legacy_conversation.updated_at).to_rfc3339()])?;
                let history = project_storage_dir
                    .join("conversations")
                    .join(format!("{}.json", legacy_conversation.id));
                import_history(&transaction, &history, conversation_id, report)?;
            }
            let legacy_history = project_storage_dir.join("history.json");
            if !has_conversations && legacy_history.is_file() {
                let conversation_id =
                    deterministic_uuid(&format!("{}:legacy-conversation", legacy_project.id));
                transaction.execute(
                    "INSERT OR IGNORE INTO conversations(id,project_id,title,created_at,updated_at) VALUES(?1,?2,'原对话',?3,?3)",
                    params![conversation_id.to_string(), project_id.to_string(), created_at.to_rfc3339()],
                )?;
                import_history(&transaction, &legacy_history, conversation_id, report)?;
            }
        }
        Self::record_import(&transaction, path, &sha)?;
        transaction.commit()?;
        report.imported += 1;
        Ok(())
    }

    #[cfg(test)]
    fn scalar_i64(&self, sql: &str) -> i64 {
        self.connection
            .query_row(sql, [], |row| row.get(0))
            .unwrap()
    }
}

fn import_history(
    transaction: &Transaction<'_>,
    path: &Path,
    conversation_id: Uuid,
    report: &mut ImportReport,
) -> Result<(), StorageError> {
    if !path.is_file() {
        return Ok(());
    }
    let sha = sha256_file(path)?;
    if Database::already_imported(transaction, path, &sha)? {
        report.skipped += 1;
        return Ok(());
    }
    let history: LegacyHistory = match serde_json::from_slice(&fs::read(path)?) {
        Ok(history) => history,
        Err(error) => {
            report
                .warnings
                .push(format!("跳过损坏的历史文件 {}：{error}", path.display()));
            Database::record_import(transaction, path, &sha)?;
            return Ok(());
        }
    };
    let base = Utc::now();
    for (index, message) in history.messages.into_iter().enumerate() {
        let content = message
            .content
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| message.content.to_string());
        let id = deterministic_uuid(&format!("{}:{index}", path.display()));
        transaction.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,created_at) VALUES(?1,?2,?3,?4,?5)",
            params![id.to_string(), conversation_id.to_string(), message.role, content, (base + chrono::Duration::milliseconds(index as i64)).to_rfc3339()])?;
    }
    Database::record_import(transaction, path, &sha)?;
    report.imported += 1;
    Ok(())
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct ImportReport {
    pub imported: usize,
    pub skipped: usize,
    pub warnings: Vec<String>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct LegacySettings {
    modern_python: String,
    arcmap_python: String,
    output_root: String,
    ai_enabled: bool,
    ai_base_url: String,
    ai_api_key: String,
    ai_model: String,
    ai_thinking_level: String,
    autonomy_mode: String,
    language: String,
}

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct LegacyProjects {
    projects: Vec<LegacyProject>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct LegacyProject {
    id: String,
    name: String,
    project_dir: String,
    map_output_dir: String,
    created_at: String,
    conversations: Vec<LegacyConversation>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct LegacyConversation {
    id: String,
    title: String,
    created_at: String,
    updated_at: String,
}

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct LegacyHistory {
    messages: Vec<LegacyMessage>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(default)]
struct LegacyMessage {
    role: String,
    content: Value,
}

fn enum_json<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_value(value)
        .ok()
        .and_then(|v| v.as_str().map(str::to_owned))
        .unwrap_or_default()
}

fn parse_autonomy(value: &str) -> AutonomyMode {
    match value {
        "autonomous" => AutonomyMode::Autonomous,
        "confirm_every_step" => AutonomyMode::ConfirmEveryStep,
        _ => AutonomyMode::ConfirmWrites,
    }
}

fn parse_uuid(value: String) -> Uuid {
    Uuid::parse_str(&value).unwrap_or_else(|_| deterministic_uuid(&value))
}
fn parse_uuid_or_v5(value: &str) -> Uuid {
    Uuid::parse_str(value).unwrap_or_else(|_| deterministic_uuid(value))
}
fn deterministic_uuid(value: &str) -> Uuid {
    Uuid::new_v5(&Uuid::NAMESPACE_OID, value.as_bytes())
}
fn parse_time(value: String) -> DateTime<Utc> {
    DateTime::parse_from_rfc3339(&value)
        .map(|v| v.with_timezone(&Utc))
        .unwrap_or_else(|_| Utc::now())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Mutex;

    #[derive(Default)]
    struct MemoryCredentials(Mutex<HashMap<String, String>>);
    impl CredentialStore for MemoryCredentials {
        fn set(&self, reference: &str, secret: &str) -> Result<(), StorageError> {
            self.0
                .lock()
                .unwrap()
                .insert(reference.into(), secret.into());
            Ok(())
        }
        fn get(&self, reference: &str) -> Result<Option<String>, StorageError> {
            Ok(self.0.lock().unwrap().get(reference).cloned())
        }
        fn delete(&self, reference: &str) -> Result<(), StorageError> {
            self.0.lock().unwrap().remove(reference);
            Ok(())
        }
    }

    #[test]
    fn schema_is_current() {
        assert_eq!(
            Database::open_memory().unwrap().schema_version().unwrap(),
            SCHEMA_VERSION
        );
    }

    #[test]
    fn legacy_import_is_idempotent_and_key_is_not_in_database() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("settings.json"),
            r#"{"ai_enabled":true,"ai_api_key":"super-secret","ai_model":"x","language":"zh"}"#,
        )
        .unwrap();
        fs::write(dir.path().join("projects.json"), r#"{"projects":[]}"#).unwrap();
        let credentials = MemoryCredentials::default();
        let mut db = Database::open_memory().unwrap();
        let first = db.import_legacy(dir.path(), &credentials).unwrap();
        let second = db.import_legacy(dir.path(), &credentials).unwrap();
        assert_eq!(first.imported, 2);
        assert_eq!(second.skipped, 2);
        assert_eq!(
            credentials.get(LLM_KEY_ACCOUNT).unwrap().as_deref(),
            Some("super-secret")
        );
        let dump: String = db
            .connection
            .query_row("SELECT body_json FROM settings", [], |row| row.get(0))
            .unwrap();
        assert!(!dump.contains("super-secret"));
    }

    #[test]
    fn marks_interrupted_tasks_uncertain() {
        let db = Database::open_memory().unwrap();
        db.connection.execute("INSERT INTO tasks(id,goal,status,created_at,updated_at) VALUES('1','x','running','x','x')", []).unwrap();
        assert_eq!(db.mark_interrupted_tasks_uncertain().unwrap(), 1);
        assert_eq!(
            db.scalar_i64("SELECT COUNT(*) FROM tasks WHERE status='uncertain'"),
            1
        );
    }

    #[test]
    fn imports_unsurfaced_legacy_history_and_tolerates_corrupt_history() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("projects.json"), r#"{"projects":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","name":"A","created_at":"2026-01-01T00:00:00+00:00","conversations":[]},{"id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","name":"B","created_at":"2026-01-01T00:00:00+00:00","conversations":[{"id":"cccccccccccccccccccccccccccccccc","title":"bad"}]}]}"#).unwrap();
        let project_a = dir
            .path()
            .join("projects")
            .join("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        let project_b = dir
            .path()
            .join("projects")
            .join("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
            .join("conversations");
        fs::create_dir_all(&project_a).unwrap();
        fs::create_dir_all(&project_b).unwrap();
        fs::write(
            project_a.join("history.json"),
            r#"{"messages":[{"role":"user","content":"旧任务"}]}"#,
        )
        .unwrap();
        fs::write(
            project_b.join("cccccccccccccccccccccccccccccccc.json"),
            "{bad json",
        )
        .unwrap();
        let mut db = Database::open_memory().unwrap();
        let report = db
            .import_legacy(dir.path(), &MemoryCredentials::default())
            .unwrap();
        assert_eq!(db.scalar_i64("SELECT COUNT(*) FROM conversations"), 2);
        assert_eq!(db.scalar_i64("SELECT COUNT(*) FROM messages"), 1);
        assert_eq!(report.warnings.len(), 1);
    }

    #[test]
    fn preview_database_is_copied_once_without_modifying_the_source() {
        let root = tempfile::tempdir().unwrap();
        let preview_path = root.path().join("GISdo Next").join("gisdo.db");
        let destination_path = root.path().join("GISdo").join("gisdo.db");
        let preview = Database::open(&preview_path).unwrap();
        let settings = Settings {
            ai_model: "preview-model".into(),
            ..Settings::default()
        };
        preview.save_settings(&settings).unwrap();
        drop(preview);

        let source_hash = sha256_file(&preview_path).unwrap();
        assert!(migrate_preview_database(&preview_path, &destination_path).unwrap());
        assert!(!migrate_preview_database(&preview_path, &destination_path).unwrap());
        assert_eq!(sha256_file(&preview_path).unwrap(), source_hash);

        let migrated = Database::open(&destination_path).unwrap();
        assert_eq!(migrated.load_settings().unwrap().ai_model, "preview-model");
    }
}
