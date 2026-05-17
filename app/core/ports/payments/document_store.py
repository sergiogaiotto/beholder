"""Port `DocumentStore` — armazenamento de PDFs/anexos do domínio payments.

Contrato hexagonal: o core (extraction_service, audit) usa este Protocol;
os adapters concretos (FilesystemDocumentStore, S3DocumentStore) ficam em
`app/adapters/storage/`. Trocar adapter é trocar a config (`document_store_mode`).

Semântica de `key`:

  - String opaca para o caller (não interpretar). Convenção interna do adapter
    pode incluir prefix por domínio + UUID + extensão, ex.:
    `payments/contracts/2026/01/<uuid>.pdf`. Adapters mantêm consistência.

  - Imutável: uma vez publicada, a chave não muda. Não há rename.

Idempotência:

  - `put(key, data)` com a mesma key + mesmo conteúdo: no-op (sucesso).
  - `put(key, data)` com a mesma key + conteúdo diferente: comportamento
    DEFINIDO pelo adapter. FS sobrescreve; S3 versiona (se bucket configurado).
    Caller deve garantir keys únicas pra evitar ambiguidade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """Metadata retornada após `put()` bem-sucedido."""
    key: str
    size_bytes: int
    sha256: str
    content_type: str
    storage_uri: str   # ex.: "file:///data/.../pdf" ou "s3://bucket/key"


class DocumentStore(Protocol):
    """Interface de armazenamento de documentos imutáveis (PDFs assinados, anexos).

    Métodos são `async` porque adapters (S3) fazem I/O bloqueante; FS adapter
    usa `aiofiles` ou run_in_executor para não bloquear o event loop.
    """

    async def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
    ) -> StoredDocument:
        """Armazena `data` na chave `key`. Retorna metadata."""
        ...

    async def get(self, key: str) -> bytes:
        """Lê o conteúdo completo. Levanta `FileNotFoundError` se ausente."""
        ...

    async def exists(self, key: str) -> bool:
        """True se a chave existe."""
        ...

    async def delete(self, key: str) -> None:
        """Remove a chave. No-op se não existir."""
        ...

    async def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        """URL temporária para download direto (sem passar pelo app).

        FS adapter retorna `file://`; S3 retorna URL HTTP assinada. Use
        para servir PDFs pesados sem ocupar workers FastAPI.
        """
        ...
