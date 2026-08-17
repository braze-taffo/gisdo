import { beforeEach, describe, expect, it } from "vitest";
import { useAppStore } from "./store";

describe("assistant streaming is conversation-scoped", () => {
  beforeEach(() => {
    useAppStore.setState({
      messages: [],
      tasks: {},
      taskConversations: {},
      activeConversationId: "conversation-a",
      activeTaskId: undefined,
    });
  });

  it("appends tokens for a task bound to the active conversation", () => {
    useAppStore.getState().bindTask("task-1", "conversation-a");
    useAppStore.getState().appendAssistantToken("task-1", "从化");
    useAppStore.getState().appendAssistantToken("task-1", "边界");
    const messages = useAppStore.getState().messages;
    expect(messages).toHaveLength(1);
    expect(messages[0].content).toBe("从化边界");
  });

  it("drops tokens after switching to another conversation (no cross-talk)", () => {
    useAppStore.getState().bindTask("task-1", "conversation-a");
    useAppStore.getState().setActiveConversation("conversation-b");
    useAppStore.getState().appendAssistantToken("task-1", "串台");
    expect(useAppStore.getState().messages).toHaveLength(0);
  });

  it("running state only reflects the active conversation's tasks", () => {
    useAppStore.getState().bindTask("task-a", "conversation-a");
    useAppStore.getState().bindTask("task-b", "conversation-b");
    useAppStore.getState().setTaskStatus("task-b", "running");
    expect(useAppStore.getState().conversationRunning()).toBe(false);
    useAppStore.getState().setTaskStatus("task-a", "planning");
    expect(useAppStore.getState().conversationRunning()).toBe(true);
  });

  it("terminal status releases the task lock instead of pinning activeTaskId", () => {
    useAppStore.getState().bindTask("task-1", "conversation-a");
    useAppStore.getState().setTaskStatus("task-1", "completed");
    expect(useAppStore.getState().activeTaskId).toBeUndefined();
    expect(useAppStore.getState().conversationRunning()).toBe(false);
  });
});
