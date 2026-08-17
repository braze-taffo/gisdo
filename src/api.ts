import { invoke } from "@tauri-apps/api/core";
import type { BootstrapPayload, Conversation, Project, RuntimeConfig, RuntimeKind, Settings, TaskRecord, UiMessage } from "./types";

const demoSettings: Settings = {
  modern_python: "C:\\Program Files\\GeoScene\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
  arcmap_python: "C:\\Python27\\ArcGIS10.8\\python.exe",
  output_root: "E:\\GISdo\\outputs",
  ai_enabled: true,
  ai_base_url: "https://api.example.com/v1",
  ai_credential_ref: "llm-api-key",
  ai_model: "planner-model",
  ai_thinking_level: "medium",
  ai_timeout_seconds: 300,
  worker_timeout_seconds: 1800,
  autonomy_mode: "confirm_writes",
  language: "zh",
  execution_engine: "worker",
};

const demoProjects: Project[] = [
  { id: "demo-guangzhou", name: "广州建筑与行政区", project_dir: "E:\\GIS\\广州建筑项目", map_output_dir: "E:\\GIS\\广州建筑项目\\outputs", created_at: new Date().toISOString() },
  { id: "demo-roads", name: "从化道路制图", project_dir: "E:\\GIS\\从化道路", map_output_dir: "E:\\GIS\\从化道路\\maps", created_at: new Date().toISOString() },
];

const demoConversations: Conversation[] = [
  { id: "demo-conversation", project_id: "demo-guangzhou", title: "从化区建筑裁剪与校验", created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
];

function isTauri() {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function call<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (isTauri()) return invoke<T>(command, args);
  return demoCall(command, args) as Promise<T>;
}

async function demoCall(command: string, args?: Record<string, unknown>): Promise<unknown> {
  switch (command) {
    case "bootstrap": return { settings: demoSettings, projects: demoProjects, tool_count: 518, stable_prefix_hash: "4ff80a9a-demo-stable-prefix", data_model: "sqlite_independent_from_legacy_json" } satisfies BootstrapPayload;
    case "get_settings": return demoSettings;
    case "save_settings": return (args?.input as { settings: Settings }).settings;
    case "list_projects": return demoProjects;
    case "create_project": return { id: crypto.randomUUID(), name: args?.name, project_dir: args?.projectDir, map_output_dir: args?.mapOutputDir, created_at: new Date().toISOString() };
    case "update_project": return args?.project;
    case "list_conversations": return demoConversations.filter((item) => item.project_id === args?.projectId);
    case "list_messages": return [];
    case "create_conversation": return { id: crypto.randomUUID(), project_id: args?.projectId, title: args?.title ?? "新对话", created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
    case "rename_conversation": return { ...demoConversations[0], title: args?.title };
    case "discover_runtimes": return [
      { kind: "pro", python_path: demoSettings.modern_python, version: "Python 3.11.11 · ArcPy ready", healthy: true },
      { kind: "arcmap", python_path: demoSettings.arcmap_python, version: "Python 2.7.10 · ArcPy ready", healthy: true },
    ] satisfies RuntimeConfig[];
    case "probe_runtime": return { kind: args?.kind, python_path: args?.pythonPath, healthy: true };
    case "list_tasks": return [];
    case "get_task": return null;
    case "start_task": throw new Error("浏览器演示模式不执行本机 GIS 任务；请在 Tauri 桌面应用中运行。");
    default: return null;
  }
}

export const api = {
  bootstrap: () => call<BootstrapPayload>("bootstrap"),
  getSettings: () => call<Settings>("get_settings"),
  saveSettings: (settings: Settings, apiKey?: string) => call<Settings>("save_settings", { input: { settings, api_key: apiKey || null } }),
  listProjects: () => call<Project[]>("list_projects"),
  createProject: (name: string, projectDir: string, mapOutputDir: string) => call<Project>("create_project", { name, projectDir, mapOutputDir }),
  updateProject: (project: Project) => call<Project>("update_project", { project }),
  listConversations: (projectId: string) => call<Conversation[]>("list_conversations", { projectId }),
  listMessages: (conversationId: string) => call<Array<UiMessage & { created_at: string }>>("list_messages", { conversationId }),
  createConversation: (projectId: string, title?: string) => call<Conversation>("create_conversation", { projectId, title }),
  renameConversation: (projectId: string, conversationId: string, title: string) => call<Conversation>("rename_conversation", { projectId, conversationId, title }),
  startTask: (conversationId: string | undefined, goal: string, context?: unknown) => call<string>("start_task", { conversationId, goal, context }),
  approvePlan: (taskId: string, planHash: string) => call<void>("approve_plan", { taskId, planHash }),
  answerTaskQuestion: (taskId: string, answer: string) => call<void>("answer_task_question", { taskId, answer }),
  cancelTask: (taskId: string) => call<void>("cancel_task", { taskId }),
  getTask: (taskId: string) => call<TaskRecord | null>("get_task", { taskId }),
  listTasks: () => call<TaskRecord[]>("list_tasks"),
  discoverRuntimes: () => call<RuntimeConfig[]>("discover_runtimes"),
  probeRuntime: (kind: RuntimeKind, pythonPath: string) => call<RuntimeConfig>("probe_runtime", { kind, pythonPath }),
  setExecutionEngine: (engine: "worker" | "legacy") => call<Settings>("set_execution_engine", { engine }),
};
