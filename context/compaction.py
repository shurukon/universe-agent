"""context/compaction.py — sliding window + summarization (§12, §13).

When approaching the model's context limit (80%): summarize old messages (keep
decisions and tool results, drop small talk), move large file contents to L3
Star references instead of inline text, and compress repeated tool outputs.
"""
from __future__ import annotations

from typing import Any


COMPACTION_TRIGGER_PCT = 0.85  # the 2026-11 migration decision: ONE compute-based pass at 85%

# Round-7 parity: the /compact summary template (reference harness,
# 9 sections — the "All user messages" rule keeps security-relevant
# constraints verbatim so they outlive the summary).
SUMMARY_TEMPLATE = """Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
<section_start>1. Primary Request and Intent:<section_end> Capture the user's explicit requests, incorporated feedback, and all stated intents — verbatim where possible.
<section_start>2. Key Technical Concepts:<section_end> Important concepts, constraints, and domain rules discussed.
<section_start>3. Files and Code Sections:<section_end> Files read/written/edited and why; key snippets preserved.
<section_start>4. Errors and fixes:<section_end> Errors encountered and how they were fixed (attempt → problem → fix → lesson).
<section_start>5. Problem Solving:<section_end> Problem-solving strategies carried through the conversation.
<section_start>6. All user messages:<section_end> List ALL user messages that are not tool results, VERBATIM. Preserve any security-relevant instructions or constraints verbatim so they remain in effect after compaction (sensitive files or data to avoid, operations that must not be performed, credential or secret handling rules).
<section_start>7. Pending Tasks:<section_end> Tasks explicitly requested but not yet done.
<section_start>8. Current Work:<section_end> What was in progress immediately before this summary.
<section_start>9. Optional Next Step:<section_end> The next step directly in line with the user's most recent explicit requests — state it only if the prior turn shows it."""


async def compact_with_model(messages: list[dict[str, Any]],
                             llm_client: Any, *,
                             agent: str = "universe") -> bool:
    """Build the middle summary with the model (CC /compact semantics).
    On success it REWRITES `messages` in place: atoms (tool_calls → tool
    pairs + user turns) stay verbatim, assistant prose collapses into one
    system summary turn, and the recent `keep_recent` turns stay. Returns
    False (messages untouched) whenever no real provider summary could be
    produced, so the heuristic fallback in the caller still runs.
    """
    if llm_client is None or not messages:
        return False
    keep_recent = 6
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    if len(rest) <= keep_recent + 1:
        return False
    middle = rest[:-keep_recent]
    transcript: list[str] = []
    for m in middle:
        role = str(m.get("role", "")).upper()
        content = m.get("content") or ""
        if m.get("tool_calls"):
            calls = ", ".join(
                str(tc.get("function", {}).get("name", "?"))
                for tc in m["tool_calls"])
            transcript.append(f"[ASSISTANT tool calls] {calls}")
        elif m.get("role") == "tool":
            transcript.append(f"[TOOL] {str(content)[:600]}")
        elif m.get("role") == "user":
            transcript.append(f"[USER] {content}")
        else:
            transcript.append(f"[{role}] {str(content)[:900]}")
    prompt = SUMMARY_TEMPLATE + "\n\nCONVERSATION TO SUMMARIZE:\n" + \
        "\n".join(transcript)
    try:
        resp = await llm_client.complete(agent=agent, messages=[
            {"role": "system", "content":
             "You are a compaction engine. Return ONLY the filled summary."},
            {"role": "user", "content": prompt},
        ], max_tokens=2000)
    except Exception:
        return False
    summary_text = ""
    try:
        # LLMResponse (providers/client.py) exposes the completion as
        # `.text` — the old code sniffed resp.choices/resp.content, which
        # the dataclass never carries, so summary_text was ALWAYS "" and
        # every compaction burned a real LLM call just to fall back to the
        # keyword heuristic. Read the field the response actually has.
        summary_text = str(resp.text or "")
    except Exception:
        return False
    summary_text = summary_text.strip()
    if not summary_text:
        return False
    # atoms (tool protocol pairs + user turns) stay verbatim; assistant
    # prose collapses into the one summary system turn
    atoms = [m for m in middle
             if m.get("tool_calls") or m.get("role") == "tool"
             or m.get("role") == "user"]
    summary_turn = {"role": "system",
                    "content": "Prior context summary (compacted):\n"
                               + summary_text[:16000]}
    messages[:] = sys_msgs + atoms + [summary_turn] + rest[-keep_recent:]
    return True


def context_window_for_model(model: str) -> int:
    try:
        from security.capability import _context_window_for
        return _context_window_for(model)
    except Exception:
        return 128000


def should_compact(current_tokens: int, max_tokens: int, model: str | None = None) -> bool:
    return current_tokens >= max_tokens * COMPACTION_TRIGGER_PCT


def compact_messages(messages: list[dict[str, Any]], *,
                     keep_recent: int = 6,
                     keep_system: bool = True) -> list[dict[str, Any]]:
    """Compact a message list. Keep system + recent N; summarize the middle
    into a single 'summary' message. Drops pure-smalltalk.

    Two correctness rules learned from a production crash ('NoneType'
    object has no attribute 'lower' inside a long research loop):
      * an assistant tool-call turn stores content=None (the KEY exists, so
        m.get('content','') still returns None — the old code then hit
        None.lower()); content is coerced before any string work.
      * an assistant message with tool_calls and its following role='tool'
        responses are a PROTOCOL ATOM on OpenAI-compatible endpoints —
        splitting or dropping them 400s every later request. They are kept
        verbatim, in order, OUTSIDE the summarizable middle.
    """
    if len(messages) <= keep_recent + 1:
        return messages
    sys_msgs = [m for m in messages if m.get("role") == "system"] if keep_system else []
    rest = [m for m in messages if m.get("role") != "system"]
    if len(rest) <= keep_recent:
        return sys_msgs + rest
    middle = rest[:-keep_recent]
    recent = rest[-keep_recent:]
    # tool-call atoms ride along untouched; user messages are ALWAYS kept —
    # the seven-literal-substring heuristic deleted a user's constraint
    # whenever it lacked the exact words "decision"/"plan"/... while every
    # bulky tool result survived (audit 2026-09)
    atoms = [m for m in middle
             if m.get("tool_calls") or m.get("role") == "tool"
             or m.get("role") == "user"]
    prose = [m for m in middle
             if not (m.get("tool_calls") or m.get("role") == "tool"
                     or m.get("role") == "user")]
    summary_parts: list[str] = []
    for m in prose:
        content = m.get("content") or ""
        # Keep all user constraints verbatim; keep assistant prose that contains decisions
        if m.get("role") == "user" or any(k in content.lower() for k in ("decision", "agreed", "plan", "result", "created", "wrote", "fixed", "error", "constraint", "must not", "never", "always")):
            summary_parts.append(f"[{m.get('role')}] {content[:300]}")
    summary = "Compacted prior context:\n" + "\n".join(summary_parts) if summary_parts else "[prior context compacted]"
    # summary is a NEW system turn; place it after the kept atoms so the
    # tool_calls->tool pairing (and their order) never breaks
    return sys_msgs + atoms + [{"role": "system", "content": summary}] + recent


def compact_tool_output(output: str, max_chars: int = 2000) -> str:
    """Compress repeated tool outputs (e.g. five npm install logs -> summary)."""
    if len(output) <= max_chars:
        return output
    # keep head + tail + a count of repeated lines
    lines = output.splitlines()
    if len(lines) > 20:
        head = lines[:5]
        tail = lines[-5:]
        assembled = "\n".join(head + [f"... [{len(lines)-10} lines compacted] ..."] + tail)
        # five head lines + five tail lines can still exceed the cap when
        # individual lines are huge (minified JS, base64 blobs) — the
        # advertised max_chars must hold on the ASSEMBLED result
        if len(assembled) > max_chars:
            assembled = assembled[:max_chars] + "\n...[truncated]"
        return assembled
    return output[:max_chars] + "\n...[truncated]"


def sliding_budget(total_tokens: int) -> dict[str, int]:
    """Return the §13 sliding context-budget allocation in tokens."""
    from config import CONTEXT_BUDGET
    return {k: int(total_tokens * pct) for k, pct in CONTEXT_BUDGET.items()}
