"""Factory do DocumentStore — escolhe adapter conforme `settings.document_store_mode`.

Single source of truth para resolver o adapter. Não recriar a cada uso:
o factory mantém instância singleton por settings (lru_cache).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.core.ports.payments.document_store import DocumentStore


@lru_cache(maxsize=1)
def get_document_store() -> DocumentStore:
    """Retorna o adapter configurado. Singleton por processo."""
    s = get_settings()
    mode = (s.document_store_mode or "filesystem").lower()

    if mode == "filesystem":
        from app.adapters.storage.filesystem_document_store import FilesystemDocumentStore

        root = s.document_store_fs_root or (Path(__file__).resolve().parents[3] / "data" / "documents")
        return FilesystemDocumentStore(root=root)

    if mode == "s3":
        from app.adapters.storage.s3_document_store import S3DocumentStore

        return S3DocumentStore(
            bucket=s.s3_bucket,
            endpoint_url=s.s3_endpoint_url or None,
            access_key=s.s3_access_key or None,
            secret_key=s.s3_secret_key or None,
            region=s.s3_region or "us-east-1",
        )

    raise ValueError(
        f"document_store_mode inválido: '{mode}'. Use 'filesystem' ou 's3'."
    )
