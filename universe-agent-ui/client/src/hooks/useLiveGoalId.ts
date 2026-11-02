/**
 * hooks/useLiveGoalId.ts — the one "goal on screen" selector, shared by
 * every pane that follows the user's eyes (Activity, Progress, status
 * strip/card, side conversation): the goal pinned by the ?goal= view,
 * else the active task's live streaming message goal. Selector consumers
 * stay in lockstep — one copy here instead of five drifted ones.
 */
import { useChat } from "@/stores/chat";

export function useLiveGoalId(): string | null {
  return useChat(s => {
    const viewed = s.viewedGoalId;
    if (viewed) return viewed;
    const active = s.activeTaskId;
    if (!active) return null;
    const live = s.messages.find(
      m =>
        m.role === "agent" &&
        m.streaming &&
        s.taskByMessage[m.turn_id] === active
    );
    return live?.turn_id ?? null;
  });
}
