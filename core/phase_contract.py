"""Deterministic phase contracts for long-running adaptive goals."""
from __future__ import annotations

from typing import Any

MEDIA_CATEGORIES = {"media_production", "video_editing", "audio_video"}

# Media-goal markers — one list, read by is_media_goal below. Do not
# duplicate these words anywhere else; both the classifier prompt and the
# acceptance checker import this constant.
MEDIA_GOAL_MARKERS = ("video", "footage", "film", "audio", "ffmpeg", "subtitle", "voiceover")

# Subtitle-goal markers (requirement gate in acceptance.py).
SUBTITLE_GOAL_MARKERS = ("subtitle", "subtitles", "translation", "caption")

# The phrasings a "no-dialogue decision" artifact may use to honestly state
# that the media has no intelligible speech. media_enforcer.py WRITES one of
# these and acceptance.py READS them — if these lists ever diverge, a
# truthful no-dialogue decision gets rejected as missing subtitles.
NO_DIALOGUE_MARKERS = (
    "no dialogue", "no-dialogue", "empty dialogue", "empty-dialogue",
    "no intelligible speech",
)

# The ONE canonical phrasing the media enforcer writes (engine-fix round):
# consumers that need a single stable string (EDIT_DECISIONS.md phrasing)
# use this, so a marker reorder in NO_DIALOGUE_MARKERS can never silently
# change the artifact text acceptance.py greps for.
CANONICAL_NO_DIALOGUE_MARKER = NO_DIALOGUE_MARKERS[0]  # "no dialogue"


def is_media_goal(category: str, goal_text: str) -> bool:
    lowered = goal_text.casefold()
    return category in MEDIA_CATEGORIES or any(
        marker in lowered for marker in MEDIA_GOAL_MARKERS
    )


def media_phase_plan(target_stem: str = "suzume") -> list[dict[str, Any]]:
    """The bounded media pipeline required by the acceptance contract.

    target_stem: the input file's stem, so the instructed artifact names
    (output/{stem}_final_edit.mp4, output/{stem}_subtitles.srt) match what
    the enforcer/acceptance actually derive from the real input — the old
    hardcoded "suzume" instructed agents to produce artifacts the checker
    looked for under a different name whenever the input wasn't suzume_*.mp4.
    """
    return [
        {
            "agent": "planning", "phase_id": "capability_check", "phase_kind": "capability",
            "budget": 2, "required_tools": ["CapabilityCatalog"],
            "acceptance": ["catalog inspected", "capability sufficiency/gap recorded", "quality bar defined"],
            "instruction": "Inspect capability_catalog once, decide whether registered tools are sufficient, record any capability_gap, and define the acceptance contract. Do not perform implementation or repeat discovery.",
        },
        {
            "agent": "research", "phase_id": "method_research", "phase_kind": "research",
            "budget": 3, "required_tools": ["WebSearch", "WebFetch", "WebResearch"],
            "acceptance": ["current best method researched", "alternatives compared", "selected method and verification steps returned"],
            "instruction": "Research the best current safe method and compare FFmpeg, subtitle/transcription, and suitable open-source tools. Return one selected execution recipe with concrete commands/steps. Do not repeat research after this phase succeeds.",
        },
        {
            "agent": "code", "phase_id": "input_probe", "phase_kind": "execution",
            "budget": 3, "required_tools": ["LS", "Bash"],
            "acceptance": ["input exists", "ffprobe streams/duration/resolution/audio captured", "probe evidence saved"],
            "instruction": "Execute the input probe now. Inspect the supplied media with real file and ffprobe tools and save a machine-readable probe artifact. Do not merely describe commands.",
        },
        {
            "agent": "code", "phase_id": "scene_timestamps", "phase_kind": "execution",
            "budget": 4, "required_tools": ["Bash", "Write"],
            "acceptance": ["actual scene/timestamp evidence saved", "three edit windows selected from the real input"],
            "instruction": "Use the input probe and real media inspection to choose timestamps for at least three intentional edits. Save scene/timestamp decisions; do not invent timestamps without evidence.",
        },
        {
            "agent": "code", "phase_id": "ffmpeg_edit", "phase_kind": "execution",
            "budget": 5, "required_tools": ["Bash"],
            "acceptance": ["FFmpeg command actually ran", "final MP4 exists", "video and expected audio streams are present"],
            "instruction": f"Run the selected FFmpeg edit now using the saved timestamps. Produce output/{target_stem}_final_edit.mp4 and verify its non-zero size. Do not return success before a real output file exists.",
        },
        {
            "agent": "code", "phase_id": "subtitle_decision", "phase_kind": "execution",
            "budget": 3, "required_tools": ["Write", "Bash"],
            "acceptance": ["SRT exists or truthful no-dialogue decision exists", "decision artifact explains evidence and limits"],
            "instruction": f"Decide subtitles from the actual audio/content. Create output/{target_stem}_subtitles.srt only for real dialogue, or create a truthful empty-dialogue decision note. Save output/EDIT_DECISIONS.md.",
        },
        {
            "agent": "review", "phase_id": "ffprobe", "phase_kind": "verification",
            "budget": 3, "required_tools": ["Bash", "Read"],
            "acceptance": ["ffprobe validates final MP4", "duration/video/audio checks recorded"],
            "instruction": "Run ffprobe and read the final artifacts. Record objective media verification evidence. Reject missing, empty, or invalid outputs.",
        },
        {
            "agent": "review", "phase_id": "final_review", "phase_kind": "verification",
            "budget": 3, "required_tools": ["Read", "Bash", "CapabilityCatalog"],
            "acceptance": ["all phases evidenced", "tool lifecycle truthful", "quality/limitations reported", "task_success only with proof"],
            "instruction": "Perform the final acceptance review across every phase and artifact. Confirm only with evidence; otherwise task_success=false and name the exact missing phase.",
        },
    ]


def phase_for_step(step: dict[str, Any]) -> str:
    return str(step.get("phase_id", step.get("agent", "unknown")))


def should_insert_connector_phase(handoff: dict[str, Any]) -> bool:
    context = handoff.get("context_for_memory") or {}
    gap = context.get("capability_gap")
    if isinstance(gap, list):
        return bool(gap)
    if isinstance(gap, dict):
        return bool(gap.get("missing") or gap.get("required"))
    return bool(gap)
