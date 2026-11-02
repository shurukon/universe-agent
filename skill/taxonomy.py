"""Deterministic taxonomy and solar-system registry for bundled skills."""
from __future__ import annotations

import time
from typing import Any

from storage.local import get_storage

TAXONOMY_VERSION = "2026.08.1"

SOLAR_SYSTEMS: dict[str, str] = {
    "software_engineering": "Implementation, frameworks, languages, frontend/backend, code quality, and testing.",
    "product_design": "UI/UX, visual systems, design systems, accessibility, typography, and frontend experience.",
    "research_intelligence": "Web research, literature, source evaluation, and research operations.",
    "data_intelligence": "Data analysis, SQL, data platforms, validation, and visualization.",
    "operations_delivery": "Planning, project management, writing, workspace automation, and delivery workflows.",
    "platform_operations": "DevOps, deployment, observability, SRE, CI/CD, and infrastructure.",
    "security_trust": "Security audit, application security, identity, privacy, and agent guardrails.",
    "browser_automation": "Browser control, webapp testing, extraction, and visual verification.",
    "integration_protocols": "API design/integration, GraphQL, MCP, connectors, and authentication integrations.",
    "agent_meta": "Skill authoring, agent workflows, orchestration, and prompt protocols.",
    "general_fallback": "Audited fallback for records that cannot be semantically assigned yet.",
}

ORBITS: dict[str, dict[str, str]] = {
    "software_engineering": {
        "frontend": "Frontend implementation and browser-facing application patterns.",
        "backend": "Backend services, persistence, and server implementation.",
        "testing_quality": "Testing, QA, TDD, regression, and verification.",
        "languages_frameworks": "Languages, frameworks, and implementation patterns.",
        "data_engineering": "Storage, data access, and data-oriented implementation.",
        "code_quality": "Coding standards, refactoring, debugging, and maintainability.",
    },
    "product_design": {
        "ui_ux": "User experience, interaction, and interface patterns.",
        "design_systems": "Design systems, component systems, and visual foundations.",
        "visual_style": "Visual styles, branding, and creative direction.",
        "typography_color": "Typography, color palettes, and visual tokens.",
        "accessibility": "Accessibility and inclusive design.",
        "frontend_experience": "Frontend design patterns and web artifacts.",
    },
    "research_intelligence": {
        "web_research": "Web search, browsing, and evidence gathering.",
        "literature": "Literature review, papers, and scholarly analysis.",
        "source_analysis": "Dossiers, source criticism, and comparative analysis.",
        "research_operations": "Research workflows and operational research.",
    },
    "data_intelligence": {
        "data_analysis": "Analysis, validation, and structured data workflows.",
        "data_visualization": "Charts, dashboards, and visual analytics.",
        "sql_analytics": "SQL and analytical querying.",
        "data_platforms": "DuckDB, ClickHouse, and data platform operations.",
    },
    "operations_delivery": {
        "planning": "Planning, project management, and task decomposition.",
        "writing_communications": "Writing, communications, and documentation.",
        "project_management": "Project and work management systems.",
        "marketing": "Marketing and growth workflows.",
        "workspace_automation": "Files and productivity workspace automation.",
    },
    "platform_operations": {
        "devops": "Infrastructure and DevOps workflows.",
        "deployment": "Deployment and hosting.",
        "observability": "Observability, monitoring, and incident response.",
        "sre": "SRE and reliability engineering.",
        "ci_cd": "Build, test, and delivery automation.",
    },
    "security_trust": {
        "security_audit": "Security auditing and red teaming.",
        "application_security": "Application security and hardening.",
        "authentication_identity": "Authentication, authorization, and identity.",
        "privacy_guardrails": "Privacy, safety, and agent guardrails.",
    },
    "browser_automation": {
        "browser_control": "Browser navigation, interaction, and automation.",
        "browser_testing": "Browser QA and webapp testing.",
        "web_extraction": "Extraction, OCR, and structured web capture.",
        "visual_verification": "Screenshots, vision review, and visual evidence.",
    },
    "integration_protocols": {
        "api_design": "API design and contract patterns.",
        "api_integration": "External API and service integrations.",
        "graphql": "GraphQL and schema-driven APIs.",
        "mcp_connectors": "MCP and connector protocols.",
        "auth_integrations": "OAuth and service authentication integrations.",
    },
    "agent_meta": {
        "skill_authoring": "Creating and maintaining skills.",
        "agent_workflows": "Agent-specific workflows and protocols.",
        "orchestration": "Orchestration and multi-agent coordination.",
        "prompt_protocols": "Prompt and execution protocols.",
    },
    "general_fallback": {"needs_review": "Unresolved skill awaiting taxonomy review."},
}

AGENT_OWNERSHIP: dict[str, tuple[str, str]] = {
    "universe": ("operations_delivery", "planning"),
}

# S1 migration: the retired ladder specialists' skills route to their heirs.
# Search-time aliasing (l4_procedural resolves against this) stays robust
# even when bundled skills_data re-ingests the old target_agent names.
LEGACY_AGENT_ALIASES: dict[str, str] = {
    "planning": "universe",
    "write": "universe",
    "file": "universe",
    "api": "universe",
    "data": "universe",
    "devops": "universe",
    "browser": "research",
    "general": "universe",  # pre-v22 rows of the doer itself
}

CATEGORY_RULES: dict[str, tuple[str, str]] = {
    "code_generation": ("software_engineering", "languages_frameworks"),
    "tech_stack": ("software_engineering", "languages_frameworks"),
    "ui_ux_design": ("product_design", "ui_ux"),
    "ui_style": ("product_design", "visual_style"),
    "color_palette": ("product_design", "typography_color"),
    "typography": ("product_design", "typography_color"),
    "ui_reasoning": ("product_design", "ui_ux"),
    "ux_guideline": ("product_design", "ui_ux"),
    "design_system": ("product_design", "design_systems"),
    "frontend-design": ("product_design", "frontend_experience"),
    "brand-guidelines": ("product_design", "visual_style"),
    "webapp-testing": ("browser_automation", "browser_testing"),
    "browser_automation": ("browser_automation", "browser_control"),
    "web_research": ("research_intelligence", "web_research"),
    "research": ("research_intelligence", "research_operations"),
    "data_analysis": ("data_intelligence", "data_analysis"),
    "data_viz": ("data_intelligence", "data_visualization"),
    "sql_analytics": ("data_intelligence", "sql_analytics"),
    "devops": ("platform_operations", "devops"),
    "observability": ("platform_operations", "observability"),
    "security": ("security_trust", "application_security"),
    "security_audit": ("security_trust", "security_audit"),
    "api_design": ("integration_protocols", "api_design"),
    "api_integration": ("integration_protocols", "api_integration"),
    "graphql": ("integration_protocols", "graphql"),
    "mcp-builder": ("integration_protocols", "mcp_connectors"),
    "project_management": ("operations_delivery", "project_management"),
    "writing": ("operations_delivery", "writing_communications"),
    "internal-comms": ("operations_delivery", "writing_communications"),
    "file_management": ("operations_delivery", "workspace_automation"),
    "skill-creator": ("agent_meta", "skill_authoring"),
}


def _tokens(skill: Any) -> str:
    values = [getattr(skill, key, "") for key in ("name", "source", "description", "category", "target_agent")]
    for key in ("tags", "triggers"):
        values.extend(getattr(skill, key, []) or [])
    return " ".join(str(v) for v in values).casefold()


def assign_skill(skill: Any) -> dict[str, Any]:
    category = str(getattr(skill, "category", "") or "").casefold()
    target = str(getattr(skill, "target_agent", "") or "").casefold()
    # S1: re-ingested skills carrying retired agent names route to their heirs
    target = LEGACY_AGENT_ALIASES.get(target, target)
    blob = _tokens(skill)
    reason = f"category:{category}" if category in CATEGORY_RULES else ""
    system_orbit = CATEGORY_RULES.get(category)
    if system_orbit is None:
        if any(x in blob for x in ("test", "qa", "regression", "tdd", "testing")):
            system_orbit = ("browser_automation", "browser_testing") if "browser" in blob or "webapp" in blob else ("software_engineering", "testing_quality")
            reason = "name/tag:test-signal"
        elif any(x in blob for x in ("frontend", "backend", "express", "react", "web", "javascript", "typescript", "css", "html")):
            system_orbit = ("software_engineering", "frontend" if "frontend" in blob or "css" in blob or "html" in blob else "backend")
            reason = "name/tag:web-signal"
        elif target in AGENT_OWNERSHIP:
            system_orbit = AGENT_OWNERSHIP[target]
            reason = f"target_agent:{target}"
        else:
            system_orbit = ("general_fallback", "needs_review")
            reason = "fallback:unresolved"
    system, orbit = system_orbit
    confidence = 0.98 if category in CATEGORY_RULES else (0.90 if target in AGENT_OWNERSHIP else 0.55)
    needs_review = confidence < 0.80
    return {
        "solar_system": system,
        "orbit": orbit,
        "agent": target or None,
        "confidence": confidence,
        "reason": reason,
        "needs_review": needs_review,
        "taxonomy_version": TAXONOMY_VERSION,
    }


def _id(prefix: str, value: str) -> str:
    return f"{prefix}-{value}"


def seed_registry(st: Any) -> None:
    now = time.time()
    for system, description in SOLAR_SYSTEMS.items():
        sid = _id("skill-system", system)
        st.execute(
            "INSERT OR REPLACE INTO skill_solar_systems(id,name,description,trust_level,taxonomy_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?);",
            (sid, system, description, "bundled_trusted", TAXONOMY_VERSION, now, now),
        )
        for orbit, orbit_description in ORBITS.get(system, {}).items():
            oid = _id(f"{sid}-orbit", orbit)
            st.execute(
                "INSERT OR REPLACE INTO skill_orbits(id,solar_system_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?,?);",
                (oid, sid, orbit, orbit_description, now, now),
            )
    for agent, (system, orbit) in AGENT_OWNERSHIP.items():
        sid = _id("skill-system", system)
        oid = _id(f"{sid}-orbit", orbit)
        st.execute(
            "INSERT OR REPLACE INTO skill_agent_ownership(agent,solar_system_id,orbit_id,ownership,created_at) VALUES(?,?,?,?,?);",
            (agent, sid, oid, "primary", now),
        )


def rebuild_taxonomy() -> dict[str, int]:
    st = get_storage()
    skills = st.query_all("SELECT * FROM skills ORDER BY id;")
    now = time.time()
    counts = {"skills": 0, "review": 0, "systems": len(SOLAR_SYSTEMS), "orbits": sum(len(v) for v in ORBITS.values())}
    with st.transaction() as conn:
        seed_registry(conn)
        for row in skills:
            class RowSkill:
                pass
            obj = RowSkill()
            for key in row.keys():
                setattr(obj, key, row[key])
            obj.tags = _json_list(row["tags"])
            obj.triggers = _json_list(row["triggers"])
            assignment = assign_skill(obj)
            sid = _id("skill-system", assignment["solar_system"])
            oid = _id(f"{sid}-orbit", assignment["orbit"])
            tags = _json_list(row["tags"])
            for tag in (f"system:{assignment['solar_system']}", f"orbit:{assignment['orbit']}", f"category:{row['category'] or 'general'}"):
                if tag not in tags:
                    tags.append(tag)
            if row["target_agent"]:
                tag = f"agent:{row['target_agent']}"
                if tag not in tags:
                    tags.append(tag)
            conn.execute(
                "UPDATE skills SET solar_system_id=?, orbit_id=?, taxonomy_version=?, taxonomy_confidence=?, taxonomy_reason=?, taxonomy_needs_review=?, tags=? WHERE id=?;",
                (sid, oid, TAXONOMY_VERSION, assignment["confidence"], assignment["reason"], 1 if assignment["needs_review"] else 0, _json(tags), row["id"]),
            )
            conn.execute(
                "INSERT INTO skill_taxonomy_audit(skill_id,taxonomy_version,solar_system_id,orbit_id,agent,confidence,reason,needs_review,created_at) VALUES(?,?,?,?,?,?,?,?,?);",
                (row["id"], TAXONOMY_VERSION, sid, oid, row["target_agent"] or None, assignment["confidence"], assignment["reason"], 1 if assignment["needs_review"] else 0, now),
            )
            counts["skills"] += 1
            counts["review"] += 1 if assignment["needs_review"] else 0
    return counts


def _json_list(value: Any) -> list[str]:
    import json
    try:
        out = json.loads(value or "[]") if isinstance(value, str) else value
        return [str(x) for x in out] if isinstance(out, list) else []
    except Exception:
        return []


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)
