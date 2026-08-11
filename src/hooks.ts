import { useEffect } from "react";
import { listen } from "@tauri-apps/api/event";
import { useAppStore } from "./store";
import type { TaskPlan, TaskStatus, WorkerState } from "./types";

function workerStatusName(status: unknown): WorkerState {
  if (typeof status === "string") return status as WorkerState;
  if (status && typeof status === "object") return Object.keys(status)[0] as WorkerState;
  return "stopped";
}

export function useBackendEvents() {
  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return undefined;
    const unlisteners = Promise.all([
      listen<{ runtime: string; status: unknown }>("worker_status", ({ payload }) => useAppStore.getState().setWorkerState(payload.runtime, workerStatusName(payload.status))),
      listen<{ type: "task_status"; task_id: string; status: TaskStatus }>("task_status", ({ payload }) => useAppStore.getState().setTaskStatus(payload.task_id, payload.status)),
      listen<{ type: "plan_ready"; task_id: string; plan: TaskPlan; plan_hash: string }>("plan_ready", ({ payload }) => useAppStore.getState().setPendingPlan({ taskId: payload.task_id, plan: payload.plan, hash: payload.plan_hash })),
      listen<{ type: "task_question"; task_id: string; question: string; options: string[] }>("task_question", ({ payload }) => useAppStore.getState().setPendingQuestion({ taskId: payload.task_id, question: payload.question, options: payload.options })),
      listen<{ type: "step_started"; task_id: string; step_id: string }>("step_started", ({ payload }) => useAppStore.getState().setStepState(payload.step_id, "running")),
      listen<{ type: "step_progress"; task_id: string; step_id: string; message: string }>("step_progress", ({ payload }) => useAppStore.getState().setStepState(payload.step_id, "running", payload.message)),
      listen<{ type: "step_completed"; task_id: string; result: { step_id: string } }>("step_completed", ({ payload }) => useAppStore.getState().setStepState(payload.result.step_id, "completed")),
      listen<{ type: "assistant_token"; task_id: string; token: string }>("assistant_token", ({ payload }) => useAppStore.getState().appendAssistantToken(payload.task_id, payload.token)),
      listen<{ type: "task_completed"; task_id: string; report: string }>("task_completed", ({ payload }) => {
        const state = useAppStore.getState();
        state.finishAssistant(payload.task_id, payload.report);
        state.setTaskStatus(payload.task_id, "completed");
        state.setPendingPlan(undefined);
      }),
      listen<{ type: "task_failed"; task_id: string; message: string }>("task_failed", ({ payload }) => {
        useAppStore.getState().appendAssistantToken(payload.task_id, `\n\n> 任务失败：${payload.message}`);
        useAppStore.getState().finishAssistant(payload.task_id, "");
        useAppStore.getState().setTaskStatus(payload.task_id, "failed");
      }),
    ]);
    return () => { void unlisteners.then((items) => items.forEach((unlisten) => unlisten())); };
  }, []);
}
