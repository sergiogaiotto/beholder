"""Adapters de DocumentStore (FS, S3) + factory."""

from app.adapters.storage.factory import get_document_store

__all__ = ["get_document_store"]
