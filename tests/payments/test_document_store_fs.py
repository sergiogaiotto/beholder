"""Smoke tests do FilesystemDocumentStore — valida contrato do Port.

NÃO requer DB nem Redis nem MinIO. Roda em qualquer ambiente onde o
filesystem é gravável (tmp_path do pytest).
"""

from __future__ import annotations

import hashlib

import pytest

from app.adapters.storage.filesystem_document_store import FilesystemDocumentStore


@pytest.mark.asyncio
async def test_put_and_get_roundtrip(tmp_path):
    store = FilesystemDocumentStore(root=tmp_path)
    payload = b"contract bytes \xff\x00\xfe"
    key = "payments/contracts/2026/test.pdf"

    stored = await store.put(key, payload, content_type="application/pdf")

    assert stored.key == key
    assert stored.size_bytes == len(payload)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.content_type == "application/pdf"
    assert stored.storage_uri.startswith("file://")

    fetched = await store.get(key)
    assert fetched == payload


@pytest.mark.asyncio
async def test_exists_true_then_false_after_delete(tmp_path):
    store = FilesystemDocumentStore(root=tmp_path)
    key = "payments/ephemeral.bin"
    await store.put(key, b"abc")

    assert await store.exists(key) is True

    await store.delete(key)
    assert await store.exists(key) is False

    # delete é idempotente — segunda chamada não levanta
    await store.delete(key)


@pytest.mark.asyncio
async def test_get_missing_raises_filenotfound(tmp_path):
    store = FilesystemDocumentStore(root=tmp_path)
    with pytest.raises(FileNotFoundError):
        await store.get("inexistente.pdf")


@pytest.mark.asyncio
async def test_key_escape_blocked(tmp_path):
    """Path traversal via `../` deve ser bloqueado."""
    store = FilesystemDocumentStore(root=tmp_path)
    with pytest.raises(ValueError, match="escapes storage root"):
        await store.put("../escape.pdf", b"malicious")


@pytest.mark.asyncio
async def test_meta_json_written(tmp_path):
    store = FilesystemDocumentStore(root=tmp_path)
    key = "with_meta.pdf"
    await store.put(key, b"hello", content_type="application/pdf")

    meta_file = tmp_path / f"{key}.meta.json"
    assert meta_file.is_file()
    import json
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["key"] == key
    assert meta["size_bytes"] == 5
    assert meta["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert meta["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_presigned_url_is_file_uri(tmp_path):
    store = FilesystemDocumentStore(root=tmp_path)
    key = "x.pdf"
    await store.put(key, b"abc")
    url = await store.presigned_url(key)
    assert url.startswith("file://")
