"""case_engine/repointel_client.py — Selective RepoIntel Intelligence Client.

Owner:      ICE × RepoIntel Integration.
Purpose:    Provides granular, tool-level access to RepoIntel's repository
            knowledge base (AST symbols, call graph, static findings, code chunks).
            Designed specifically for UNFINISHED or IN-PROGRESS projects:
            - ICE selectively queries specific tools (symbol lookups, callers,
              architecture, findings) rather than dumping raw full scans.
            - Prevents false-alarm noise on emerging code by grounding against
              ICE goal clarity levels (clarity 1-2 expects missing pieces).
            - Automatically evaluates goal code maturity (scaffolded -> candidate ->
              connected -> tested) based on AST presence, call edges, and test files.

Transports:
    1. Direct SQLite (default): fast, zero-dependency read-only query of repointel.db.
    2. Stdio MCP: interacts with app.core.mcp.server if subprocess is configured.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default locations where repointel knowledge base lives, in priority order:
#   1. REPOINTEL_DIR (explicit operator choice)
#   2. the sibling checkout in a shared workspace
#      (<workspace>/ICE-improved/ice/case_engine -> <workspace>/repointel-ai-2)
#   3. the historical developer path (kept last for backward compatibility)
_SIBLING_REPOINTEL = str(Path(__file__).resolve().parents[3] / "repointel-ai-2")
DEFAULT_REPOINTEL_PATHS = [
    os.getenv("REPOINTEL_DIR", ""),
    _SIBLING_REPOINTEL,
    r"C:\Users\sreen\OneDrive\Pictures\Bharath Gitam\ai !411\repo intel ai\repointel-ai-2",
]

# File names that must never be served through get_file_snippet, even when
# they sit inside the RepoIntel checkout (secrets, keys, credentials).
_SECRET_NAME_RE = re.compile(
    r"(^\.env(?!\.example$)(\..*)?$|\.pem$|\.key$|\.p12$|\.pfx$|"
    r"^id_(rsa|dsa|ecdsa|ed25519)|^credentials(\..*)?$|"
    r"^secrets?\.(json|ya?ml|toml)$|\.sqlite3?$|\.db$)",
    re.IGNORECASE,
)
_MAX_SNIPPET_LINES = 2000


def find_repointel_db() -> Optional[str]:
    """Locate the repointel.db SQLite file across standard search paths."""
    env_db = os.getenv("REPOINTEL_DB", "").strip()
    if env_db and os.path.exists(env_db):
        return env_db

    for base in DEFAULT_REPOINTEL_PATHS:
        if not base or not os.path.exists(base):
            continue
        candidates = [
            os.path.join(base, "data", "repointel.db"),
            os.path.join(base, "repointel.db"),
            os.path.join(base, "backend", "data", "repointel.db"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    return None


@dataclass
class CodeSymbol:
    id: str
    qualified_name: str
    entity_type: str
    file_path: str
    source_code: str
    callers_count: int = 0


@dataclass
class CodeMaturityAssessment:
    goal_id: str
    statement: str
    suggested_maturity: str
    candidate_symbols: List[str]
    callers_found: List[str]
    tests_found: List[str]
    evidence_summary: str
    divergence_risk: str
    checks_passed: int = 0
    checks_total: int = 0
    check_details: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


# Words that describe goals in general rather than code: matching them against
# symbol names produces noise ("answer", "under", "seconds", "laptop", ...).
_GOAL_STOP_WORDS = {
    "the", "and", "for", "with", "must", "should", "will", "all", "project", "system",
    "that", "this", "from", "into", "than", "under", "over", "about", "when", "what",
    "keep", "make", "able", "able", "have", "need", "needs", "want", "users", "user",
    "developer", "developers", "questions", "question", "answer", "answers", "seconds",
    "second", "minutes", "laptop", "fast", "faster", "quick", "quickly", "good", "better",
    "repository", "repositories", "code", "codebase", "feature", "features", "work",
    "works", "every", "only", "less", "more", "such", "some", "each", "also", "very",
}


class RepoIntelClient:
    """Selective, read-only interface to RepoIntel knowledge base and tools."""

    def __init__(self, db_path: Optional[str] = None, repo: Optional[str] = None):
        self.db_path = db_path or find_repointel_db()
        # Repository scope (name or id). RepoIntel symbol ids are not
        # repo-qualified, so unscoped graph queries mix every indexed repo.
        self._repo_ref = repo if repo is not None else os.getenv("REPOINTEL_REPO", "").strip() or None
        self._repo_id_cache: Optional[str] = None
        if not self.db_path or not os.path.exists(self.db_path):
            logger.warning(
                "RepoIntel database not found. Tool calls will return empty mock results."
            )

    def resolve_repo_id(self, repo: Optional[str] = None) -> Optional[str]:
        """Map a repository name or id to an id (most recently indexed name match)."""
        ref = repo or self._repo_ref
        if not ref:
            return None
        if repo is None and self._repo_id_cache:
            return self._repo_id_cache
        conn = self._get_connection()
        if not conn:
            return None
        try:
            row = conn.execute("SELECT id FROM repositories WHERE id = ?", (ref,)).fetchone()
            if not row:
                tail = re.split(r"[\\/]", ref.rstrip("/\\"))[-1]
                row = conn.execute(
                    "SELECT id FROM repositories WHERE name = ? OR name = ? OR url = ? "
                    "ORDER BY last_indexed_at DESC LIMIT 1", (ref, tail, ref)).fetchone()
            rid = row[0] if row else None
        finally:
            conn.close()
        if repo is None:
            self._repo_id_cache = rid
        return rid

    def _scope(self, alias: str, repo: Optional[str] = None) -> Tuple[str, list]:
        """SQL fragment restricting `alias`.id to one repository's symbols."""
        rid = self.resolve_repo_id(repo)
        if not rid:
            return "", []
        prefix = f"file:{rid}:"
        return (f" AND {alias}.id IN (SELECT target_id FROM edges WHERE edge_type = 'CONTAINS' "
                f"AND substr(source_id, 1, ?) = ?)", [len(prefix), prefix])

    @property
    def is_available(self) -> bool:
        return bool(self.db_path and os.path.exists(self.db_path))

    def _get_connection(self) -> Optional[sqlite3.Connection]:
        if not self.is_available:
            return None
        # Open in URI read-only mode so RepoIntel files are never locked or modified
        uri = f"file:{Path(self.db_path).resolve().as_posix()}?mode=ro"
        # timeout: wait out a RepoIntel writer (Phase 1 indexing) instead of
        # failing immediately with "database is locked" under concurrent load.
        return sqlite3.connect(
            uri, uri=True, timeout=float(os.getenv("REPOINTEL_DB_TIMEOUT", "5"))
        )

    # -----------------------------------------------------------------------
    # 1. High-level Repository Overview
    # -----------------------------------------------------------------------

    def list_repositories(self) -> List[Dict[str, Any]]:
        """List all repositories indexed by RepoIntel."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, name, url, branch, last_indexed_at, stats FROM repositories")
            results = []
            for row in cur.fetchall():
                stats = {}
                try:
                    stats = json.loads(row[5]) if row[5] else {}
                except Exception:
                    pass
                results.append({
                    "repo_id": row[0],
                    "name": row[1],
                    "url": row[2],
                    "branch": row[3],
                    "last_indexed_at": row[4],
                    "stats": stats,
                })
            return results
        finally:
            conn.close()

    def get_architecture(self, repo_id: Optional[str] = None) -> Dict[str, Any]:
        """Get codebase architectural metrics (files, functions, classes, edges, top hubs)."""
        conn = self._get_connection()
        if not conn:
            return {"error": "RepoIntel database unavailable", "files_count": 0}
        try:
            cur = conn.cursor()
            # File count
            if self.resolve_repo_id(repo_id):
                cur.execute("SELECT count(*) FROM files WHERE repo_id = ?", (self.resolve_repo_id(repo_id),))
            else:
                cur.execute("SELECT count(*) FROM files")
            file_count = cur.fetchone()[0]

            rid = self.resolve_repo_id(repo_id)
            frag, fparams = self._scope("n", rid)
            # Symbol counts by entity_type
            if rid:
                prefix = f"file:{rid}:"
                cur.execute(
                    "SELECT n.entity_type, count(*) FROM nodes n WHERE n.entity_type != 'Repository'"
                    " AND (n.id IN (SELECT target_id FROM edges WHERE edge_type = 'CONTAINS'"
                    " AND substr(source_id, 1, ?) = ?) OR substr(n.id, 1, ?) = ?)"
                    " GROUP BY n.entity_type", fparams + [len(prefix), prefix])
            else:
                cur.execute(
                    "SELECT entity_type, count(*) FROM nodes WHERE entity_type != 'Repository' GROUP BY entity_type"
                )
            entity_counts = dict(cur.fetchall())

            # Call edges count (calls INTO this repo's symbols)
            cur.execute(
                "SELECT count(*) FROM edges e JOIN nodes n ON e.target_id = n.id "
                "WHERE e.edge_type = 'CALLS'" + frag, fparams)
            calls_count = cur.fetchone()[0]

            # Top hub symbols (symbols with highest in-degree / most called)
            cur.execute(
                """
                SELECT n.qualified_name, n.file_path, count(e.source_id) as callers
                FROM edges e
                JOIN nodes n ON e.target_id = n.id
                WHERE e.edge_type = 'CALLS'""" + frag + """
                GROUP BY n.id
                ORDER BY callers DESC
                LIMIT 10
                """, fparams
            )
            top_hubs = [
                {"name": r[0], "file": r[1], "callers": r[2]}
                for r in cur.fetchall()
            ]

            return {
                "file_count": file_count,
                "symbols_by_type": entity_counts,
                "call_edges_count": calls_count,
                "top_hubs": top_hubs,
            }
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # 2. Granular Symbol & Graph Retrieval
    # -----------------------------------------------------------------------

    def search_code(self, query: str, repo_id: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Search code: RepoIntel's hybrid retrieval when its API is reachable
        (semantic + lexical + graph, reranked), else SQL LIKE — first on the
        whole query, then on its most distinctive words (a sentence never
        matches LIKE '%sentence%')."""
        semantic = self._semantic_hits(query, repo_id, limit)
        if semantic:
            return semantic
        hits = self._search_code_sql(query, repo_id, limit)
        if hits or len(query.split()) < 2:
            return hits
        words = sorted({w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", query)
                        if w.lower() not in _GOAL_STOP_WORDS}, key=lambda w: (-len(w), w))
        seen: set = set()
        for w in words[:3]:
            for h in self._search_code_sql(w, repo_id, limit):
                if h["id"] not in seen:
                    seen.add(h["id"])
                    hits.append(h)
        return hits[:limit]

    def _semantic_hits(self, query: str, repo_id: Optional[str], limit: int) -> List[Dict[str, Any]]:
        rid = self.resolve_repo_id(repo_id)
        base = os.getenv("REPOINTEL_URL", "").rstrip("/")
        if not rid or not base:
            return []
        try:
            import httpx

            r = httpx.get(f"{base}/api/v1/search/{rid}", params={"q": query[:400], "k": max(limit, 5)},
                          timeout=float(os.getenv("REPOINTEL_SEARCH_TIMEOUT", "20")))
            if r.status_code != 200:
                return []
            return [{
                "id": f"{h['file_path']}:{h['start_line']}",
                "symbol": h.get("symbol_name") or h["file_path"],
                "entity_type": h.get("chunk_type"),
                "file_path": h["file_path"],
                "snippet": h.get("snippet", ""),
                "source": "repointel_hybrid_search",
                "score": h.get("score"),
            } for h in r.json().get("results", [])][:limit]
        except Exception as exc:
            logger.debug("RepoIntel semantic search unavailable: %s", exc)
            return []

    def _search_code_sql(self, query: str, repo_id: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            pattern = f"%{query}%"
            rid = self.resolve_repo_id(repo_id)
            frag, fparams = self._scope("nodes", rid)
            cur.execute(
                """
                SELECT id, qualified_name, entity_type, file_path, source_code
                FROM nodes
                WHERE (source_code LIKE ? OR qualified_name LIKE ?)""" + frag + """
                LIMIT ?
                """,
                [pattern, pattern] + fparams + [limit],
            )
            hits: List[Dict[str, Any]] = []
            for r in cur.fetchall():
                hits.append({
                    "id": r[0],
                    "symbol": r[1],
                    "entity_type": r[2],
                    "file_path": r[3],
                    "snippet": (r[4] or "")[:350],
                    "source": "ast_node",
                })
            # Search documents if space permits
            if len(hits) < limit:
                cur.execute(
                    """
                    SELECT id, file_path, doc_type, content
                    FROM documents
                    WHERE content LIKE ?""" + (" AND repo_id = ?" if rid else "") + """
                    LIMIT ?
                    """,
                    [pattern] + ([rid] if rid else []) + [limit - len(hits)],
                )
                for r in cur.fetchall():
                    hits.append({
                        "id": r[0],
                        "symbol": r[1],
                        "entity_type": r[2],
                        "file_path": r[1],
                        "snippet": (r[3] or "")[:350],
                        "source": "document",
                    })
            return hits
        finally:
            conn.close()

    def search_symbols(self, query: str, limit: int = 10, repo_id: Optional[str] = None) -> List[CodeSymbol]:
        """Search AST nodes (functions, classes, methods) matching a query."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            pattern = f"%{query}%"
            cur.execute(
                """
                SELECT n.id, n.qualified_name, n.entity_type, n.file_path, n.source_code,
                       (SELECT count(*) FROM edges WHERE target_id = n.id AND edge_type = 'CALLS') as caller_cnt
                FROM nodes n
                WHERE (n.qualified_name LIKE ? OR n.file_path LIKE ?)""" + self._scope("n", repo_id)[0] + """
                ORDER BY caller_cnt DESC
                LIMIT ?
                """,
                [pattern, pattern] + self._scope("n", repo_id)[1] + [limit],
            )
            symbols = []
            for r in cur.fetchall():
                symbols.append(CodeSymbol(
                    id=r[0],
                    qualified_name=r[1],
                    entity_type=r[2],
                    file_path=r[3],
                    source_code=r[4][:500] if r[4] else "",
                    callers_count=r[5],
                ))
            return symbols
        finally:
            conn.close()

    def get_callers(self, symbol_name: str, repo_id: Optional[str] = None) -> List[Dict[str, str]]:
        """Find functions/files that call the given symbol."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT src.qualified_name, src.file_path, e.edge_type
                FROM edges e
                JOIN nodes tgt ON e.target_id = tgt.id
                JOIN nodes src ON e.source_id = src.id
                WHERE (tgt.qualified_name = ? OR tgt.qualified_name LIKE ?)
                  AND e.edge_type = 'CALLS'""" + self._scope("src", repo_id)[0] + """
                LIMIT 20
                """,
                [symbol_name, f"%{symbol_name}%"] + self._scope("src", repo_id)[1],
            )
            return [
                {"caller_name": r[0], "file_path": r[1], "edge_type": r[2]}
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # 3. Static Findings & Gaps (Filtered for Unfinished Code)
    # -----------------------------------------------------------------------

    def get_findings(
        self,
        repo_id: Optional[str] = None,
        severity: Optional[str] = None,
        finding_type: Optional[str] = None,
        file_path: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Get static analysis findings with optional filtering to prevent alarm noise."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            query = "SELECT id, rule_id, severity, finding_type, message, file_path, line_start FROM findings WHERE 1=1"
            params: List[Any] = []
            if repo_id:
                query += " AND repo_id = ?"
                params.append(repo_id)
            if severity:
                query += " AND severity = ?"
                params.append(severity)
            if finding_type:
                query += " AND finding_type = ?"
                params.append(finding_type)
            if file_path:
                query += " AND file_path LIKE ?"
                params.append(f"%{file_path}%")
            query += " ORDER BY line_start ASC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            return [
                {
                    "finding_id": r[0],
                    "rule_id": r[1],
                    "severity": r[2],
                    "finding_type": r[3],
                    "message": r[4],
                    "file_path": r[5],
                    "line": r[6],
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def explain_finding(self, finding_id: str, repo_id: Optional[str] = None) -> Dict[str, Any]:
        """Retrieve in-depth rule description, severity, and remediation advice for a specific finding."""
        conn = self._get_connection()
        if not conn:
            return {"error": "RepoIntel database unavailable"}
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, rule_id, severity, finding_type, message, file_path, line_start, line_end, tool, confidence, metadata
                FROM findings
                WHERE id = ? OR rule_id = ?
                LIMIT 1
                """,
                (finding_id, finding_id),
            )
            row = cur.fetchone()
            if not row:
                return {"error": f"Finding '{finding_id}' not found"}

            meta = {}
            if row[10]:
                try:
                    meta = json.loads(row[10])
                except Exception:
                    pass

            return {
                "finding_id": row[0],
                "rule_id": row[1],
                "severity": row[2],
                "finding_type": row[3],
                "message": row[4],
                "file_path": row[5],
                "line_start": row[6],
                "line_end": row[7],
                "tool": row[8],
                "confidence": row[9],
                "metadata": meta,
            }
        finally:
            conn.close()

    def get_file_snippet(
        self, file_path: str, start_line: Optional[int] = 1, end_line: Optional[int] = 100
    ) -> Dict[str, Any]:
        """Read a sanitized excerpt of code from a file indexed by RepoIntel."""
        norm_path = (file_path or "").replace("\\", "/").strip()
        # Reject traversal, POSIX-absolute, UNC and drive-letter paths
        # (os.path.join(base, "C:/x") silently discards base on Windows).
        if (
            not norm_path
            or ".." in norm_path.split("/")
            or norm_path.startswith("/")
            or re.match(r"^[A-Za-z]:", norm_path)
        ):
            return {"error": "Path traversal rejected", "file_path": file_path}
        if _SECRET_NAME_RE.search(Path(norm_path).name):
            return {"error": "Access to secret-bearing files is denied", "file_path": file_path}

        target_file = None
        for base in DEFAULT_REPOINTEL_PATHS:
            if not base or not os.path.isdir(base):
                continue
            base_real = os.path.realpath(base)
            for cand in (
                os.path.join(base_real, norm_path),
                os.path.join(base_real, "backend", norm_path),
            ):
                cand_real = os.path.realpath(cand)
                # Containment check also defeats symlink escapes.
                try:
                    inside = os.path.commonpath([base_real, cand_real]) == base_real
                except ValueError:  # different drives on Windows
                    inside = False
                if inside and os.path.isfile(cand_real):
                    target_file = cand_real
                    break
            if target_file:
                break

        if target_file and os.path.isfile(target_file):
            try:
                with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                start = max(1, start_line or 1)
                end = min(len(all_lines), end_line or len(all_lines))
                end = min(end, start + _MAX_SNIPPET_LINES - 1)
                if start > end:
                    return {
                        "error": "start_line is past the end of the file",
                        "file_path": file_path,
                        "total_lines": len(all_lines),
                    }
                snippet = "".join(all_lines[start - 1 : end])
                return {
                    "file_path": file_path,
                    "start_line": start,
                    "end_line": end,
                    "total_lines": len(all_lines),
                    "content": snippet,
                }
            except Exception as exc:
                logger.warning("Could not read file %s: %s", target_file, exc)

        conn = self._get_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT qualified_name, entity_type, source_code FROM nodes WHERE file_path LIKE ? LIMIT 5",
                    (f"%{file_path}%",),
                )
                nodes = cur.fetchall()
                if nodes:
                    combined = "\n\n".join(f"# {n[1]}: {n[0]}\n{n[2]}" for n in nodes)
                    return {
                        "file_path": file_path,
                        "source": "ast_nodes",
                        "content": combined[:4000],
                    }
            finally:
                conn.close()

        return {"error": f"File '{file_path}' not found", "file_path": file_path}

    def get_callees(self, symbol_name: str, limit: int = 20) -> List[Dict[str, str]]:
        """Find functions or classes called downstream by the given symbol."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT tgt.qualified_name, tgt.file_path, tgt.entity_type
                FROM edges e
                JOIN nodes src ON e.source_id = src.id
                JOIN nodes tgt ON e.target_id = tgt.id
                WHERE (src.qualified_name = ? OR src.qualified_name LIKE ?)
                  AND e.edge_type = 'CALLS'
                LIMIT ?
                """,
                (symbol_name, f"%{symbol_name}%", limit),
            )
            return [
                {"callee_name": r[0], "file_path": r[1], "entity_type": r[2]}
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def get_impact_radius(
        self, symbol_name: str, max_depth: int = 5, limit: int = 20,
        repo_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Upstream Blast Radius: traces recursive callers using SQLite WITH RECURSIVE CTE.

        Uses comma-padded path tracking for cycle prevention matching RepoIntel's graph store.
        """
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            query = """
            WITH RECURSIVE blast_radius(node_id, depth, path) AS (
                SELECT
                    e.source_id AS node_id,
                    1 AS depth,
                    ',' || n.id || ',' || e.source_id || ',' AS path
                FROM edges e
                JOIN nodes n ON e.target_id = n.id
                WHERE (n.id = ? OR n.qualified_name = ?)
                  AND e.edge_type = 'CALLS'

                UNION ALL

                SELECT
                    e.source_id AS node_id,
                    br.depth + 1 AS depth,
                    br.path || e.source_id || ',' AS path
                FROM edges e
                JOIN blast_radius br ON e.target_id = br.node_id
                WHERE e.edge_type = 'CALLS'
                  AND instr(br.path, ',' || e.source_id || ',') = 0
                  AND br.depth < ?
            )
            SELECT
                n.id AS node_id,
                n.qualified_name AS name,
                n.entity_type AS label,
                n.file_path,
                MIN(br.depth) AS path_length,
                COUNT(DISTINCT br.path) AS num_call_paths
            FROM blast_radius br
            JOIN nodes n ON br.node_id = n.id
            WHERE 1 = 1""" + self._scope("n", repo_id)[0] + """
            GROUP BY n.id, n.qualified_name, n.entity_type, n.file_path
            ORDER BY path_length ASC, num_call_paths DESC
            LIMIT ?
            """
            cur.execute(query, [symbol_name, symbol_name, max_depth] + self._scope("n", repo_id)[1] + [limit])
            return [
                {
                    "node_id": r[0],
                    "name": r[1],
                    "label": r[2],
                    "file_path": r[3],
                    "path_length": r[4],
                    "num_call_paths": r[5],
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def list_gaps(
        self, repo_id: Optional[str] = None, gap_type: str = "all", limit: int = 50
    ) -> Dict[str, Any]:
        """List codebase gaps: test_gap, doc_gap, incomplete_feature, and error_handling_gap."""
        types = (
            ["test_gap", "doc_gap", "incomplete_feature", "error_handling_gap"]
            if gap_type == "all"
            else [gap_type]
        )
        all_gaps: List[Dict[str, Any]] = []
        for gt in types:
            all_gaps.extend(self.get_findings(repo_id=repo_id, finding_type=gt, limit=limit))

        by_type: Dict[str, int] = {}
        for g in all_gaps:
            ft = g.get("finding_type") or "unknown"
            by_type[ft] = by_type.get(ft, 0) + 1

        return {
            "repo_id": repo_id or "all",
            "gap_type": gap_type,
            "total_gaps": len(all_gaps),
            "by_type": by_type,
            "gaps": all_gaps[:limit],
        }

    def get_health_score(self, repo_id: Optional[str] = None) -> Dict[str, Any]:
        """Calculate the aggregate codebase health score (0-100) and letter grade (A-D)."""
        findings = self.get_findings(repo_id=repo_id, limit=10000)
        by_sev: Dict[str, int] = {}
        for f in findings:
            s = f.get("severity") or "info"
            by_sev[s] = by_sev.get(s, 0) + 1

        penalty = (
            by_sev.get("blocker", 0) * 10
            + by_sev.get("critical", 0) * 5
            + by_sev.get("major", 0) * 1
            + by_sev.get("minor", 0) * 0.1
        )
        score = max(0.0, round(100.0 - penalty, 1))
        grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
        return {
            "repo_id": repo_id or "all",
            "health_score": score,
            "grade": grade,
            "by_severity": by_sev,
            "total_findings": len(findings),
        }

    # -----------------------------------------------------------------------
    # 4. Goal Maturity Verification (The Core Bridge)
    # -----------------------------------------------------------------------

    def _semantic_candidates(self, statement: str, k: int = 8) -> List[str]:
        """Code symbols for a goal via RepoIntel GET /api/v1/search/{repo} (no LLM).
        Returns [] when RepoIntel's API is not reachable or no repo is scoped."""
        rid = self.resolve_repo_id()
        base = os.getenv("REPOINTEL_URL", "").rstrip("/")
        if not rid or not base:
            return []
        try:
            import httpx

            r = httpx.get(f"{base}/api/v1/search/{rid}", params={"q": statement[:400], "k": k},
                          timeout=float(os.getenv("REPOINTEL_SEARCH_TIMEOUT", "20")))
            if r.status_code != 200:
                return []
            out = []
            for h in r.json().get("results", []):
                if h.get("chunk_type") != "code" or not h.get("symbol_name"):
                    continue
                desc = f"{h['symbol_name']} (Function in {h['file_path']})"
                if desc not in out:
                    out.append(desc)
            return out[:5]
        except Exception as exc:  # network / server down: keyword fallback
            logger.debug("RepoIntel semantic search unavailable: %s", exc)
            return []

    @staticmethod
    def _extract_goal_keywords(text: str) -> List[str]:
        raw_words = re.findall(r"[a-z0-9_]{3,}", (text or "").lower())
        keywords: List[str] = []
        for w in raw_words:
            # Also split snake_case components so e.g. compute_discount -> compute, discount
            parts = [w] + ([p for p in w.split("_") if len(p) >= 3] if "_" in w else [])
            for tok in parts:
                if tok not in _GOAL_STOP_WORDS and len(tok) >= 4 and tok not in keywords:
                    keywords.append(tok)
        for w in list(keywords):
            for suf in ("ings", "ing", "ers", "er", "ed", "es", "s", "ation", "ions", "ion"):
                if w.endswith(suf) and len(w) - len(suf) >= 4:
                    stem = w[: -len(suf)]
                    if stem not in keywords:
                        keywords.append(stem)
                    break
        return keywords

    @staticmethod
    def _is_trivial_stub(source_code: str) -> bool:
        """Detect empty/pass/.../NotImplementedError-only stub bodies."""
        if not (source_code or "").strip():
            return True
        import ast as _ast

        try:
            tree = _ast.parse(source_code)
            defs = [
                n for n in tree.body
                if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))
            ]
            if defs:
                for node in defs:
                    stmts = list(node.body)
                    if (
                        stmts
                        and isinstance(stmts[0], _ast.Expr)
                        and isinstance(stmts[0].value, _ast.Constant)
                        and isinstance(stmts[0].value.value, str)
                    ):
                        stmts = stmts[1:]
                    for st in stmts:
                        if isinstance(st, _ast.Pass):
                            continue
                        if (
                            isinstance(st, _ast.Expr)
                            and isinstance(st.value, _ast.Constant)
                            and st.value.value is Ellipsis
                        ):
                            continue
                        if isinstance(st, _ast.Return) and (
                            st.value is None
                            or (isinstance(st.value, _ast.Constant) and st.value.value is None)
                        ):
                            continue
                        if isinstance(st, _ast.Raise) and "NotImplementedError" in _ast.dump(st):
                            continue
                        return False
                return True
        except SyntaxError:
            pass

        lines = [
            ln.strip()
            for ln in source_code.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        body: list[str] = []
        for ln in lines:
            if ln.startswith(("def ", "async def ", "class ", "@")):
                if ":" in ln:
                    trailing = ln.split(":", 1)[1].strip()
                    if trailing:
                        body.append(trailing)
            else:
                body.append(ln)
        meaningful = [
            ln for ln in body
            if not (
                (ln.startswith('"""') and ln.endswith('"""'))
                or (ln.startswith("'''") and ln.endswith("'''"))
            )
        ]
        if not meaningful:
            return True
        return all(
            ln in ("pass", "...") or "NotImplementedError" in ln or ln == "return None"
            for ln in meaningful
        )

    def decompose_goal_checks(
        self,
        statement: str,
        success_criteria: Optional[str] = None,
        target_kind: str = "goal",
    ) -> List[Dict[str, Any]]:
        """Break an abstract goal and optional success_criteria into concrete capability checks."""
        checks: List[Dict[str, Any]] = []
        seen_clauses: set[str] = set()

        def _split_clauses(raw: Optional[str]) -> List[str]:
            if not raw or not raw.strip():
                return []
            # Split on bullets, newlines, semicolons, and conjunction boundaries
            parts = re.split(r"(?:\r?\n|;|^\s*[-*]\s+|\s+(?:and|when|so that|while|with)\s+)", raw, flags=re.IGNORECASE | re.MULTILINE)
            out = []
            for p in parts:
                cleaned = re.sub(r"^\s*\d+[\.\)]\s*", "", (p or "").strip(" \t-,.;:"))
                if len(cleaned) >= 6:
                    out.append(cleaned)
            return out or [raw.strip()]

        for kind_label, raw_text in (("capability", statement), ("criteria", success_criteria)):
            for idx, clause in enumerate(_split_clauses(raw_text)):
                norm = clause.lower()
                if norm in seen_clauses:
                    continue
                seen_clauses.add(norm)
                kws = self._extract_goal_keywords(clause)
                requires_tests = bool(re.search(r"\b(test|tests|tested|pytest|unittest|verif|benchmark|assert)\w*\b", norm))
                requires_clean = bool(re.search(r"\b(zero|0|no)\s+(?:blocking|critical|finding|findings|error|bugs?|gaps?)\b|\bclean\b|\baudit\b", norm))
                checks.append({
                    "id": f"chk_{kind_label}_{idx + 1}",
                    "kind": kind_label,
                    "text": clause,
                    "keywords": kws[:8],
                    "requires_tests": requires_tests,
                    "requires_clean_audit": requires_clean,
                })
        return checks[:10]

    def _find_matches_for_keywords(
        self,
        cur: sqlite3.Cursor,
        clause_text: str,
        keywords: List[str],
        rid: Optional[str],
    ) -> Tuple[List[Tuple[str, str, str, str]], bool]:
        """Return ([(qualified_name, file_path, entity_type, source_code), ...], used_semantic)."""
        frag, fparams = self._scope("nodes", rid)
        code_only = " AND entity_type IN ('Function','Class','Method','Module','File')"
        rows_out: List[Tuple[str, str, str, str]] = []
        seen_keys: set[Tuple[str, str]] = set()
        used_semantic = False

        # 1. Prefer RepoIntel hybrid search (bridges abstract goal phrasing to concrete symbols)
        sem = self._semantic_candidates(clause_text, k=6)
        if sem:
            used_semantic = True
            for sym_desc in sem[:5]:
                sym_name = sym_desc.split(" ")[0]
                cur.execute(
                    "SELECT qualified_name, file_path, entity_type, source_code FROM nodes "
                    "WHERE qualified_name = ?" + code_only + frag + " LIMIT 2",
                    [sym_name] + fparams,
                )
                fetched = cur.fetchall()
                if fetched:
                    for qn, fp, et, sc in fetched:
                        if (qn, fp) not in seen_keys:
                            seen_keys.add((qn, fp))
                            rows_out.append((qn, fp, et, sc or ""))
                else:
                    # Preserve semantic candidate even if file/function format came from HTTP index
                    m_fp = re.search(r"in\s+([^)]+)\)$", sym_desc)
                    fp = m_fp.group(1) if m_fp else ""
                    if (sym_name, fp) not in seen_keys:
                        seen_keys.add((sym_name, fp))
                        rows_out.append((sym_name, fp, "Function", "# matched via semantic search"))

        if rows_out or not keywords:
            return rows_out, used_semantic

        # 2. Rank keywords by SQLite IDF-like specificity across symbol names & paths
        scored: List[Tuple[int, str]] = []
        for kw in keywords[:10]:
            cur.execute(
                "SELECT count(*) FROM nodes WHERE (qualified_name LIKE ? OR file_path LIKE ?)"
                + code_only + frag,
                [f"%{kw}%", f"%{kw}%"] + fparams,
            )
            n = cur.fetchone()[0]
            if n:
                scored.append((n, kw))

        for _n, kw in sorted(scored)[:4]:
            cur.execute(
                "SELECT qualified_name, file_path, entity_type, source_code FROM nodes "
                "WHERE (qualified_name LIKE ? OR file_path LIKE ?)" + code_only + frag + " LIMIT 4",
                [f"%{kw}%", f"%{kw}%"] + fparams,
            )
            for qn, fp, et, sc in cur.fetchall():
                if (qn, fp) not in seen_keys:
                    seen_keys.add((qn, fp))
                    rows_out.append((qn, fp, et, sc or ""))

        # 3. Abstract goals often appear in docstrings/comments/error messages even when not in symbol names
        if not rows_out:
            for kw in keywords[:5]:
                if len(kw) < 4:
                    continue
                cur.execute(
                    "SELECT qualified_name, file_path, entity_type, source_code FROM nodes "
                    "WHERE source_code LIKE ?" + code_only + frag + " LIMIT 4",
                    [f"%{kw}%"] + fparams,
                )
                for qn, fp, et, sc in cur.fetchall():
                    if (qn, fp) not in seen_keys:
                        seen_keys.add((qn, fp))
                        rows_out.append((qn, fp, et, sc or ""))
                if rows_out:
                    break

        return rows_out, used_semantic

    def check_goal_code_maturity(
        self,
        goal_id: str,
        statement: str,
        target_kind: str = "goal",
        success_criteria: Optional[str] = None,
    ) -> CodeMaturityAssessment:
        """Inspect the codebase AST, call-graph, tests, findings & capability checks.

        Maturity ladder rules:
        - no_candidate: No symbols or code files match the goal's concepts.
        - scaffolded: File or placeholder exists, but function bodies or classes are empty/stubbed.
        - candidate: Matching functions/classes exist with non-trivial implementation.
        - connected: The candidate symbol is called by other application components.
        - tested: Tests exist referencing or importing the candidate symbol.
        """
        checks = self.decompose_goal_checks(statement, success_criteria=success_criteria, target_kind=target_kind)
        keywords = self._extract_goal_keywords(f"{statement} {success_criteria or ''}")
        rid = self.resolve_repo_id()
        conn = self._get_connection()
        if not conn or not keywords:
            return CodeMaturityAssessment(
                goal_id=goal_id,
                statement=statement,
                suggested_maturity="no_candidate",
                candidate_symbols=[],
                callers_found=[],
                tests_found=[],
                evidence_summary="RepoIntel database not reachable or statement empty.",
                divergence_risk="unknown",
                checks_passed=0,
                checks_total=len(checks),
                check_details=[],
                metrics={},
            )

        try:
            cur = conn.cursor()
            candidate_symbols: List[str] = []
            non_stub_symbols: List[str] = []
            callers_found: List[str] = []
            tests_found: List[str] = []
            check_details: List[Dict[str, Any]] = []
            tfrag, tparams = self._scope("nodes", rid)

            for chk in checks:
                matches, used_semantic = self._find_matches_for_keywords(cur, chk["text"], chk["keywords"], rid)
                chk_non_stub = []
                chk_callers = []
                chk_tests = []
                chk_files = set()

                for qn, fp, et, sc in matches:
                    s_desc = f"{qn} ({et} in {fp})"
                    if s_desc not in candidate_symbols:
                        candidate_symbols.append(s_desc)
                    is_stub = (et in ("Function", "Method", "Class")) and self._is_trivial_stub(sc)
                    if not is_stub:
                        chk_non_stub.append(s_desc)
                        if s_desc not in non_stub_symbols:
                            non_stub_symbols.append(s_desc)
                    if fp:
                        chk_files.add(fp)

                    # Callers for this symbol
                    cur.execute(
                        "SELECT src.qualified_name, src.file_path FROM edges e "
                        "JOIN nodes tgt ON e.target_id = tgt.id "
                        "JOIN nodes src ON e.source_id = src.id "
                        "WHERE tgt.qualified_name = ? AND e.edge_type = 'CALLS'" + self._scope("src", rid)[0] + " LIMIT 3",
                        [qn] + self._scope("src", rid)[1],
                    )
                    for cr_qn, cr_fp in cur.fetchall():
                        c_desc = f"{cr_qn} in {cr_fp}"
                        if "test" in (cr_fp or "").lower():
                            if c_desc not in tests_found:
                                tests_found.append(c_desc)
                            if c_desc not in chk_tests:
                                chk_tests.append(c_desc)
                        else:
                            if c_desc not in callers_found:
                                callers_found.append(c_desc)
                            if c_desc not in chk_callers:
                                chk_callers.append(c_desc)

                    # Tests referencing symbol
                    if len(qn) >= 4:
                        cur.execute(
                            "SELECT qualified_name, file_path FROM nodes "
                            "WHERE entity_type IN ('Function','Method','Class') "
                            "AND (file_path LIKE '%test%') AND source_code LIKE ?" + tfrag + " LIMIT 3",
                            [f"%{qn}%"] + tparams,
                        )
                        for tr_qn, tr_fp in cur.fetchall():
                            t_desc = f"{tr_qn} in {tr_fp}"
                            if t_desc not in tests_found:
                                tests_found.append(t_desc)
                            if t_desc not in chk_tests:
                                chk_tests.append(t_desc)

                blocking_in_files = 0
                if chk_files:
                    placeholders = ",".join("?" for _ in chk_files)
                    f_query = (
                        f"SELECT count(*) FROM findings WHERE severity IN ('blocker','critical') "
                        f"AND file_path IN ({placeholders})"
                    )
                    f_args: List[Any] = sorted(chk_files)
                    if rid:
                        f_query += " AND repo_id = ?"
                        f_args.append(rid)
                    cur.execute(f_query, f_args)
                    blocking_in_files = int(cur.fetchone()[0] or 0)

                if not matches:
                    c_status, c_score, c_passed = "missing", 0.0, False
                elif not chk_non_stub:
                    c_status, c_score, c_passed = "scaffolded", 0.25, False
                elif chk_tests and blocking_in_files == 0:
                    c_status, c_score, c_passed = "verified_clean", 1.0, True
                elif chk_tests:
                    c_status, c_score, c_passed = "tested", 0.85, True
                elif chk_callers:
                    c_status, c_score, c_passed = "connected", 0.75, not chk["requires_tests"]
                else:
                    c_status, c_score, c_passed = "implemented", 0.6, not chk["requires_tests"]

                if chk["requires_clean_audit"] and blocking_in_files > 0:
                    c_passed = False
                    c_score = min(c_score, 0.4)

                check_details.append({
                    "id": chk["id"],
                    "kind": chk["kind"],
                    "text": chk["text"][:160],
                    "status": c_status,
                    "passed": bool(c_passed),
                    "score": c_score,
                    "used_semantic": used_semantic,
                    "matched_symbols": [f"{m[0]} ({m[2]} in {m[1]})" for m in matches[:3]],
                    "blocking_findings": blocking_in_files,
                })
        finally:
            conn.close()

        # Repository-wide quantitative health & gap metrics (for measurable progress tracking)
        health = self.get_health_score(repo_id=rid)
        gaps = self.list_gaps(repo_id=rid, limit=50)
        checks_passed = sum(1 for c in check_details if c["passed"])
        checks_total = len(check_details)
        mean_check_score = round(sum(c["score"] for c in check_details) / checks_total, 4) if checks_total else 0.0
        by_sev = health.get("by_severity") or {}
        metrics = {
            "checks_passed": checks_passed,
            "checks_total": checks_total,
            "check_pass_rate": round(checks_passed / checks_total, 4) if checks_total else 0.0,
            "mean_check_score": mean_check_score,
            "candidate_count": len(candidate_symbols),
            "caller_count": len(callers_found),
            "test_count": len(tests_found),
            "health_score": health.get("health_score"),
            "health_grade": health.get("grade"),
            "total_findings": health.get("total_findings", 0),
            "blocker_critical_findings": int(by_sev.get("blocker", 0)) + int(by_sev.get("critical", 0)),
            "gap_findings": int(gaps.get("total_gaps", 0)),
        }

        if not candidate_symbols:
            suggested = "no_candidate"
            summary = "No AST symbols or files found matching the goal or its decomposed checks."
        elif not non_stub_symbols:
            suggested = "scaffolded"
            summary = f"Found {len(candidate_symbols)} stub/placeholder symbol(s); implementation incomplete ({checks_passed}/{checks_total} checks passed)."
        elif tests_found:
            suggested = "tested"
            summary = (
                f"{checks_passed}/{checks_total} capability checks passed "
                f"(health {metrics['health_score']}, {len(tests_found)} test refs, {len(callers_found)} callers)."
            )
        elif callers_found:
            suggested = "connected"
            summary = (
                f"{checks_passed}/{checks_total} capability checks passed; "
                f"called in {len(callers_found)} places (needs tests)."
            )
        else:
            suggested = "candidate"
            summary = (
                f"{checks_passed}/{checks_total} checks matched {len(candidate_symbols)} non-stub symbols, "
                "but no callers or tests linked yet."
            )

        return CodeMaturityAssessment(
            goal_id=goal_id,
            statement=statement,
            suggested_maturity=suggested,
            candidate_symbols=candidate_symbols[:8],
            callers_found=callers_found[:6],
            tests_found=tests_found[:6],
            evidence_summary=summary,
            divergence_risk="low" if (non_stub_symbols and (callers_found or tests_found)) else "scaffold_only",
            checks_passed=checks_passed,
            checks_total=checks_total,
            check_details=check_details,
            metrics=metrics,
        )

    # -----------------------------------------------------------------------
    # 5. ML Quality Predictions, Git History & Repository Context
    # -----------------------------------------------------------------------

    def get_quality_predictions(
        self,
        file_path: Optional[str] = None,
        prediction_type: Optional[str] = None,
        min_probability: float = 0.0,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Get ML defect, technical debt, and smell predictions generated by RepoIntel."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            query = (
                "SELECT id, file_path, symbol_id, prediction_type, probability, confidence "
                "FROM quality_predictions WHERE probability >= ?"
            )
            params: List[Any] = [min_probability]
            if file_path:
                query += " AND file_path LIKE ?"
                params.append(f"%{file_path}%")
            if prediction_type:
                query += " AND prediction_type = ?"
                params.append(prediction_type)
            query += " ORDER BY probability DESC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            return [
                {
                    "id": r[0],
                    "file_path": r[1],
                    "symbol_id": r[2],
                    "prediction_type": r[3],
                    "probability": round(r[4], 3) if r[4] else 0.0,
                    "confidence": round(r[5], 3) if r[5] else 0.0,
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def get_git_commits(
        self, file_path: Optional[str] = None, author: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get recent Git commits, authors, commit messages, and diff sizes."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            query = "SELECT hash, author, timestamp, message, additions, deletions, files_changed FROM commits WHERE 1=1"
            params: List[Any] = []
            if author:
                query += " AND author LIKE ?"
                params.append(f"%{author}%")
            if file_path:
                query += " AND files_changed LIKE ?"
                params.append(f"%{file_path}%")
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            commits = []
            for r in cur.fetchall():
                try:
                    files = json.loads(r[6]) if r[6] else []
                except Exception:
                    files = []
                commits.append({
                    "hash": r[0],
                    "author": r[1],
                    "timestamp": r[2],
                    "message": r[3],
                    "additions": r[4],
                    "deletions": r[5],
                    "files_changed": files,
                })
            return commits
        finally:
            conn.close()

    def get_git_hotspots(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get the most frequently changed codebase files (churn hotspots) across commit history."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT file_path, count(*) as change_cnt
                FROM (
                    SELECT json_each.value as file_path
                    FROM commits, json_each(commits.files_changed)
                )
                WHERE file_path != ''
                GROUP BY file_path
                ORDER BY change_cnt DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [{"file_path": r[0], "change_count": r[1]} for r in cur.fetchall()]
        finally:
            conn.close()

    def get_issues(
        self, state: Optional[str] = None, label: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get GitHub issues associated with the repository."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            query = "SELECT number, title, body, labels, state, created_at, closed_at FROM issues WHERE 1=1"
            params: List[Any] = []
            if state:
                query += " AND state = ?"
                params.append(state)
            if label:
                query += " AND labels LIKE ?"
                params.append(f"%{label}%")
            query += " ORDER BY number ASC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            issues = []
            for r in cur.fetchall():
                try:
                    labels = json.loads(r[3]) if r[3] else []
                except Exception:
                    labels = []
                issues.append({
                    "number": r[0],
                    "title": r[1],
                    "body": (r[2] or "")[:300],
                    "labels": labels,
                    "state": r[4],
                    "created_at": r[5],
                    "closed_at": r[6],
                })
            return issues
        finally:
            conn.close()

    def get_pull_requests(self, state: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Get GitHub pull requests associated with the repository."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            query = "SELECT number, title, body, reviewers, state, merged_at, files_changed FROM pull_requests WHERE 1=1"
            params: List[Any] = []
            if state:
                query += " AND state = ?"
                params.append(state)
            query += " ORDER BY number ASC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            prs = []
            for r in cur.fetchall():
                try:
                    reviewers = json.loads(r[3]) if r[3] else []
                except Exception:
                    reviewers = []
                prs.append({
                    "number": r[0],
                    "title": r[1],
                    "body": (r[2] or "")[:300],
                    "reviewers": reviewers,
                    "state": r[4],
                    "merged_at": r[5],
                })
            return prs
        finally:
            conn.close()
