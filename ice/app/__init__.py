"""app/ — FastAPI application layer for the Investigation Continuity Engine.

Thin HTTP surface over case_engine/ (spec Part 4 §13). No business logic
lives in this package: every handler delegates to a case_engine/ function
through the service shims in app/deps.py, and every response body is a
Pydantic model defined in app/schemas.py.
"""