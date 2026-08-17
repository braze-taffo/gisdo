import { create } from "zustand";
import type { BootstrapPayload, Conversation, Project, Settings, TaskPlan, TaskRecord, UiMessage, WorkerState } from "./types";
import { newId } from "./lib";

const RUNNING_STATUSES = new Set(["planning", "running", "awaiting_approval", "needs_input", "cancelling"]);
/** 流式 token 的合并窗口：高频 token 逐条写入会造成 O(n²) 的数组复制与 Markdown 重解析。 */
const TOKEN_FLUSH_MS = 80;

/** 按 task 聚合 token，窗口到期一次性 flush 进 store。 */
const tokenBuffers = new Map<string, { text: string; timer: ReturnType<typeof setTimeout> }>();

function flushTokens(taskId: string) {
  const entry = tokenBuffers.get(taskId);
  if (!entry) return;
  tokenBuffers.delete(taskId);
  clearTimeout(entry.timer);
  if (entry.text) useAppStore.getState().appendAssistantToken(taskId, entry.text);
}

interface AppStore {
  boot?: BootstrapPayload;
  settings?: Settings;
  projects: Project[];
  conversations: Conversation[];
  activeProjectId?: string;
  activeConversationId?: string;
  activeTaskId?: string;
  /** taskId -> conversationId：流式 token 只应写入任务所属的会话视图。 */
  taskConversations: Record<string, string | undefined>;
  tasks: Record<string, TaskRecord>;
  messages: UiMessage[];
  pendingPlan?: { taskId: string; plan: TaskPlan; hash: string };
  currentPlan?: TaskPlan;
  pendingQuestion?: { taskId: string; question: string; options: string[] };
  stepStates: Record<string, { status: "pending" | "running" | "completed"; message?: string }>;
  workerStates: Record<string, WorkerState>;
  setBootstrap: (boot: BootstrapPayload) => void;
  setConversations: (items: Conversation[]) => void;
  setActiveProject: (id?: string) => void;
  setActiveConversation: (id?: string) => void;
  addMessage: (message: Omit<UiMessage, "id">) => string;
  setMessages: (messages: UiMessage[]) => void;
  /** 流式 token 是否属于当前会话；不属于时丢弃（切走会话后不再串台）。 */
  taskInView: (taskId: string) => boolean;
  appendAssistantToken: (taskId: string, token: string) => void;
  /** 事件监听入口：token 进入 80ms 合并窗口，到期批量写入。 */
  queueAssistantToken: (taskId: string, token: string) => void;
  finishAssistant: (taskId: string, fallback: string) => void;
  setTask: (task: TaskRecord) => void;
  setTaskStatus: (taskId: string, status: TaskRecord["status"]) => void;
  bindTask: (taskId: string, conversationId: string | undefined) => void;
  setPendingPlan: (plan?: AppStore["pendingPlan"]) => void;
  setPendingQuestion: (question?: AppStore["pendingQuestion"]) => void;
  setStepState: (stepId: string, status: "pending" | "running" | "completed", message?: string) => void;
  setWorkerState: (runtime: string, status: WorkerState) => void;
  setSettings: (settings: Settings) => void;
  /** 当前会话内是否有任务在运行；其他会话的任务不应禁用这里的输入框。 */
  conversationRunning: () => boolean;
}

export const useAppStore = create<AppStore>((set, get) => ({
  projects: [], conversations: [], tasks: {}, messages: [], stepStates: {}, taskConversations: {},
  workerStates: { pro: "stopped", arcmap: "stopped" },
  setBootstrap: (boot) => set({ boot, settings: boot.settings, projects: boot.projects, activeProjectId: boot.projects[0]?.id,
    ...(!("__TAURI_INTERNALS__" in window) ? { workerStates: { pro: "ready", arcmap: "ready" } as Record<string, WorkerState> } : {}) }),
  setConversations: (conversations) => set({ conversations, activeConversationId: conversations[0]?.id }),
  setActiveProject: (activeProjectId) => set({ activeProjectId, activeConversationId: undefined, conversations: [], messages: [] }),
  setActiveConversation: (activeConversationId) => set({ activeConversationId, messages: [] }),
  addMessage: (message) => { const id = newId(); set({ messages: [...get().messages, { ...message, id }] }); return id; },
  setMessages: (messages) => set({ messages }),
  taskInView: (taskId) => {
    const state = get();
    return state.taskConversations[taskId] === undefined || state.taskConversations[taskId] === state.activeConversationId;
  },
  appendAssistantToken: (taskId, token) => set((state) => {
    if (!state.taskInView(taskId)) return {};
    const existingIndex = state.messages.findIndex((message) => message.id === `assistant-${taskId}`);
    if (existingIndex < 0) return { messages: [...state.messages, { id: `assistant-${taskId}`, role: "assistant", content: token, streaming: true }] };
    const messages = [...state.messages];
    messages[existingIndex] = { ...messages[existingIndex], content: messages[existingIndex].content + token, streaming: true };
    return { messages };
  }),
  queueAssistantToken: (taskId, token) => {
    const state = get();
    // 不在当前视图的任务连缓冲都不必积累。
    if (!state.taskInView(taskId)) return;
    const entry = tokenBuffers.get(taskId);
    if (entry) {
      entry.text += token;
      return;
    }
    tokenBuffers.set(taskId, {
      text: token,
      timer: setTimeout(() => flushTokens(taskId), TOKEN_FLUSH_MS),
    });
  },
  finishAssistant: (taskId, fallback) => {
    // 收尾前把合并窗口里的剩余 token 全部落盘，避免最后一段被吞。
    flushTokens(taskId);
    set((state) => {
    if (!state.taskInView(taskId)) return {};
    const id = `assistant-${taskId}`;
    const existing = state.messages.findIndex((message) => message.id === id);
    if (existing < 0) return { messages: [...state.messages, { id, role: "assistant", content: fallback, streaming: false }] };
    const messages = [...state.messages]; messages[existing] = { ...messages[existing], streaming: false };
    return { messages };
    });
  },
  setTask: (task) => set((state) => ({ tasks: { ...state.tasks, [task.id]: task }, activeTaskId: task.id })),
  setTaskStatus: (taskId, status) => set((state) => {
    // 终态之后不再把 activeTaskId 强行拽回旧任务（新会话发消息时会被旧任务锁住输入）。
    const terminal = !RUNNING_STATUSES.has(status);
    const task = (state.tasks[taskId] ?? { id: taskId, goal: "", created_at: "", updated_at: "" }) as TaskRecord;
    return { tasks: { ...state.tasks, [taskId]: { ...task, status } }, activeTaskId: terminal && state.activeTaskId === taskId ? undefined : state.activeTaskId ?? taskId };
  }),
  bindTask: (taskId, conversationId) => set((state) => ({ taskConversations: { ...state.taskConversations, [taskId]: conversationId }, activeTaskId: taskId })),
  setPendingPlan: (pendingPlan) => set((state) => ({ pendingPlan, currentPlan: pendingPlan?.plan ?? state.currentPlan, stepStates: pendingPlan ? Object.fromEntries(pendingPlan.plan.steps.map((step) => [step.id, state.stepStates[step.id] ?? { status: "pending" as const }])) : state.stepStates })),
  setPendingQuestion: (pendingQuestion) => set({ pendingQuestion }),
  setStepState: (stepId, status, message) => set((state) => ({ stepStates: { ...state.stepStates, [stepId]: { status, message } } })),
  setWorkerState: (runtime, status) => set((state) => ({ workerStates: { ...state.workerStates, [runtime]: status } })),
  setSettings: (settings) => set({ settings }),
  conversationRunning: () => {
    const state = get();
    return Object.entries(state.taskConversations).some(([taskId, conversationId]) => {
      if (conversationId !== state.activeConversationId) return false;
      const status = state.tasks[taskId]?.status;
      return Boolean(status && RUNNING_STATUSES.has(status));
    });
  },
}));
