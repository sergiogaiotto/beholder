"""Use case: Ingestão XLSX/MSRV5 via UI (Fase 3.5).

Tela `/payments/empreiteiras-wf/ingestao` permite a controladoria subir
arquivos de origem (XLSX da empreiteira, MSRV5 TXT, etc.) sem CLI. O
service faz três coisas:

  1. Lista o catálogo de projeções (`list_projections`).
  2. Recebe um upload e enfileira a carga (`queue_upload`) — salva no
     DocumentStore, cria IngestionRun(PENDING), despacha actor dramatiq.
  3. Devolve histórico de runs (`list_recent_runs`, `get_run`).

Por que enfileirar via dramatiq?
  - Cargas grandes (Analítico WF ~3min, MSRV5 ~9min em dev) excedem o
    timeout da requisição HTTP. O worker dramatiq tem TimeLimit de 10
    minutos default e roda em processo separado — não bloqueia o uvicorn.
  - Infra de worker já entregue pela Fase 0; healthcheck.actor valida a
    cadeia FastAPI → Redis → worker.

Decisões fixas:
  - DocumentStore atua como staging — service salva o upload com prefix
    `payments/ingestion/<run_id>/<filename>`. Actor baixa pra path temp,
    roda load_source_by_path, limpa.
  - source_type pré-determinado no catálogo (PROJECTION_CATALOG) — não
    inferimos de extensão pra evitar surpresa silenciosa quando user sobe
    arquivo errado.
  - Não validamos magic bytes do upload — `load_source_by_path` falha
    fast no parser quando o arquivo não bate o formato.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from app.adapters.db.repositories.payments import PgIngestionRunRepository
from app.adapters.sap.projections import PROJECTIONS_DIR, load_projection
from app.adapters.storage.factory import get_document_store
from app.core.domain.payments import IngestionRun, IngestionStatus
from app.core.services.payments.ingestion.registry import target_table_for


# Catálogo de projeções suportadas pela UI. As `key`s casam com os YAMLs
# em `app/adapters/sap/projections/configs/`. `source_type` é o valor que
# vai pra IngestionRun.source_type (vide ALLOWED_SOURCE_TYPES). `accept`
# é o atributo HTML do `<input type="file">` — orienta o user sem bloquear
# (validação real é no parser).
@dataclass(frozen=True)
class ProjectionInfo:
    key: str            # nome do YAML (sem ext): 'wf_payment'
    label: str          # texto mostrado no card da UI
    description: str    # 1-line hint abaixo do label
    accept: str         # ex: '.xlsx', '.txt'
    source_type: str    # IngestionRun.source_type (ALLOWED_SOURCE_TYPES)


PROJECTION_CATALOG: tuple[ProjectionInfo, ...] = (
    ProjectionInfo(
        key="supplier_bridge",
        label="Contratos-Empreiteiras",
        description="DE-PARA contrato SAP ↔ REF WS ↔ CNPJ (≈147 linhas).",
        accept=".xlsx",
        source_type="xlsx",
    ),
    ProjectionInfo(
        key="wf_payment",
        label="Analítico WF1+WF2",
        description="Pagamentos analíticos (≈869k rows, particionada por trimestre).",
        accept=".xlsx",
        source_type="analitico_wf",
    ),
    ProjectionInfo(
        key="msrv5",
        label="MSRV5 — LPU (TXT)",
        description="Lista de Preços Unitários extraída do MSRV5 (≈3.1M rows, cp1252).",
        accept=".txt",
        source_type="msrv5_txt",
    ),
    ProjectionInfo(
        key="ekko",
        label="SAP EKKO (cabeça de pedido)",
        description="Pedidos de compra SAP (1 linha por documento).",
        accept=".xlsx",
        source_type="xlsx",
    ),
    ProjectionInfo(
        key="ekpo",
        label="SAP EKPO (itens de pedido)",
        description="Itens dos pedidos SAP.",
        accept=".xlsx",
        source_type="xlsx",
    ),
    ProjectionInfo(
        key="esll",
        label="SAP ESLL (itens de serviço)",
        description="Itens de serviço SAP (rateio de pedidos).",
        accept=".xlsx",
        source_type="xlsx",
    ),
    ProjectionInfo(
        key="gc",
        label="Guia de Conferência",
        description="GC dos pedidos (cabeça + materiais).",
        accept=".xlsx",
        source_type="xlsx",
    ),
    ProjectionInfo(
        key="cost_center",
        label="Centros de Custo",
        description="Mapeamento contábil de centro de custo.",
        accept=".xlsx",
        source_type="xlsx",
    ),
)

_BY_KEY: dict[str, ProjectionInfo] = {p.key: p for p in PROJECTION_CATALOG}


def get_projection_info(key: str) -> ProjectionInfo | None:
    return _BY_KEY.get(key)


def _storage_key_for(run_id: UUID, filename: str) -> str:
    """Convenção de chave no DocumentStore para uploads de ingestão.

    Formato: `payments/ingestion/<run_id>/<filename>`. Inclui o run_id
    pra colisão zero entre uploads simultâneos com mesmo filename.
    """
    return f"payments/ingestion/{run_id}/{filename}"


class PaymentsIngestionService:
    """Use case: enfileira uploads e expõe histórico de runs."""

    def __init__(
        self,
        runs_repo: PgIngestionRunRepository | None = None,
        document_store=None,
    ):
        self.runs_repo = runs_repo or PgIngestionRunRepository()
        self.document_store = document_store or get_document_store()

    # ----------------------------------------------------- Catálogo

    def list_projections(self) -> list[dict[str, str]]:
        """Devolve catálogo de projeções no formato consumido pelo template
        de Ingestão (1 card por projeção)."""
        return [
            {
                "key": p.key,
                "label": p.label,
                "description": p.description,
                "accept": p.accept,
                "source_type": p.source_type,
            }
            for p in PROJECTION_CATALOG
        ]

    # ----------------------------------------------------- Enfileirar

    async def queue_upload(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        projection_name: str,
        user_id: UUID | None,
    ) -> UUID:
        """Salva o upload + cria IngestionRun(PENDING) + despacha actor.
        Retorna o `run_id` para a UI fazer polling.

        Raises:
          ValueError: projection_name desconhecida ou file_bytes vazio.
          FileNotFoundError: YAML da projeção ausente (raro — bug de catálogo).
        """
        info = get_projection_info(projection_name)
        if info is None:
            raise ValueError(
                f"projection desconhecida: {projection_name!r}. "
                f"Conhecidas: {sorted(_BY_KEY)}"
            )
        if not file_bytes:
            raise ValueError("upload vazio (0 bytes)")

        # Resolve target_table do YAML — barato (só load + Pydantic) e nos dá
        # erro fast se o YAML quebrou.
        config = load_projection(PROJECTIONS_DIR / f"{projection_name}.yaml")
        target_table = target_table_for(config.target_entity)

        # Pré-cria o run em PENDING. Actor faz mark_running depois.
        run = IngestionRun(
            source_type=info.source_type,
            source_filename=filename,
            source_size_bytes=len(file_bytes),
            source_sha256=hashlib.sha256(file_bytes).hexdigest(),
            target_table=target_table,
            status=IngestionStatus.PENDING,
            triggered_by_user_id=user_id,
            metadata={
                "projection_target": config.target_entity,
                "projection_name": projection_name,
                "via": "ui_upload",
            },
        )
        await self.runs_repo.create(run)

        # Sobe pro DocumentStore (storage_key inclui run_id → único).
        storage_key = _storage_key_for(run.id, filename)
        await self.document_store.put(
            storage_key,
            file_bytes,
            content_type="application/octet-stream",
        )

        # Despacha actor. Import tardio evita ciclo se houver.
        from app.workers.payments_ingest import ingest_source

        ingest_source.send(
            run_id=str(run.id),
            storage_key=storage_key,
            projection_name=projection_name,
            triggered_by_user_id=str(user_id) if user_id else None,
        )
        return run.id

    # ----------------------------------------------------- Histórico

    async def list_recent_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Últimas N execuções, com formatação para a tabela da UI."""
        runs = await self.runs_repo.list_recent(limit=limit)
        return [self._serialize_run(r) for r in runs]

    async def get_run(self, run_id: UUID) -> dict[str, Any] | None:
        """Detalhe de 1 run — usado pelo polling de status."""
        run = await self.runs_repo.get(run_id)
        if run is None:
            return None
        return self._serialize_run(run)

    @staticmethod
    def _serialize_run(run: IngestionRun) -> dict[str, Any]:
        """Formato pronto pro template: campos formatados + status label."""
        elapsed = None
        if run.finished_at and run.started_at:
            elapsed = (run.finished_at - run.started_at).total_seconds()

        return {
            "id": str(run.id),
            "source_type": run.source_type,
            "source_filename": run.source_filename,
            "source_size_bytes": run.source_size_bytes or 0,
            "target_table": run.target_table,
            "status": run.status.value,
            "status_label": {
                "pending":   "Aguardando",
                "running":   "Em execução",
                "completed": "Concluído",
                "failed":    "Falhou",
            }.get(run.status.value, run.status.value),
            "rows_read": run.rows_read,
            "rows_inserted": run.rows_inserted,
            "rows_skipped": run.rows_skipped,
            "rows_failed": run.rows_failed,
            "started_at": run.started_at,
            "started_at_fmt": run.started_at.strftime("%d/%m/%Y %H:%M:%S"),
            "finished_at": run.finished_at,
            "elapsed_seconds": elapsed,
            "elapsed_fmt": (
                f"{elapsed:.1f}s" if elapsed is not None and elapsed < 60
                else (f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed else "—")
            ),
            "error_message": run.error_message,
            "projection_name": (run.metadata or {}).get("projection_name", ""),
        }
