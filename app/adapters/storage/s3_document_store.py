"""Adapter S3 (boto3) do DocumentStore.

Compatível com:
  - AWS S3 (produção): `s3_endpoint_url=""` (default), credenciais via env/IAM
  - MinIO (desenvolvimento local): `s3_endpoint_url="http://minio:9000"`, root user/pass

O cliente boto3 é síncrono — todas as chamadas vão para `asyncio.to_thread`
para não bloquear o event loop. Considerar aiobotocore se latência S3 dominar
em pico de extração PDF (Fase 4); por ora, simplicidade > otimização.

Bucket é criado on-demand se não existir (idempotente, modo dev).
"""

from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
from typing import BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.ports.payments.document_store import StoredDocument


class S3DocumentStore:
    """Implementação S3 (AWS ou MinIO) do DocumentStore."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region,
            # `s3v4` é exigido por MinIO e AWS S3 em regiões mais novas.
            config=BotoConfig(signature_version="s3v4"),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """Cria bucket se não existir (idempotente, fail-safe em dev)."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code", "")
            if err_code in ("404", "NoSuchBucket"):
                self._client.create_bucket(Bucket=self.bucket)
            elif err_code == "403":
                # Bucket existe mas sem permissão de head — assume que está OK
                pass
            else:
                raise

    async def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
    ) -> StoredDocument:
        if isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        else:
            payload = data.read()
        sha = hashlib.sha256(payload).hexdigest()

        def _put() -> None:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=payload,
                ContentType=content_type,
                # SHA-256 vai como metadata customizada — útil pra integridade
                # sem precisar baixar o objeto inteiro.
                Metadata={"sha256": sha},
            )

        await asyncio.to_thread(_put)

        return StoredDocument(
            key=key,
            size_bytes=len(payload),
            sha256=sha,
            content_type=content_type,
            storage_uri=f"s3://{self.bucket}/{key}",
        )

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()

        try:
            return await asyncio.to_thread(_get)
        except ClientError as e:
            err = e.response.get("Error", {}).get("Code", "")
            if err in ("NoSuchKey", "404"):
                raise FileNotFoundError(f"S3 key not found: {key}") from e
            raise

    async def exists(self, key: str) -> bool:
        def _exists() -> bool:
            try:
                self._client.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as e:
                err = e.response.get("Error", {}).get("Code", "")
                if err in ("NoSuchKey", "404"):
                    return False
                raise

        return await asyncio.to_thread(_exists)

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            # delete_object é idempotente — não levanta se a key não existe
            self._client.delete_object(Bucket=self.bucket, Key=key)

        await asyncio.to_thread(_delete)

    async def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        def _gen() -> str:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )

        return await asyncio.to_thread(_gen)
