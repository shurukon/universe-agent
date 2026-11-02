"""eval/suite.py — the v1 smoke-test suite (§16).

10-15 golden tasks covering: code generation (a few languages), one refactor,
one test-writing, one documentation, one data-analysis, one research, one
file-operation, and at least one multi-agent handoff task. Each task has an
input, an expected-output rubric, and a time budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class EvalTask:
    id: str
    category: str
    description: str
    goal: str
    expected_agents: list[str]
    rubric: Callable[[dict], tuple[bool, str]]  # (summary) -> (passed, reason)
    # the task's key nouns (audit 2026-10): the rubric's cheap content check
    # requires the final work to reference AT LEAST ONE of them — agent
    # presence / step count alone is not a pass. Substring matching only,
    # no LLM. Composed onto the base rubric in __post_init__.
    keywords: tuple[str, ...] = ()
    time_budget_s: int = 120

    def __post_init__(self) -> None:
        if self.keywords:
            self.rubric = _with_content_check(self.keywords, self.rubric)


def _reply_text(summary: dict) -> str:
    """Every surface the final reply reaches: the chat-mode reply, each
    handoff step's what-was-done, and the produced artifact paths."""
    parts = [str(summary.get("reply") or "")]
    for s in summary.get("steps", []):
        parts.append(str(s.get("what") or ""))
    for a in summary.get("artifacts") or []:
        parts.append(str(a if not isinstance(a, dict)
                         else (a.get("path") or a)))
    return " ".join(parts).casefold()


def _with_content_check(keywords: tuple[str, ...],
                        base: Callable[[dict], tuple[bool, str]]):
    """Compose a base rubric with the cheap content check: after the base
    passes, the recorded work must reference at least one key noun."""
    def check(summary: dict) -> tuple[bool, str]:
        ok, reason = base(summary)
        if not ok:
            return False, reason
        if keywords:
            text = _reply_text(summary)
            if not any(k in text for k in keywords):
                return False, (f"reply/work never mentions any of "
                               f"{list(keywords)} — agent presence alone "
                               f"is not enough")
        return True, reason
    return check


def _rubric_agents(expected: list[str]):
    def check(summary: dict) -> tuple[bool, str]:
        actual = [s["agent"] for s in summary.get("steps", [])]
        if not all(a in actual for a in expected):
            return False, f"expected agents {expected}, got {actual}"
        return True, "all expected agents engaged"
    return check


def _rubric_success():
    def check(summary: dict) -> tuple[bool, str]:
        if summary.get("success"):
            return True, "task succeeded"
        return False, "task failed"
    return check


def _rubric_handoff():
    def check(summary: dict) -> tuple[bool, str]:
        steps = summary.get("steps", [])
        if len(steps) >= 3:
            return True, f"{len(steps)}-agent handoff chain"
        return False, f"only {len(steps)} steps; need >=3 for handoff test"
    return check


def _rubric_classification(category: str):
    def check(summary: dict) -> tuple[bool, str]:
        cls = summary.get("classification", {})
        if cls.get("category") == category:
            return True, f"classified as {category}"
        return False, f"expected {category}, got {cls.get('category')}"
    return check


def _rubric_memory():
    def check(summary: dict) -> tuple[bool, str]:
        if summary.get("promoted_to_l3"):
            return True, "memory promoted to L3"
        # also accept if gravity is high enough that promotion would happen
        from config import GRAVITY_THRESHOLDS
        if summary.get("gravity_score", 0) >= GRAVITY_THRESHOLDS["asteroid"]:
            return True, f"gravity {summary['gravity_score']:.2f} indicates learning"
        return False, "no memory promotion / low gravity"
    return check


SUITE: list[EvalTask] = [
    EvalTask("code-python-1", "code_generation",
             "Write a Python function to read a CSV",
             "write a python function to read a csv file and print the rows",
             ["universe", "code", "review"],
             _rubric_agents(["universe", "code", "review"]),
             keywords=("csv",)),
    EvalTask("code-python-2", "code_generation",
             "Write a Python async fetcher",
             "write a python async function to fetch a url and return the json",
             ["universe", "code", "review"],
             _rubric_agents(["code"]),
             keywords=("json", "fetch", "url")),
    EvalTask("code-js-1", "code_generation",
             "Write a JS Express route",
             "write a javascript express route that returns hello world",
             ["universe", "code", "review"],
             _rubric_agents(["code"]),
             keywords=("express", "route")),
    EvalTask("code-ts-1", "code_generation",
             "Write a TypeScript interface",
             "write a typescript interface for a user with name email and age",
             ["universe", "code", "review"],
             _rubric_agents(["code"]),
             keywords=("typescript", "interface")),
    EvalTask("refactor-1", "refactor",
             "Refactor a long function",
             "refactor this python function into smaller helpers: def f(x): return x*2+1",
             ["universe", "code", "review"],
             _rubric_agents(["code"]),
             keywords=("refactor", "helper", "function")),
    EvalTask("test-write-1", "test_writing",
             "Write a pytest test",
             "write a pytest test for a function that adds two numbers",
             ["universe", "code", "review"],
             _rubric_agents(["code"]),
             keywords=("pytest", "test")),
    EvalTask("docs-1", "documentation",
             "Write a README",
             "write a readme for a python cli tool that manages todos",
             ["universe", "universe", "review"],
             _rubric_agents(["universe"]),
             keywords=("readme", "todo")),
    EvalTask("data-1", "data_analysis",
             "Analyze CSV data",
             "analyze a csv of sales data and report the top product by revenue",
             ["universe", "universe", "review"],
             _rubric_agents(["universe"]),
             keywords=("revenue", "product", "sales")),
    EvalTask("research-1", "research",
             "Research a topic",
             "research the differences between sqlite and postgres for small apps",
             ["universe", "research", "universe"],
             _rubric_agents(["research"]),
             keywords=("sqlite", "postgres")),
    EvalTask("file-1", "file_operation",
             "Process a document",
             "convert a markdown file to a pdf",
             ["universe", "universe", "review"],
             _rubric_agents(["universe"]),
             keywords=("pdf", "markdown")),
    EvalTask("devops-1", "general",
             "Write a Dockerfile",
             "write a dockerfile for a python flask app",
             ["universe", "universe", "review"],
             _rubric_agents(["universe"]),
             keywords=("dockerfile", "docker", "flask")),
    EvalTask("security-1", "security",
             "Security review",
             "review this code for security issues: def login(user, pw): return user.check(pw)",
             ["universe", "security", "review"],
             _rubric_agents(["security"]),
             keywords=("security", "login")),
    EvalTask("api-1", "api_integration",
             "Build an API client",
             "build a python client for a rest api that returns users",
             ["universe", "universe", "review"],
             _rubric_agents(["universe"]),
             keywords=("api", "client", "rest")),
    EvalTask("handoff-1", "multi_agent_handoff",
             "Multi-agent handoff",
             "build a small rest api with tests and documentation",
             ["universe", "code", "review"],
             _rubric_handoff(),
             keywords=("api", "test", "documentation")),
    EvalTask("memory-1", "memory_correctness",
             "Memory promotion",
             "write a python csv reader with error handling",
             ["universe", "code", "review"],
             _rubric_memory(),
             keywords=("csv", "reader", "error")),
]


def get_suite() -> list[EvalTask]:
    return list(SUITE)
