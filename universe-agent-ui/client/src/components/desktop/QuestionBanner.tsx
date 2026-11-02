/**
 * components/desktop/QuestionBanner.tsx — ask_user's UI half (Phase 1
 * broker → Phase 2 surface). When the model calls question.ask, the
 * bridge's QuestionsBroker parks a structured question and emits
 * question_required; this banner renders it above the composer — the
 * consent line's sibling, never a modal: the options are buttons, an
 * allow_free_text question also accepts typed input, and the broker's
 * own auto-continue timer (Settings) resolves it if the user is away.
 */
import { useState } from "react";
import { HelpCircle } from "lucide-react";
import { useChat } from "@/stores/chat";

export default function QuestionBanner() {
  const questions = useChat(s => s.questions);
  const answerQuestion = useChat(s => s.answerQuestion);
  const [free, setFree] = useState<Record<string, string>>({});

  if (questions.length === 0) return null;
  const q = questions[0]; // one at a time, like the consent line

  const submitFree = () => {
    const t = (free[q.pending_id] ?? "").trim();
    if (!t) return;
    void answerQuestion(q.pending_id, t);
    setFree(f => ({ ...f, [q.pending_id]: "" }));
  };

  return (
    <div
      className="q-banner"
      role="status"
      aria-live="polite"
      aria-label="The assistant asks a question"
    >
      <span className="q-text">
        <HelpCircle
          size={14}
          strokeWidth={1.8}
          style={{
            verticalAlign: "-2px",
            marginRight: 6,
            color: "var(--accent-cyan)",
          }}
          aria-hidden="true"
        />
        {q.question || "The assistant needs an answer to continue."}
      </span>
      <span className="q-opts" role="group" aria-label="Answer options">
        {q.options.map(o => (
          <button
            key={o}
            type="button"
            className="q-opt"
            onClick={() => void answerQuestion(q.pending_id, o)}
          >
            {o}
          </button>
        ))}
      </span>
      {q.allow_free_text && (
        <span className="q-free">
          <input
            value={free[q.pending_id] ?? ""}
            onChange={e =>
              setFree(f => ({ ...f, [q.pending_id]: e.target.value }))
            }
            onKeyDown={e => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitFree();
              }
            }}
            placeholder="or type your own answer…"
            aria-label="Free-text answer"
            maxLength={500}
          />
          <button
            type="button"
            className="q-opt"
            onClick={submitFree}
            disabled={!(free[q.pending_id] ?? "").trim()}
          >
            Send
          </button>
        </span>
      )}
    </div>
  );
}
