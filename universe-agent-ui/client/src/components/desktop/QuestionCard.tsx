/**
 * components/desktop/QuestionCard.tsx — the AskUserQuestion surface.
 *
 * The ZCode contract, rendered the ZCode way (2026-11-12, user order:
 * "مشابهة تماما لخاصتك والخاصة بzcode وبنفس نمط التصميم الاحترافي").
 *
 * What the model can ask (the broker's normalized shape):
 *   questions: [{ question, header?, options: [{value, label, description}],
 *                 multiSelect? }]
 *
 * How it behaves (measured off the shipped ZCode renderer):
 *   - ONE question at a time; "1 / 3" steps through the list.
 *   - ArrowUp / ArrowDown move the selection, Enter picks it and advances,
 *     Ctrl/Cmd+Enter submits the whole set, Escape goes back a question.
 *   - multiSelect toggles several options; single-select advances on pick.
 *   - "Other" free text is always offered (ZCode's `customAnswer`).
 *   - The answer is submitted as the answers MAP, keyed by the question
 *     text: { "<question>": "value" | "value, value" }.
 *
 * It is a CARD docked above the composer (the consent banner's sibling),
 * never a modal: the conversation stays readable behind it.
 *
 * THE STATE BUG THIS FILE AVOIDS (found live on 2026-11-12): a click
 * handler that calls `toggle()` and then reads the picked value straight
 * afterwards reads the STALE state - React has not re-rendered yet - so
 * "pick an option" never advanced and the card sat there doing nothing.
 * Every mutation here therefore computes the NEXT answers map itself and
 * passes it on explicitly.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, HelpCircle } from "lucide-react";
import { useChat } from "@/stores/chat";
import type { QuestionAnswers, QuestionSpec } from "@/lib/api";

/** question text -> the picked values */
type Picks = Record<string, string[]>;

export default function QuestionCard() {
  const questions = useChat(s => s.questions);
  const answerQuestion = useChat(s => s.answerQuestion);
  const pending = questions[0]; // one call at a time, like the consent line

  const specs: QuestionSpec[] = useMemo(
    () => (pending?.questions ?? []).filter(q => q && q.question),
    [pending]
  );
  const [index, setIndex] = useState(0);
  const [picks, setPicks] = useState<Picks>({});
  const [custom, setCustom] = useState<Record<string, string>>({});
  const [cursor, setCursor] = useState(0);

  const pendingId = pending?.pending_id;
  const current = specs[index];

  // a new call (or a new question set) restarts the walk
  useEffect(() => {
    setIndex(0);
    setPicks({});
    setCustom({});
    setCursor(0);
  }, [pendingId, specs.length]);

  /** the values of one question, INCLUDING the not-yet-rendered pick */
  const valuesOf = (spec: QuestionSpec, extra?: string | null): string[] => {
    const chosen = picks[spec.question] ?? [];
    const own = (custom[spec.question] ?? "").trim();
    const list = extra !== undefined && extra !== null
      ? (spec.multiSelect
          ? (chosen.includes(extra) ? chosen : [...chosen, extra])
          : [extra])
      : chosen;
    return own ? [...list, own] : list;
  };

  /** the answers map for every question, with the pending pick merged in */
  const buildAnswers = (extraSpec?: QuestionSpec,
                        extra?: string | null): QuestionAnswers => {
    const out: QuestionAnswers = {};
    for (const spec of specs) {
      const vals = spec === extraSpec
        ? valuesOf(spec, extra)
        : valuesOf(spec);
      if (vals.length) out[spec.question] = vals.join(", ");
    }
    return out;
  };

  const submit = (answers?: QuestionAnswers) => {
    if (!pending) return;
    const final = answers ?? buildAnswers();
    if (!Object.keys(final).length) return;
    void answerQuestion(pending.pending_id, final);
  };

  /** commit a pick: single-select advances (or submits), multi toggles */
  const commit = (spec: QuestionSpec, value: string) => {
    if (spec.multiSelect) {
      setPicks(p => {
        const cur = p[spec.question] ?? [];
        return {
          ...p,
          [spec.question]: cur.includes(value)
            ? cur.filter(v => v !== value)
            : [...cur, value],
        };
      });
      return;
    }
    // single-select: the answer is decided NOW - advance or submit with the
    // NEXT map computed here (the state has not re-rendered yet).
    const answers = buildAnswers(spec, value);
    if (index < specs.length - 1) {
      setPicks(p => ({ ...p, [spec.question]: [value] }));
      setIndex(i => i + 1);
      setCursor(0);
      return;
    }
    submit(answers);
  };

  const advance = (spec: QuestionSpec) => {
    const answers = buildAnswers();
    if (!Object.keys(answers).length) return;
    if (index < specs.length - 1) {
      setIndex(i => i + 1);
      setCursor(0);
    } else {
      submit(answers);
    }
  };

  // keyboard: the ZCode key map (ArrowUp/Down move, Enter advances,
  // Ctrl/Cmd+Enter submits, Escape steps back)
  useEffect(() => {
    if (!current) return;
    const onKey = (e: KeyboardEvent) => {
      const opts = current.options ?? [];
      if (!opts.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setCursor(c => Math.min(opts.length - 1, c + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setCursor(c => Math.max(0, c - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (e.ctrlKey || e.metaKey) {
          submit();
          return;
        }
        const opt = opts[cursor];
        if (opt) commit(current, opt.value);
      } else if (e.key === "Escape") {
        e.preventDefault();
        if (index > 0) {
          setIndex(i => i - 1);
          setCursor(0);
        }
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [current, index, cursor, specs.length, picks, custom]);

  if (!pending || !current) return null;

  const opts = current.options ?? [];
  const multi = !!current.multiSelect;
  const own = custom[current.question] ?? "";
  const picked = picks[current.question] ?? [];

  return (
    <div className="q-card" role="group" aria-label="The assistant asks a question">
      <div className="q-head">
        <span className="q-glyph" aria-hidden="true">
          <HelpCircle size={14} strokeWidth={1.8} />
        </span>
        {current.header && <span className="q-header-chip">{current.header}</span>}
        {specs.length > 1 && (
          <span
            className="q-step"
            aria-label={`Question ${index + 1} of ${specs.length}`}
          >
            {index + 1} / {specs.length}
          </span>
        )}
      </div>

      <div className="q-question">{current.question}</div>

      <div className="q-options" role="group" aria-label="Answer options">
        {opts.map((o, i) => {
          const isPicked = picked.includes(o.value);
          const isCursor = i === cursor;
          return (
            <button
              key={o.value}
              type="button"
              className={`q-option${isPicked ? " picked" : ""}${
                isCursor ? " cursor" : ""
              }`}
              onMouseEnter={() => setCursor(i)}
              onClick={() => commit(current, o.value)}
              aria-pressed={isPicked}
            >
              <span className="q-mark" aria-hidden="true">
                {isPicked ? (
                  <Check size={12} strokeWidth={2.2} />
                ) : multi ? (
                  <span className="q-box" />
                ) : (
                  <span className="q-dot" />
                )}
              </span>
              <span className="q-opt-text">
                <span className="q-opt-label">{o.label || o.value}</span>
                {o.description && (
                  <span className="q-opt-desc">{o.description}</span>
                )}
              </span>
            </button>
          );
        })}
      </div>

      {pending.allow_free_text && (
        <div className="q-other">
          <input
            className="q-other-input"
            value={own}
            onChange={e =>
              setCustom(c => ({ ...c, [current.question]: e.target.value }))
            }
            onKeyDown={e => {
              if (e.key === "Enter") {
                e.preventDefault();
                advance(current);
              }
            }}
            placeholder="Other — type your own answer…"
            aria-label="Your own answer"
            maxLength={500}
          />
        </div>
      )}

      <div className="q-actions">
        {index > 0 && (
          <button
            type="button"
            className="q-btn ghost"
            onClick={() => {
              setIndex(i => i - 1);
              setCursor(0);
            }}
          >
            <ChevronLeft size={13} strokeWidth={2} />
            Back
          </button>
        )}
        {index < specs.length - 1 ? (
          <button
            type="button"
            className="q-btn primary"
            disabled={!valuesOf(current).length}
            onClick={() => advance(current)}
          >
            Next
            <ChevronRight size={13} strokeWidth={2} />
          </button>
        ) : (
          <button
            type="button"
            className="q-btn primary"
            disabled={!valuesOf(current).length}
            onClick={() => submit()}
          >
            Answer
          </button>
        )}
      </div>
    </div>
  );
}
