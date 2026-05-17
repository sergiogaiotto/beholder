"""Adapter FS do DocumentStore.

Armazena PDFs em diretório local (default: `<repo>/data/documents/`). Útil em
desenvolvimento e em testes — substitui S3 sem precisar de MinIO rodando.

Layout em disco:
    <root>/<key>             (arquivos binários crus)
    <root>/<key>.meta.json   (sha256, size, content_type, criado_em)

A separação meta/conteúdo permite `exists()` sem precisar abrir o binário e
auditoria sem ler o PDF inteiro.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.core.ports.payments.document_store import StoredDocument


class FilesystemDocumentStore:
    """Implementação local do DocumentStore."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        # Bloqueia escape (../) — todas as keys ficam dentro de `root`
        candidate = (self.root / key).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as e:
            raise ValueError(f"Key '{key}' escapes storage root") from e
        return candidate

    def _meta_path_for(self, key: str) -> Path:
        p = self._path_for(key)
        return p.with_suffix(p.suffix + ".meta.json")

    async def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
    ) -> StoredDocument:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        else:
            payload = data.read()

        sha = hashlib.sha256(payload).hexdigest()

        # I/O em thread pool — não bloqueia o event loop
        def _write() -> None:
            path.write_bytes(payload)
            meta = {
                "key": key,
                "size_bytes": len(payload),
                "sha256": sha,
                "content_type": content_type,
                "stored_at": datetime.now(timezone.utc).isoformat(),
            }
            self._meta_path_for(key).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        await asyncio.to_thread(_write)

        return StoredDocument(
            key=key,
            size_bytes=len(payload),
            sha256=sha,
            content_type=content_type,
            storage_uri=path.as_uri(),
        )

    async def get(self, key: str) -> bytes:
        path = self._path_for(key)
        if not path.is_file():
            raise FileNotFoundError(f"Document key not found: {key}")
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        path = self._path_for(key)
        return await asyncio.to_thread(path.is_file)

    async def delete(self, key: str) -> None:
        path = self._path_for(key)
        meta_path = self._meta_path_for(key)

        def _delete() -> None:
            path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)

    async def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        # FS não tem URLs assinadas — retorna file:// (válido para apps locais).
        # Caller que precise de URL pública deve usar S3 adapter.
        return self._path_for(key).as_uri()
