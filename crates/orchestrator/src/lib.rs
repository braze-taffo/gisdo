use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use async_trait::async_trait;
use chrono::Utc;
use futures_util::future::join_all;
use gisdo_document_intake::{
    DEFAULT_MAX_CORPUS_CHARACTERS, DEFAULT_MAX_DOCUMENT_CHARACTERS, DEFAULT_MAX_DOCUMENTS,
    extract_corpus,
};
use gisdo_domain::{
    AutonomyMode, PlanOutcome, PlanStep, RuntimeKind, StepResult, TaskPlan, TaskRecord, TaskStatus,
};
use gisdo_llm::{LlmClient, LlmError, LlmMetrics};
use gisdo_safety::{SafetyError, ToolRegistry, plan_hash, validate_plan};
use gisdo_worker_client::{WorkerError, WorkerSupervisor};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use thiserror::Error;
use tokio::sync::{Mutex, broadcast, oneshot};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

pub const MAX_REPLANS: u8 = 1;
const DOCUMENT_INTAKE_SKILL: &str = include_str!("../../../skills/document-intake/SKILL.md");
const DOCUMENT_INTAKE_CONTRACT: &str =
    include_str!("../../../skills/document-intake/references/planning-contract.md");

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum OrchestratorEvent {
    TaskStatus {
        task_id: Uuid,
        status: TaskStatus,
    },
    PlanReady {
        task_id: Uuid,
        plan: TaskPlan,
        plan_hash: String,
    },
    TaskQuestion {
        task_id: Uuid,
        question: String,
        options: Vec<String>,
    },
    StepStarted {
        task_id: Uuid,
        step_id: String,
    },
    StepProgress {
        task_id: Uuid,
        step_id: String,
        message: String,
    },
    StepCompleted {
        task_id: Uuid,
        result: StepResult,
    },
    AssistantToken {
        task_id: Uuid,
        token: String,
    },
    LogLine {
        task_id: Uuid,
        line: String,
    },
    TaskCompleted {
        task_id: Uuid,
        report: String,
        results: Vec<StepResult>,
    },
    TaskFailed {
        task_id: Uuid,
        message: String,
        uncertain_outputs: Vec<String>,
    },
    LlmMetric {
        task_id: Uuid,
        phase: String,
        metrics: LlmMetrics,
    },
}

#[derive(Debug, Error)]
pub enum OrchestratorError {
    #[error(transparent)]
    Safety(#[from] SafetyError),
    #[error(transparent)]
    Worker(#[from] WorkerError),
    #[error(transparent)]
    Llm(#[from] LlmError),
    #[error("Planner 返回的 JSON 无效：{0}")]
    PlannerJson(String),
    #[error("计划哈希不匹配")]
    PlanHashMismatch,
    #[error("任务未等待确认")]
    NotAwaitingApproval,
    #[error("任务被取消")]
    Cancelled,
    #[error("执行后端错误：{0}")]
    Backend(String),
    #[error("任务不存在：{0}")]
    TaskNotFound(Uuid),
}

#[async_trait]
pub trait Planner: Send + Sync {
    async fn create_plan(&self, context: &Value) -> Result<PlannedOutcome, OrchestratorError>;
}

pub struct PlannedOutcome {
    pub outcome: PlanOutcome,
    pub metrics: Option<LlmMetrics>,
}
pub struct ReportedOutput {
    pub text: String,
    pub metrics: Option<LlmMetrics>,
}

pub struct LlmPlanner {
    client: LlmClient,
}
impl LlmPlanner {
    pub fn new(client: LlmClient) -> Self {
        Self { client }
    }
}

#[async_trait]
impl Planner for LlmPlanner {
    async fn create_plan(&self, context: &Value) -> Result<PlannedOutcome, OrchestratorError> {
        let response = self.client.plan(context, |_| {}).await?;
        let outcome = serde_json::from_str(extract_json_object(&response.content))
            .map_err(|error| OrchestratorError::PlannerJson(error.to_string()))?;
        Ok(PlannedOutcome {
            outcome,
            metrics: Some(response.metrics),
        })
    }
}

/// 容忍模型在 JSON 前后附加的说明文字或 Markdown 围栏：
/// 先剥 ``` 围栏，否则截取首个 `{` 到末个 `}` 之间；都不命中则原样返回。
fn extract_json_object(text: &str) -> &str {
    let trimmed = text.trim();
    if trimmed.starts_with("```") {
        let without_fence = trimmed
            .strip_prefix("```json")
            .or_else(|| trimmed.strip_prefix("```"))
            .unwrap_or(trimmed)
            .trim_end_matches("```")
            .trim();
        if serde_json::from_str::<serde_json::Value>(without_fence).is_ok() {
            return without_fence;
        }
    }
    if let (Some(start), Some(end)) = (trimmed.find('{'), trimmed.rfind('}'))
        && start < end
        && serde_json::from_str::<serde_json::Value>(&trimmed[start..=end]).is_ok()
    {
        return &trimmed[start..=end];
    }
    trimmed
}

#[async_trait]
pub trait Reporter: Send + Sync {
    async fn report(
        &self,
        summary: &Value,
        emit: &(dyn Fn(String) + Send + Sync),
    ) -> Result<ReportedOutput, OrchestratorError>;
}

#[async_trait]
impl Reporter for LlmClient {
    async fn report(
        &self,
        summary: &Value,
        emit: &(dyn Fn(String) + Send + Sync),
    ) -> Result<ReportedOutput, OrchestratorError> {
        let response = LlmClient::report(self, summary, |token| emit(token.to_owned())).await?;
        Ok(ReportedOutput {
            text: response.content,
            metrics: Some(response.metrics),
        })
    }
}

#[async_trait]
pub trait ExecutionBackend: Send + Sync {
    async fn inspect_paths(
        &self,
        runtime: RuntimeKind,
        task_id: Uuid,
        roots: Vec<PathBuf>,
    ) -> Result<Value, OrchestratorError>;
    async fn execute_group(
        &self,
        runtime: RuntimeKind,
        task_id: Uuid,
        hash: &str,
        steps: Vec<PlanStep>,
    ) -> Result<Vec<Value>, OrchestratorError>;
    async fn cancel(&self, runtime: RuntimeKind);
}

#[async_trait]
impl ExecutionBackend for WorkerSupervisor {
    async fn inspect_paths(
        &self,
        runtime: RuntimeKind,
        task_id: Uuid,
        roots: Vec<PathBuf>,
    ) -> Result<Value, OrchestratorError> {
        Ok(self.inspect_paths(runtime, task_id, roots, 300).await?)
    }

    async fn execute_group(
        &self,
        runtime: RuntimeKind,
        task_id: Uuid,
        hash: &str,
        steps: Vec<PlanStep>,
    ) -> Result<Vec<Value>, OrchestratorError> {
        if runtime == RuntimeKind::Native {
            return steps.iter().map(execute_native_step).collect();
        }
        Ok(self
            .execute(runtime, task_id, hash.to_owned(), steps)
            .await?)
    }
    async fn cancel(&self, runtime: RuntimeKind) {
        let _ = self.cancel_runtime(runtime).await;
    }
}

pub fn needs_plan_confirmation(
    plan: &TaskPlan,
    registry: &ToolRegistry,
    autonomy: AutonomyMode,
) -> bool {
    match autonomy {
        AutonomyMode::Autonomous => false,
        AutonomyMode::ConfirmEveryStep => !plan.steps.is_empty(),
        AutonomyMode::ConfirmWrites => plan
            .steps
            .iter()
            .any(|step| registry.is_write(&step.tool).unwrap_or(true)),
    }
}

pub fn execution_waves(plan: &TaskPlan) -> Result<Vec<Vec<PlanStep>>, SafetyError> {
    let ids: BTreeSet<_> = plan.steps.iter().map(|s| s.id.as_str()).collect();
    for step in &plan.steps {
        for dependency in &step.depends_on {
            if !ids.contains(dependency.as_str()) {
                return Err(SafetyError::MissingDependency {
                    step: step.id.clone(),
                    dependency: dependency.clone(),
                });
            }
        }
    }
    let mut remaining: BTreeMap<_, _> = plan
        .steps
        .iter()
        .map(|s| (s.id.clone(), s.clone()))
        .collect();
    let mut completed = BTreeSet::new();
    let mut waves = Vec::new();
    while !remaining.is_empty() {
        let ready: Vec<_> = remaining
            .values()
            .filter(|s| s.depends_on.iter().all(|d| completed.contains(d)))
            .cloned()
            .collect();
        if ready.is_empty() {
            return Err(SafetyError::DependencyCycle);
        }
        for step in &ready {
            remaining.remove(&step.id);
            completed.insert(step.id.clone());
        }
        waves.push(ready);
    }
    Ok(waves)
}

pub struct DagExecutor<B: ExecutionBackend> {
    backend: Arc<B>,
}

impl<B: ExecutionBackend> DagExecutor<B> {
    pub fn new(backend: Arc<B>) -> Self {
        Self { backend }
    }

    pub async fn execute(
        &self,
        task_id: Uuid,
        plan: &TaskPlan,
        hash: &str,
        cancel: &CancellationToken,
        emit: &(dyn Fn(OrchestratorEvent) + Send + Sync),
    ) -> Result<Vec<StepResult>, OrchestratorError> {
        let mut all_results = Vec::new();
        for wave in execution_waves(plan)? {
            if cancel.is_cancelled() {
                return Err(OrchestratorError::Cancelled);
            }
            let mut by_runtime: HashMap<RuntimeKind, Vec<PlanStep>> = HashMap::new();
            for step in wave {
                emit(OrchestratorEvent::StepStarted {
                    task_id,
                    step_id: step.id.clone(),
                });
                by_runtime.entry(step.runtime).or_default().push(step);
            }
            let active_runtimes: Vec<_> = by_runtime.keys().copied().collect();
            let futures = by_runtime.into_iter().map(|(runtime, steps)| {
                let backend = self.backend.clone();
                let hash = hash.to_owned();
                async move {
                    let started = Instant::now();
                    let values = backend
                        .execute_group(runtime, task_id, &hash, steps.clone())
                        .await?;
                    let results = steps
                        .into_iter()
                        .enumerate()
                        .map(|(index, step)| StepResult {
                            step_id: step.id,
                            ok: true,
                            summary: values
                                .get(index)
                                .cloned()
                                .unwrap_or_else(|| json!({"ok":true})),
                            artifacts: values.get(index).map(result_artifacts).unwrap_or_default(),
                            duration_ms: started.elapsed().as_millis() as u64,
                        })
                        .collect::<Vec<_>>();
                    Ok::<_, OrchestratorError>(results)
                }
            });
            let mut futures = join_all(futures);
            let grouped = tokio::select! {
                groups = &mut futures => groups,
                _ = cancel.cancelled() => {
                    join_all(active_runtimes.into_iter().map(|runtime| self.backend.cancel(runtime))).await;
                    // 等待在飞请求观察到进程被杀后走完回收清理；
                    // 直接丢弃 future 会把死进程留在 slot 里，让下一个任务撞上坏管道。
                    let _ = tokio::time::timeout(Duration::from_secs(10), futures).await;
                    return Err(OrchestratorError::Cancelled);
                }
            };
            for group in grouped {
                let results = group?;
                for result in results {
                    emit(OrchestratorEvent::StepCompleted {
                        task_id,
                        result: result.clone(),
                    });
                    all_results.push(result);
                }
            }
        }
        Ok(all_results)
    }
}

struct LiveTask {
    record: TaskRecord,
    approval: Option<oneshot::Sender<String>>,
    answer: Option<oneshot::Sender<String>>,
    cancel: CancellationToken,
    write_execution_started: Arc<AtomicBool>,
}

pub struct Orchestrator<P: Planner, B: ExecutionBackend, R: Reporter> {
    planner: Arc<P>,
    backend: Arc<B>,
    executor: Arc<DagExecutor<B>>,
    reporter: Arc<R>,
    registry: Arc<ToolRegistry>,
    autonomy: AutonomyMode,
    tasks: Arc<Mutex<HashMap<Uuid, LiveTask>>>,
    events: broadcast::Sender<OrchestratorEvent>,
}

impl<P: Planner + 'static, B: ExecutionBackend + 'static, R: Reporter + 'static>
    Orchestrator<P, B, R>
{
    pub fn new(
        planner: Arc<P>,
        backend: Arc<B>,
        reporter: Arc<R>,
        registry: Arc<ToolRegistry>,
        autonomy: AutonomyMode,
    ) -> Arc<Self> {
        let (events, _) = broadcast::channel(512);
        Arc::new(Self {
            planner,
            backend: backend.clone(),
            executor: Arc::new(DagExecutor::new(backend)),
            reporter,
            registry,
            autonomy,
            tasks: Arc::new(Mutex::new(HashMap::new())),
            events,
        })
    }

    pub fn subscribe(&self) -> broadcast::Receiver<OrchestratorEvent> {
        self.events.subscribe()
    }

    pub async fn start_task(
        self: &Arc<Self>,
        conversation_id: Option<Uuid>,
        goal: String,
        context: Value,
    ) -> Uuid {
        let mut record = TaskRecord::new(conversation_id, goal);
        record.status = TaskStatus::Planning;
        let task_id = record.id;
        self.tasks.lock().await.insert(
            task_id,
            LiveTask {
                record,
                approval: None,
                answer: None,
                cancel: CancellationToken::new(),
                write_execution_started: Arc::new(AtomicBool::new(false)),
            },
        );
        let this = self.clone();
        tokio::spawn(async move {
            if let Err(error) = this.run_task(task_id, context).await {
                this.fail_task(task_id, error).await;
            }
        });
        task_id
    }

    async fn run_task(&self, task_id: Uuid, mut context: Value) -> Result<(), OrchestratorError> {
        let mut replans = 0;
        let mut inventory_ready = false;
        let (cancel, write_flag) = {
            let mut tasks = self.tasks.lock().await;
            let task = tasks
                .get_mut(&task_id)
                .ok_or(OrchestratorError::TaskNotFound(task_id))?;
            (task.cancel.clone(), task.write_execution_started.clone())
        };
        loop {
            if cancel.is_cancelled() {
                return Err(OrchestratorError::Cancelled);
            }
            if let Some((question, options)) = required_user_input(&context) {
                let answer = self
                    .await_user_input(task_id, question.clone(), options)
                    .await?;
                append_clarification(&mut context, &question, &answer);
                continue;
            }
            if !inventory_ready {
                self.enrich_context_with_inventory(task_id, &mut context)
                    .await;
                self.enrich_context_with_documents(task_id, &mut context)
                    .await;
                inventory_ready = true;
            }
            self.set_status(task_id, TaskStatus::Planning).await?;
            let planned = self.planner.create_plan(&context).await?;
            if let Some(metrics) = planned.metrics {
                let _ = self.events.send(OrchestratorEvent::LlmMetric {
                    task_id,
                    phase: "planner".into(),
                    metrics,
                });
            }
            let mut plan = match planned.outcome {
                PlanOutcome::Ready { plan } => plan,
                PlanOutcome::NeedsInput { question, options } => {
                    let answer = self
                        .await_user_input(task_id, question.clone(), options)
                        .await?;
                    append_clarification(&mut context, &question, &answer);
                    inventory_ready = false;
                    continue;
                }
            };
            plan.expected_outputs = self.registry.inferred_outputs(&plan)?;
            validate_plan(&plan, &self.registry, true)?;
            let hash = plan_hash(&plan)
                .map_err(|error| OrchestratorError::PlannerJson(error.to_string()))?;
            {
                let mut tasks = self.tasks.lock().await;
                let task = tasks
                    .get_mut(&task_id)
                    .ok_or(OrchestratorError::TaskNotFound(task_id))?;
                task.record.plan = Some(plan.clone());
                task.record.plan_hash = Some(hash.clone());
                task.record.updated_at = Utc::now();
            }
            if self.autonomy != AutonomyMode::ConfirmEveryStep
                && needs_plan_confirmation(&plan, &self.registry, self.autonomy)
            {
                if cancel.is_cancelled() {
                    return Err(OrchestratorError::Cancelled);
                }
                self.await_approval(task_id, plan.clone(), &hash).await?;
            }
            self.set_status(task_id, TaskStatus::Running).await?;
            let write_step_ids: HashSet<String> = plan
                .steps
                .iter()
                .filter(|step| self.registry.is_write(&step.tool).unwrap_or(true))
                .map(|step| step.id.clone())
                .collect();
            let emit = |event| {
                // 写步骤实际开始执行时才置位：整计划预置会把零写入的取消也标成 Uncertain。
                if let OrchestratorEvent::StepStarted { step_id, .. } = &event
                    && write_step_ids.contains(step_id)
                {
                    write_flag.store(true, Ordering::SeqCst);
                }
                let _ = self.events.send(event);
            };
            let execution = if self.autonomy == AutonomyMode::ConfirmEveryStep {
                self.execute_with_step_confirmation(task_id, &plan, &hash, &cancel, &emit)
                    .await
            } else {
                self.executor
                    .execute(task_id, &plan, &hash, &cancel, &emit)
                    .await
            };
            match execution {
                Ok(results) => {
                    if cancel.is_cancelled() {
                        return Err(OrchestratorError::Cancelled);
                    }
                    let summary = json!({"goal":plan.goal,"plan_hash":hash,"results":results,"expected_outputs":plan.expected_outputs});
                    let token_emit = |token: String| {
                        let _ = self
                            .events
                            .send(OrchestratorEvent::AssistantToken { task_id, token });
                    };
                    let reported = self.reporter.report(&summary, &token_emit).await.ok();
                    if let Some(metrics) =
                        reported.as_ref().and_then(|output| output.metrics.clone())
                    {
                        let _ = self.events.send(OrchestratorEvent::LlmMetric {
                            task_id,
                            phase: "report".into(),
                            metrics,
                        });
                    }
                    let report = reported
                        .map(|output| output.text)
                        .unwrap_or_else(|| deterministic_report(&plan, &results));
                    self.set_status(task_id, TaskStatus::Completed).await?;
                    let _ = self.events.send(OrchestratorEvent::TaskCompleted {
                        task_id,
                        report,
                        results,
                    });
                    return Ok(());
                }
                Err(error) if replans < MAX_REPLANS && is_replannable(&error) => {
                    replans += 1;
                    context["previous_failure"] = Value::String(error.to_string());
                    context["required_plan_version"] = Value::from(plan.version + 1);
                }
                Err(error) => return Err(error),
            }
        }
    }

    async fn await_user_input(
        &self,
        task_id: Uuid,
        question: String,
        options: Vec<String>,
    ) -> Result<String, OrchestratorError> {
        let (sender, receiver) = oneshot::channel();
        {
            let mut tasks = self.tasks.lock().await;
            let task = tasks
                .get_mut(&task_id)
                .ok_or(OrchestratorError::TaskNotFound(task_id))?;
            // 取消可能发生在提问挂出之前（例如规划期间点停止）；
            // 此时不再登记 sender，避免任务翻回 NeedsInput 后永久悬挂。
            if task.cancel.is_cancelled() {
                return Err(OrchestratorError::Cancelled);
            }
            task.answer = Some(sender);
        }
        self.set_status(task_id, TaskStatus::NeedsInput).await?;
        let _ = self.events.send(OrchestratorEvent::TaskQuestion {
            task_id,
            question,
            options,
        });
        receiver.await.map_err(|_| OrchestratorError::Cancelled)
    }

    async fn enrich_context_with_inventory(&self, task_id: Uuid, context: &mut Value) {
        let roots = discovery_roots(context);
        context["discovery_policy"] = json!({
            "instruction": "Use inspected datasets rather than container folders. Infer dataset roles and CRS transformations from metadata. Ask only when a material ambiguity remains after inspection.",
            "question_budget": "zero_for_discoverable_facts",
            "output_suffix": Utc::now().format("%Y%m%d_%H%M%S").to_string(),
        });
        if roots.is_empty() {
            context["data_inventory"] = json!({
                "roots": [],
                "datasets": [],
                "note": "No existing local path could be extracted from the task or project context."
            });
            return;
        }
        let _ = self.events.send(OrchestratorEvent::LogLine {
            task_id,
            line: format!("正在检查 {} 个数据位置…", roots.len()),
        });
        let inspected = match self
            .backend
            .inspect_paths(RuntimeKind::Pro, task_id, roots.clone())
            .await
        {
            Ok(inventory) => Ok((RuntimeKind::Pro, inventory)),
            Err(pro_error) => match self
                .backend
                .inspect_paths(RuntimeKind::Arcmap, task_id, roots.clone())
                .await
            {
                Ok(inventory) => Ok((RuntimeKind::Arcmap, inventory)),
                Err(arcmap_error) => Err(format!(
                    "Pro inspection failed: {pro_error}; ArcMap inspection failed: {arcmap_error}"
                )),
            },
        };
        match inspected {
            Ok((runtime, inventory)) => {
                let count = inventory
                    .get("dataset_count")
                    .and_then(Value::as_u64)
                    .unwrap_or_default();
                context["data_inventory"] = inventory;
                context["inspection_runtime"] =
                    Value::String(format!("{runtime:?}").to_lowercase());
                let _ = self.events.send(OrchestratorEvent::LogLine {
                    task_id,
                    line: format!("已识别 {count} 个可用数据集，正在决定处理方案…"),
                });
            }
            Err(error) => {
                context["data_inventory"] = json!({
                    "roots": roots,
                    "datasets": [],
                    "inspection_error": error,
                });
            }
        }
    }

    async fn enrich_context_with_documents(&self, task_id: Uuid, context: &mut Value) {
        if !document_intake_triggered(context) {
            return;
        }
        let roots = discovery_roots(context);
        let _ = self.events.send(OrchestratorEvent::LogLine {
            task_id,
            line: "正在读取项目路线图与相关资料…".into(),
        });
        let extracted = tokio::task::spawn_blocking(move || {
            extract_corpus(
                &roots,
                DEFAULT_MAX_DOCUMENTS,
                DEFAULT_MAX_DOCUMENT_CHARACTERS,
                DEFAULT_MAX_CORPUS_CHARACTERS,
            )
        })
        .await;
        match extracted {
            Ok(corpus) => {
                let count = corpus.documents.len();
                let characters = corpus.total_characters;
                context["active_skills"] = json!([{
                    "name": "document-intake",
                    "instructions": DOCUMENT_INTAKE_SKILL,
                    "reference": DOCUMENT_INTAKE_CONTRACT,
                }]);
                context["document_corpus"] = serde_json::to_value(corpus).unwrap_or_else(
                    |error| json!({"documents":[], "extraction_error":error.to_string()}),
                );
                let _ = self.events.send(OrchestratorEvent::LogLine {
                    task_id,
                    line: format!(
                        "已读取 {count} 份项目资料（{characters} 字符），正在整理交付要求…"
                    ),
                });
            }
            Err(error) => {
                context["document_corpus"] =
                    json!({"documents":[], "extraction_error":error.to_string()});
            }
        }
    }

    async fn set_status(&self, task_id: Uuid, status: TaskStatus) -> Result<(), OrchestratorError> {
        let mut tasks = self.tasks.lock().await;
        let task = tasks
            .get_mut(&task_id)
            .ok_or(OrchestratorError::TaskNotFound(task_id))?;
        task.record.status = status;
        task.record.updated_at = Utc::now();
        let _ = self
            .events
            .send(OrchestratorEvent::TaskStatus { task_id, status });
        Ok(())
    }

    async fn await_approval(
        &self,
        task_id: Uuid,
        plan: TaskPlan,
        hash: &str,
    ) -> Result<(), OrchestratorError> {
        let (sender, receiver) = oneshot::channel();
        {
            let mut tasks = self.tasks.lock().await;
            let task = tasks
                .get_mut(&task_id)
                .ok_or(OrchestratorError::TaskNotFound(task_id))?;
            // 与 await_user_input 同理：已取消的任务不再翻回 AwaitingApproval 悬挂。
            if task.cancel.is_cancelled() {
                return Err(OrchestratorError::Cancelled);
            }
            task.approval = Some(sender);
        }
        self.set_status(task_id, TaskStatus::AwaitingApproval)
            .await?;
        let _ = self.events.send(OrchestratorEvent::PlanReady {
            task_id,
            plan,
            plan_hash: hash.to_owned(),
        });
        let approved_hash = receiver.await.map_err(|_| OrchestratorError::Cancelled)?;
        if approved_hash != hash {
            return Err(OrchestratorError::PlanHashMismatch);
        }
        Ok(())
    }

    async fn execute_with_step_confirmation(
        &self,
        task_id: Uuid,
        plan: &TaskPlan,
        hash: &str,
        cancel: &CancellationToken,
        emit: &(dyn Fn(OrchestratorEvent) + Send + Sync),
    ) -> Result<Vec<StepResult>, OrchestratorError> {
        let mut results = Vec::new();
        for wave in execution_waves(plan)? {
            for mut step in wave {
                if cancel.is_cancelled() {
                    return Err(OrchestratorError::Cancelled);
                }
                let expected_outputs = self
                    .registry
                    .get(&step.tool)
                    .map(|spec| {
                        let params = step.params.as_object();
                        spec.output_params
                            .iter()
                            .filter_map(|key| {
                                params.and_then(|map| map.get(key)).and_then(Value::as_str)
                            })
                            .map(Into::into)
                            .collect()
                    })
                    .unwrap_or_default();
                step.depends_on.clear();
                let single = TaskPlan {
                    version: plan.version,
                    id: plan.id,
                    goal: format!("{} · {}", plan.goal, step.id),
                    steps: vec![step],
                    expected_outputs,
                };
                self.await_approval(task_id, single.clone(), hash).await?;
                self.set_status(task_id, TaskStatus::Running).await?;
                if self
                    .registry
                    .is_write(&single.steps[0].tool)
                    .unwrap_or(true)
                {
                    self.tasks
                        .lock()
                        .await
                        .get_mut(&task_id)
                        .ok_or(OrchestratorError::TaskNotFound(task_id))?
                        .write_execution_started
                        .store(true, Ordering::SeqCst);
                }
                results.extend(
                    self.executor
                        .execute(task_id, &single, hash, cancel, emit)
                        .await?,
                );
            }
        }
        Ok(results)
    }

    async fn fail_task(&self, task_id: Uuid, error: OrchestratorError) {
        let tasks = self.tasks.lock().await;
        let plan = tasks
            .get(&task_id)
            .and_then(|task| task.record.plan.clone());
        let write_execution_started = tasks
            .get(&task_id)
            .is_some_and(|task| task.write_execution_started.load(Ordering::SeqCst));
        drop(tasks);
        let cancelled_during_write =
            matches!(error, OrchestratorError::Cancelled) && write_execution_started;
        let write_failed = matches!(
            error,
            OrchestratorError::Worker(WorkerError::Execution {
                write_started: true,
                ..
            })
        );
        let uncertain = cancelled_during_write || write_failed;
        let status = if uncertain {
            TaskStatus::Uncertain
        } else if matches!(error, OrchestratorError::Cancelled) {
            TaskStatus::Cancelled
        } else {
            TaskStatus::Failed
        };
        let uncertain_outputs = if uncertain {
            plan.into_iter()
                .flat_map(|plan| plan.expected_outputs)
                .map(|path| path.display().to_string())
                .collect()
        } else {
            Vec::new()
        };
        let _ = self.set_status(task_id, status).await;
        // 纯取消不再补发 TaskFailed——前端会把它当失败覆盖 cancelled 状态；
        // Uncertain 仍需 TaskFailed 携带 uncertain_outputs 清单。
        if !matches!(error, OrchestratorError::Cancelled) || uncertain {
            let _ = self.events.send(OrchestratorEvent::TaskFailed {
                task_id,
                message: error.to_string(),
                uncertain_outputs,
            });
        }
    }

    pub async fn approve_plan(&self, task_id: Uuid, hash: String) -> Result<(), OrchestratorError> {
        let sender = self
            .tasks
            .lock()
            .await
            .get_mut(&task_id)
            .ok_or(OrchestratorError::TaskNotFound(task_id))?
            .approval
            .take()
            .ok_or(OrchestratorError::NotAwaitingApproval)?;
        sender.send(hash).map_err(|_| OrchestratorError::Cancelled)
    }

    pub async fn answer_question(
        &self,
        task_id: Uuid,
        answer: String,
    ) -> Result<(), OrchestratorError> {
        let sender = self
            .tasks
            .lock()
            .await
            .get_mut(&task_id)
            .ok_or(OrchestratorError::TaskNotFound(task_id))?
            .answer
            .take()
            .ok_or(OrchestratorError::NotAwaitingApproval)?;
        sender
            .send(answer)
            .map_err(|_| OrchestratorError::Cancelled)
    }

    pub async fn cancel_task(&self, task_id: Uuid) -> Result<(), OrchestratorError> {
        let mut tasks = self.tasks.lock().await;
        let task = tasks
            .get_mut(&task_id)
            .ok_or(OrchestratorError::TaskNotFound(task_id))?;
        let cancel = task.cancel.clone();
        task.approval.take();
        task.answer.take();
        drop(tasks);
        cancel.cancel();
        self.set_status(task_id, TaskStatus::Cancelling).await
    }

    pub async fn get_task(&self, task_id: Uuid) -> Option<TaskRecord> {
        self.tasks
            .lock()
            .await
            .get(&task_id)
            .map(|task| task.record.clone())
    }
    pub async fn list_tasks(&self) -> Vec<TaskRecord> {
        self.tasks
            .lock()
            .await
            .values()
            .map(|task| task.record.clone())
            .collect()
    }
    /// 是否仍有未到终态的任务；设置重建时用于决定是否把旧实例转入 retired。
    pub async fn has_active_tasks(&self) -> bool {
        let tasks = self.tasks.lock().await;
        tasks.values().any(|task| {
            matches!(
                task.record.status,
                TaskStatus::Planning
                    | TaskStatus::NeedsInput
                    | TaskStatus::AwaitingApproval
                    | TaskStatus::Running
                    | TaskStatus::Cancelling
            )
        })
    }
}

fn is_replannable(error: &OrchestratorError) -> bool {
    matches!(
        error,
        OrchestratorError::Worker(WorkerError::Execution {
            write_started: false,
            ..
        })
    ) || matches!(error, OrchestratorError::Backend(_))
}

fn execute_native_step(step: &PlanStep) -> Result<Value, OrchestratorError> {
    match step.tool.as_str() {
        "verify_png" => {
            let path = step
                .params
                .get("input")
                .and_then(Value::as_str)
                .ok_or_else(|| OrchestratorError::Backend("verify_png 缺少 input".into()))?;
            let bytes = std::fs::read(path)
                .map_err(|error| OrchestratorError::Backend(error.to_string()))?;
            if bytes.len() < 24 || &bytes[..8] != b"\x89PNG\r\n\x1a\n" {
                return Err(OrchestratorError::Backend(format!("无效 PNG：{path}")));
            }
            let width = u32::from_be_bytes(bytes[16..20].try_into().expect("four bytes"));
            let height = u32::from_be_bytes(bytes[20..24].try_into().expect("four bytes"));
            if width < 16 || height < 16 {
                return Err(OrchestratorError::Backend(format!(
                    "PNG 尺寸过小：{width}x{height}"
                )));
            }
            Ok(json!({"ok":true,"path":path,"width":width,"height":height,"bytes":bytes.len()}))
        }
        other => Err(OrchestratorError::Backend(format!(
            "Rust 原生工具尚未开放：{other}"
        ))),
    }
}

fn result_artifacts(value: &Value) -> Vec<std::path::PathBuf> {
    let mut paths: Vec<_> = value
        .get("outputs")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|output| output.get("path").and_then(Value::as_str))
        .map(std::path::PathBuf::from)
        .collect();
    if let Some(path) = value.pointer("/validation/path").and_then(Value::as_str) {
        paths.push(path.into());
    }
    paths
}

fn document_intake_triggered(context: &Value) -> bool {
    let goal = context
        .get("goal")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_lowercase();
    let document_extension = [
        ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".ods", ".md", ".txt", ".csv", ".json", ".xml",
        ".html",
    ]
    .iter()
    .any(|extension| goal.contains(extension));
    document_extension
        || [
            "路线图",
            "任务书",
            "项目资料",
            "相关资料",
            "技术方案",
            "工作方案",
            "需求文档",
            "项目文档",
            "roadmap",
            "specification",
            "project brief",
        ]
        .iter()
        .any(|marker| goal.contains(marker))
}

fn required_user_input(context: &Value) -> Option<(String, Vec<String>)> {
    let mut text = context
        .get("goal")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if let Some(clarifications) = context.get("clarifications").and_then(Value::as_array) {
        for answer in clarifications
            .iter()
            .filter_map(|item| item.get("answer"))
            .filter_map(Value::as_str)
        {
            text.push('\n');
            text.push_str(answer);
        }
    }
    let lower = text.to_lowercase();
    let buffer_intent = lower.contains("缓冲")
        || [
            "create buffer",
            "make buffer",
            "buffer around",
            "buffer the",
        ]
        .iter()
        .any(|phrase| lower.contains(phrase));
    let explicit_field = (text.contains("字段") && (text.contains('按') || text.contains("使用")))
        || lower.contains("buffer field");
    if buffer_intent
        && !document_intake_triggered(context)
        && !contains_explicit_linear_distance(&text)
        && !explicit_field
    {
        return Some((
            "缓冲距离是多少？请同时注明单位，例如“500 米”或“2 公里”。".into(),
            Vec::new(),
        ));
    }
    None
}

fn contains_explicit_linear_distance(text: &str) -> bool {
    let compact: String = text
        .to_lowercase()
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect();
    let units = [
        "kilometers",
        "kilometres",
        "kilometer",
        "kilometre",
        "meters",
        "metres",
        "meter",
        "metre",
        "公里",
        "千米",
        "英尺",
        "公尺",
        "厘米",
        "毫米",
        "km",
        "feet",
        "foot",
        "ft",
        "cm",
        "mm",
        "米",
        "m",
    ];
    for unit in units {
        for (index, _) in compact.match_indices(unit) {
            let prefix = &compact[..index];
            let token: String = prefix
                .chars()
                .rev()
                .take_while(|character| {
                    character.is_ascii_digit()
                        || matches!(
                            character,
                            '.' | '点'
                                | '半'
                                | '零'
                                | '一'
                                | '二'
                                | '两'
                                | '三'
                                | '四'
                                | '五'
                                | '六'
                                | '七'
                                | '八'
                                | '九'
                                | '十'
                                | '百'
                                | '千'
                                | '万'
                        )
                })
                .collect();
            if token.chars().any(|character| {
                character.is_ascii_digit()
                    || matches!(
                        character,
                        '半' | '零'
                            | '一'
                            | '二'
                            | '两'
                            | '三'
                            | '四'
                            | '五'
                            | '六'
                            | '七'
                            | '八'
                            | '九'
                            | '十'
                            | '百'
                            | '千'
                            | '万'
                    )
            }) {
                return true;
            }
        }
    }
    false
}

fn append_clarification(context: &mut Value, question: &str, answer: &str) {
    if !context.get("clarifications").is_some_and(Value::is_array) {
        context["clarifications"] = Value::Array(Vec::new());
    }
    if let Some(clarifications) = context
        .get_mut("clarifications")
        .and_then(Value::as_array_mut)
    {
        clarifications.push(json!({"question": question, "answer": answer}));
    }
    context["user_answer"] = Value::String(answer.to_owned());
}

fn discovery_roots(context: &Value) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    for pointer in ["/goal", "/user_answer"] {
        if let Some(text) = context.pointer(pointer).and_then(Value::as_str) {
            candidates.extend(extract_existing_paths(text));
        }
    }
    if let Some(path) = context
        .pointer("/project/project_dir")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .filter(|path| path.exists())
    {
        candidates.push(path);
    }
    if let Some(paths) = context.get("paths").and_then(Value::as_array) {
        candidates.extend(
            paths
                .iter()
                .filter_map(Value::as_str)
                .map(PathBuf::from)
                .filter(|path| path.exists()),
        );
    }
    let mut seen = BTreeSet::new();
    candidates
        .into_iter()
        .filter(|path| seen.insert(path.to_string_lossy().replace('/', "\\").to_lowercase()))
        .take(24)
        .collect()
}

fn extract_existing_paths(text: &str) -> Vec<PathBuf> {
    let trimmed = text.trim_matches(|character: char| {
        character.is_whitespace()
            || matches!(character, '`' | '\'' | '"' | '，' | '。' | '；' | ';')
    });
    if Path::new(trimmed).is_absolute() && Path::new(trimmed).exists() {
        return vec![PathBuf::from(trimmed)];
    }
    let bytes = text.as_bytes();
    let mut starts = Vec::new();
    for index in 0..bytes.len().saturating_sub(2) {
        if bytes[index].is_ascii_alphabetic()
            && bytes[index + 1] == b':'
            && matches!(bytes[index + 2], b'\\' | b'/')
        {
            starts.push(index);
        }
    }
    let mut found = Vec::new();
    for (position, start) in starts.iter().copied().enumerate() {
        let next_start = starts.get(position + 1).copied().unwrap_or(text.len());
        let mut hard_end = next_start;
        for (offset, character) in text[start..next_start].char_indices() {
            if matches!(
                character,
                '\r' | '\n' | '`' | '\'' | '"' | '<' | '>' | '|' | '，' | '。' | '；' | ';'
            ) {
                hard_end = start + offset;
                break;
            }
        }
        let segment = &text[start..hard_end];
        let mut ends: Vec<usize> = segment.char_indices().map(|(index, _)| index).collect();
        ends.push(segment.len());
        for end in ends.into_iter().rev() {
            let candidate = segment[..end].trim_end_matches(|character: char| {
                character.is_whitespace()
                    || matches!(character, ',' | ':' | ')' | ']' | '}' | '、' | '和')
            });
            if candidate.len() < 3 {
                break;
            }
            let path = PathBuf::from(candidate);
            if path.is_absolute() && path.exists() {
                found.push(path);
                break;
            }
        }
    }
    found
}

pub fn deterministic_report(plan: &TaskPlan, results: &[StepResult]) -> String {
    let mut text = format!(
        "## 汇报\n\n**任务**：{}\n\n**执行结果**：{}/{} 个步骤完成。\n",
        plan.goal,
        results.iter().filter(|r| r.ok).count(),
        plan.steps.len()
    );
    if !plan.expected_outputs.is_empty() {
        text.push_str("\n**输出**：\n");
        for output in &plan.expected_outputs {
            text.push_str(&format!("- `{}`\n", output.display()));
        }
    }
    text.push_str("\n校验由执行器随步骤自动完成；未额外运行 GetCount。");
    text
}

#[cfg(test)]
mod tests {
    use super::*;
    use gisdo_domain::ValidationPolicy;
    use serde_json::json;

    fn plan() -> TaskPlan {
        TaskPlan {
            version: 1,
            id: Uuid::nil(),
            goal: "clip".into(),
            expected_outputs: vec![],
            steps: vec![
                PlanStep {
                    id: "project".into(),
                    stage: None,
                    requirement_refs: vec![],
                    runtime: RuntimeKind::Native,
                    tool: "verify_png".into(),
                    params: json!({"input":"x"}),
                    depends_on: vec![],
                    validation: ValidationPolicy::Png,
                },
                PlanStep {
                    id: "clip".into(),
                    stage: None,
                    requirement_refs: vec![],
                    runtime: RuntimeKind::Native,
                    tool: "verify_png".into(),
                    params: json!({"input":"y"}),
                    depends_on: vec!["project".into()],
                    validation: ValidationPolicy::Png,
                },
                PlanStep {
                    id: "inspect".into(),
                    stage: None,
                    requirement_refs: vec![],
                    runtime: RuntimeKind::Native,
                    tool: "verify_png".into(),
                    params: json!({"input":"z"}),
                    depends_on: vec![],
                    validation: ValidationPolicy::Png,
                },
            ],
        }
    }

    #[test]
    fn dag_builds_parallel_waves() {
        let waves = execution_waves(&plan()).unwrap();
        assert_eq!(waves.len(), 2);
        assert_eq!(waves[0].len(), 2);
        assert_eq!(waves[1][0].id, "clip");
    }

    #[test]
    fn confirmation_policy_uses_registry_write_flag() {
        let registry = ToolRegistry::builtin();
        let read_plan = plan();
        assert!(!needs_plan_confirmation(
            &read_plan,
            &registry,
            AutonomyMode::ConfirmWrites
        ));
        assert!(needs_plan_confirmation(
            &read_plan,
            &registry,
            AutonomyMode::ConfirmEveryStep
        ));
    }

    #[test]
    fn fallback_report_is_single_markdown_document() {
        let report = deterministic_report(&plan(), &[]);
        assert_eq!(report.matches("## 汇报").count(), 1);
        assert!(!report.contains("GetCount 工具会"));
    }

    #[test]
    fn extracts_existing_windows_paths_from_a_natural_language_goal() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("广州建筑");
        let boundary = temp.path().join("从化区矢量边界");
        std::fs::create_dir_all(&source).unwrap();
        std::fs::create_dir_all(&boundary).unwrap();
        let goal = format!("请用 {} 和 {} 做裁剪", source.display(), boundary.display());
        let paths = extract_existing_paths(&goal);
        assert_eq!(paths, [source, boundary]);
    }

    #[test]
    fn buffer_requires_an_explicit_distance_and_unit() {
        let missing = json!({"goal":"给道路建立缓冲区"});
        assert!(required_user_input(&missing).is_some());

        let explicit = json!({"goal":"给道路建立 500 米缓冲区"});
        assert!(required_user_input(&explicit).is_none());

        let chinese_number = json!({"goal":"给河流生成两公里缓冲区"});
        assert!(required_user_input(&chinese_number).is_none());

        let documented = json!({"goal":"按照项目任务书给道路建立缓冲区"});
        assert!(required_user_input(&documented).is_none());
    }

    #[test]
    fn clipping_does_not_require_an_extra_business_parameter() {
        let context = json!({"goal":"用从化区边界裁剪广州建筑"});
        assert!(required_user_input(&context).is_none());
    }

    #[test]
    fn a_buffer_clarification_satisfies_the_parameter_gate() {
        let mut context = json!({"goal":"给道路建立缓冲区"});
        append_clarification(&mut context, "缓冲距离是多少？", "道路两侧各 800 米");
        assert!(required_user_input(&context).is_none());
    }

    #[test]
    fn roadmap_or_document_path_activates_document_intake() {
        assert!(document_intake_triggered(&json!({
            "goal":"按照项目路线图和相关资料完成整套制图"
        })));
        assert!(document_intake_triggered(&json!({
            "goal":"读取 E:\\项目\\任务书.pdf 后制作地图"
        })));
        assert!(!document_intake_triggered(&json!({
            "goal":"用从化边界裁剪建筑"
        })));
    }

    #[test]
    fn planner_json_extraction_tolerates_fences_and_prose() {
        assert_eq!(
            extract_json_object("```json\n{\"outcome\":\"ready\"}\n```"),
            "{\"outcome\":\"ready\"}"
        );
        assert_eq!(
            extract_json_object("好的，这是计划：{\"a\":1} 请确认。"),
            "{\"a\":1}"
        );
        assert_eq!(extract_json_object("  {\"a\":1}  "), "{\"a\":1}");
        assert_eq!(extract_json_object("完全不是 JSON"), "完全不是 JSON");
    }
}
