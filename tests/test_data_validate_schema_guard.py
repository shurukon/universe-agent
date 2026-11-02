"""tests/test_data_validate_schema_guard.py — DataValidate must refuse a
malformed schema with a structured refusal instead of crashing with
'list' object has no attribute 'items' (the 2026-10 matrix audit)."""
from __future__ import annotations

import pytest


async def _call(*, args: dict):
    from connectors.builtin import get_registry
    reg = get_registry()
    return await reg.call("DataValidate", agent="universe", turn_id="g-dv",
                          args=args)


@pytest.fixture()
def _dv_env(tmp_path, monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    work.joinpath("inbox.csv").write_text("name,qty\nalpha,2\nbeta,7\n",
                                          encoding="utf-8")
    monkeypatch.setattr("connectors.builtin.files._project_root",
                        lambda: work)
    from hooks import reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    from security.capability import reset_gate_for_tests
    reset_gate_for_tests().set_auto_grant(True)
    return work


async def test_list_schema_is_refused_not_crashed(_dv_env):
    r = await _call(args={"path": "inbox.csv",
                          "schema": {"columns": ["name", "qty"]}})
    body = r.get("result", r)
    assert body.get("ok") is False
    assert "schema must be a mapping" in str(body.get("error", ""))


async def test_columns_mapping_validates(_dv_env):
    r = await _call(args={"path": "inbox.csv",
                          "schema": {"columns": {
                              # dtype vocabulary is pandas dtypes ("object",
                              # "int64" — see the tool's docstring), not "str"
                              "name": {"dtype": "object"},
                              "qty": {"dtype": "int64", "min": 0}}}})
    body = r.get("result", r)
    assert body.get("ok") is True, body
