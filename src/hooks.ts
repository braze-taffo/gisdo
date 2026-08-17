import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { listen } from "@tauri-apps/api/event";
import { useAppStore } from "./store";
import type { TaskPlan, TaskStatus, WorkerState } from "./types";

function workerStatusName(status: unknown): WorkerState {
  if (typeof status === "string") return status as WorkerState;
  if (status && typeof status === "object" && "status" in status) {
    const tag = (status as { status: unknown }).status;
    if (typeof tag === "string") return tag as WorkerState;
  }
  return "stopped";
}

/** 已取消/uncertain 的任务不再被 task_failed 覆盖成“任务失败”。 */
const NON_FAILURE_STATUSES = new Set(["cancelled", "uncertain", "cancelling"]);

export function useBackendEvents() {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return undefined;
    const store = () => useAppStore.getState();
    const unlisteners = Promise.all([
      listen<{ runtime: string; status: unknown }>("worker_status", ({ payload }) => store().setWorkerState(payload.runtime, workerStatusName(payload.status))),
      listen<{ type: "task_status"; task_id: string; status: TaskStatus }>("task_status", ({ payload }) => store().setTaskStatus(payload.task_id, payload.status)),
      listen<{ type: "plan_ready"; task_id: string; plan: TaskPlan; plan_hash: string }>("plan_ready", ({ payload }) => store().setPendingPlan({ taskId: payload.task_id, plan: payload.plan, hash: payload.plan_hash })),
      listen<{ type: "task_question"; task_id: string; question: string; options: string[] }>("task_question", ({ payload }) => store().setPendingQuestion({ taskId: payload.task_id, question: payload.question, options: payload.options })),
      listen<{ type: "step_started"; task_id: string; step_id: string }>("step_started", ({ payload }) => store().setStepState(payload.step_id, "running")),
      listen<{ type: "step_progress"; task_id: string; step_id: string; message: string }>("step_progress", ({ payload }) => store().setStepState(payload.step_id, "running", payload.message)),
      listen<{ type: "step_completed"; task_id: string; result: { step_id: string } }>("step_completed", ({ payload }) => store().setStepState(payload.result.step_id, "completed")),
      listen<{ type: "assistant_token"; task_id: string; token: string }>("assistant_token", ({ payload }) => store().queueAssistantToken(payload.task_id, payload.token)),
      listen<{ type: "task_completed"; task_id: string; report: string }>("task_completed", ({ payload }) => {
        const state = store();
        const conversationId = state.taskConversations[payload.task_id];
        state.finishAssistant(payload.task_id, payload.report);
        state.setTaskStatus(payload.task_id, "completed");
        state.setPendingPlan(undefined);
        state.setPendingQuestion(undefined);
        if (conversationId) void queryClient.invalidateQueries({ queryKey: ["messages", conversationId] });
      }),
      listen<{ type: "task_failed"; task_id: string; message: string; uncertain_outputs?: string[] }>("task_failed", ({ payload }) => {
        const state = store();
        const current = state.tasks[payload.task_id]?.status;
        if (current && NON_FAILURE_STATUSES.has(current)) {
          // 取消/uncertain 的终态由 task_status 事件表达，不追加失败文案。
          state.finishAssistant(payload.task_id, "");
          state.setPendingPlan(undefined);
          state.setPendingQuestion(undefined);
          return;
        }
        state.appendAssistantToken(payload.task_id, `\n\n> 任务失败：${payload.message}`);
        state.finishAssistant(payload.task_id, "");
        state.setTaskStatus(payload.task_id, "failed");
        state.setPendingPlan(undefined);
        state.setPendingQuestion(undefined);
      }),
    ]);
    return () => { void unlisteners.then((items) => items.forEach((unlisten) => unlisten())); };
  }, [queryClient]);
}
