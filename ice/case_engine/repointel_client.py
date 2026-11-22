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

# Default locations where repointel knowledge base lives
DEFAULT_REPOINTEL_PATHS = [
    r"C:\Users\sreen\OneDrive\Pictures\Bharath Gitam\ai !411\repo intel ai\repointel-ai-2",
    os.getenv("REPOINTEL_DIR", ""),
]


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


class RepoIntelClient:
    """Selective, read-only interface to RepoIntel knowledge base and tools."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or find_repointel_db()
        if not self.db_path or not os.path.exists(self.db_path):
            logger.warning(
                "RepoIntel database not found. Tool calls will return empty mock results."
            )

    @property
    def is_available(self) -> bool:
        return bool(self.db_path and os.path.exists(self.db_path))

    def _get_connection(self) -> Optional[sqlite3.Connection]:
        if not self.is_available:
            return None
        # Open in URI read-only mode so RepoIntel files are never locked or modified
        uri = f"file:{Path(self.db_path).resolve().as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True)

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
            if repo_id:
                cur.execute("SELECT count(*) FROM files WHERE repo_id = ?", (repo_id,))
            else:
                cur.execute("SELECT count(*) FROM files")
            file_count = cur.fetchone()[0]

            # Symbol counts by entity_type
            cur.execute(
                "SELECT entity_type, count(*) FROM nodes WHERE entity_type != 'Repository' GROUP BY entity_type"
            )
            entity_counts = dict(cur.fetchall())

            # Call edges count
            cur.execute("SELECT count(*) FROM edges WHERE edge_type = 'CALLS'")
            calls_count = cur.fetchone()[0]

            # Top hub symbols (symbols with highest in-degree / most called)
            cur.execute(
                """
                SELECT n.qualified_name, n.file_path, count(e.source_id) as callers
                FROM edges e
                JOIN nodes n ON e.target_id = n.id
                WHERE e.edge_type = 'CALLS'
                GROUP BY n.id
                ORDER BY callers DESC
                LIMIT 10
                """
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
        """Search code implementations in nodes and documentation in documents."""
        conn = self._get_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            pattern = f"%{query}%"
            cur.execute(
                """
                SELECT id, qualified_name, entity_type, file_path, source_code
                FROM nodes
                WHERE source_code LIKE ? OR qualified_name LIKE ?
                LIMIT ?
                """,
                (pattern, pattern, limit),
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
                    WHERE content LIKE ?
                    LIMIT ?
                    """,
                    (pattern, limit - len(hits)),
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

    def search_symbols(self, query: str, limit: int = 10) -> List[CodeSymbol]:
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
                WHERE n.qualified_name LIKE ? OR n.file_path LIKE ?
                ORDER BY caller_cnt DESC
                LIMIT ?
                """,
                (pattern, pattern, limit),
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

    def get_callers(self, symbol_name: str) -> List[Dict[str, str]]:
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
                  AND e.edge_type = 'CALLS'
                LIMIT 20
                """,
                (symbol_name, f"%{symbol_name}%"),
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
        self, symbol_name: str, max_depth: int = 5, limit: int = 20
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
            GROUP BY n.id, n.qualified_name, n.entity_type, n.file_path
            ORDER BY path_length ASC, num_call_paths DESC
            LIMIT ?
            """
            cur.execute(query, (symbol_name, symbol_name, max_depth, limit))
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

    def check_goal_code_maturity(
        self, goal_id: str, statement: str, target_kind: str = "goal"
    ) -> CodeMaturityAssessment:
        """Inspect the codebase AST & call-graph to assess goal maturity.

        Maturity ladder rules:
        - no_candidate: No symbols or code files match the goal's concepts.
        - scaffolded: File or placeholder exists, but function bodies or classes are empty/stubbed.
        - candidate: Matching functions/classes exist with non-trivial implementation.
        - connected: The candidate symbol is called by other application components.
        - tested: Tests exist referencing or importing the candidate symbol.
        """
        # Extract keywords from the goal statement
        raw_words = re.findall(r"[a-z0-9_]{3,}", statement.lower())
        stop_words = {"the", "and", "for", "with", "must", "should", "will", "all", "project", "system"}
        keywords = [w for w in raw_words if w not in stop_words]

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
            )

        try:
            cur = conn.cursor()
            candidate_symbols: List[str] = []
            callers_found: List[str] = []
            tests_found: List[str] = []

            # 1. Search for matching nodes
            for kw in keywords[:4]:
                cur.execute(
                    """
                    SELECT qualified_name, file_path, entity_type
                    FROM nodes
                    WHERE qualified_name LIKE ? OR file_path LIKE ?
                    LIMIT 5
                    """,
                    (f"%{kw}%", f"%{kw}%"),
                )
                for r in cur.fetchall():
                    s_desc = f"{r[0]} ({r[2]} in {r[1]})"
                    if s_desc not in candidate_symbols:
                        candidate_symbols.append(s_desc)

            # 2. Check if candidate symbols have callers
            for s in candidate_symbols[:3]:
                sym_name = s.split(" ")[0]
                cur.execute(
                    """
                    SELECT src.qualified_name, src.file_path
                    FROM edges e
                    JOIN nodes tgt ON e.target_id = tgt.id
                    JOIN nodes src ON e.source_id = src.id
                    WHERE tgt.qualified_name = ? AND e.edge_type = 'CALLS'
                    LIMIT 3
                    """,
                    (sym_name,),
                )
                for r in cur.fetchall():
                    c_desc = f"{r[0]} in {r[1]}"
                    if "test" in r[1].lower():
                        tests_found.append(c_desc)
                    elif c_desc not in callers_found:
                        callers_found.append(c_desc)

            # 3. Determine suggested maturity
            if not candidate_symbols:
                suggested = "no_candidate"
                summary = "No AST symbols or files found matching the goal's keywords."
            elif tests_found:
                suggested = "tested"
                summary = f"Candidate code found in {len(candidate_symbols)} symbols with tests in {len(tests_found)} test locations."
            elif callers_found:
                suggested = "connected"
                summary = f"Candidate code found and actively called by {len(callers_found)} callers."
            else:
                suggested = "candidate"
                summary = f"Candidate code found in {len(candidate_symbols)} symbols, but not yet called by application workflows."

            return CodeMaturityAssessment(
                goal_id=goal_id,
                statement=statement,
                suggested_maturity=suggested,
                candidate_symbols=candidate_symbols[:6],
                callers_found=callers_found[:4],
                tests_found=tests_found[:4],
                evidence_summary=summary,
                divergence_risk="low" if (callers_found or tests_found) else "scaffold_only",
            )
        finally:
            conn.close()

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
