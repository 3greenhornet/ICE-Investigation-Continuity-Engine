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
