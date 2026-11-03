# Skill Sources & Attribution

The bundled skills taxonomy (`skills_data/`) was generated from the upstream
repositories listed below — the initial four (ECC, UI UX Pro Max, Open
Design, Anthropic Skills) extracted by `scripts/extract_real_skills.py`,
plus the later license-verified additions in the sections that follow: 18
trusted sources in total (see README and `docs/SKILLS_EXPANSION.md`). This
file exists to satisfy the attribution terms of their licenses and to
document exactly what was included, what was deliberately excluded, and why.

## Community skill registries
- **Repository:** https://github.com/affaan-m/ECC
- **License:** MIT — Copyright (c) 2026 Affaan Mustafa
- **Included:** all 271 `skills/*/SKILL.md` entries, verbatim body content,
  re-tagged with a `target_agent` and a canonical Universe category inferred
  from the skill's folder name.
- **Confidence on load:** 0.95 (trusted)

## UI UX Pro Max Skill
- **Repository:** https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
- **License:** MIT — Copyright (c) 2024 Next Level Builder
- **Included:** styles.csv (85), colors.csv (161), typography.csv (74),
  ux-guidelines.csv (99), charts.csv (26), ui-reasoning.csv (162), and
  stacks/*.csv (17 tech-stack guideline sets), each row/stack transformed
  into one Universe skill file.
- **Deliberately excluded:** `design.csv` — unstructured, single-column,
  Chinese-language legacy data with no stable field boundaries and low
  marginal value over the already-structured `styles.csv`. Noted here
  rather than silently dropped.
- **Confidence on load:** 0.95 (trusted)

## Open Design
- **Repository:** https://github.com/nexu-io/open-design
- **License:** Apache-2.0
- **Included:** 150 of 152 `design-systems/*/DESIGN.md` entries (all except
  the internal `_schema` folder, which is tooling, not a design system),
  each combined with its `tokens.css` machine-readable variables where
  present.
- **Note on real-brand-named systems** (e.g. `tesla`, `shopify`, `discord`):
  these are abstract design-token sets the upstream authors describe as
  "inspired by" each brand's publicly known visual identity — color values,
  spacing, and type scale, not copied logos, marks, or proprietary assets.
  No affiliation with or endorsement by those companies is claimed or
  implied by Universe.
- **Confidence on load:** 0.90 (trusted)

## Anthropic Official Skills
- **Repository:** https://github.com/anthropics/skills
- **License:** mixed per-skill — only the Apache-2.0 subset is included
- **Included (12, all verified Apache-2.0 via their own `LICENSE.txt`):**
  algorithmic-art, brand-guidelines, canvas-design, claude-api,
  frontend-design, internal-comms, mcp-builder, skill-creator,
  slack-gif-creator, theme-factory, web-artifacts-builder, webapp-testing.
- **Deliberately excluded — do not add later without re-checking the
  license:** `Docx`, `Pdf`, `Pptx`, `Xlsx` ship a separate
  "Anthropic Internal/Services-Only" `LICENSE.txt` that explicitly
  prohibits extraction, reproduction, and redistribution of those
  materials outside Anthropic's own Services. `doc-coauthoring` ships no
  `LICENSE.txt` at all, so it is excluded on the same conservative basis.
  Universe's File Agent (originally scoped around these four document
  skills in the v1 blueprint) does not carry pre-loaded document-handling
  skills as a result — see the note in the v1 Blueprint, §5.
- **Confidence on load:** 0.90 (trusted)

## Reproducing this extraction

```bash
git clone --depth 1 https://github.com/affaan-m/ECC.git
git clone --depth 1 https://github.com/nextlevelbuilder/ui-ux-pro-max-skill.git
git clone --depth 1 https://github.com/nexu-io/open-design.git
git clone --depth 1 https://github.com/anthropics/skills.git anthropic-skills
python3 scripts/extract_real_skills.py --repo-root /path/to/clones
```

## 5. Browserbase Skills

- **Repository**: github.com/browserbase/skills
- **License**: MIT (declared per-file in each SKILL.md's own front-matter —
  no repo-level LICENSE file exists; see `docs/THIRD_PARTY_LICENSES/BrowserbaseSkills.LICENSE`)
- **Included**: 16 skills — browser automation, cookie sync, fetch, UI
  testing patterns
- **Target agent**: `browser` (was 0 skills before this addition)
- **Confidence**: 0.90 (trusted_load_lower)

## 6. DuckDB Skills

- **Repository**: github.com/duckdb/duckdb-skills
- **License**: MIT
- **Included**: 9 skills — querying, file reading, database attachment
- **Target agent**: `data` (was 0 skills before this addition)
- **Confidence**: 0.90

## 7. ClickHouse Agent Skills

- **Repository**: github.com/ClickHouse/agent-skills
- **License**: Apache-2.0
- **Included**: 10 skills — best practices, chdb (pandas-compatible),
  architecture guidance
- **Target agent**: `data`
- **Confidence**: 0.90

## 8. Corey Haines Marketing Skills

- **Repository**: github.com/coreyhaines31/marketingskills
- **License**: MIT
- **Included**: 47 skills — copywriting, copy editing, content strategy,
  email sequences
- **Target agent**: `write` (was 2 skills before this addition)
- **Confidence**: 0.90

## 9. Google Workspace CLI

- **Repository**: github.com/googleworkspace/cli
- **License**: Apache-2.0
- **Included**: 95 skills — Drive, Sheets, Docs, Gmail, Calendar operations
- **Target agent**: `file` (was 0 skills before this addition — the gap
  this document's own §4 note about the File Agent flagged explicitly)
- **Confidence**: 0.90

## Deliberately excluded (2026-07 review), same standard as OpenHuman/GPL-3.0 above

- **trailofbits/skills** — CC-BY-SA-4.0. ShareAlike requires any
  redistribution/adaptation to carry the same license; incompatible with
  this project's MIT license the same way GPL-3.0 was. 21 genuinely
  excellent security skills (static-analysis, semgrep-rule-creator,
  differential-review) left out on licensing grounds alone — quality was
  not in question, compatibility was.
- **hashicorp/agent-skills** — MPL-2.0. Weaker (file-level) copyleft than
  GPL/CC-BY-SA, and arguably a defensible case for inclusion, but strictly
  outside this project's stated MIT/Apache-2.0-only policy. Flagged for an
  explicit project-owner decision rather than unilaterally included or
  excluded.
- Apollo GraphQL, Auth0, Sentry, Brave Search, and the pm-skills set in
  alirezarezvani/claude-skills (candidates for `api`, `devops`, `research`,
  and `planning` respectively) were identified as promising but not yet
  license-verified against their own direct source repos — see
  `docs/SKILLS_EXPANSION.md` for the full status and what's still open.

Re-run this whenever an upstream source updates; the script is idempotent —
it wipes and regenerates `skills_data/` from scratch each time, so stale
entries from a removed upstream skill never linger.

## 10. Apollo GraphQL Skills

- **Repository**: github.com/apollographql/skills — **License**: MIT
- **Included**: 14 skills — apollo-client, apollo-federation, apollo-server,
  apollo-router, graphql-schema, graphql-operations, rover
- **Target agent**: `api`

## 11. Auth0 Skills

- **Repository**: github.com/auth0/agent-skills (path: `plugins/auth0/skills/`)
  — **License**: Apache-2.0
- **Included**: 45 skills — authentication/MFA integration across every
  major framework and platform
- **Target agent**: `api`

## 12. Sentry Skills

- **Repository**: github.com/getsentry/sentry-skills — **License**: Apache-2.0
- **Included**: 28 skills — observability, error tracking, CI/security review
- **Target agent**: `devops`

## 13. Brave Search Skills

- **Repository**: github.com/brave/brave-search-skills — **License**: MIT
- **Included**: 11 skills — web/news/image search, spellcheck, suggestions
- **Target agent**: `research`

## 14. UnitOne Security Skills

- **Repository**: github.com/UnitOneAI/SecuritySkills — **License**: MIT
- **Included**: 45 skills — threat modeling, secure code review, cloud
  security review (AWS/Azure/GCP), compliance gap analysis, zero-trust
  assessment. Verified as a clean MIT alternative after trailofbits/skills
  (CC-BY-SA-4.0) was excluded — comparable depth, compatible license.
- **Target agent**: `security`

## 15. BagelHole DevOps + Security Skills

- **Repository**: github.com/BagelHole/DevOps-Security-Agent-Skills —
  **License**: MIT
- **Included**: 144 skills, split by upstream folder: `devops/` +
  `infrastructure/` (Kubernetes, Terraform, CI/CD, cloud platforms) → `devops`
  agent; `security/` (hardening, scanning, secrets, network) → `security`
  agent. `compliance/` (SOC2/GDPR/audit) was left out — doesn't map cleanly
  to any of Universe's 12 agents.
- **Target agents**: `devops`, `security`

## 16–18. Alireza Rezvani's Claude Skills (split by folder)

- **Repository**: github.com/alirezarezvani/claude-skills — **License**: MIT
- Unlike every other source here, this repo is not itself an index — it
  contains substantial first-party content (600+ scripts, 700+ templates)
  spanning engineering, marketing, product, research, and business
  operations. Only the folders relevant to Universe's under-resourced agents
  were extracted:
  - `project-management/skills/` (9 skills: pm-skills, jira-expert,
    scrum-master, confluence-expert...) → **`planning`** agent (was 0
    skills — this closes the last remaining zero-count agent)
  - `research/` (9 skills: literature review, grants, patents, dossiers) →
    `research` agent
  - `research-ops/skills/` (5 skills: product/clinical/market research) →
    `research` agent

## Final agent distribution (1,584 trusted skills, 0 quarantine)

| Agent | Before any expansion | After |
|---|---|---|
| design | 775 | 809 |
| code | 200 | 206 |
| devops | 11 | 148 |
| file | 0 | 95 |
| security | 10 | 90 |
| api | 6 | 65 |
| write | 2 | 49 |
| research | 10 | 35 |
| review | 20 | 20 |
| data | 0 | 19 |
| browser | 0 | 16 |
| **planning** | **0** | **9** |

Every one of Universe's 12 Core Agents now has real, license-verified,
professional-grade skill content — no agent is at zero.
