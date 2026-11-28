"""Unit tests ensuring ICE Streamlit UI modules are valid, pure-HTTP, and route-aligned."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path


def test_streamlit_ui_modules_import_and_never_import_case_engine():
    ui_root = Path(__file__).resolve().parents[2] / "ui"
    py_files = list(ui_root.rglob("*.py"))
    assert len(py_files) >= 6

    for p in py_files:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("case_engine"), f"{p} imported {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("case_engine"), f"{p} imported {node.module}"

    for mod_name in (
        "ui.api_client",
        "ui.screens.case_overview",
        "ui.screens.goals_board",
        "ui.screens.hypothesis_board",
        "ui.screens.evidence_graph",
        "ui.screens.discovery_lab",
        "ui.screens.investigation_trace",
        "ui.screens.decision_debugger",
    ):
        mod = importlib.import_module(mod_name)
        assert mod is not None


def test_ui_api_client_endpoints_exist_in_fastapi_app():
    import sys

    ice_root = str(Path(__file__).resolve().parents[2])
    if ice_root not in sys.path:
        sys.path.insert(0, ice_root)
    from app.main import app

    registered = set(app.openapi()["paths"].keys())
    expected_paths = {
        "/api/v1/cases",
        "/api/v1/cases/{case_id}",
        "/api/v1/cases/{case_id}/summary",
        "/api/v1/cases/{case_id}/goals",
        "/api/v1/goals/{goal_id}/maturity/sync",
        "/api/v1/goals/{goal_id}/decompose",
        "/api/v1/cases/{case_id}/goal-reconciliation",
        "/api/v1/cases/{case_id}/goals/adjacent-proposals",
        "/api/v1/cases/{case_id}/evidence/web-research",
        "/api/v1/discovery",
        "/api/v1/discovery/fields",
        "/api/v1/discovery/mechanisms/{mechanism_id}/decision",
        "/api/v1/mechanisms",
        "/api/v1/repointel/lab-proposals",
    }
    missing = expected_paths - registered
    assert not missing, f"UI API endpoints missing from FastAPI app: {missing}"
