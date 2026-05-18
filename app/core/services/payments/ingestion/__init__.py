"""Loader de ingestão para o bounded-context payments.

  registry.py — REPO_REGISTRY (target_entity → repo class)
  loader.py   — load_source (orquestra parser+projection+repo+IngestionRun)
"""

from app.core.services.payments.ingestion.loader import (
    LoadResult,
    load_source,
    load_source_by_path,
)
from app.core.services.payments.ingestion.registry import (
    REPO_REGISTRY,
    resolve_repo,
)

__all__ = [
    "REPO_REGISTRY",
    "resolve_repo",
    "LoadResult",
    "load_source",
    "load_source_by_path",
]
