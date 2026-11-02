from __future__ import annotations


from core.acceptance import evaluate_goal
from core.agent.base_agent import BaseAgent, UNIVERSE_EXECUTION_PROTOCOL
from core.phase_contract import media_phase_plan
from core.memory.fingerprint import extract_meta_block
from connectors.builtin import get_registry


def test_nested_universe_meta_is_not_truncated():
    payload = extract_meta_block(
        '```universe_meta {"mode":"turn_confirmed","classification":'
        '{"category":"video_editing","domain":"video","intent":"edit",'
        '"complexity":"high"}}```'
    )
    assert payload is not None
    assert payload["classification"]["category"] == "video_editing"


def test_media_contract_is_closed_and_ordered():
    plan = media_phase_plan()
    assert [step["phase_id"] for step in plan] == [
        "capability_check", "method_research", "input_probe", "scene_timestamps",
        "ffmpeg_edit", "subtitle_decision", "ffprobe", "final_review",
    ]
    assert "api" not in [step["agent"] for step in plan]
    assert all(int(step["budget"]) > 0 for step in plan)


# S1d: _default_plan and _recovery_steps are gone with the ladder — the
# general agent owns planning and recovery itself.


def test_effective_prompt_contains_capability_and_acceptance_protocol():
    message = BaseAgent().build_messages({}, "perform a complex task", "execute")
    assert UNIVERSE_EXECUTION_PROTOCOL in message[0]["content"]
    assert "CapabilityCatalog" in message[0]["content"]
    assert "acceptance criteria" in message[0]["content"]


def test_capability_catalog_is_registered():
    registry = get_registry()
    assert registry.get("CapabilityCatalog") is not None


def test_media_acceptance_rejects_missing_output(tmp_path):
    result = evaluate_goal(
        goal_text="edit this video and produce final_edit.mp4",
        classification={"category": "video_editing"},
        handoffs=[], root=tmp_path,
    )
    assert result["success"] is False
    assert "verified edited media output" in result["failure"]


def test_media_acceptance_accepts_truthful_no_dialogue_decision(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source = tmp_path / "source.mp4"
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest", str(source)
    ], check=True, capture_output=True)
    (output / "EDIT_DECISIONS.md").write_text("No dialogue was intelligible; no-dialogue decision.", encoding="utf-8")
    source.rename(output / "suzume_final_edit.mp4")
    result = evaluate_goal(
        goal_text="edit this video with subtitles and produce final_edit.mp4",
        classification={"category": "video_editing"},
        handoffs=[], root=tmp_path,
    )
    assert result["success"] is True
