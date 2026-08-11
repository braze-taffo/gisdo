use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use gisdo_domain::{ExecutionEngine, PlanStep, RuntimeKind};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sysinfo::{Pid, System};
use thiserror::Error;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, BufWriter, Lines};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::{Mutex, broadcast};
use tokio::time::timeout;
use uuid::Uuid;

pub const PROTOCOL_VERSION: u32 = 1;
pub const MAX_TASKS_PER_WORKER: u32 = 20;
pub const MAX_WORKER_MEMORY_BYTES: u64 = 1_500 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WorkerRequest {
    Hello {
        protocol: u32,
    },
    ExecutePlan {
        request_id: Uuid,
        task_id: Uuid,
        plan_hash: String,
        steps: Vec<PlanStep>,
    },
    InspectPaths {
        request_id: Uuid,
        roots: Vec<PathBuf>,
        max_items: u32,
    },
    Shutdown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WorkerEvent {
    Ready {
        protocol: u32,
        runtime: RuntimeKind,
        python_version: String,
        arcpy_version: Option<String>,
        pid: u32,
    },
    StepStarted {
        request_id: Uuid,
        step_id: String,
    },
    Progress {
        request_id: Uuid,
        step_id: String,
        percent: Option<f32>,
        message: String,
    },
    StepCompleted {
        request_id: Uuid,
        step_id: String,
        result: Value,
        artifacts: Vec<PathBuf>,
    },
    PlanCompleted {
        request_id: Uuid,
        results: Vec<Value>,
    },
    InspectionCompleted {
        request_id: Uuid,
        inventory: Value,
    },
    Error {
        request_id: Option<Uuid>,
        step_id: Option<String>,
        code: String,
        message: String,
        write_started: bool,
        severe: bool,
    },
    Log {
        level: String,
        message: String,
    },
    Stopped,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum WorkerStatus {
    Stopped,
    Starting,
    Ready { pid: u32, tasks_completed: u32 },
    Busy { pid: u32, task_id: Uuid },
    Recycling { reason: String },
    Failed { message: String },
}

#[derive(Debug, Error)]
pub enum WorkerError {
    #[error("未配置 {0:?} Worker")]
    NotConfigured(RuntimeKind),
    #[error("Worker 启动失败：{0}")]
    Spawn(#[from] std::io::Error),
    #[error("Worker 握手超时")]
    HandshakeTimeout,
    #[error("Worker 协议错误：{0}")]
    Protocol(String),
    #[error("Worker 执行失败（write_started={write_started}, severe={severe}）：{message}")]
    Execution {
        message: String,
        write_started: bool,
        severe: bool,
    },
    #[error("Worker 意外退出")]
    Exited,
    #[error("任务已取消，部分输出状态不确定")]
    Cancelled,
}

impl WorkerError {
    pub fn may_fallback_legacy(&self) -> bool {
        matches!(
            self,
            Self::HandshakeTimeout | Self::Spawn(_) | Self::Protocol(_)
        )
    }
}

#[derive(Debug, Clone)]
pub struct WorkerConfig {
    pub runtime: RuntimeKind,
    pub python_path: PathBuf,
    pub script_path: PathBuf,
    pub startup_timeout: Duration,
}

impl WorkerConfig {
    pub fn new(
        runtime: RuntimeKind,
        python_path: impl Into<PathBuf>,
        script_path: impl Into<PathBuf>,
    ) -> Self {
        Self {
            runtime,
            python_path: python_path.into(),
            script_path: script_path.into(),
            startup_timeout: Duration::from_secs(45),
        }
    }
}

struct WorkerProcess {
    child: Child,
    input: BufWriter<ChildStdin>,
    output: Lines<BufReader<ChildStdout>>,
    tasks_completed: u32,
    pid: u32,
    severe_error: bool,
    #[cfg(windows)]
    _job: windows_job::Job,
}

impl WorkerProcess {
    async fn start(config: WorkerConfig) -> Result<Self, WorkerError> {
        let mut command = Command::new(&config.python_path);
        command
            .arg("-u")
            .arg(&config.script_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            command.creation_flags(0x0800_0000);
        }
        let mut child = command.spawn()?;
        let pid = child.id().ok_or(WorkerError::Exited)?;
        #[cfg(windows)]
        let job = windows_job::Job::assign(pid).map_err(WorkerError::Spawn)?;
        let input = child
            .stdin
            .take()
            .ok_or_else(|| WorkerError::Protocol("stdin 不可用".into()))?;
        let output = child
            .stdout
            .take()
            .ok_or_else(|| WorkerError::Protocol("stdout 不可用".into()))?;
        if let Some(stderr) = child.stderr.take() {
            let mut lines = BufReader::new(stderr).lines();
            tokio::spawn(async move {
                while let Ok(Some(line)) = lines.next_line().await {
                    tracing::warn!(worker_stderr = %line);
                }
            });
        }
        let mut process = Self {
            child,
            input: BufWriter::new(input),
            output: BufReader::new(output).lines(),
            tasks_completed: 0,
            pid,
            severe_error: false,
            #[cfg(windows)]
            _job: job,
        };
        process
            .send(&WorkerRequest::Hello {
                protocol: PROTOCOL_VERSION,
            })
            .await?;
        let line = timeout(config.startup_timeout, process.output.next_line())
            .await
            .map_err(|_| WorkerError::HandshakeTimeout)??
            .ok_or(WorkerError::Exited)?;
        match parse_event(&line)? {
            WorkerEvent::Ready {
                protocol, runtime, ..
            } if protocol == PROTOCOL_VERSION && runtime == config.runtime => Ok(process),
            event => Err(WorkerError::Protocol(format!("握手响应不匹配：{event:?}"))),
        }
    }

    async fn send(&mut self, request: &WorkerRequest) -> Result<(), WorkerError> {
        let json = serde_json::to_vec(request)
            .map_err(|error| WorkerError::Protocol(error.to_string()))?;
        self.input.write_all(&json).await?;
        self.input.write_all(b"\n").await?;
        self.input.flush().await?;
        Ok(())
    }

    async fn execute(
        &mut self,
        task_id: Uuid,
        plan_hash: String,
        steps: Vec<PlanStep>,
        events: &broadcast::Sender<WorkerEvent>,
    ) -> Result<Vec<Value>, WorkerError> {
        let request_id = Uuid::new_v4();
        let may_write = steps
            .iter()
            .any(|step| step.validation != gisdo_domain::ValidationPolicy::None);
        self.send(&WorkerRequest::ExecutePlan {
            request_id,
            task_id,
            plan_hash,
            steps,
        })
        .await?;
        loop {
            let line = self
                .output
                .next_line()
                .await
                .map_err(|error| WorkerError::Execution {
                    message: error.to_string(),
                    write_started: may_write,
                    severe: true,
                })?
                .ok_or_else(|| WorkerError::Execution {
                    message: "Worker 在请求完成前退出".into(),
                    write_started: may_write,
                    severe: true,
                })?;
            let event = parse_event(&line).map_err(|error| WorkerError::Execution {
                message: error.to_string(),
                write_started: may_write,
                severe: true,
            })?;
            let _ = events.send(event.clone());
            match event {
                WorkerEvent::PlanCompleted {
                    request_id: id,
                    results,
                } if id == request_id => {
                    self.tasks_completed += 1;
                    return Ok(results);
                }
                WorkerEvent::Error {
                    request_id: Some(id),
                    message,
                    write_started,
                    severe,
                    ..
                } if id == request_id => {
                    self.severe_error |= severe;
                    return Err(WorkerError::Execution {
                        message,
                        write_started,
                        severe,
                    });
                }
                _ => {}
            }
        }
    }

    async fn inspect_paths(
        &mut self,
        roots: Vec<PathBuf>,
        max_items: u32,
        events: &broadcast::Sender<WorkerEvent>,
    ) -> Result<Value, WorkerError> {
        let request_id = Uuid::new_v4();
        self.send(&WorkerRequest::InspectPaths {
            request_id,
            roots,
            max_items,
        })
        .await?;
        loop {
            let line = self.output.next_line().await?.ok_or(WorkerError::Exited)?;
            let event = parse_event(&line)?;
            let _ = events.send(event.clone());
            match event {
                WorkerEvent::InspectionCompleted {
                    request_id: id,
                    inventory,
                } if id == request_id => return Ok(inventory),
                WorkerEvent::Error {
                    request_id: Some(id),
                    message,
                    severe,
                    ..
                } if id == request_id => {
                    self.severe_error |= severe;
                    return Err(WorkerError::Execution {
                        message,
                        write_started: false,
                        severe,
                    });
                }
                _ => {}
            }
        }
    }

    fn memory_bytes(&self) -> u64 {
        let mut system = System::new();
        system.refresh_processes(
            sysinfo::ProcessesToUpdate::Some(&[Pid::from_u32(self.pid)]),
            true,
        );
        system
            .process(Pid::from_u32(self.pid))
            .map(|process| process.memory())
            .unwrap_or(0)
    }

    fn should_recycle(&self) -> Option<String> {
        if self.severe_error {
            Some("严重 ArcPy 异常".into())
        } else if self.tasks_completed >= MAX_TASKS_PER_WORKER {
            Some(format!("已完成 {} 个任务", self.tasks_completed))
        } else if self.memory_bytes() > MAX_WORKER_MEMORY_BYTES {
            Some("内存超过 1.5 GB".into())
        } else {
            None
        }
    }

    async fn shutdown(mut self) {
        let _ = self.send(&WorkerRequest::Shutdown).await;
        if timeout(Duration::from_secs(3), self.child.wait())
            .await
            .is_err()
        {
            let _ = self.child.kill().await;
        }
    }

    async fn hard_cancel(&mut self) -> Result<(), WorkerError> {
        self.child.kill().await?;
        Err(WorkerError::Cancelled)
    }
}

fn parse_event(line: &str) -> Result<WorkerEvent, WorkerError> {
    serde_json::from_str(line).map_err(|error| {
        WorkerError::Protocol(format!("stdout 必须只包含 JSONL：{error}; line={line:?}"))
    })
}

struct Slot {
    config: WorkerConfig,
    process: Option<WorkerProcess>,
}

#[derive(Clone)]
pub struct WorkerSupervisor {
    slots: Arc<parking_lot::RwLock<HashMap<RuntimeKind, Arc<Mutex<Slot>>>>>,
    active_pids: Arc<parking_lot::RwLock<HashMap<RuntimeKind, u32>>>,
    engine: Arc<parking_lot::RwLock<ExecutionEngine>>,
    events: broadcast::Sender<WorkerEvent>,
    statuses: broadcast::Sender<(RuntimeKind, WorkerStatus)>,
}

impl WorkerSupervisor {
    pub fn new(configs: impl IntoIterator<Item = WorkerConfig>) -> Self {
        let slots = configs
            .into_iter()
            .map(|config| {
                (
                    config.runtime,
                    Arc::new(Mutex::new(Slot {
                        config,
                        process: None,
                    })),
                )
            })
            .collect();
        let (events, _) = broadcast::channel(256);
        let (statuses, _) = broadcast::channel(64);
        Self {
            slots: Arc::new(parking_lot::RwLock::new(slots)),
            active_pids: Arc::new(parking_lot::RwLock::new(HashMap::new())),
            engine: Arc::new(parking_lot::RwLock::new(ExecutionEngine::Worker)),
            events,
            statuses,
        }
    }

    pub fn set_engine(&self, engine: ExecutionEngine) {
        *self.engine.write() = engine;
    }

    pub async fn reconfigure(&self, configs: impl IntoIterator<Item = WorkerConfig>) {
        self.shutdown().await;
        let slots = configs
            .into_iter()
            .map(|config| {
                (
                    config.runtime,
                    Arc::new(Mutex::new(Slot {
                        config,
                        process: None,
                    })),
                )
            })
            .collect();
        *self.slots.write() = slots;
    }

    pub fn subscribe_events(&self) -> broadcast::Receiver<WorkerEvent> {
        self.events.subscribe()
    }
    pub fn subscribe_status(&self) -> broadcast::Receiver<(RuntimeKind, WorkerStatus)> {
        self.statuses.subscribe()
    }

    pub async fn prewarm(&self) {
        let slots: Vec<_> = self.slots.read().values().cloned().collect();
        let handles: Vec<_> = slots
            .into_iter()
            .map(|slot| {
                let statuses = self.statuses.clone();
                tokio::spawn(async move {
                    let mut slot = slot.lock().await;
                    let runtime = slot.config.runtime;
                    let _ = statuses.send((runtime, WorkerStatus::Starting));
                    match WorkerProcess::start(slot.config.clone()).await {
                        Ok(process) => {
                            let pid = process.pid;
                            slot.process = Some(process);
                            let _ = statuses.send((
                                runtime,
                                WorkerStatus::Ready {
                                    pid,
                                    tasks_completed: 0,
                                },
                            ));
                        }
                        Err(error) => {
                            let _ = statuses.send((
                                runtime,
                                WorkerStatus::Failed {
                                    message: error.to_string(),
                                },
                            ));
                        }
                    }
                })
            })
            .collect();
        for handle in handles {
            let _ = handle.await;
        }
    }

    pub async fn execute(
        &self,
        runtime: RuntimeKind,
        task_id: Uuid,
        plan_hash: String,
        steps: Vec<PlanStep>,
    ) -> Result<Vec<Value>, WorkerError> {
        let slot = self
            .slots
            .read()
            .get(&runtime)
            .cloned()
            .ok_or(WorkerError::NotConfigured(runtime))?;
        if *self.engine.read() == ExecutionEngine::Legacy {
            let config = slot.lock().await.config.clone();
            return self
                .execute_oneshot(config, task_id, plan_hash, steps)
                .await;
        }
        let mut slot = slot.lock().await;
        if slot.process.is_none() {
            let _ = self.statuses.send((runtime, WorkerStatus::Starting));
            match WorkerProcess::start(slot.config.clone()).await {
                Ok(process) => slot.process = Some(process),
                Err(error) if error.may_fallback_legacy() => {
                    let config = slot.config.clone();
                    drop(slot);
                    return self
                        .execute_oneshot(config, task_id, plan_hash, steps)
                        .await;
                }
                Err(error) => return Err(error),
            }
        }
        let process = slot.process.as_mut().expect("started");
        self.active_pids.write().insert(runtime, process.pid);
        let _ = self.statuses.send((
            runtime,
            WorkerStatus::Busy {
                pid: process.pid,
                task_id,
            },
        ));
        let result = process
            .execute(task_id, plan_hash, steps, &self.events)
            .await;
        self.active_pids.write().remove(&runtime);
        let failed = result.is_err();
        if let Some(reason) = process
            .should_recycle()
            .or_else(|| failed.then(|| "Worker 执行或协议失败".to_owned()))
        {
            let _ = self
                .statuses
                .send((runtime, WorkerStatus::Recycling { reason }));
            if let Some(process) = slot.process.take() {
                process.shutdown().await;
            }
        } else {
            let _ = self.statuses.send((
                runtime,
                WorkerStatus::Ready {
                    pid: process.pid,
                    tasks_completed: process.tasks_completed,
                },
            ));
        }
        result
    }

    pub async fn inspect_paths(
        &self,
        runtime: RuntimeKind,
        task_id: Uuid,
        roots: Vec<PathBuf>,
        max_items: u32,
    ) -> Result<Value, WorkerError> {
        let slot = self
            .slots
            .read()
            .get(&runtime)
            .cloned()
            .ok_or(WorkerError::NotConfigured(runtime))?;
        let mut slot = slot.lock().await;
        if slot.process.is_none() {
            let _ = self.statuses.send((runtime, WorkerStatus::Starting));
            slot.process = Some(WorkerProcess::start(slot.config.clone()).await?);
        }
        let process = slot.process.as_mut().expect("started");
        self.active_pids.write().insert(runtime, process.pid);
        let _ = self.statuses.send((
            runtime,
            WorkerStatus::Busy {
                pid: process.pid,
                task_id,
            },
        ));
        let result = process.inspect_paths(roots, max_items, &self.events).await;
        self.active_pids.write().remove(&runtime);
        if let Some(reason) = process.should_recycle() {
            let _ = self
                .statuses
                .send((runtime, WorkerStatus::Recycling { reason }));
            if let Some(process) = slot.process.take() {
                process.shutdown().await;
            }
        } else {
            let _ = self.statuses.send((
                runtime,
                WorkerStatus::Ready {
                    pid: process.pid,
                    tasks_completed: process.tasks_completed,
                },
            ));
        }
        result
    }

    async fn execute_oneshot(
        &self,
        config: WorkerConfig,
        task_id: Uuid,
        plan_hash: String,
        steps: Vec<PlanStep>,
    ) -> Result<Vec<Value>, WorkerError> {
        let runtime = config.runtime;
        let _ = self.statuses.send((runtime, WorkerStatus::Starting));
        let mut process = WorkerProcess::start(config).await?;
        let pid = process.pid;
        let _ = self
            .statuses
            .send((runtime, WorkerStatus::Busy { pid, task_id }));
        let result = process
            .execute(task_id, plan_hash, steps, &self.events)
            .await;
        process.shutdown().await;
        let _ = self.statuses.send((runtime, WorkerStatus::Stopped));
        result
    }

    pub async fn cancel_runtime(&self, runtime: RuntimeKind) -> Result<(), WorkerError> {
        let active_pid = { self.active_pids.read().get(&runtime).copied() };
        if let Some(pid) = active_pid {
            #[cfg(windows)]
            {
                let _ = Command::new("taskkill")
                    .args(["/PID", &pid.to_string(), "/T", "/F"])
                    .creation_flags(0x0800_0000)
                    .output()
                    .await;
            }
            #[cfg(not(windows))]
            {
                let _ = Command::new("kill")
                    .args(["-KILL", &pid.to_string()])
                    .output()
                    .await;
            }
            self.active_pids.write().remove(&runtime);
            let _ = self.statuses.send((runtime, WorkerStatus::Stopped));
            return Err(WorkerError::Cancelled);
        }
        let slot = self
            .slots
            .read()
            .get(&runtime)
            .cloned()
            .ok_or(WorkerError::NotConfigured(runtime))?;
        let mut slot = slot.lock().await;
        let Some(process) = slot.process.as_mut() else {
            return Ok(());
        };
        let result = process.hard_cancel().await;
        slot.process = None;
        let _ = self.statuses.send((runtime, WorkerStatus::Stopped));
        result
    }

    pub async fn shutdown(&self) {
        let slots: Vec<_> = self.slots.read().values().cloned().collect();
        for slot in slots {
            let mut slot = slot.lock().await;
            if let Some(process) = slot.process.take() {
                process.shutdown().await;
            }
        }
    }
}

pub fn configured_worker(runtime: RuntimeKind, python: &Path, app_root: &Path) -> WorkerConfig {
    let folder = match runtime {
        RuntimeKind::Pro => "pro",
        RuntimeKind::Arcmap => "arcmap",
        _ => "legacy",
    };
    WorkerConfig::new(
        runtime,
        python,
        app_root
            .join("workers")
            .join(folder)
            .join("worker_server.py"),
    )
}

#[cfg(windows)]
mod windows_job {
    use std::io;
    use std::mem::{size_of, zeroed};
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        SetInformationJobObject,
    };
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
    };

    pub struct Job(HANDLE);
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn assign(pid: u32) -> io::Result<Self> {
            unsafe {
                let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if job.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(
                    job,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const _,
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                ) == 0
                {
                    CloseHandle(job);
                    return Err(io::Error::last_os_error());
                }
                let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
                if process.is_null() {
                    CloseHandle(job);
                    return Err(io::Error::last_os_error());
                }
                let assigned = AssignProcessToJobObject(job, process);
                CloseHandle(process);
                if assigned == 0 {
                    CloseHandle(job);
                    return Err(io::Error::last_os_error());
                }
                Ok(Self(job))
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protocol_round_trips_unicode() {
        let event = WorkerEvent::Progress {
            request_id: Uuid::nil(),
            step_id: "裁剪".into(),
            percent: Some(50.0),
            message: "中文路径 E:\\临时文件夹".into(),
        };
        let json = serde_json::to_string(&event).unwrap();
        assert_eq!(
            serde_json::from_str::<WorkerEvent>(&json)
                .unwrap()
                .to_string_for_test(),
            "裁剪"
        );
    }

    #[test]
    fn fallback_is_forbidden_after_write_started() {
        let handshake = WorkerError::HandshakeTimeout;
        let before = WorkerError::Execution {
            message: "handshake".into(),
            write_started: false,
            severe: false,
        };
        let during = WorkerError::Execution {
            message: "crash".into(),
            write_started: true,
            severe: true,
        };
        assert!(handshake.may_fallback_legacy());
        assert!(!before.may_fallback_legacy());
        assert!(!during.may_fallback_legacy());
    }

    trait TestEvent {
        fn to_string_for_test(&self) -> String;
    }
    impl TestEvent for WorkerEvent {
        fn to_string_for_test(&self) -> String {
            match self {
                WorkerEvent::Progress { step_id, .. } => step_id.clone(),
                _ => String::new(),
            }
        }
    }
}
