use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use directories::UserDirs;
use gisdo_domain::{
    Conversation, ExecutionEngine, Message, Project, RuntimeConfig, RuntimeKind, Settings,
    TaskRecord,
};
use gisdo_llm::{LlmClient, LlmConfig};
use gisdo_orchestrator::{LlmPlanner, Orchestrator, OrchestratorEvent};
use gisdo_safety::ToolRegistry;
use gisdo_storage::{CredentialStore, Database, PerformanceMetric, WindowsCredentialStore};
use gisdo_worker_client::{WorkerConfig, WorkerSupervisor, configured_worker};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::sync::{Mutex, RwLock};
use uuid::Uuid;

type LiveOrchestrator = Orchestrator<LlmPlanner, WorkerSupervisor, LlmClient>;

struct AppState {
    database: Mutex<Database>,
    workers: Arc<WorkerSupervisor>,
    orchestrator: RwLock<Option<Arc<LiveOrchestrator>>>,
    registry: Arc<ToolRegistry>,
    app_root: PathBuf,
}

#[derive(Debug, Serialize)]
struct BootstrapPayload {
    settings: Settings,
    projects: Vec<Project>,
    tool_count: usize,
    stable_prefix_hash: String,
    data_model: &'static str,
}

#[derive(Debug, Deserialize)]
struct SaveSettingsInput {
    settings: Settings,
    #[serde(default)]
    api_key: Option<String>,
}

fn command_error(error: impl std::fmt::Display) -> String {
    error.to_string()
}

#[tauri::command]
async fn bootstrap(state: State<'_, AppState>) -> Result<BootstrapPayload, String> {
    let database = state.database.lock().await;
    Ok(BootstrapPayload {
        settings: database.load_settings().map_err(command_error)?,
        projects: database.list_projects().map_err(command_error)?,
        tool_count: state.registry.len(),
        stable_prefix_hash: LlmClient::stable_prefix_hash(),
        data_model: "sqlite_independent_from_legacy_json",
    })
}

#[tauri::command]
async fn get_settings(state: State<'_, AppState>) -> Result<Settings, String> {
    state
        .database
        .lock()
        .await
        .load_settings()
        .map_err(command_error)
}

#[tauri::command]
async fn save_settings(
    app: AppHandle,
    state: State<'_, AppState>,
    input: SaveSettingsInput,
) -> Result<Settings, String> {
    let credentials = WindowsCredentialStore;
    let mut settings = input.settings;
    if let Some(api_key) = input.api_key.filter(|key| !key.trim().is_empty()) {
        let reference = "llm-api-key";
        credentials
            .set(reference, &api_key)
            .map_err(command_error)?;
        settings.ai_credential_ref = Some(reference.into());
    }
    state
        .database
        .lock()
        .await
        .save_settings(&settings)
        .map_err(command_error)?;
    state
        .workers
        .reconfigure(worker_configs(&settings, &state.app_root))
        .await;
    state.workers.set_engine(settings.execution_engine);
    let workers = state.workers.clone();
    tauri::async_runtime::spawn(async move {
        workers.prewarm().await;
    });
    rebuild_orchestrator(&app, &state, &settings).await?;
    Ok(settings)
}

#[tauri::command]
async fn list_projects(state: State<'_, AppState>) -> Result<Vec<Project>, String> {
    state
        .database
        .lock()
        .await
        .list_projects()
        .map_err(command_error)
}

#[tauri::command]
async fn create_project(
    state: State<'_, AppState>,
    name: String,
    project_dir: PathBuf,
    map_output_dir: PathBuf,
) -> Result<Project, String> {
    let project = Project {
        id: Uuid::new_v4(),
        name: clean_title(&name, 80),
        project_dir,
        map_output_dir,
        created_at: Utc::now(),
    };
    state
        .database
        .lock()
        .await
        .upsert_project(&project)
        .map_err(command_error)?;
    Ok(project)
}

#[tauri::command]
async fn update_project(state: State<'_, AppState>, project: Project) -> Result<Project, String> {
    state
        .database
        .lock()
        .await
        .upsert_project(&project)
        .map_err(command_error)?;
    Ok(project)
}

#[tauri::command]
async fn list_conversations(
    state: State<'_, AppState>,
    project_id: Uuid,
) -> Result<Vec<Conversation>, String> {
    state
        .database
        .lock()
        .await
        .list_conversations(project_id)
        .map_err(command_error)
}

#[tauri::command]
async fn list_messages(
    state: State<'_, AppState>,
    conversation_id: Uuid,
) -> Result<Vec<Message>, String> {
    state
        .database
        .lock()
        .await
        .list_messages(conversation_id)
        .map_err(command_error)
}

#[tauri::command]
async fn create_conversation(
    state: State<'_, AppState>,
    project_id: Uuid,
    title: Option<String>,
) -> Result<Conversation, String> {
    let now = Utc::now();
    let conversation = Conversation {
        id: Uuid::new_v4(),
        project_id,
        title: clean_title(title.as_deref().unwrap_or("新对话"), 40),
        created_at: now,
        updated_at: now,
    };
    state
        .database
        .lock()
        .await
        .upsert_conversation(&conversation)
        .map_err(command_error)?;
    Ok(conversation)
}

#[tauri::command]
async fn rename_conversation(
    state: State<'_, AppState>,
    project_id: Uuid,
    conversation_id: Uuid,
    title: String,
) -> Result<Conversation, String> {
    let database = state.database.lock().await;
    let mut conversation = database
        .list_conversations(project_id)
        .map_err(command_error)?
        .into_iter()
        .find(|item| item.id == conversation_id)
        .ok_or_else(|| "会话不存在".to_owned())?;
    conversation.title = clean_title(&title, 40);
    conversation.updated_at = Utc::now();
    database
        .upsert_conversation(&conversation)
        .map_err(command_error)?;
    Ok(conversation)
}

#[tauri::command]
async fn start_task(
    state: State<'_, AppState>,
    conversation_id: Option<Uuid>,
    goal: String,
    context: Option<Value>,
) -> Result<Uuid, String> {
    let orchestrator = state
        .orchestrator
        .read()
        .await
        .clone()
        .ok_or_else(|| "LLM 尚未配置，无法规划任务".to_owned())?;
    let mut context = context.unwrap_or_else(|| serde_json::json!({"goal":goal}));
    let database = state.database.lock().await;
    if let Some(conversation_id) = conversation_id {
        database
            .append_message(&Message {
                id: Uuid::new_v4(),
                conversation_id,
                role: "user".into(),
                content: goal.clone(),
                created_at: Utc::now(),
            })
            .map_err(command_error)?;
    }
    if let Some(project_id) = context
        .get("project_id")
        .and_then(Value::as_str)
        .and_then(|value| Uuid::parse_str(value).ok())
        && let Some(project) = database
            .list_projects()
            .map_err(command_error)?
            .into_iter()
            .find(|project| project.id == project_id)
    {
        context["project"] = serde_json::to_value(project).map_err(command_error)?;
    }
    context["settings"] = serde_json::json!({"output_root":database.load_settings().map_err(command_error)?.output_root});
    drop(database);
    let task_id = orchestrator
        .start_task(conversation_id, goal, context)
        .await;
    if let Some(task) = orchestrator.get_task(task_id).await {
        state
            .database
            .lock()
            .await
            .save_task(&task)
            .map_err(command_error)?;
    }
    Ok(task_id)
}

#[tauri::command]
async fn approve_plan(
    state: State<'_, AppState>,
    task_id: Uuid,
    plan_hash: String,
) -> Result<(), String> {
    state
        .orchestrator
        .read()
        .await
        .as_ref()
        .ok_or_else(|| "Orchestrator 不可用".to_owned())?
        .approve_plan(task_id, plan_hash)
        .await
        .map_err(command_error)
}

#[tauri::command]
async fn answer_task_question(
    state: State<'_, AppState>,
    task_id: Uuid,
    answer: String,
) -> Result<(), String> {
    state
        .orchestrator
        .read()
        .await
        .as_ref()
        .ok_or_else(|| "Orchestrator 不可用".to_owned())?
        .answer_question(task_id, answer)
        .await
        .map_err(command_error)
}

#[tauri::command]
async fn cancel_task(state: State<'_, AppState>, task_id: Uuid) -> Result<(), String> {
    state
        .orchestrator
        .read()
        .await
        .as_ref()
        .ok_or_else(|| "Orchestrator 不可用".to_owned())?
        .cancel_task(task_id)
        .await
        .map_err(command_error)
}

#[tauri::command]
async fn get_task(state: State<'_, AppState>, task_id: Uuid) -> Result<Option<TaskRecord>, String> {
    Ok(match state.orchestrator.read().await.as_ref() {
        Some(orchestrator) => orchestrator.get_task(task_id).await,
        None => None,
    })
}

#[tauri::command]
async fn list_tasks(state: State<'_, AppState>) -> Result<Vec<TaskRecord>, String> {
    Ok(match state.orchestrator.read().await.as_ref() {
        Some(orchestrator) => orchestrator.list_tasks().await,
        None => Vec::new(),
    })
}

#[tauri::command]
async fn discover_runtimes(state: State<'_, AppState>) -> Result<Vec<RuntimeConfig>, String> {
    let settings = state
        .database
        .lock()
        .await
        .load_settings()
        .map_err(command_error)?;
    let candidates = [
        (RuntimeKind::Pro, settings.modern_python),
        (
            RuntimeKind::Pro,
            r"C:\Program Files\GeoScene\Pro\bin\Python\envs\arcgispro-py3\python.exe".into(),
        ),
        (
            RuntimeKind::Pro,
            r"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe".into(),
        ),
        (RuntimeKind::Arcmap, settings.arcmap_python),
        (
            RuntimeKind::Arcmap,
            r"C:\Python27\ArcGIS10.8\python.exe".into(),
        ),
        (
            RuntimeKind::Arcmap,
            r"C:\Python27\ArcGIS10.7\python.exe".into(),
        ),
    ];
    let mut seen = std::collections::BTreeSet::new();
    let mut runtimes = Vec::new();
    for (kind, candidate) in candidates {
        if candidate.is_empty() || !seen.insert(candidate.to_lowercase()) {
            continue;
        }
        let path = PathBuf::from(candidate);
        if path.is_file() {
            runtimes.push(probe_python(kind, path).await);
        }
    }
    Ok(runtimes)
}

#[tauri::command]
async fn probe_runtime(kind: RuntimeKind, python_path: PathBuf) -> Result<RuntimeConfig, String> {
    Ok(probe_python(kind, python_path).await)
}

#[tauri::command]
async fn set_execution_engine(
    state: State<'_, AppState>,
    engine: ExecutionEngine,
) -> Result<Settings, String> {
    let database = state.database.lock().await;
    let mut settings = database.load_settings().map_err(command_error)?;
    settings.execution_engine = engine;
    database.save_settings(&settings).map_err(command_error)?;
    state.workers.set_engine(engine);
    Ok(settings)
}

async fn probe_python(kind: RuntimeKind, python_path: PathBuf) -> RuntimeConfig {
    let result = tokio::process::Command::new(&python_path)
        .arg("--version")
        .output()
        .await;
    match result {
        Ok(output) => {
            let bytes = if output.stdout.is_empty() {
                &output.stderr
            } else {
                &output.stdout
            };
            RuntimeConfig {
                kind,
                python_path,
                version: Some(String::from_utf8_lossy(bytes).trim().to_owned()),
                healthy: output.status.success(),
            }
        }
        Err(error) => RuntimeConfig {
            kind,
            python_path,
            version: Some(error.to_string()),
            healthy: false,
        },
    }
}

fn clean_title(value: &str, max_chars: usize) -> String {
    let compact = value.split_whitespace().collect::<Vec<_>>().join(" ");
    let cleaned: String = compact.chars().take(max_chars).collect();
    if cleaned.is_empty() {
        "未命名".into()
    } else {
        cleaned
    }
}

async fn rebuild_orchestrator(
    app: &AppHandle,
    state: &AppState,
    settings: &Settings,
) -> Result<(), String> {
    if !settings.ai_enabled {
        *state.orchestrator.write().await = None;
        return Ok(());
    }
    let reference = settings
        .ai_credential_ref
        .as_deref()
        .ok_or_else(|| "未配置 API Key 凭据引用".to_owned())?;
    let api_key = WindowsCredentialStore
        .get(reference)
        .map_err(command_error)?
        .ok_or_else(|| "Windows 凭据中没有 API Key".to_owned())?;
    let client = LlmClient::new(LlmConfig {
        base_url: settings.ai_base_url.clone(),
        api_key,
        model: settings.ai_model.clone(),
        timeout_seconds: 120,
        thinking_level: settings.ai_thinking_level.clone(),
    })
    .map_err(command_error)?;
    let orchestrator = Orchestrator::new(
        Arc::new(LlmPlanner::new(client.clone())),
        state.workers.clone(),
        Arc::new(client),
        state.registry.clone(),
        settings.autonomy_mode,
    );
    bridge_orchestrator_events(app.clone(), orchestrator.clone());
    *state.orchestrator.write().await = Some(orchestrator);
    Ok(())
}

fn bridge_orchestrator_events(app: AppHandle, orchestrator: Arc<LiveOrchestrator>) {
    let mut events = orchestrator.subscribe();
    tauri::async_runtime::spawn(async move {
        while let Ok(event) = events.recv().await {
            match &event {
                OrchestratorEvent::StepCompleted { task_id, result } => {
                    let state = app.state::<AppState>();
                    if let Err(error) = state
                        .database
                        .lock()
                        .await
                        .save_step_result(*task_id, result)
                    {
                        tracing::warn!(%error, "step persistence failed");
                    }
                }
                OrchestratorEvent::TaskCompleted {
                    task_id, report, ..
                } => {
                    if let Some(task) = orchestrator.get_task(*task_id).await {
                        let state = app.state::<AppState>();
                        let database = state.database.lock().await;
                        if let Err(error) = database.save_task(&task) {
                            tracing::warn!(%error, "task persistence failed");
                        }
                        if let Some(conversation_id) = task.conversation_id {
                            let message = Message {
                                id: Uuid::new_v4(),
                                conversation_id,
                                role: "assistant".into(),
                                content: report.clone(),
                                created_at: Utc::now(),
                            };
                            if let Err(error) = database.append_message(&message) {
                                tracing::warn!(%error, "assistant message persistence failed");
                            }
                        }
                    }
                }
                OrchestratorEvent::TaskStatus { task_id, .. }
                | OrchestratorEvent::PlanReady { task_id, .. }
                | OrchestratorEvent::TaskFailed { task_id, .. } => {
                    if let Some(task) = orchestrator.get_task(*task_id).await {
                        let state = app.state::<AppState>();
                        if let Err(error) = state.database.lock().await.save_task(&task) {
                            tracing::warn!(%error, "task persistence failed");
                        }
                    }
                }
                OrchestratorEvent::LlmMetric {
                    task_id,
                    phase,
                    metrics,
                } => {
                    let state = app.state::<AppState>();
                    let metric = PerformanceMetric {
                        task_id: Some(*task_id),
                        phase: phase.clone(),
                        duration_ms: metrics.elapsed_ms,
                        first_token_ms: metrics.first_token_ms,
                        input_tokens: metrics.usage.input_tokens,
                        output_tokens: metrics.usage.output_tokens,
                        cached_tokens: metrics.usage.cached_tokens,
                    };
                    if let Err(error) = state.database.lock().await.record_metric(&metric) {
                        tracing::warn!(%error, "LLM metric persistence failed");
                    }
                }
                _ => {}
            }
            let name = match &event {
                OrchestratorEvent::TaskStatus { .. } => "task_status",
                OrchestratorEvent::PlanReady { .. } => "plan_ready",
                OrchestratorEvent::TaskQuestion { .. } => "task_question",
                OrchestratorEvent::StepStarted { .. } => "step_started",
                OrchestratorEvent::StepProgress { .. } => "step_progress",
                OrchestratorEvent::StepCompleted { .. } => "step_completed",
                OrchestratorEvent::AssistantToken { .. } => "assistant_token",
                OrchestratorEvent::LogLine { .. } => "log_line",
                OrchestratorEvent::TaskCompleted { .. } => "task_completed",
                OrchestratorEvent::TaskFailed { .. } => "task_failed",
                OrchestratorEvent::LlmMetric { .. } => "log_line",
            };
            let _ = app.emit(name, event);
        }
    });
}

fn bridge_worker_events(app: AppHandle, workers: Arc<WorkerSupervisor>) {
    let mut statuses = workers.subscribe_status();
    let status_app = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Ok((runtime, status)) = statuses.recv().await {
            let _ = status_app.emit(
                "worker_status",
                serde_json::json!({"runtime":runtime,"status":status}),
            );
        }
    });
    let mut events = workers.subscribe_events();
    tauri::async_runtime::spawn(async move {
        while let Ok(event) = events.recv().await {
            let _ = app.emit("log_line", serde_json::json!({"worker_event":event}));
        }
    });
}

fn worker_configs(settings: &Settings, app_root: &Path) -> Vec<WorkerConfig> {
    let mut configs = Vec::new();
    if !settings.modern_python.is_empty() {
        configs.push(configured_worker(
            RuntimeKind::Pro,
            Path::new(&settings.modern_python),
            app_root,
        ));
    }
    if !settings.arcmap_python.is_empty() {
        configs.push(configured_worker(
            RuntimeKind::Arcmap,
            Path::new(&settings.arcmap_python),
            app_root,
        ));
    }
    configs
}

pub fn run() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    tauri::Builder::default()
        .setup(|app| {
            let app_root = if cfg!(debug_assertions) {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .expect("repo root")
                    .to_owned()
            } else {
                app.path()
                    .resource_dir()
                    .map_err(|error| anyhow::anyhow!(error))?
            };
            let mut database = Database::open_default()?;
            if let Some(user_dirs) = UserDirs::new() {
                let legacy_root = user_dirs.home_dir().join(".gisdo");
                if legacy_root.is_dir()
                    && let Err(error) =
                        database.import_legacy(&legacy_root, &WindowsCredentialStore)
                {
                    tracing::warn!(%error, "legacy import skipped");
                }
            }
            let settings = database.load_settings()?;
            let inventory = include_str!("../../fixtures/arcgis_tool_inventory_510.json");
            let registry = Arc::new(ToolRegistry::builtin().with_arcgis_inventory(inventory)?);
            let workers = Arc::new(WorkerSupervisor::new(worker_configs(&settings, &app_root)));
            workers.set_engine(settings.execution_engine);
            let state = AppState {
                database: Mutex::new(database),
                workers: workers.clone(),
                orchestrator: RwLock::new(None),
                registry,
                app_root,
            };
            app.manage(state);
            bridge_worker_events(app.handle().clone(), workers.clone());
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_millis(200)).await;
                workers.prewarm().await;
                let state = handle.state::<AppState>();
                let settings = match state.database.lock().await.load_settings() {
                    Ok(value) => value,
                    Err(error) => {
                        tracing::error!(%error);
                        return;
                    }
                };
                if let Err(error) = rebuild_orchestrator(&handle, &state, &settings).await {
                    tracing::warn!(%error, "LLM orchestrator unavailable");
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bootstrap,
            get_settings,
            save_settings,
            list_projects,
            create_project,
            update_project,
            list_conversations,
            create_conversation,
            rename_conversation,
            list_messages,
            start_task,
            approve_plan,
            answer_task_question,
            cancel_task,
            get_task,
            list_tasks,
            discover_runtimes,
            probe_runtime,
            set_execution_engine,
        ])
        .run(tauri::generate_context!())
        .expect("GISdo failed to start");
}
