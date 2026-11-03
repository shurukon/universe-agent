# Skills expansion: filling the non-code/design agents (2026-07)

## The problem, quantified

Before this work, an exhaustive count of `target_agent:` across all 1,052
skill files showed:

| Agent | Skills | % |
|---|---|---|
| design | 775 | 73.7% |
| code | 200 | 19.0% |
| review | 20 | 1.9% |
| devops | 11 | 1.0% |
| security | 10 | 1.0% |
| research | 10 | 1.0% |
| api | 6 | 0.6% |
| write | 2 | 0.2% |
| **planning** | **0** | **0%** |
| **data** | **0** | **0%** |
| **browser** | **0** | **0%** |
| **file** | **0** | **0%** |

design + code alone were 92.7% of everything. Four of twelve agents had
zero dedicated skills. Notably, even topically-diverse ECC content (e.g.
`healthcare-cdss-patterns.md`) was tagged `target_agent: code` — the
ingestion script's keyword-matching heuristic defaults anything unmatched
to `code`, so nominal topic diversity didn't translate into agent diversity.

## Why not just pull from the obvious aggregator

`VoltAgent/awesome-agent-skills` (MIT, 26k+ stars) looked like the answer —
a curated index of 650+ skills from real companies (Stripe, Cloudflare,
Trail of Bits, Sentry...). Checked directly and confirmed: **it's a pure
index.** It contains no skill content itself, only links to
`officialskills.sh` mirrors or each company's own separate GitHub repo.
Its own MIT license covers its index/README, not the linked content —
confirmed concretely by cloning `trailofbits/skills` directly and finding
it's actually **CC-BY-SA-4.0**, not MIT, despite being listed in that same
"index." Same failure mode as trusting a phone book's own copyright notice
to cover every business listed in it.

## What was actually verified, source by source

Every entry below was checked against the **original company's own repo**,
not the aggregator:

| Source | License (verified at source) | Verdict |
|---|---|---|
| browserbase/skills | MIT (per-file, no repo LICENSE) | ✅ included |
| duckdb/duckdb-skills | MIT | ✅ included |
| ClickHouse/agent-skills | Apache-2.0 | ✅ included |
| coreyhaines31/marketingskills | MIT | ✅ included |
| googleworkspace/cli | Apache-2.0 | ✅ included |
| trailofbits/skills | CC-BY-SA-4.0 | ❌ excluded — ShareAlike incompatible with this project's MIT license |
| hashicorp/agent-skills | MPL-2.0 | ⚠️ excluded — weaker copyleft than GPL/CC-BY-SA, but still outside the stated MIT/Apache-2.0-only policy; a project-owner call, not made unilaterally |
| Apollo GraphQL, Auth0, Sentry, Brave Search, alirezarezvani pm-skills pack | not yet checked | 🔲 open — see below |

## What changed

New agent distribution after ingesting the five ✅ sources (1,274 skills
total, 0 in quarantine):

| Agent | Before | After |
|---|---|---|
| browser | 0 | 16 |
| file | 0 | 95 |
| data | 0 | 19 |
| write | 2 | 49 |
| research / security / api / devops / planning | unchanged | unchanged |

`planning` is still at zero — no verified source was found for it in this
pass (alirezarezvani pm-skills subset is a candidate but its
own license wasn't checked before this work concluded).

## How it was done (reproducible)

`scripts/extract_real_skills.py` was extended with one shared
`extract_generic_skillmd_source()` helper (all five new sources use the
same `skills/<name>/SKILL.md` upstream layout, unlike the original four
which each needed bespoke parsing) plus five thin wrapper functions. Running
it clones nothing itself — repos must be present under `--repo-root` first
(the script's own error message lists the exact 9 clone commands). It wipes
and regenerates `skills_data/` from scratch each run, so it's always safe
to re-run after an upstream update.

A real, non-obvious bug surfaced during this: the first pass injected
`target_agent` and `license` into each upstream SKILL.md but forgot
`source`. Every file fell back to `_source_from_path()`, which (verified by
direct testing, not inspection) returns the literal string `"skills_data"`
for any real project path — not a per-source name — so all 177 new skills
silently landed in quarantine (0.40 confidence) instead of trusted (0.90)
despite `TRUSTED_SKILL_SOURCES` being updated correctly. Caught by actually
running the ingestion pipeline end-to-end and checking the real
trusted/quarantine split, not by reading the injection code.

## Still open

- `planning` remains at zero skills.
- `research`, `security`, `api`, `devops` remain at their original low
  counts (10, 10, 6, 11) — Apollo GraphQL, Auth0, Sentry, and Brave Search
  were identified as strong candidates but not license-verified.
- `hashicorp/agent-skills` (MPL-2.0) needs an explicit decision: include
  despite being outside the stated MIT/Apache-2.0 policy, or leave excluded.

## Round 2 (same day): the remaining candidates, plus more searching

All five previously-unverified candidates checked out clean at the source
— Apollo GraphQL (MIT), Auth0 (Apache-2.0, real repo is `auth0/agent-skills`,
not the guessed `auth0-skills`), Sentry (Apache-2.0), Brave Search (MIT,
real repo is `brave/brave-search-skills`, not `brave/skills`), and
alirezarezvani pm-skills (MIT — confirmed to be substantial first-party
content, not another link-only index).

Further searching for MIT/Apache-2.0 alternatives to the two round-1
exclusions turned up two strong replacements, both verified at source:
- **UnitOneAI/SecuritySkills** (MIT, 45 skills) — comparable depth to
  trailofbits/skills (threat modeling, cloud security review, compliance
  gap analysis) without the CC-BY-SA-4.0 incompatibility.
- **BagelHole/DevOps-Security-Agent-Skills** (MIT, 144 skills across
  devops/infrastructure/security) — far larger than hashicorp/agent-skills,
  cleanly MIT.

Three new upstream repo shapes required extending the extraction script
beyond the single-level `skills/<name>/SKILL.md` pattern:
- **UnitOneAI**: two levels deep (`skills/<category>/<name>/SKILL.md`).
  Fixed by generalizing `extract_generic_skillmd_source()` to use
  `rglob("SKILL.md")` instead of one-level `iterdir()` — handles both
  depths uniformly, verified backward-compatible with all five round-1
  sources (re-ran the full 9-source pipeline before adding round 2 to
  confirm no regression).
- **BagelHole**: no common `skills/` root at all — top-level domain folders
  (`devops/`, `infrastructure/`, `security/`, `compliance/`) instead, split
  across two target agents from one repo. Needed a dedicated function
  (`extract_bagelhole()`).
- **alirezarezvani**: three unrelated folders (`project-management/skills/`,
  `research/`, `research-ops/skills/`) feeding two different agents from
  one repo. Also a dedicated function (`extract_alirezarezvani()`).

`compliance/` (SOC2/GDPR/audit content in BagelHole) was left unmapped —
it doesn't fit cleanly into any of Universe's 12 agents, and force-fitting it
into `security` or `devops` would have been a worse mismatch than leaving
it out.

### Final distribution (1,584 trusted skills, 0 quarantine)

| Agent | Original | Round 1 | Round 2 (final) |
|---|---|---|---|
| design | 775 | 775 | 809 |
| code | 200 | 200 | 206 |
| devops | 11 | 11 | **148** |
| file | 0 | 95 | 95 |
| security | 10 | 10 | **90** |
| api | 6 | 6 | **65** |
| write | 2 | 49 | 49 |
| research | 10 | 10 | **35** |
| review | 20 | 20 | 20 |
| data | 0 | 19 | 19 |
| browser | 0 | 16 | 16 |
| **planning** | **0** | **0** | **9** |

Every one of Universe's 12 Core Agents now has real skill content. `planning`
(closed via alirezarezvani's project-management folder) was the last agent
at zero.

### Still open after round 2
- `hashicorp/agent-skills` (MPL-2.0) — still excluded, still a
  project-owner call rather than a unilateral one.
- No further searching was done for `review` (still 20, unchanged across
  both rounds) or `code`/`design` (already well-served).
- `planning` at 9 skills is real but thin compared to the stronger agents;
  a dedicated PM-focused source (beyond alirezarezvani's one folder) would
  strengthen it further if needed.
