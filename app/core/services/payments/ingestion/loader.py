"""Loader principal — orquestra parser + projection + repo + IngestionRun.

Fluxo de `load_source`:

  1. Carrega ProjectionConfig do YAML (validação Pydantic — fail-fast).
  2. Cria IngestionRun(status=pending), depois marca running.
  3. Abre o source (XLSX via openpyxl read-only, OU TXT MSRV5 streaming).
  4. Itera src → projection → batch. Quando batch atinge `load.batch_size`,
     invoca repo.bulk_insert / bulk_upsert e zera batch.
  5. Flush final do batch parcial.
  6. mark_completed (com rows_*) OU mark_failed (com exception message).

`ingestion_run_id` é injetado automaticamente nos models que têm o campo,
via patch do `defaults` do config (sem mutar o YAML). Models sem o campo
(SupplierBridge, ContractMaster) não recebem — rastreabilidade fica só
no IngestionRun.metadata.target_table.

`load_source_by_path` é o ponto de entrada conveniente quando você tem
caminho de arquivo + nome da projeção. `load_source` aceita também src_iter
custom (testes, sources alternativos).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.adapters.db.repositories.payments import PgIngestionRunRepository
from app.adapters.sap.parsers import iter_xlsx_rows, parse_msrv5
from app.adapters.sap.projections import (
    PROJECTIONS_DIR,
    ProjectionConfig,
    load_projection,
    project,
    resolve_entity,
)
from app.core.domain.payments import IngestionRun
from app.core.services.payments.ingestion.registry import (
    resolve_repo,
    target_table_for,
)


@dataclass(frozen=True)
class LoadResult:
    """Resultado de uma execução de load_source."""

    run: IngestionRun
    rows_read: int
    rows_inserted: int
    rows_failed: int


async def load_source_by_path(
    source_path: Path | str,
    projection_name: str,
    *,
    triggered_by_user_id: UUID | None = None,
    projections_dir: Path | str | None = None,
) -> LoadResult:
    """Carrega 1 arquivo completo via projeção declarada em configs/.

    Args:
      source_path: caminho do XLSX ou TXT.
      projection_name: nome do YAML (sem extensão). Ex: 'wf_payment'.
      triggered_by_user_id: opcional, fica em IngestionRun.triggered_by_user_id.
      projections_dir: override do dir de YAMLs (default: PROJECTIONS_DIR).
    """
    base_dir = Path(projections_dir) if projections_dir else PROJECTIONS_DIR
    yaml_path = base_dir / f"{projection_name}.yaml"
    config = load_projection(yaml_path)
    src_path = Path(source_path)
    return await load_source(
        config=config,
        source_path=src_path,
        triggered_by_user_id=triggered_by_user_id,
    )


async def load_source(
    *,
    config: ProjectionConfig,
    source_path: Path,
    triggered_by_user_id: UUID | None = None,
    src_iter: Iterator[dict[str, Any]] | None = None,
) -> LoadResult:
    """Versão expandida — aceita config já carregado e/ou src_iter custom.

    Útil pra tests que sintetizam dicts diretamente ou pra fluxos onde
    o source não vem de arquivo (stream de webhook, etc.).
    """
    ingestion_repo = PgIngestionRunRepository()
    target_repo = resolve_repo(config.target_entity)
    target_cls = resolve_entity(config.target_entity)

    run = IngestionRun(
        source_type=_source_type_for(config),
        source_filename=source_path.name,
        source_size_bytes=_safe_size(source_path),
        target_table=target_table_for(config.target_entity),
        triggered_by_user_id=triggered_by_user_id,
        metadata={"projection_target": config.target_entity},
    )
    await ingestion_repo.create(run)
    await ingestion_repo.mark_running(run.id)

    # Injeta ingestion_run_id nos defaults SE o model tem o campo
    # (model_copy é não-destrutivo — não muta o config carregado de YAML).
    effective_config = _inject_ingestion_run_id(config, target_cls, run.id)

    rows_read = 0
    rows_inserted = 0
    batch: list[BaseModel] = []
    method = config.load.method

    try:
        source_iter = (
            src_iter
            if src_iter is not None
            else _open_source(source_path, config)
        )

        for model in project(effective_config, source_iter):
            batch.append(model)
            rows_read += 1
            if len(batch) >= config.load.batch_size:
                rows_inserted += await _flush(target_repo, method, batch)
                batch = []

        if batch:
            rows_inserted += await _flush(target_repo, method, batch)

        await ingestion_repo.mark_completed(
            run.id,
            rows_read=rows_read,
            rows_inserted=rows_inserted,
            rows_skipped=0,
            rows_failed=0,
        )
    except Exception as exc:
        await ingestion_repo.mark_failed(run.id, error_message=repr(exc))
        raise

    return LoadResult(
        run=run,
        rows_read=rows_read,
        rows_inserted=rows_inserted,
        rows_failed=0,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _open_source(
    path: Path, config: ProjectionConfig
) -> Iterator[dict[str, Any]]:
    fmt = config.source.format
    if fmt == "xlsx":
        return iter_xlsx_rows(path, sheet_name=config.source.sheet)
    if fmt == "msrv5":
        return parse_msrv5(
            path, encoding=config.source.encoding or "cp1252"
        )
    raise ValueError(f"unsupported source format: {fmt!r}")


async def _flush(
    repo: Any, method: str, batch: list[BaseModel]
) -> int:
    fn = getattr(repo, method, None)
    if fn is None:
        raise ValueError(
            f"repo {type(repo).__name__} has no method {method!r}"
        )
    return int(await fn(batch) or 0)


def _source_type_for(config: ProjectionConfig) -> str:
    """Mapeia source.format → IngestionRun.source_type (taxonomia documentada)."""
    fmt = config.source.format
    if fmt == "xlsx":
        return "xlsx"
    if fmt == "msrv5":
        return "msrv5_txt"
    return fmt  # fallback — IngestionRun valida via ALLOWED_SOURCE_TYPES


def _safe_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _inject_ingestion_run_id(
    config: ProjectionConfig,
    target_cls: type[BaseModel],
    run_id: UUID,
) -> ProjectionConfig:
    """Adiciona ingestion_run_id ao defaults SE o domain model tem o campo.

    Não muta o config original (model_copy retorna nova instância).
    """
    if "ingestion_run_id" not in target_cls.model_fields:
        return config
    return config.model_copy(
        update={
            "defaults": {**config.defaults, "ingestion_run_id": run_id},
        }
    )
