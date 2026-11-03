#!/usr/bin/env python3
"""extract_real_skills.py — replaces generate_skills.py's synthetic content
with REAL content transformed from trusted upstream repositories, verified
license by verified license, into Universe's skill schema (§18).

Sources (cloned shallow checkout or --repo-root):
  ECC            <- github.com/affaan-m/ECC                       (MIT)
  UIUXProMax     <- github.com/nextlevelbuilder/ui-ux-pro-max-skill (MIT)
  OpenDesign     <- github.com/nexu-io/open-design                (Apache-2.0)
  AnthropicSkills<- github.com/anthropics/skills    (Apache-2.0 -- ONLY the
                    12 skills actually licensed Apache-2.0; docx/pdf/pptx/xlsx
                    carry a separate "Anthropic Internal/Services-Only"
                    LICENSE.txt that explicitly forbids extraction and
                    redistribution outside Anthropic's own Services, so they
                    are deliberately excluded here, along with doc-coauthoring
                    which ships no LICENSE.txt at all.)

Added 2026-07 — filling agents the original four left at zero (browser,
file, data) or near-zero (write). Each verified directly against its own
official upstream repo's LICENSE, not an aggregator/index (VoltAgent's
awesome-agent-skills and officialskills.sh are pure catalogs of external
links — their own MIT license covers only their indexing, not the
underlying content, confirmed by cloning trailofbits/skills directly and
finding it's actually CC-BY-SA-4.0, incompatible with this project's
MIT/Apache-2.0-only policy despite being listed in that same index):
  BrowserbaseSkills    <- github.com/browserbase/skills        (MIT)      -> browser
  DuckDBSkills         <- github.com/duckdb/duckdb-skills       (MIT)      -> data
  ClickHouseSkills     <- github.com/ClickHouse/agent-skills    (Apache-2.0) -> data
  CoreyHainesMarketing <- github.com/coreyhaines31/marketingskills (MIT)   -> write
  GoogleWorkspaceCLI   <- github.com/googleworkspace/cli        (Apache-2.0) -> file

Deliberately excluded on licensing grounds (see docs/SKILLS_EXPANSION.md):
  trailofbits/skills (CC-BY-SA-4.0 — ShareAlike incompatible with this
  project's MIT license, same reasoning as the existing OpenHuman/GPL-3.0
  exclusion); hashicorp/agent-skills (MPL-2.0 — weaker copyleft than GPL/
  CC-BY-SA, but still outside the stated MIT/Apache-2.0 allowlist; flagged
  for a project-owner decision rather than unilaterally included).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT / ".upstream-clones"   # overridden by --repo-root
OUT_ROOT = PROJECT_ROOT / "skills_data"          # overridden by --out
# NOTE: main() does `shutil.rmtree(OUT_ROOT)` on every run (see below) —
# this is why third-party LICENSE files for each source live in
# docs/THIRD_PARTY_LICENSES/, a SIBLING directory, not under skills_data/
# itself. An earlier version of this doc kept them at skills_data/_LICENSES/
# and they were silently wiped the next time this script ran. Don't move
# them back under OUT_ROOT.

STATS = {"ECC": 0, "UIUXProMax": 0, "OpenDesign": 0, "AnthropicSkills": 0,
         "BrowserbaseSkills": 0, "DuckDBSkills": 0, "ClickHouseSkills": 0,
         "CoreyHainesMarketing": 0, "GoogleWorkspaceCLI": 0,
         "ApolloGraphQL": 0, "Auth0Skills": 0, "SentrySkills": 0,
         "BraveSearchSkills": 0, "UnitOneSecuritySkills": 0,
         "BagelHoleDevOpsSecurity": 0, "AlirezaProjectManagement": 0,
         "AlirezaResearch": 0, "AlirezaResearchOps": 0, "skipped": 0}


def slugify(s: str) -> str:
    s = re.sub(r"[（）()]", "", s)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] or "item"


def fm_escape(v: str) -> str:
    v = (v or "").replace("\n", " ").replace('"', "'").strip()
    return v[:500]


def write_skill(source: str, rel_path: str, *, name: str, description: str,
                body: str, license_: str, category: str, target_agent: str,
                tags: list[str], triggers: list[str], version: str = "1.0.0") -> None:
    out = OUT_ROOT / source / rel_path
    out.parent.mkdir(parents=True, exist_ok=True)
    tags_str = json.dumps(tags[:12])
    triggers_str = json.dumps(triggers[:12])
    fm = (
        "---\n"
        f"name: {fm_escape(name)}\n"
        f"source: {source}\n"
        f"version: {version}\n"
        f"description: {fm_escape(description)}\n"
        f"tags: {tags_str}\n"
        f"triggers: {triggers_str}\n"
        f"license: {license_}\n"
        f"target_agent: {target_agent}\n"
        f"category: {category}\n"
        "---\n\n"
    )
    out.write_text(fm + body.strip() + "\n", encoding="utf-8")
    STATS[source] += 1


# ───────────────────────── 1. ECC (MIT) ─────────────────────────────────

ECC_AGENT_MAP = [
    (("security", "vuln", "secrets", "owasp", "crypto", "pentest"), "security", "security"),
    (("docker", "kubernetes", "K8s", "deploy", "ci-cd", "terraform", "infra",
      "devops", "helm", "ansible", "homelab", "network", "monitoring"), "devops", "devops"),
    (("test", "tdd", "qa-", "-qa", "lint", "code-review"), "review", "code_generation"),
    (("api-", "-api", "rest-", "graphql", "grpc", "openapi", "webhook"), "api", "api_integration"),
    (("design", "ui-", "-ui", "ux-", "figma", "frontend", "motion", "css-",
      "-css", "tailwind"), "design", "ui_ux_design"),
    (("research", "rag-", "-rag", "scrape", "scientific"), "research", "research"),
    (("docs-", "-docs", "markdown", "writing", "technical-writing"), "write", "writing"),
]


def agent_and_category_for_ecc(folder_name: str) -> tuple[str, str]:
    """Map an ECC skill folder name to (target_agent, canonical §5 category)."""
    blob = folder_name.lower()
    for keys, agent, category in ECC_AGENT_MAP:
        if any(k in blob for k in keys):
            return agent, category
    return "code", "code_generation"


def extract_ecc() -> None:
    root = REPO_ROOT / "ECC" / "skills"
    if not root.exists():
        print("ECC skills/ not found, skipping")
        return
    for skill_dir in sorted(root.iterdir()):
        md = skill_dir / "SKILL.md"
        if not md.is_file():
            continue
        text = md.read_text(encoding="utf-8", errors="ignore")
        fm: dict[str, str] = {}
        body = text
        if text.startswith("---"):
            parts = text[3:].split("---", 1)
            if len(parts) == 2:
                for line in parts[0].strip().splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        fm[k.strip()] = v.strip()
                body = parts[1].strip()
        name = fm.get("name", skill_dir.name)
        desc = fm.get("description", f"ECC skill: {name}")
        category_prefix = skill_dir.name.split("-")[0]
        target_agent, category = agent_and_category_for_ecc(skill_dir.name)
        triggers = [t for t in re.split(r"[,/]| and ", name.replace("-", " ")) if t.strip()][:6]
        write_skill("ECC", f"{skill_dir.name}.md", name=name, description=desc,
                    body=body, license_="MIT", category=category,
                    target_agent=target_agent, tags=[category_prefix, "ecc"],
                    triggers=triggers)


# ──────────────────── 2. UI UX Pro Max (MIT) ────────────────────────────

def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def extract_uiuxpromax() -> None:
    data = REPO_ROOT / "uiuxpromax" / "src" / "ui-ux-pro-max" / "data"
    if not data.exists():
        print("uiuxpromax data/ not found, skipping")
        return

    # styles.csv -> one skill per visual style
    p = data / "styles.csv"
    if p.exists():
        for row in read_csv_rows(p):
            name = (row.get("Style Category") or row.get("Style Name")
                    or row.get("Name") or f"style-{row.get('No', '')}")
            desc = row.get("Best For") or row.get("Keywords") or ""
            body = "\n".join(f"**{k}:** {v}" for k, v in row.items() if v)
            write_skill("UIUXProMax", f"styles/{slugify(name)}.md",
                        name=f"UI Style: {name}", description=desc[:300] or name,
                        body=body, license_="MIT", category="ui_style",
                        target_agent="design", tags=["style", "ui"],
                        triggers=[name])

    # colors.csv -> one skill per palette
    p = data / "colors.csv"
    if p.exists():
        for row in read_csv_rows(p):
            product = row.get("Product Type", "").strip()
            name = product or f"palette-{row.get('No', '')}"
            body = "\n".join(f"**{k}:** {v}" for k, v in row.items() if v)
            write_skill("UIUXProMax", f"colors/{slugify(name)}.md",
                        name=f"Color Palette: {name}",
                        description=f"Color palette for {name}",
                        body=body, license_="MIT", category="color_palette",
                        target_agent="design", tags=["color", "palette"],
                        triggers=[name])

    # typography.csv -> one skill per font pairing
    p = data / "typography.csv"
    if p.exists():
        for row in read_csv_rows(p):
            name = row.get("Font Pairing Name") or next(iter(row.values()))
            body = "\n".join(f"**{k}:** {v}" for k, v in row.items() if v)
            write_skill("UIUXProMax", f"typography/{slugify(name)}.md",
                        name=f"Font Pairing: {name}",
                        description=row.get("Best For", name),
                        body=body, license_="MIT", category="typography",
                        target_agent="design", tags=["typography", "fonts"],
                        triggers=[name, row.get("Category", "")])

    # ux-guidelines.csv -> one skill per UX issue/guideline
    p = data / "ux-guidelines.csv"
    if p.exists():
        for row in read_csv_rows(p):
            name = row.get("Issue") or next(iter(row.values()))
            body = "\n".join(f"**{k}:** {v}" for k, v in row.items() if v)
            write_skill("UIUXProMax", f"ux-guidelines/{slugify(name)}.md",
                        name=f"UX Guideline: {name}",
                        description=row.get("Description", name),
                        body=body, license_="MIT", category="ux_guideline",
                        target_agent="design", tags=["ux", row.get("Platform", "")],
                        triggers=[name, row.get("Category", "")])

    # charts.csv -> one skill per data-viz pattern
    p = data / "charts.csv"
    if p.exists():
        for row in read_csv_rows(p):
            name = row.get("Data Type") or next(iter(row.values()))
            body = "\n".join(f"**{k}:** {v}" for k, v in row.items() if v)
            write_skill("UIUXProMax", f"charts/{slugify(name)}.md",
                        name=f"Chart Pattern: {name}",
                        description=row.get("Best Chart Type", name),
                        body=body, license_="MIT", category="data_viz",
                        target_agent="design", tags=["chart", "data-viz"],
                        triggers=[name])

    # ui-reasoning.csv -> one skill per UI decision pattern
    p = data / "ui-reasoning.csv"
    if p.exists():
        for row in read_csv_rows(p):
            name = row.get("UI_Category") or next(iter(row.values()))
            body = "\n".join(f"**{k}:** {v}" for k, v in row.items() if v)
            write_skill("UIUXProMax", f"reasoning/{slugify(name)}-{abs(hash(str(row)))%9999}.md",
                        name=f"UI Reasoning: {name}",
                        description=row.get("Recommended_Pattern", name),
                        body=body, license_="MIT", category="ui_reasoning",
                        target_agent="design", tags=["reasoning", "ui"],
                        triggers=[name])

    # stacks/*.csv -> one skill PER STACK (cohesive guideline set, cross-agent)
    stacks = data / "stacks"
    if stacks.exists():
        for stack_csv in sorted(stacks.glob("*.csv")):
            rows = read_csv_rows(stack_csv)
            stack_name = stack_csv.stem
            lines = [f"# {stack_name} — Best Practices ({len(rows)} guidelines)\n"]
            for row in rows:
                guideline = row.get("Guideline", "")
                desc = row.get("Description", "")
                do = row.get("Do", "")
                dont = row.get("Don't", row.get("Dont", ""))
                sev = row.get("Severity", "")
                lines.append(f"## {guideline}\n{desc}\n- **Do:** {do}\n- **Don't:** {dont}\n- Severity: {sev}\n")
            write_skill("UIUXProMax", f"stacks/{slugify(stack_name)}.md",
                        name=f"{stack_name} Best Practices",
                        description=f"{len(rows)} curated {stack_name} guidelines (state, perf, a11y, patterns)",
                        body="\n".join(lines), license_="MIT", category="tech_stack",
                        target_agent="", tags=["stack", stack_name],
                        triggers=[stack_name])

    # design.csv intentionally skipped: unstructured single-column legacy
    # data (Chinese-language, no stable field boundaries) with low marginal
    # value over the already-structured styles.csv. Noted, not silently
    # dropped.
    print("  (design.csv skipped: unstructured legacy data, superseded by styles.csv)")


# ────────────────────── 3. Open Design (Apache-2.0) ──────────────────────

def extract_opendesign() -> None:
    root = REPO_ROOT / "opendesign" / "design-systems"
    if not root.exists():
        print("opendesign design-systems/ not found, skipping")
        return
    for sys_dir in sorted(root.iterdir()):
        if not sys_dir.is_dir() or sys_dir.name.startswith("_"):
            continue
        design_md = sys_dir / "DESIGN.md"
        if not design_md.is_file():
            continue
        body = design_md.read_text(encoding="utf-8", errors="ignore")
        # pull a short description from the blockquote line right after the title
        desc_match = re.search(r"^>\s*(.+)$", body, re.MULTILINE)
        desc = desc_match.group(1) if desc_match else f"Design system inspired by {sys_dir.name}"
        tokens_css = sys_dir / "tokens.css"
        if tokens_css.is_file():
            css_vars = re.findall(r"(--[\w-]+:\s*[^;]+;)", tokens_css.read_text(encoding="utf-8", errors="ignore"))
            if css_vars:
                body += "\n\n## Machine-readable tokens (paste verbatim into `:root`)\n```css\n:root {\n  "
                body += "\n  ".join(css_vars[:60]) + "\n}\n```\n"
        write_skill("OpenDesign", f"{sys_dir.name}.md",
                    name=f"Design System: {sys_dir.name}", description=desc[:300],
                    body=body, license_="Apache-2.0", category="design_system",
                    target_agent="design", tags=["design-system", sys_dir.name],
                    triggers=[sys_dir.name])


# ───────────────── 4. Anthropic Skills (Apache-2.0 subset only) ─────────

APACHE_VERIFIED_SKILLS = {
    "algorithmic-art": "design", "brand-guidelines": "design",
    "canvas-design": "design", "model-context": "api",
    "frontend-design": "design", "internal-comms": "write",
    "mcp-builder": "api", "skill-creator": "",
    "slack-gif-creator": "design", "theme-factory": "design",
    "web-artifacts-builder": "design", "webapp-testing": "review",
}
EXCLUDED_RESTRICTED = {"Docx", "Pdf", "Pptx", "Xlsx", "doc-coauthoring"}


def extract_anthropic_skills() -> None:
    root = REPO_ROOT / "anthropic-skills" / "skills"
    if not root.exists():
        print("anthropic-skills/skills not found, skipping")
        return
    for skill_dir in sorted(root.iterdir()):
        if not skill_dir.is_dir():
            continue
        name = skill_dir.name
        if name in EXCLUDED_RESTRICTED:
            STATS["skipped"] += 1
            continue
        if name not in APACHE_VERIFIED_SKILLS:
            # unknown skill with no verified Apache LICENSE.txt -> exclude,
            # don't guess on licensing.
            lic = skill_dir / "LICENSE.txt"
            if not (lic.is_file() and "Apache License" in lic.read_text(encoding="utf-8", errors="ignore")):
                STATS["skipped"] += 1
                continue
            target_agent = ""
        else:
            target_agent = APACHE_VERIFIED_SKILLS[name]
        md = skill_dir / "SKILL.md"
        if not md.is_file():
            continue
        text = md.read_text(encoding="utf-8", errors="ignore")
        fm: dict[str, str] = {}
        body = text
        if text.startswith("---"):
            parts = text[3:].split("---", 1)
            if len(parts) == 2:
                for line in parts[0].strip().splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        fm[k.strip()] = v.strip()
                body = parts[1].strip()
        desc = fm.get("description", f"Anthropic skill: {name}")
        write_skill("AnthropicSkills", f"{name}.md",
                    name=fm.get("name", name), description=desc,
                    body=body, license_="Apache-2.0", category=name,
                    target_agent=target_agent, tags=[name, "anthropic"],
                    triggers=[name])


# ───── 5-9. New sources sharing skills/<name>/SKILL.md structure ─────────
# (browserbase/skills, duckdb/duckdb-skills, ClickHouse/agent-skills,
# coreyhaines31/marketingskills, googleworkspace/cli — added 2026-07)

def _parse_simple_skillmd(text: str) -> tuple[dict[str, str], str]:
    """Same tolerant front-matter parse as extract_ecc() uses — top-level
    `key: value` lines only; nested/multi-line YAML values are ignored
    rather than mis-parsed, which is fine since only name/description/
    license are read from it here."""
    fm: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text[3:].split("---", 1)
        if len(parts) == 2:
            for line in parts[0].strip().splitlines():
                if ":" in line and not line.startswith(" "):
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip()
            body = parts[1].strip()
    return fm, body


def extract_generic_skillmd_source(source: str, repo_subdir: str, *, license_: str,
                                   target_agent: str, category: str, extra_tag: str,
                                   skills_root: str = "skills") -> None:
    """rglob-based (not iterdir-based) so this handles ANY nesting depth: the
    original five sources are skills/<name>/SKILL.md (1 level), but
    UnitOneAI/SecuritySkills is skills/<category>/<name>/SKILL.md (2 levels)
    — rglob("SKILL.md") finds both uniformly. Output filenames are namespaced
    by the skill's parent + grandparent folder names (not just skill_dir.name)
    to avoid collisions across categories in deeper structures."""
    root = REPO_ROOT / repo_subdir / skills_root
    if not root.exists():
        print(f"{source}: {root} not found, skipping")
        return
    for md in sorted(root.rglob("SKILL.md")):
        skill_dir = md.parent
        rel = skill_dir.relative_to(root)
        out_name = "_".join(rel.parts) if len(rel.parts) > 1 else rel.parts[0]
        text = md.read_text(encoding="utf-8", errors="ignore")
        fm, body = _parse_simple_skillmd(text)
        name = fm.get("name", skill_dir.name)
        desc = fm.get("description", f"{source} skill: {name}")
        skill_license = fm.get("license", license_)
        triggers = [t for t in re.split(r"[,/]| and ", name.replace("-", " ")) if t.strip()][:6]
        write_skill(source, f"{out_name}.md", name=name, description=desc,
                    body=body, license_=skill_license, category=category,
                    target_agent=target_agent, tags=[extra_tag, skill_dir.name.split("-")[0]],
                    triggers=triggers)


def extract_browserbase() -> None:
    extract_generic_skillmd_source("BrowserbaseSkills", "browserbase", license_="MIT",
                                   target_agent="browser", category="browser_automation",
                                   extra_tag="browserbase")


def extract_duckdb() -> None:
    extract_generic_skillmd_source("DuckDBSkills", "duckdb", license_="MIT",
                                   target_agent="data", category="data_analysis",
                                   extra_tag="duckdb")


def extract_clickhouse() -> None:
    extract_generic_skillmd_source("ClickHouseSkills", "clickhouse", license_="Apache-2.0",
                                   target_agent="data", category="data_analysis",
                                   extra_tag="clickhouse")


def extract_corey_marketing() -> None:
    extract_generic_skillmd_source("CoreyHainesMarketing", "coreyhaines", license_="MIT",
                                   target_agent="write", category="writing",
                                   extra_tag="marketing")


def extract_google_workspace() -> None:
    extract_generic_skillmd_source("GoogleWorkspaceCLI", "googleworkspace", license_="Apache-2.0",
                                   target_agent="file", category="file_management",
                                   extra_tag="google-workspace")


def extract_apollo() -> None:
    extract_generic_skillmd_source("ApolloGraphQL", "apollographql", license_="MIT",
                                   target_agent="api", category="api_design",
                                   extra_tag="graphql")


def extract_auth0() -> None:
    extract_generic_skillmd_source("Auth0Skills", "auth0/plugins/auth0", license_="Apache-2.0",
                                   target_agent="api", category="api_design",
                                   extra_tag="authentication")


def extract_sentry() -> None:
    extract_generic_skillmd_source("SentrySkills", "sentry", license_="Apache-2.0",
                                   target_agent="devops", category="observability",
                                   extra_tag="sentry")


def extract_brave_search() -> None:
    extract_generic_skillmd_source("BraveSearchSkills", "brave", license_="MIT",
                                   target_agent="research", category="WebResearch",
                                   extra_tag="brave-search")


def extract_unitone_security() -> None:
    # 2-level nesting (skills/<category>/<name>/SKILL.md) — the generalized
    # rglob-based extractor handles this exactly like the 1-level sources.
    extract_generic_skillmd_source("UnitOneSecuritySkills", "unitone", license_="MIT",
                                   target_agent="security", category="security_audit",
                                   extra_tag="security")


def extract_bagelhole() -> None:
    """No single skills/ root — top-level domain folders instead
    (devops/, infrastructure/, compliance/, security/). devops/ and
    infrastructure/ both map to the devops agent (cloud/IaC is classic
    devops territory); security/ maps to the security agent, complementing
    UnitOneAI. compliance/ (SOC2/GDPR/audit) doesn't cleanly fit any of
    Universe's 12 agents and is left out rather than force-fit somewhere."""
    root = REPO_ROOT / "bagelhole"
    if not root.exists():
        print("BagelHole: not found, skipping")
        return
    domain_to_agent = {"devops": "devops", "infrastructure": "devops", "security": "security"}
    for domain, agent in domain_to_agent.items():
        domain_dir = root / domain
        if not domain_dir.exists():
            continue
        for md in sorted(domain_dir.rglob("SKILL.md")):
            skill_dir = md.parent
            rel = skill_dir.relative_to(root)
            out_name = "_".join(rel.parts)
            text = md.read_text(encoding="utf-8", errors="ignore")
            fm, body = _parse_simple_skillmd(text)
            name = fm.get("name", skill_dir.name)
            desc = fm.get("description", f"BagelHole skill: {name}")
            skill_license = fm.get("license", "MIT")
            triggers = [t for t in re.split(r"[,/]| and ", name.replace("-", " ")) if t.strip()][:6]
            write_skill("BagelHoleDevOpsSecurity", f"{out_name}.md", name=name,
                        description=desc, body=body, license_=skill_license,
                        category="devops" if agent == "devops" else "security_audit",
                        target_agent=agent, tags=["bagelhole", domain], triggers=triggers)


def extract_alirezarezvani() -> None:
    """Different repo shape entirely: no shared skills/ root, and — unlike
    BagelHole — the folders that matter here don't even share one common
    parent (project-management/skills/ vs research/*/skills/ vs
    research-ops/skills/). Two target agents from three distinct locations."""
    root = REPO_ROOT / "alirezarezvani"
    if not root.exists():
        print("alirezarezvani pm-skills: not found, skipping")
        return

    def _walk(base: Path, source: str, agent: str, category: str, tag: str) -> None:
        if not base.exists():
            return
        for md in sorted(base.rglob("SKILL.md")):
            skill_dir = md.parent
            rel = skill_dir.relative_to(root)
            out_name = "_".join(rel.parts)
            text = md.read_text(encoding="utf-8", errors="ignore")
            fm, body = _parse_simple_skillmd(text)
            name = fm.get("name", skill_dir.name)
            desc = fm.get("description", f"{source} skill: {name}")
            skill_license = fm.get("license", "MIT")
            triggers = [t for t in re.split(r"[,/]| and ", name.replace("-", " ")) if t.strip()][:6]
            write_skill(source, f"{out_name}.md", name=name, description=desc, body=body,
                        license_=skill_license, category=category, target_agent=agent,
                        tags=[tag], triggers=triggers)

    _walk(root / "project-management" / "skills", "AlirezaProjectManagement",
          "planning", "project_management", "planning")
    _walk(root / "research", "AlirezaResearch", "research", "research", "research")
    _walk(root / "research-ops" / "skills", "AlirezaResearchOps", "research",
          "research", "research")


# ───────────────────────────── main ──────────────────────────────────────

def main() -> None:
    global REPO_ROOT, OUT_ROOT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT,
                    help="Directory containing the four cloned repos "
                         "(ECC/, uiuxpromax/, opendesign/, anthropic-skills/)")
    ap.add_argument("--out", type=Path, default=OUT_ROOT,
                    help="Output directory (will be wiped and regenerated)")
    args = ap.parse_args()
    REPO_ROOT = args.repo_root
    OUT_ROOT = args.out

    if not REPO_ROOT.exists():
        raise SystemExit(
            f"--repo-root {REPO_ROOT} does not exist. Clone the required repos first:\n"
            f"  git clone --depth 1 https://github.com/affaan-m/ECC.git {REPO_ROOT}/ECC\n"
            f"  git clone --depth 1 https://github.com/nextlevelbuilder/ui-ux-pro-max-skill.git {REPO_ROOT}/uiuxpromax\n"
            f"  git clone --depth 1 https://github.com/nexu-io/open-design.git {REPO_ROOT}/opendesign\n"
            f"  git clone --depth 1 https://github.com/anthropics/skills.git {REPO_ROOT}/anthropic-skills\n"
            f"  git clone --depth 1 https://github.com/browserbase/skills.git {REPO_ROOT}/browserbase\n"
            f"  git clone --depth 1 https://github.com/duckdb/duckdb-skills.git {REPO_ROOT}/duckdb\n"
            f"  git clone --depth 1 https://github.com/ClickHouse/agent-skills.git {REPO_ROOT}/clickhouse\n"
            f"  git clone --depth 1 https://github.com/coreyhaines31/marketingskills.git {REPO_ROOT}/coreyhaines\n"
            f"  git clone --depth 1 https://github.com/googleworkspace/cli.git {REPO_ROOT}/googleworkspace\n"
            f"  git clone --depth 1 https://github.com/apollographql/skills.git {REPO_ROOT}/apollographql\n"
            f"  git clone --depth 1 https://github.com/auth0/agent-skills.git {REPO_ROOT}/auth0\n"
            f"  git clone --depth 1 https://github.com/getsentry/sentry-skills.git {REPO_ROOT}/sentry\n"
            f"  git clone --depth 1 https://github.com/brave/brave-search-skills.git {REPO_ROOT}/brave\n"
            f"  git clone --depth 1 https://github.com/UnitOneAI/SecuritySkills.git {REPO_ROOT}/unitone\n"
            f"  git clone --depth 1 https://github.com/BagelHole/DevOps-Security-Agent-Skills.git {REPO_ROOT}/bagelhole\n"
            f"  git clone --depth 1 https://github.com/alirezarezvani/pm-skills.git {REPO_ROOT}/alirezarezvani"
        )

    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    OUT_ROOT.mkdir(parents=True)

    print("Extracting ECC (MIT)...")
    extract_ecc()
    print("Extracting UI UX Pro Max (MIT)...")
    extract_uiuxpromax()
    print("Extracting Open Design (Apache-2.0)...")
    extract_opendesign()
    print("Extracting Anthropic Skills (Apache-2.0 subset only)...")
    extract_anthropic_skills()
    print("Extracting Browserbase Skills (MIT)...")
    extract_browserbase()
    print("Extracting DuckDB Skills (MIT)...")
    extract_duckdb()
    print("Extracting ClickHouse Skills (Apache-2.0)...")
    extract_clickhouse()
    print("Extracting Corey Haines Marketing Skills (MIT)...")
    extract_corey_marketing()
    print("Extracting Google Workspace CLI Skills (Apache-2.0)...")
    extract_google_workspace()
    print("Extracting Apollo GraphQL Skills (MIT)...")
    extract_apollo()
    print("Extracting Auth0 Skills (Apache-2.0)...")
    extract_auth0()
    print("Extracting Sentry Skills (Apache-2.0)...")
    extract_sentry()
    print("Extracting Brave Search Skills (MIT)...")
    extract_brave_search()
    print("Extracting UnitOne Security Skills (MIT)...")
    extract_unitone_security()
    print("Extracting BagelHole DevOps+Security Skills (MIT)...")
    extract_bagelhole()
    print("Extracting Alireza Rezvani's Planning + Research Skills (MIT)...")
    extract_alirezarezvani()

    print("\n=== Extraction complete ===")
    for k, v in STATS.items():
        print(f"  {k}: {v}")
    print(f"  TOTAL real skill files written: {sum(v for k, v in STATS.items() if k != 'skipped')}")


if __name__ == "__main__":
    main()
