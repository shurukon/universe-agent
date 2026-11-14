# Skills — the Procedural memory tier

**Status (2026-11-10): the shipped catalog is EMPTY, and that is a deliberate, documented state.**

## What ships

| Piece | Where | State |
|---|---|---|
| The loader | `skills/loader.py` | **stub** — `load_all_skills()` returns all-zero counts; `ensure_skills_bootstrapped()` reports `bootstrapped: False` |
| The trusted sources | `config.TRUSTED_SKILL_SOURCES` | **empty tuple** `()` — the corpus was purged on 2026-10-31 |
| The bundled corpus directory | `skills_data/` | **does not exist** (it is no longer shipped in the wheel — see `pyproject.toml`) |
| The ingestion pipeline | `skill/ingestion.py` (8-step `ingest_all_bundled()`), `skill/quarantine.py`, `skill/signing.py`, `skill/taxonomy.py` | **live code, no corpus to ingest** — the pipeline has no source directory to read |
| The agent-facing tool | `connectors/builtin/skills.py` | **stub** — `Skill` search returns `count: 0`; read/activate answer *"skills are disabled — no trusted corpus"* |
| The prompt block | `core/agent/universe_agent.py` (`RELEVANT SKILLS`) | present, but empty until a corpus exists |
| The Docker quarantine sandbox | `security/sandbox.py` | implemented, **not reachable** — nothing can be quarantined when nothing is ingested |

## What this means in practice

- The Procedural tier of the five-tier memory contributes **nothing** today. The other
  four tiers (Working / Episodic / Semantic / Meta) are live.
- The agent's system prompt says *"5. SKILLS ARE DISABLED"* — that line is the truth.
  A second line in the same prompt still mentions invoking the Skill tool for
  suggestions; it is stale prose and is being removed with the next prompt pass.
- `ToolSearch` searches the live tool registry, so it never suggests a skill.

## How to restore it

1. Put a corpus under `skills_data/` (one directory per source, with a `skills.json`
   manifest each) **or** add the source paths to `config.TRUSTED_SKILL_SOURCES`.
2. Call `skills.loader.ensure_skills_bootstrapped()` at startup — the bridge does this
   today, but the loader is a stub, so it is a no-op until step 1 is real.
3. Wire `skill.ingestion.ingest_all_bundled()` (it has no caller today) into that
   bootstrap path.
4. Re-open `connectors/builtin/skills.py` and let the `Skill` tool answer for real.
5. The quarantine tier (`security/sandbox.py`) becomes reachable automatically once
   step 3 ingests anything.

## Tests

`tests/test_skills_corpus.py` pins the honest current state: the catalog is empty,
the loader reports zeros, and `pyproject.toml` does not ship a directory that does
not exist. When a corpus lands, that test is the checklist to flip.
