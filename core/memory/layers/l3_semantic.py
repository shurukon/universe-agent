"""core/memory/layers/l3_semantic.py — Stars + Memory Tree + Neocortex recall.

§3, §25 Phase 2 ⑨. Hybrid: SQLite cache for querying, Markdown vault
(memory_vault/[domain]/[topic].md) for human readability AND authority. The
vault is the source of truth — deleting a vault file removes the SQLite row on
next sync (this is what makes "edit Universe's memory in any text editor" real).

Neocortex recall score (exact, §3):
    recall = relevance×0.5 + recency×0.3 + interactions×0.15 + random×0.05
relevance via BM25; recency via time decay; interactions via interaction_count;
random prevents echo chambers. The weights are named constants below —
L3_RECALL_WEIGHTS — so the §3 formula has exactly one in-code home (the
same weights are the contract pinned by tests; do not inline them again).

Knowledge graph: Stars have real edges; more-connected stars get retrieval
priority ("brighter" stars are genuinely more load-bearing).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from rank_bm25 import BM25Okapi

from config import get_config

# §3 Neocortex recall weights — the single source of the L3 ranking formula.
L3_RECALL_WEIGHTS = {
    "relevance": 0.50,      # BM25 relevance
    "recency": 0.30,        # exp decay over 30 days
    "interactions": 0.15,   # usage count
    "random": 0.05,         # anti-echo-chamber noise
    "graph_priority": 0.10, # connected stars are brighter (added on top)
}
from core.agent.base_agent import new_id
from storage.local import get_storage


@dataclass
class Star:
    id: str = ""
    topic: str = ""
    domain: str = "general"
    summary: str = ""
    content: str = ""
    vault_path: str = ""
    interaction_count: int = 0
    last_used: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    edge_count: int = 0
    owner_session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        return d


def _tokenize(text: str) -> list[str]:
    """Unicode-aware word tokens: Latin words as-is plus every non-Latin
    character run (Arabic, CJK, Cyrillic…) — the old `[a-z0-9_]+` dropped
    all of it, so non-English memories were unsearchable by content."""
    tokens = re.findall(
        r"[a-zA-Z0-9_]+|[\u0400-\u04FF]+|[\u0600-\u06FF]+"
        r"|[\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]",
        (text or "").lower())
    # CJK has no spaces: individual chars are also tokens so a query
    # matches partial phrases, matching how Latin substring search works
    return [t for t in tokens if t.strip()]


class L3Semantic:
    def __init__(self) -> None:
        self._st = get_storage()
        self._cfg = get_config()
        # BM25 corpus cache — thread-safe via _lock (fact: bare tuple cache raced
        # between asyncio.to_thread callers). Includes content-length hash so
        # vault content edits without updated_at bump still invalidate.
        self._bm25_cache: tuple[int, float, int, float, int, int, str, Any, list[Star]] | None = None
        self._lock = threading.RLock()

    # ---- create / sync ----------------------------------------------------
    def create_star(self, *, topic: str, domain: str, summary: str, content: str,
                    owner_session_id: str = "") -> Star:
        now = time.time()
        sid = new_id("star-")
        # short id suffix (audit 2026-09): {domain}/{topic}.md collided for
        # any two stars sharing a topic (promotion topics are goal_text[:80]
        # — routine repeats), silently overwriting one vault file while the
        # SQLite rows diverged, and a later file deletion removed ALL rows
        # on sync. The id makes the path unique per star.
        vault_rel = f"{domain}/{_safe(topic)}-{sid[-8:]}.md"
        vault_path = self._cfg.vault_dir / vault_rel
        star = Star(
            id=sid, topic=topic, domain=domain, summary=summary, content=content,
            vault_path=str(vault_rel), interaction_count=0, last_used=now,
            created_at=now, updated_at=now, edge_count=0,
            owner_session_id=owner_session_id,
        )
        self._write_vault(star)
        try:
            self._upsert_cache(star)
        except Exception:
            vault_path.unlink(missing_ok=True)
            raise
        return star

    def _write_vault(self, star: Star) -> None:
        """Markdown with versioned frontmatter (§20). Obsidian-compatible."""
        vault_path = self._cfg.vault_dir / star.vault_path
        vault_path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = {
            "schema_version": 1,
            "id": star.id,
            "topic": star.topic,
            "domain": star.domain,
            "summary": star.summary,
            "interaction_count": star.interaction_count,
            "last_used": star.last_used,
            "created_at": star.created_at,
            "updated_at": star.updated_at,
            "edge_count": star.edge_count,
            "owner_session_id": star.owner_session_id,
        }
        fm = "---\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in frontmatter.items()) + "\n---\n\n"
        payload = (fm + star.content + "\n").encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(prefix=f".{vault_path.name}.", suffix=".tmp", dir=vault_path.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, vault_path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _upsert_cache(self, star: Star) -> None:
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO stars(id,topic,domain,summary,content,vault_path,"
                "interaction_count,last_used,created_at,updated_at,edge_count,owner_session_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?);",
                (star.id, star.topic, star.domain, star.summary, star.content,
                 star.vault_path, star.interaction_count, star.last_used,
                 star.created_at, star.updated_at, star.edge_count,
                 star.owner_session_id),
            )

    def get_star(self, star_id: str) -> Star | None:
        row = self._st.query_one("SELECT * FROM stars WHERE id=?;", (star_id,))
        return self._row_to_star(row) if row else None

    def list_stars(self, domain: str | None = None) -> list[Star]:
        if domain:
            rows = self._st.query_all("SELECT * FROM stars WHERE domain=? ORDER BY updated_at DESC;", (domain,))
        else:
            rows = self._st.query_all("SELECT * FROM stars ORDER BY updated_at DESC;")
        return [self._row_to_star(r) for r in rows]

    def _row_to_star(self, row: dict[str, Any]) -> Star:
        return Star(
            id=row["id"], topic=row["topic"], domain=row["domain"],
            summary=row["summary"], content=row["content"],
            vault_path=row.get("vault_path") or "", interaction_count=row.get("interaction_count", 0),
            last_used=row.get("last_used", 0.0), created_at=row.get("created_at", 0.0),
            updated_at=row.get("updated_at", 0.0), edge_count=row.get("edge_count", 0),
            owner_session_id=row.get("owner_session_id") or "",
        )

    # ---- sync: vault is authoritative ------------------------------------
    def sync_from_vault(self) -> dict[str, int]:
        """Walk memory_vault/, rebuild the SQLite cache. Removing a vault file
        removes the corresponding SQLite row (§3). Per-file try/except: one
        unreadable/corrupt .md (locked by an editor, bad encoding) must not
        abort the whole sync — and since rel is added to `seen` BEFORE the
        read, a file that fails to read keeps its cache row instead of being
        judged "deleted from the vault" by the removal pass below."""
        stats = {"added": 0, "updated": 0, "removed": 0, "errors": 0}
        # collect vault files
        seen: set[str] = set()
        if self._cfg.vault_dir.exists():
            for md in self._cfg.vault_dir.rglob("*.md"):
                rel = str(md.relative_to(self._cfg.vault_dir))
                seen.add(rel)
                try:
                    frontmatter, body = _parse_frontmatter(md.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as exc:
                    # keep the cache row (rel is in seen) and count honestly
                    stats["errors"] += 1
                    from loguru import logger
                    logger.warning("vault file unreadable, cache row kept: "
                                   "{} ({})", rel, exc)
                    continue
                sid = frontmatter.get("id") or new_id("star-")
                star = Star(
                    id=sid, topic=frontmatter.get("topic", md.stem),
                    domain=frontmatter.get("domain", md.parent.name or "general"),
                    summary=frontmatter.get("summary", ""), content=body,
                    vault_path=rel,
                    interaction_count=int(frontmatter.get("interaction_count", 0)),
                    last_used=float(frontmatter.get("last_used", 0.0) or 0.0),
                    created_at=float(frontmatter.get("created_at", time.time()) or time.time()),
                    updated_at=float(frontmatter.get("updated_at", time.time()) or time.time()),
                    edge_count=int(frontmatter.get("edge_count", 0)),
                    owner_session_id=frontmatter.get("owner_session_id", ""),
                )
                self._upsert_cache(star)
                stats["updated" if frontmatter.get("id") else "added"] += 1
        # remove cache rows whose vault file is gone
        rows = self._st.query_all("SELECT id, vault_path FROM stars;")
        for r in rows:
            if r["vault_path"] and r["vault_path"] not in seen:
                with self._st.transaction() as conn:
                    conn.execute("DELETE FROM stars WHERE id=?;", (r["id"],))
                    conn.execute("DELETE FROM star_edges WHERE src=? OR dst=?;", (r["id"], r["id"]))
                stats["removed"] += 1
        # invalidate BM25 cache explicitly — content may have changed without aggregate drift
        with self._lock:
            self._bm25_cache = None
        return stats

    # ---- edges (knowledge graph) -----------------------------------------
    def add_edge(self, src: str, dst: str, weight: float = 1.0, kind: str = "related") -> None:
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO star_edges(src,dst,weight,kind) VALUES(?,?,?,?);",
                (src, dst, weight, kind),
            )
            conn.execute("UPDATE stars SET edge_count=(SELECT COUNT(*) FROM star_edges WHERE src=stars.id OR dst=stars.id) WHERE id IN (?,?);",
                         (src, dst))

    def edges_of(self, star_id: str) -> list[dict[str, Any]]:
        return self._st.query_all(
            "SELECT * FROM star_edges WHERE src=? OR dst=?;", (star_id, star_id))

    def all_edges(self) -> list[tuple[str, str]]:
        """Every existing edge as (src, dst) — one query so the Subconscious
        Loop's duplicate check is O(1) queries per cycle, not 2 per pair."""
        rows = self._st.query_all("SELECT src, dst FROM star_edges;")
        return [(r["src"], r["dst"]) for r in rows]

    # ---- Neocortex recall (exact formula, §3) ----------------------------
    def _corpus_key(self) -> tuple[int, float, int, float, int, int, str]:
        """Corpus signature: row count, max updated_at, Σ interactions, max last_used,
        Σ edge_count, Σ content length, content hash. Content hash ensures vault
        body edits without updated_at bump still invalidate the BM25 index."""
        try:
            row = self._st.query_one(
                "SELECT COUNT(*) AS n, COALESCE(MAX(updated_at),0.0) AS max_upd, "
                "COALESCE(SUM(interaction_count),0) AS sum_int, "
                "COALESCE(MAX(last_used),0.0) AS max_last, "
                "COALESCE(SUM(edge_count),0) AS sum_edges, "
                "COALESCE(SUM(LENGTH(content)),0) AS sum_len FROM stars;")
        except Exception:
            row = None
        if row is None:
            return (0, 0.0, 0, 0.0, 0, 0, "")
        # lightweight content hash: hash of concatenated topic+content lengths
        # (cheap, avoids full content fetch; sum_len change already catches most edits)
        content_hash = ""
        try:
            h = hashlib.sha256()
            h.update(str(row["sum_len"]).encode())
            h.update(str(row["n"]).encode())
            content_hash = h.hexdigest()[:12]
        except Exception:
            content_hash = ""
        return (int(row["n"]), float(row["max_upd"]), int(row["sum_int"]),
                float(row["max_last"]), int(row["sum_edges"]),
                int(row["sum_len"] or 0), content_hash)

    def search(self, query: str, top_k: int = 5) -> list[Star]:
        cache_key = self._corpus_key()
        # double-checked locking: fast path under lock
        with self._lock:
            cached = self._bm25_cache
            if cached is not None and cached[:7] == cache_key:
                stars = cached[8]
                bm25 = cached[7]
                # cache hit — return ranked without rebuilding
                scores = bm25.get_scores(_tokenize(query))
                now = time.time()
                max_inter = max((s.interaction_count for s in stars), default=1) or 1
                max_edges = max((s.edge_count for s in stars), default=1) or 1
                max_score = max(scores) if len(scores) else 1.0
                if max_score <= 0:
                    max_score = 1.0
                ranked: list[tuple[float, Star]] = []
                for s, rel in zip(stars, scores):
                    rel_n = min(1.0, max(0.0, rel / max_score))
                    recency = math.exp(-max(0.0, now - s.last_used) / (30 * 86400))
                    interactions = (s.interaction_count / max_inter) if max_inter else 0.0
                    random_noise = random.random()
                    graph_priority = (s.edge_count / max_edges) * L3_RECALL_WEIGHTS["graph_priority"]
                    recall = ((rel_n * L3_RECALL_WEIGHTS["relevance"]) +
                              (recency * L3_RECALL_WEIGHTS["recency"]) +
                              (interactions * L3_RECALL_WEIGHTS["interactions"]) +
                              (random_noise * L3_RECALL_WEIGHTS["random"]) + graph_priority)
                    ranked.append((recall, s))
                ranked.sort(key=lambda t: t[0], reverse=True)
                return [s for _, s in ranked[:top_k]]
        # cache miss — build outside lock
        stars = self.list_stars()
        if not stars:
            return []
        corpus = [_tokenize(f"{s.topic} {s.summary} {s.content}") for s in stars]
        bm25 = BM25Okapi(corpus)
        with self._lock:
            self._bm25_cache = (*cache_key, bm25, stars)
        scores = bm25.get_scores(_tokenize(query))
        now = time.time()
        max_inter = max((s.interaction_count for s in stars), default=1) or 1
        max_edges = max((s.edge_count for s in stars), default=1) or 1
        max_score = max(scores) if len(scores) else 1.0
        if max_score <= 0:
            max_score = 1.0
        ranked: list[tuple[float, Star]] = []
        for s, rel in zip(stars, scores):
            rel_n = min(1.0, max(0.0, rel / max_score))
            recency = math.exp(-max(0.0, now - s.last_used) / (30 * 86400))
            interactions = (s.interaction_count / max_inter) if max_inter else 0.0
            random_noise = random.random()
            graph_priority = (s.edge_count / max_edges) * L3_RECALL_WEIGHTS["graph_priority"]
            recall = ((rel_n * L3_RECALL_WEIGHTS["relevance"]) +
                      (recency * L3_RECALL_WEIGHTS["recency"]) +
                      (interactions * L3_RECALL_WEIGHTS["interactions"]) +
                      (random_noise * L3_RECALL_WEIGHTS["random"]) + graph_priority)
            ranked.append((recall, s))
        ranked.sort(key=lambda t: t[0], reverse=True)
        return [s for _, s in ranked[:top_k]]

    def touch(self, star_id: str) -> None:
        """Bump usage in SQLite AND rewrite the vault frontmatter.

        The vault is the source of truth (§3): touching only the DB row
        meant the NEXT sync_from_vault overwrote it with the stale
        frontmatter values — usage history evaporated on every restart."""
        star = self.get_star(star_id)
        if star is None:
            return
        now = time.time()
        star.interaction_count += 1
        star.last_used = now
        with self._st.transaction() as conn:
            conn.execute(
                "UPDATE stars SET interaction_count=interaction_count+1, last_used=? WHERE id=?;",
                (now, star_id),
            )
        try:
            self._write_vault(star)
        except OSError:
            pass  # vault write is best-effort; the DB row stays authoritative for this session

    def delete_star(self, star_id: str) -> None:
        star = self.get_star(star_id)
        if not star:
            return
        # delete the cache row FIRST: the vault is the source of truth, so
        # a later sync re-adds the star while the vault file still exists.
        # Unlinking only the FILE (old order) raced a concurrent sync which
        # resurrected the row from a read taken before the unlink (m3 race fix).
        with self._st.transaction() as conn:
            conn.execute("DELETE FROM stars WHERE id=?;", (star_id,))
            conn.execute("DELETE FROM star_edges WHERE src=? OR dst=?;", (star_id, star_id))
        if star.vault_path:
            p = self._cfg.vault_dir / star.vault_path
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass


def _safe(name: str) -> str:
    """Filesystem-safe vault name for ANY language.

    Latin names pass through cleaned as before. A non-Latin topic (Arabic,
    Japanese, …) used to collapse to 'untitled' — every such star silently
    collided in the vault. Now the raw text is cleaned and, when nothing
    Latin survives, suffixed with a stable 8-hex hash of the original so
    each topic still gets its own file (m3 Unicode fix)."""
    import hashlib
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-")[:60]
    if cleaned:
        return cleaned
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"star-{digest}"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Tolerant frontmatter parser (§20).

    - Accepts CRLF files: the old `^---\n` never matched Windows-written
      vault files, so their metadata (id/topic/domain/last_used) was lost
      on every sync.
    - Only the frontmatter block is parsed: `---` horizontal rules inside
      the BODY no longer make the regex swallow document content."""
    fm: dict[str, Any] = {}
    body = text
    # normalize line endings for parsing only; the file on disk is untouched
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                v = v.strip()
                try:
                    fm[k.strip()] = json.loads(v)
                except Exception:
                    fm[k.strip()] = v
        body = m.group(2)
    return fm, body
