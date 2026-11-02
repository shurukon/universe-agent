"""connectors/builtin/data.py — pandas_query + data_validate tools (Phase 3).

Two tools the original architecture promised but never built:
  * pandas_query  — load CSV/JSON/Excel into a DataFrame, run a transformation
                    or aggregation, return a structured summary (head, dtypes,
                    describe, plus the result of the requested op).
  * data_validate — validate a dataset against an expected schema (column
                    names + dtypes). Returns a real per-column mismatch report,
                    not a fake 'ok'.

Both are real — they run real pandas operations on real files. No mock returns.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry


# Expression-guard for the `query` op (audit 2026-10, CRITICAL): pandas
# installed WITHOUT numexpr silently falls back to Python's eval engine,
# and MRO attribute chains (`df.__class__.mro()…`) execute ARBITRARY code
# — verified live. Defense in depth: (1) numexpr is a required dependency
# and the query is forced to the numexpr engine; (2) even with numexpr,
# expressions carrying dunder access / dynamic-eval shapes are rejected
# outright before they reach pandas.
_EXPR_DENY = re.compile(
    r"__|getattr|globals|locals|builtins|eval\(|exec\(|import|open\(|"
    r"system|lambda|`|;|\bdf\s*\.")

EXPR_ENGINE_NOTE = ("query runs on the numexpr engine; attribute/dunder "
                    "expressions are rejected")


def _load_df(path: str):
    """Load a CSV/JSON/Excel file into a pandas DataFrame. Raises on unknown
    extensions so the caller can return a real error, not a fake success."""
    import pandas as pd
    p = Path(path)
    if not p.is_absolute():
        # the gate scopes relative paths against the project root — the
        # loader must resolve the SAME way (audit 2026-10: it used to
        # resolve against the process cwd, which diverged from the scope
        # check whenever the bridge/agent cwd != project root)
        from connectors.builtin.files import _project_root
        p = _project_root() / p
    if not p.exists():
        raise FileNotFoundError(str(p))
    suffix = p.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(p)
    if suffix == ".tsv":
        return pd.read_csv(p, sep="\t")
    if suffix == ".json":
        return pd.read_json(p)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(p)
    if suffix == ".parquet":
        return pd.read_parquet(p)
    raise ValueError(f"unsupported file extension: {suffix!r}")


def pandas_query(path: str = "", op: str = "head",
                 columns: list[str] | None = None,
                 n: int = 5,
                 expr: str = "", query: str = "", cmd: str = "",
                 content: str = "", file: str = "") -> dict:
    """Load `path` into a DataFrame and apply `op`:
      head        — first n rows
      tail        — last n rows
      describe    — pandas describe() of numeric columns
      dtypes      — column dtypes
      count       — row count + per-column non-null counts
      value_counts— value_counts() of `columns[0]`
      groupby_agg — groupby(columns[0]).agg({columns[1]: expr}) (expr like 'mean')
      query       — pandas .query(expr) and return head of result
      columns     — just list the column names
    Returns a structured JSON-serialisable summary.
    """
    # `content` used to be part of the path fallback chain — a call with
    # path="" and file=<absolute path> then produced ZERO path candidates
    # at the gate and read ANY file on disk at consent=auto (audit 2026-10,
    # verified live). content stays an inline-data surface, not a path.
    path = str(path or file)
    if query and not expr:
        expr = str(query)
    if cmd and op == "head":
        op = str(cmd)
    if not path:
        return {"ok": False, "error": "pandas_query requires a `path` or `file`"}
    from connectors.builtin.files import _within_scope
    if not _within_scope(path):
        return {"ok": False,
                "error": f"path outside scope: {path}",
                "hint": "PandasQuery reads workspace files (project root "
                        "and file allowlist) only"}
    try:
        df = _load_df(path)
    except Exception as e:
        return {"ok": False, "error": f"load failed: {e}", "path": path}
    # capture original shape/columns BEFORE any filtering
    orig_shape = list(df.shape)
    orig_columns = list(df.columns)
    if columns:
        # filter to requested columns (ignore missing ones with a warning in result)
        missing = [c for c in columns if c not in df.columns]
        cols = [c for c in columns if c in df.columns]
        df = df[cols] if cols else df
    else:
        missing = []
    try:
        # the `n` row-count parameter used to be uncapped — a head(10**9)
        # streamed a multi-megabyte response into the model context
        try:
            n = max(1, min(int(n or 5), 200))
        except (TypeError, ValueError):
            n = 5
        result: dict[str, Any] = {
            "ok": True, "path": path,
            "shape": orig_shape,
            "columns": orig_columns,
        }
        if missing:
            result["missing_columns_requested"] = missing
        if op == "head":
            result["rows"] = json.loads(df.head(n).to_json(orient="records"))
        elif op == "tail":
            result["rows"] = json.loads(df.tail(n).to_json(orient="records"))
        elif op == "describe":
            result["summary"] = json.loads(df.describe().to_json())
        elif op == "dtypes":
            result["dtypes"] = {c: str(t) for c, t in df.dtypes.items()}
        elif op == "count":
            result["row_count"] = int(len(df))
            result["non_null_per_column"] = {c: int(df[c].notna().sum())
                                              for c in df.columns}
        elif op == "value_counts":
            if not columns:
                return {"ok": False, "error": "value_counts requires `columns`",
                        "path": path}
            vc = df[columns[0]].value_counts().head(n)
            result["value_counts"] = {str(k): int(v) for k, v in vc.items()}
        elif op == "groupby_agg":
            if not columns or len(columns) < 2 or not expr:
                return {"ok": False, "error": "groupby_agg requires columns=[by, target] and expr (e.g. 'mean')",
                        "path": path}
            import pandas as _pd
            grouped = df.groupby(columns[0])[columns[1]].agg(expr)
            result["groups"] = {str(k): (float(v) if not _pd.isna(v) else None)
                                for k, v in grouped.items()}
        elif op == "query":
            if not expr:
                return {"ok": False, "error": "query requires `expr`",
                        "path": path}
            # CRITICAL fix (audit 2026-10): pandas without numexpr falls
            # back to Python's eval engine and MRO attribute chains
            # execute arbitrary code (verified live with
            # df.__class__.mro()…). Belt + suspenders:
            #   1. reject denylisted expression shapes outright
            #   2. force engine="numexpr" when importable
            #   3. refuse to run on the bare python engine when numexpr
            #      is absent
            bad = _EXPR_DENY.search(expr)
            if bad:
                return {"ok": False,
                        "error": f"query expression rejected: attribute/"
                                 f"dunder/eval shapes are not allowed "
                                 f"(matched {bad.group(0)!r})",
                        "path": path}
            engine = None
            try:
                import numexpr  # noqa: F401
                engine = "numexpr"
            except ImportError:
                pass
            if engine is None:
                return {"ok": False,
                        "error": "numexpr is not installed; refusing to run "
                                 "queries on the python eval engine "
                                 "(pip install numexpr)",
                        "path": path}
            sub = df.query(expr, engine=engine)
            result["rows"] = json.loads(sub.head(n).to_json(orient="records"))
            result["matching_rows"] = int(len(sub))
        elif op == "columns":
            pass  # already returned above
        else:
            return {"ok": False, "error": f"unknown op {op!r}", "path": path}
        return result
    except Exception as e:
        return {"ok": False, "error": f"op failed: {e}", "path": path,
                "op": op}


def data_validate(path: str, schema: dict) -> dict:
    """Validate the dataset at `path` against `schema`.

    schema = {
        "columns": {
            "name": {"dtype": "object", "required": true},
            "age":  {"dtype": "int64",  "required": true, "min": 0, "max": 150},
            "email":{"dtype": "object", "required": false, "regex": "^[^@]+@[^@]+$"}
        }
    }
    Returns a real per-column report: which columns are missing, which have the
    wrong dtype, which rows violate min/max/regex. Not a fake 'ok'."""
    from connectors.builtin.files import _within_scope
    if not _within_scope(path):
        return {"ok": False,
                "error": f"path outside scope: {path}",
                "hint": "DataValidate reads workspace files only"}
    schema_cols = (schema or {}).get("columns")
    if not isinstance(schema, dict) or not isinstance(schema_cols, dict):
        return {
            "ok": False,
            "error": ("schema must be a mapping of \"columns\" → "
                      "{name: {\"dtype\": …, \"min\": …, \"max\": …, "
                      "\"regex\": …}}; got columns as "
                      f"{type(schema_cols).__name__ if schema_cols is not None else 'missing'}"),
            "path": path,
        }
    try:
        df = _load_df(path)
    except Exception as e:
        return {"ok": False, "error": f"load failed: {e}", "path": path}
    cols = schema.get("columns", {})
    errors: list[dict[str, Any]] = []
    for col, spec in cols.items():
        if col not in df.columns:
            if spec.get("required", True):
                errors.append({"column": col, "issue": "missing_required"})
            continue
        actual_dtype = str(df[col].dtype)
        expected_dtype = spec.get("dtype")
        if expected_dtype and actual_dtype != expected_dtype:
            errors.append({"column": col, "issue": "dtype_mismatch",
                           "expected": expected_dtype, "actual": actual_dtype})
        if "min" in spec:
            try:
                bad = df[df[col] < spec["min"]].index.tolist()
                if bad:
                    errors.append({"column": col, "issue": "below_min",
                                   "min": spec["min"],
                                   "row_indices": [int(i) for i in bad[:20]]})
            except TypeError:
                pass  # non-numeric column; min check skipped
        if "max" in spec:
            try:
                bad = df[df[col] > spec["max"]].index.tolist()
                if bad:
                    errors.append({"column": col, "issue": "above_max",
                                   "max": spec["max"],
                                   "row_indices": [int(i) for i in bad[:20]]})
            except TypeError:
                pass
        if "regex" in spec:
            import re
            pat = re.compile(spec["regex"])
            # apply only to non-null values
            mask = df[col].notna() & ~df[col].astype(str).str.match(pat)
            bad = df[mask].index.tolist()
            if bad:
                errors.append({"column": col, "issue": "regex_mismatch",
                               "regex": spec["regex"],
                               "row_indices": [int(i) for i in bad[:20]]})
        null_count = int(df[col].isna().sum())
        if spec.get("required", True) and null_count > 0:
            errors.append({"column": col, "issue": "has_nulls_in_required",
                           "null_count": null_count})
    # check for unexpected columns
    expected_cols = set(cols.keys())
    actual_cols = set(df.columns)
    extra = sorted(actual_cols - expected_cols)
    return {
        "ok": len(errors) == 0 and not extra,
        "path": path,
        "row_count": int(len(df)),
        "expected_columns": sorted(expected_cols),
        "actual_columns": sorted(actual_cols),
        "extra_columns": extra,
        "errors": errors,
        "error_count": len(errors),
    }


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        name="PandasQuery", capability="Read",
        description="Load CSV/JSON/Excel/Parquet into a DataFrame and run an op (head/describe/groupby_agg/query/etc.)",
        handler=pandas_query, consent="auto",
        resources=["path:glob:**/*"],
        # `file` is an explicit filesystem location for this tool: declare
        # it so the gate scope-checks BOTH spellings (audit 2026-10: the
        # bare registration let a `file=<absolute path>` call produce zero
        # path candidates and read any file on disk at consent=auto)
        path_args=["path", "file"],
    ))
    reg.register(Tool(
        name="DataValidate", capability="Read",
        description="Validate a dataset against an expected schema (columns, dtypes, min/max/regex)",
        handler=data_validate, consent="auto",
        resources=["path:glob:**/*"],
    ))
