"""Goal-level acceptance checks based on real filesystem artifacts and probes."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


_MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

def _project_root() -> Path:
    try:
        from config import get_config
        configured = get_config().get("project_root", "")
        if configured:
            return Path(configured).expanduser().resolve()
    except Exception:
        pass
    return Path.cwd().resolve()


def _media_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in _MEDIA_SUFFIXES:
            continue
        lowered = path.name.casefold()
        if any(token in lowered for token in ("input", "source", "original", "raw", "la-sirene")):
            continue
        if any(token in lowered for token in ("final", "edit", "output", "render", "processed", "result")):
            candidates.append(path)
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _probe_media(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {"ok": False, "path": str(path), "error": "missing_or_empty"}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"ok": True, "path": str(path), "bytes": path.stat().st_size,
                "probe": "ffprobe_unavailable"}
    import os as _os
    proc = subprocess.Popen(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        # its own process group: the timeout path killpg()s the tree —
        # without this the probe shares OUR group and a hung ffprobe
        # takes down the whole server/runner (found by the suite 2026-11)
        start_new_session=(_os.name != "nt"),
    )
    try:
        out, err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        # kill the whole tree: subprocess.run's timeout only killed the
        # direct child, and a wrapper shell's grandchildren (the actual
        # ffprobe) kept running as orphans well past the budget
        # (audit 2026-09, verified live by the hanging-ffprobe test)
        import os as _os
        if _os.name == "nt":
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True)
            except Exception:
                pass
        else:
            try:
                import signal as _signal
                import os as _os2
                _os2.killpg(_os2.getpgid(proc.pid), _signal.SIGKILL)
            except Exception:
                pass
        # a hung ffprobe (corrupt media, dead network drive) must fail THIS
        # probe honestly, not crash the whole acceptance pass
        return {"ok": False, "path": str(path), "error": "ffprobe_timeout"}
    stdout, stderr = out or "", err or ""
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    streams = [s.get("codec_type") for s in payload.get("streams", []) if isinstance(s, dict)]
    duration = payload.get("format", {}).get("duration")
    return {"ok": proc.returncode == 0 and bool(streams), "path": str(path),
            "bytes": path.stat().st_size, "streams": streams, "duration": duration,
            "stderr": stderr[-500:] if proc.returncode else ""}


def evaluate_goal(*, goal_text: str, classification: dict[str, Any],
                  handoffs: list[dict[str, Any]], root: Path | None = None) -> dict[str, Any]:
    """Return explicit acceptance evidence; never trust model prose as proof."""
    root = (root or _project_root()).resolve()
    category = str(classification.get("category", "general"))
    lowered = goal_text.casefold()
    evidence: dict[str, Any] = {"root": str(root), "requirements": [], "checks": []}
    # media/subtitle/no-dialogue word lists live in phase_contract — ONE
    # source shared with media_enforcer so they can never diverge
    from core.phase_contract import (SUBTITLE_GOAL_MARKERS, NO_DIALOGUE_MARKERS,
                                     is_media_goal)
    is_media = is_media_goal(category, goal_text)
    if is_media:
        evidence["requirements"].append("verified_media_output")
        candidates = _media_candidates(root)
        probes = [_probe_media(path) for path in candidates[:5]]
        evidence["checks"].append({"name": "media_output", "candidates": probes})
        if not any(item.get("ok") for item in probes):
            evidence["success"] = False
            evidence["failure"] = "No verified edited media output was found; subtitles or scratch files are insufficient."
            return evidence
        if any(token in lowered for token in SUBTITLE_GOAL_MARKERS):
            evidence["requirements"].append("subtitle_artifact")
            subtitle_files = list(root.rglob("*.srt")) + list(root.rglob("*.vtt"))
            valid_subtitles = [p for p in subtitle_files if p.is_file() and p.stat().st_size > 0]
            decision_files = [p for p in root.rglob("EDIT_DECISIONS.md") if p.is_file() and p.stat().st_size > 0]
            no_dialogue_decisions = []
            for decision in decision_files:
                try:
                    text = decision.read_text(encoding="utf-8", errors="replace").casefold()
                except OSError:
                    continue
                if any(marker in text for marker in NO_DIALOGUE_MARKERS):
                    no_dialogue_decisions.append(str(decision))
            evidence["checks"].append({"name": "subtitles", "files": [str(p) for p in valid_subtitles],
                                        "no_dialogue_decisions": no_dialogue_decisions})
            if not valid_subtitles and not no_dialogue_decisions:
                evidence["success"] = False
                evidence["failure"] = "Subtitle requirement was requested but no valid SRT/VTT or truthful no-dialogue decision was found."
                return evidence
        evidence["success"] = True
        evidence["verified_outputs"] = [item for item in probes if item.get("ok")]
        return evidence

    evidence["checks"].append({"name": "agent_tool_evidence", "handoffs": len(handoffs)})
    build_markers = ("build", "create", "implement", "full stack", "application", "app", "write a python", "artifact")
    requires_execution = category in {"code_generation", "web_development", "api_integration", "document_processing"} or any(marker in lowered for marker in build_markers)
    successful_tool_handoffs = [h for h in handoffs if h.get("task_success", False) and h.get("tools_used")]
    if requires_execution and not successful_tool_handoffs:
        evidence["success"] = False
        evidence["failure"] = "No successful agent handoff with verified tool evidence was recorded. Provider or agent failures cannot be accepted as completion."
        return evidence
    # Honesty rule for everything else: an empty run, or a run where every
    # handoff failed, is NOT a success. "Completed with zero successful
    # steps" used to mark goals green over a wall of provider errors —
    # the user sees the truthful verdict instead.
    if not handoffs:
        evidence["success"] = False
        evidence["failure"] = "No agent handoffs were recorded — nothing was done."
        return evidence
    successful_handoffs = [h for h in handoffs if h.get("task_success", False)]
    if not successful_handoffs:
        evidence["success"] = False
        evidence["failure"] = ("Every agent step failed (%d attempts). "
                               "This goal is not complete.") % len(handoffs)
        return evidence
    evidence["success"] = True
    return evidence
