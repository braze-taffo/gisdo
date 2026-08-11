export type RuntimeKind = "pro" | "arcmap" | "legacy" | "native";
export type TaskStatus = "queued" | "planning" | "needs_input" | "awaiting_approval" | "running" | "cancelling" | "completed" | "failed" | "cancelled" | "uncertain";
export type AutonomyMode = "confirm_writes" | "autonomous" | "confirm_every_step";

export interface Settings {
  modern_python: string;
  arcmap_python: string;
  output_root: string;
  ai_enabled: boolean;
  ai_base_url: string;
  ai_credential_ref?: string;
  ai_model: string;
  ai_thinking_level: string;
  autonomy_mode: AutonomyMode;
  language: string;
  execution_engine: "worker" | "legacy";
}

export interface Project {
  id: string;
  name: string;
  project_dir: string;
  map_output_dir: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface PlanStep {
  id: string;
  stage?: string;
  requirement_refs?: string[];
  runtime: RuntimeKind;
  tool: string;
  params: Record<string, unknown>;
  depends_on: string[];
  validation: "none" | "dataset" | "png" | "package";
}

export interface TaskPlan {
  version: number;
  id: string;
  goal: string;
  steps: PlanStep[];
  expected_outputs: string[];
}

export interface TaskRecord {
  id: string;
  conversation_id?: string;
  goal: string;
  status: TaskStatus;
  plan?: TaskPlan;
  plan_hash?: string;
  created_at: string;
  updated_at: string;
}

export interface BootstrapPayload {
  settings: Settings;
  projects: Project[];
  tool_count: number;
  stable_prefix_hash: string;
  data_model: string;
}

export interface RuntimeConfig {
  kind: RuntimeKind;
  python_path: string;
  version?: string;
  healthy: boolean;
}

export interface UiMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  streaming?: boolean;
}

export type WorkerState = "stopped" | "starting" | "ready" | "busy" | "recycling" | "failed";
