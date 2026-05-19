"""PaymentsExtractionService — orquestra upload/extração de PDFs de contrato (Fase 4).

Fluxo upload (ui-driven):

  1. UI POSTa PDF + uploaded_by → service.queue_upload()
  2. Service salva no DocumentStore, cria ExtractionJob(status='pending'),
     despacha actor dramatiq
  3. Actor (em payments_extraction.py) chama service.process(job_id)
  4. `process`: baixa PDF do storage → texto → LLM → set_results(status='review')
  5. UI HITL mostra resultados, controladoria aprova → service.approve(job_id, edited)
  6. Approve cria ContractMaster + ContractVersion + (Fase 4.x) LPUItem

Cliente LLM é injetado — `MockExtractionClient` pra tests, Maritaca real
em prod (config a definir no Bloco B se preciso).
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments.extraction_repo import (
    PgExtractionJobRepository,
)
from app.adapters.storage.factory import get_document_store
from app.core.domain.payments import ExtractionJob, ExtractionStatus
from app.core.services.payments.extraction._client import (
    LLMExtractionClient,
    MockExtractionClient,
)
from app.core.services.payments.extraction.schemas import (
    ExtractedContractFields,
)

logger = logging.getLogger(__name__)


def _storage_key_for_pdf(job_id: UUID, filename: str) -> str:
    """Convenção: payments/contracts/<job_id>/<filename>."""
    return f"payments/contracts/{job_id}/{filename}"


def _pdf_to_text(pdf_bytes: bytes) -> str:
    """PDF binário → texto plain. Usa pdfplumber (já é dep da Pré-C);
    docling pode substituir depois quando processarmos tabelas LPU.

    Em caso de erro, devolve string vazia — pipeline detecta e marca o job
    como failed com mensagem clara em vez de propagar exception."""
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pdfplumber não instalado — extração impossível")
        return ""

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)
        try:
            with pdfplumber.open(str(tmp_path)) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
                return "\n\n".join(pages)
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.exception("falha em pdfplumber")
        return ""


class PaymentsExtractionService:
    """Use case: extração de PDFs + workflow HITL."""

    def __init__(
        self,
        *,
        jobs_repo: PgExtractionJobRepository | None = None,
        document_store=None,
        llm_client: LLMExtractionClient | None = None,
    ):
        self.jobs_repo = jobs_repo or PgExtractionJobRepository()
        self.document_store = document_store or get_document_store()
        # Default mock — caller em produção injeta MaritacaExtractionClient.
        self.llm_client = llm_client or MockExtractionClient()

    # =================================================== Upload + queue

    async def queue_upload(
        self,
        *,
        pdf_bytes: bytes,
        filename: str,
        uploaded_by_id: UUID,
    ) -> UUID:
        """Salva PDF no storage, cria job(PENDING) e despacha actor.
        Retorna o `job_id` para a UI fazer polling."""
        if not pdf_bytes:
            raise ValueError("PDF vazio (0 bytes)")
        if not filename.lower().endswith(".pdf"):
            raise ValueError(f"filename precisa terminar em .pdf: {filename!r}")

        job = ExtractionJob(
            pdf_storage_key="",  # preenchido após put no DocStore
            pdf_filename=filename,
            pdf_size_bytes=len(pdf_bytes),
            status=ExtractionStatus.PENDING,
            uploaded_by_id=uploaded_by_id,
        )
        storage_key = _storage_key_for_pdf(job.id, filename)
        job.pdf_storage_key = storage_key

        await self.document_store.put(
            storage_key, pdf_bytes, content_type="application/pdf",
        )
        await self.jobs_repo.create(job)

        # Despacha o actor. Import tardio evita ciclo de importação.
        from app.workers.payments_extraction import extract_pdf

        extract_pdf.send(job_id=str(job.id))
        return job.id

    # ===================================================== Process (worker)

    async def process(self, job_id: UUID) -> None:
        """Pipeline: storage → pdf_text → LLM → set_results.
        Chamado pelo actor dramatiq."""
        job = await self.jobs_repo.get(job_id)
        if job is None:
            raise ValueError(f"job {job_id} não encontrado")

        await self.jobs_repo.update_status(job_id, status=ExtractionStatus.EXTRACTING)

        try:
            pdf_bytes = await self.document_store.get(job.pdf_storage_key)
            pdf_text = _pdf_to_text(pdf_bytes)
            if not pdf_text:
                await self.jobs_repo.update_status(
                    job_id, status=ExtractionStatus.FAILED,
                    error_message="pdf_to_text retornou vazio",
                )
                return

            result = await self.llm_client.extract(
                pdf_text=pdf_text, pdf_filename=job.pdf_filename,
            )
            # Pydantic → dict pra JSONB.
            extracted = result.fields.model_dump(mode="json")
            confidence = result.fields.confidence_per_field()

            await self.jobs_repo.set_results(
                job_id,
                extracted_fields=extracted,
                confidence_per_field=confidence,
                cost_brl=result.cost_brl,
                llm_model_used=result.llm_model_used,
            )
            logger.info(
                "extract_pdf OK job=%s model=%s cost=R$%.4f",
                job_id, result.llm_model_used, float(result.cost_brl),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("extract_pdf failed job=%s: %s", job_id, exc)
            await self.jobs_repo.update_status(
                job_id, status=ExtractionStatus.FAILED,
                error_message=repr(exc)[:500],
            )

    # ===================================================== Listings

    async def list_recent_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Histórico de extrações pra UI — mais recentes primeiro."""
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT
                    ej.id, ej.pdf_filename, ej.status, ej.cost_brl,
                    ej.created_at, ej.extraction_finished_at,
                    ej.error_message, ej.llm_model_used,
                    u.username AS uploaded_by_username
                FROM payments.extraction_job ej
                LEFT JOIN users u ON u.id = ej.uploaded_by_id
                ORDER BY ej.created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [self._serialize_job(r) for r in rows]

    async def get_job_detail(self, job_id: UUID) -> dict[str, Any] | None:
        """Detalhe completo de 1 job pra tela HITL."""
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT
                    ej.*, u.username AS uploaded_by_username
                FROM payments.extraction_job ej
                LEFT JOIN users u ON u.id = ej.uploaded_by_id
                WHERE ej.id = $1
                """,
                job_id,
            )
            if row is None:
                return None

        import json
        def _parse(v):
            if v is None:
                return {}
            if isinstance(v, (dict, list)):
                return v
            return json.loads(v)

        return {
            "id": str(row["id"]),
            "pdf_filename": row["pdf_filename"],
            "pdf_storage_key": row["pdf_storage_key"],
            "pdf_size_bytes": int(row["pdf_size_bytes"] or 0),
            "pdf_pages": row["pdf_pages"],
            "status": row["status"],
            "extracted_fields": _parse(row["extracted_fields"]),
            "confidence_per_field": _parse(row["confidence_per_field"]),
            "cost_brl": float(row["cost_brl"] or 0),
            "llm_model_used": row["llm_model_used"],
            "error_message": row["error_message"],
            "uploaded_by_username": row["uploaded_by_username"],
            "created_at": row["created_at"],
            "extraction_finished_at": row["extraction_finished_at"],
        }

    # =================================================== Workflow HITL (Bloco B)

    async def approve_job(
        self,
        job_id: UUID,
        *,
        edited_fields: dict[str, Any],
        approved_by_id: UUID,
    ) -> UUID:
        """Aprova um job em status='review' — materializa em
        ContractMaster + ContractVersion. Retorna contract_master_id.

        Lógica:
          1. Busca o job. Falha se não estiver em 'review'.
          2. Resolve empreiteira via supplier_bridge.get_by_cnpj — se já
             existe, reusa contract_master existente (cria nova version).
             Se não, cria supplier + master + version do zero.
          3. Vincula extraction_job.contract_master_id + status='approved'.

        `edited_fields` aceita os mesmos campos de ExtractedContractFields
        (validação Pydantic — fail-fast se shape errado).
        """
        from app.adapters.db.repositories.payments.contract_repos import (
            PgContractMasterRepository,
            PgContractVersionRepository,
            PgSupplierBridgeRepository,
        )
        from app.core.domain.payments import (
            ContractMaster,
            ContractVersion,
            SupplierBridge,
        )
        from app.core.services.payments.extraction.schemas import (
            ExtractedContractFields,
        )

        job = await self.jobs_repo.get(job_id)
        if job is None:
            raise ValueError(f"job {job_id} não encontrado")
        if job.status != ExtractionStatus.REVIEW:
            raise ValueError(
                f"job {job_id} em status {job.status.value!r}, esperado 'review'"
            )

        # Valida campos via Pydantic — re-shape do dict editado.
        fields = ExtractedContractFields(**edited_fields)
        if not fields.empreiteira_cnpj:
            raise ValueError("empreiteira_cnpj é obrigatório para aprovar")

        sb_repo = PgSupplierBridgeRepository()
        cm_repo = PgContractMasterRepository()
        cv_repo = PgContractVersionRepository()

        # Resolve supplier — se há ao menos 1 com mesmo CNPJ, reusa contrato.
        existing_suppliers = await sb_repo.get_by_cnpj(fields.empreiteira_cnpj)
        supplier_id: UUID
        contract_master_id: UUID

        if existing_suppliers:
            supplier_id = existing_suppliers[0].id
            # Verifica se já existe contract_master pra esse supplier — busca
            # via contrato_num_sap (placeholder neste fluxo) ou força criação
            # de novo master por supplier. Para MVP: 1 master por supplier
            # via primeiro hit de supplier; se cm existir, reusa.
            sb = existing_suppliers[0]
            cm = await cm_repo.get_by_contrato(sb.contrato_num_sap)
            if cm:
                contract_master_id = cm.id
            else:
                cm_new = ContractMaster(
                    supplier_bridge_id=supplier_id,
                    contrato_num_sap=sb.contrato_num_sap,
                    ref_ws=sb.ref_ws,
                    cnpj=fields.empreiteira_cnpj,
                    is_monitored=True,
                    created_by_id=approved_by_id,
                )
                await cm_repo.create(cm_new)
                contract_master_id = cm_new.id
        else:
            # Supplier inédito: precisa de contrato_num_sap + ref_ws fictícios.
            # Convenção: contrato_num_sap = "EXT-<job_id_short>",
            # ref_ws = "EXT-<job_id_short>" — UI HITL pode editar depois.
            short = str(job_id)[:8]
            sb_new = SupplierBridge(
                categoria=fields.categoria or "EXTRAIDO",
                empreiteira=fields.empreiteira_nome or "DESCONHECIDA",
                contrato_num_sap=f"EXT-{short}",
                ref_ws=f"EXT-{short}",
                numero_fornecedor_sap=f"EXT{short}",
                cnpj=fields.empreiteira_cnpj,
            )
            await sb_repo.bulk_upsert([sb_new])
            supplier_id = sb_new.id
            cm_new = ContractMaster(
                supplier_bridge_id=supplier_id,
                contrato_num_sap=sb_new.contrato_num_sap,
                ref_ws=sb_new.ref_ws,
                cnpj=fields.empreiteira_cnpj,
                is_monitored=True,
                created_by_id=approved_by_id,
            )
            await cm_repo.create(cm_new)
            contract_master_id = cm_new.id

        # Cria nova ContractVersion sempre — version_number = N+1.
        existing_versions = await cv_repo.list_for_master(contract_master_id)
        next_version = (
            max((v.version_number for v in existing_versions), default=0) + 1
        )
        cv = ContractVersion(
            contract_master_id=contract_master_id,
            version_number=next_version,
            valid_from=fields.valid_from or datetime.utcnow().date(),
            valid_to=fields.valid_to or datetime.utcnow().date(),
            val_fix_cab=fields.val_fix_cab,
            objeto_contrato=fields.objeto_contrato,
            tecnologia=fields.tecnologia,
            atividade=fields.atividade,
            uf=fields.uf or [],
            cidade=fields.cidade or [],
            extracted_by_llm_model=job.llm_model_used,
            extracted_cost_brl=job.cost_brl,
            reviewed_by_id=approved_by_id,
            reviewed_at=datetime.utcnow(),
        )
        await cv_repo.create(cv)
        await cm_repo.set_current_version(contract_master_id, cv.id)

        # Marca job como approved e vincula o contract_master.
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.extraction_job
                SET status = $1, contract_master_id = $2
                WHERE id = $3
                """,
                ExtractionStatus.APPROVED.value, contract_master_id, job_id,
            )

        return contract_master_id

    async def reject_job(
        self,
        job_id: UUID,
        *,
        reason: str,
        rejected_by_id: UUID,
    ) -> None:
        """Rejeita um job em status='review' — marca como FAILED com
        error_message contendo o motivo + user."""
        job = await self.jobs_repo.get(job_id)
        if job is None:
            raise ValueError(f"job {job_id} não encontrado")
        if job.status != ExtractionStatus.REVIEW:
            raise ValueError(
                f"job {job_id} em status {job.status.value!r}, esperado 'review'"
            )
        msg = f"rejeitado por {rejected_by_id}: {reason.strip() or '(sem motivo)'}"[:500]
        await self.jobs_repo.update_status(
            job_id, status=ExtractionStatus.FAILED, error_message=msg,
        )

    @staticmethod
    def _serialize_job(row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "pdf_filename": row["pdf_filename"],
            "status": row["status"],
            "status_label": {
                "pending":    "Aguardando",
                "extracting": "Extraindo",
                "review":     "Revisão",
                "approved":   "Aprovado",
                "failed":     "Falhou",
            }.get(row["status"], row["status"]),
            "cost_brl_fmt": f"R$ {float(row['cost_brl'] or 0):.4f}",
            "llm_model_used": row["llm_model_used"] or "—",
            "created_at": row["created_at"],
            "created_at_fmt": row["created_at"].strftime("%d/%m/%Y %H:%M"),
            "finished_at_fmt": (
                row["extraction_finished_at"].strftime("%d/%m/%Y %H:%M")
                if row["extraction_finished_at"]
                else "—"
            ),
            "uploaded_by_username": row["uploaded_by_username"] or "—",
            "error_message": row["error_message"],
        }
