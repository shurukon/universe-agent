

def test_build_goal_without_successful_tool_evidence_is_not_accepted():
    from core.acceptance import evaluate_goal
    evidence = evaluate_goal(
        goal_text="Build a full stack FlowBoard application",
        classification={"category": "web_development"},
        handoffs=[{"agent": "code", "task_success": False, "tools_used": [], "what_was_done": "provider 403"}],
    )
    assert evidence["success"] is False
    assert "tool evidence" in evidence["failure"]


def test_build_goal_with_successful_tool_evidence_can_be_accepted():
    from core.acceptance import evaluate_goal
    evidence = evaluate_goal(
        goal_text="Build a full stack FlowBoard application",
        classification={"category": "web_development"},
        handoffs=[{"agent": "code", "task_success": True, "tools_used": ["Write", "Bash"], "what_was_done": "created and verified"}],
    )
    assert evidence["success"] is True
