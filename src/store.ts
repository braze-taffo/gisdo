import { create } from "zustand";
import type { BootstrapPayload, Conversation, Project, Settings, TaskPlan, TaskRecord, UiMessage, WorkerState } from "./types";
import { newId } from "./lib";

interface AppStore {
  boot?: BootstrapPayload;
  settings?: Settings;
  projects: Project[];
  conversations: Conversation[];
  activeProjectId?: string;
  activeConversationId?: string;
  activeTaskId?: string;
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
  appendAssistantToken: (taskId: string, token: string) => void;
  finishAssistant: (taskId: string, fallback: string) => void;
  setTask: (task: TaskRecord) => void;
  setTaskStatus: (taskId: string, status: TaskRecord["status"]) => void;
  setPendingPlan: (plan?: AppStore["pendingPlan"]) => void;
  setPendingQuestion: (question?: AppStore["pendingQuestion"]) => void;
  setStepState: (stepId: string, status: "pending" | "running" | "completed", message?: string) => void;
  setWorkerState: (runtime: string, status: WorkerState) => void;
  setSettings: (settings: Settings) => void;
}

export const useAppStore = create<AppStore>((set, get) => ({
  projects: [], conversations: [], tasks: {}, messages: [], stepStates: {}, workerStates: { pro: "stopped", arcmap: "stopped" },
  setBootstrap: (boot) => set({ boot, settings: boot.settings, projects: boot.projects, activeProjectId: boot.projects[0]?.id,
    ...(!("__TAURI_INTERNALS__" in window) ? { workerStates: { pro: "ready", arcmap: "ready" } as Record<string, WorkerState> } : {}) }),
  setConversations: (conversations) => set({ conversations, activeConversationId: conversations[0]?.id }),
  setActiveProject: (activeProjectId) => set({ activeProjectId, activeConversationId: undefined, conversations: [], messages: [] }),
  setActiveConversation: (activeConversationId) => set({ activeConversationId, messages: [] }),
  addMessage: (message) => { const id = newId(); set({ messages: [...get().messages, { ...message, id }] }); return id; },
  setMessages: (messages) => set({ messages }),
  appendAssistantToken: (taskId, token) => set((state) => {
    const existingIndex = state.messages.findIndex((message) => message.id === `assistant-${taskId}`);
    if (existingIndex < 0) return { messages: [...state.messages, { id: `assistant-${taskId}`, role: "assistant", content: token, streaming: true }] };
    const messages = [...state.messages];
    messages[existingIndex] = { ...messages[existingIndex], content: messages[existingIndex].content + token, streaming: true };
    return { messages };
  }),
  finishAssistant: (taskId, fallback) => set((state) => {
    const id = `assistant-${taskId}`;
    const existing = state.messages.findIndex((message) => message.id === id);
    if (existing < 0) return { messages: [...state.messages, { id, role: "assistant", content: fallback, streaming: false }] };
    const messages = [...state.messages]; messages[existing] = { ...messages[existing], streaming: false };
    return { messages };
  }),
  setTask: (task) => set((state) => ({ tasks: { ...state.tasks, [task.id]: task }, activeTaskId: task.id })),
  setTaskStatus: (taskId, status) => set((state) => ({ tasks: { ...state.tasks, [taskId]: { ...(state.tasks[taskId] ?? { id: taskId, goal: "", created_at: "", updated_at: "" }), status } as TaskRecord }, activeTaskId: taskId })),
  setPendingPlan: (pendingPlan) => set((state) => ({ pendingPlan, currentPlan: pendingPlan?.plan ?? state.currentPlan, stepStates: pendingPlan ? Object.fromEntries(pendingPlan.plan.steps.map((step) => [step.id, state.stepStates[step.id] ?? { status: "pending" as const }])) : state.stepStates })),
  setPendingQuestion: (pendingQuestion) => set({ pendingQuestion }),
  setStepState: (stepId, status, message) => set((state) => ({ stepStates: { ...state.stepStates, [stepId]: { status, message } } })),
  setWorkerState: (runtime, status) => set((state) => ({ workerStates: { ...state.workerStates, [runtime]: status } })),
  setSettings: (settings) => set({ settings }),
}));
