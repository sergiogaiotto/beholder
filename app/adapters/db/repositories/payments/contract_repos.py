"""Implementação asyncpg dos 4 repos de contratos:
PgSupplierBridge, PgContractMaster, PgContractVersion, PgContractClause.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments._helpers import (
    format_pgvector,
    parse_pgvector,
    record_to_dict,
)
from app.core.domain.payments import (
    ContractClause,
    ContractMaster,
    ContractVersion,
    SupplierBridge,
)
from app.core.ports.payments.repositories import (
    ContractClauseRepository,
    ContractMasterRepository,
    ContractVersionRepository,
    SupplierBridgeRepository,
)


# ---------------------------------------------------------------------------
# SupplierBridge
# ---------------------------------------------------------------------------


class PgSupplierBridgeRepository(SupplierBridgeRepository):

    async def bulk_upsert(self, items: list[SupplierBridge]) -> int:
        if not items:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                """
                INSERT INTO payments.supplier_bridge (
                    id, categoria, empreiteira, contrato_num_sap, ref_ws,
                    numero_fornecedor_sap, cnpj, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (contrato_num_sap, ref_ws) DO UPDATE SET
                    categoria = EXCLUDED.categoria,
                    empreiteira = EXCLUDED.empreiteira,
                    numero_fornecedor_sap = EXCLUDED.numero_fornecedor_sap,
                    cnpj = EXCLUDED.cnpj
                """,
                [
                    (
                        s.id, s.categoria, s.empreiteira, s.contrato_num_sap,
                        s.ref_ws, s.numero_fornecedor_sap, s.cnpj, s.created_at,
                    )
                    for s in items
                ],
            )
        return len(items)

    async def get(self, supplier_id: UUID) -> SupplierBridge | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.supplier_bridge WHERE id = $1", supplier_id
            )
            return SupplierBridge.model_validate(record_to_dict(row)) if row else None

    async def get_by_contrato(self, contrato_num_sap: str) -> SupplierBridge | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT * FROM payments.supplier_bridge
                WHERE contrato_num_sap = $1
                LIMIT 1
                """,
                contrato_num_sap,
            )
            return SupplierBridge.model_validate(record_to_dict(row)) if row else None

    async def get_by_cnpj(self, cnpj: str) -> list[SupplierBridge]:
        async with connect_payments() as c:
            rows = await c.fetch(
                "SELECT * FROM payments.supplier_bridge WHERE cnpj = $1", cnpj
            )
            return [SupplierBridge.model_validate(record_to_dict(r)) for r in rows]

    async def list_all(self) -> list[SupplierBridge]:
        async with connect_payments() as c:
            rows = await c.fetch(
                "SELECT * FROM payments.supplier_bridge ORDER BY empreiteira, contrato_num_sap"
            )
            return [SupplierBridge.model_validate(record_to_dict(r)) for r in rows]

    async def count(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.supplier_bridge")
            return int(n or 0)


# ---------------------------------------------------------------------------
# ContractMaster
# ---------------------------------------------------------------------------


class PgContractMasterRepository(ContractMasterRepository):

    async def create(self, master: ContractMaster) -> ContractMaster:
        async with connect_payments() as c:
            await c.execute(
                """
                INSERT INTO payments.contract_master (
                    id, supplier_bridge_id, contrato_num_sap, ref_ws, cnpj,
                    current_version_id, is_monitored, created_by_id,
                    created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                master.id, master.supplier_bridge_id, master.contrato_num_sap,
                master.ref_ws, master.cnpj, master.current_version_id,
                master.is_monitored, master.created_by_id,
                master.created_at, master.updated_at,
            )
            return master

    async def get(self, master_id: UUID) -> ContractMaster | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.contract_master WHERE id = $1", master_id
            )
            return ContractMaster.model_validate(record_to_dict(row)) if row else None

    async def get_by_contrato(self, contrato_num_sap: str) -> ContractMaster | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT * FROM payments.contract_master
                WHERE contrato_num_sap = $1
                LIMIT 1
                """,
                contrato_num_sap,
            )
            return ContractMaster.model_validate(record_to_dict(row)) if row else None

    async def set_current_version(self, master_id: UUID, version_id: UUID) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.contract_master
                SET current_version_id = $1, updated_at = NOW()
                WHERE id = $2
                """,
                version_id, master_id,
            )

    async def set_monitored(self, master_id: UUID, monitored: bool) -> None:
        async with connect_payments() as c:
            await c.execute(
                """
                UPDATE payments.contract_master
                SET is_monitored = $1, updated_at = NOW()
                WHERE id = $2
                """,
                monitored, master_id,
            )

    async def list_monitored(self) -> list[ContractMaster]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.contract_master
                WHERE is_monitored = TRUE
                ORDER BY contrato_num_sap
                """
            )
            return [ContractMaster.model_validate(record_to_dict(r)) for r in rows]

    async def list_all(self) -> list[ContractMaster]:
        async with connect_payments() as c:
            rows = await c.fetch(
                "SELECT * FROM payments.contract_master ORDER BY contrato_num_sap"
            )
            return [ContractMaster.model_validate(record_to_dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# ContractVersion
# ---------------------------------------------------------------------------


class PgContractVersionRepository(ContractVersionRepository):

    async def create(self, version: ContractVersion) -> ContractVersion:
        async with connect_payments() as c:
            await c.execute(
                """
                INSERT INTO payments.contract_version (
                    id, contract_master_id, version_number, valid_from, valid_to,
                    val_fix_cab, objeto_contrato, tecnologia, atividade,
                    uf, cidade, pdf_storage_key, extracted_by_llm_model,
                    extracted_cost_brl, confidence_avg, reviewed_by_id,
                    reviewed_at, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16, $17, $18
                )
                """,
                version.id, version.contract_master_id, version.version_number,
                version.valid_from, version.valid_to,
                version.val_fix_cab, version.objeto_contrato, version.tecnologia,
                version.atividade, version.uf, version.cidade,
                version.pdf_storage_key, version.extracted_by_llm_model,
                version.extracted_cost_brl, version.confidence_avg,
                version.reviewed_by_id, version.reviewed_at, version.created_at,
            )
            return version

    async def get(self, version_id: UUID) -> ContractVersion | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.contract_version WHERE id = $1", version_id
            )
            return ContractVersion.model_validate(record_to_dict(row)) if row else None

    async def get_current_for_master(
        self, master_id: UUID, *, at: date | None = None
    ) -> ContractVersion | None:
        target_date = at if at is not None else date.today()
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT * FROM payments.contract_version
                WHERE contract_master_id = $1
                  AND $2 BETWEEN valid_from AND valid_to
                ORDER BY version_number DESC
                LIMIT 1
                """,
                master_id, target_date,
            )
            return ContractVersion.model_validate(record_to_dict(row)) if row else None

    async def list_for_master(self, master_id: UUID) -> list[ContractVersion]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.contract_version
                WHERE contract_master_id = $1
                ORDER BY version_number
                """,
                master_id,
            )
            return [ContractVersion.model_validate(record_to_dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# ContractClause (pgvector)
# ---------------------------------------------------------------------------


def _clause_record_to_model(row) -> ContractClause:
    """Pgvector vem como string '[v1,v2,...]' — parse manual."""
    d = record_to_dict(row)
    if d.get("embedding") is not None:
        d["embedding"] = parse_pgvector(d["embedding"])
    return ContractClause.model_validate(d)


class PgContractClauseRepository(ContractClauseRepository):

    async def bulk_insert(self, clauses: list[ContractClause]) -> int:
        if not clauses:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                """
                INSERT INTO payments.contract_clause (
                    id, contract_version_id, clausula_numero, secao,
                    texto, embedding, pagina_pdf, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6::vector, $7, $8)
                """,
                [
                    (
                        cc.id, cc.contract_version_id, cc.clausula_numero,
                        cc.secao, cc.texto, format_pgvector(cc.embedding),
                        cc.pagina_pdf, cc.created_at,
                    )
                    for cc in clauses
                ],
            )
        return len(clauses)

    async def get(self, clause_id: UUID) -> ContractClause | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.contract_clause WHERE id = $1", clause_id
            )
            return _clause_record_to_model(row) if row else None

    async def list_for_version(self, version_id: UUID) -> list[ContractClause]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.contract_clause
                WHERE contract_version_id = $1
                ORDER BY pagina_pdf NULLS LAST, clausula_numero NULLS LAST
                """,
                version_id,
            )
            return [_clause_record_to_model(r) for r in rows]

    async def search_by_embedding(
        self,
        embedding: list[float],
        *,
        contract_version_id: UUID | None = None,
        limit: int = 10,
    ) -> list[ContractClause]:
        vec_param = format_pgvector(embedding)
        sql = """
            SELECT * FROM payments.contract_clause
            WHERE ($2::uuid IS NULL OR contract_version_id = $2)
              AND embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $3
        """
        async with connect_payments() as c:
            rows = await c.fetch(sql, vec_param, contract_version_id, limit)
            return [_clause_record_to_model(r) for r in rows]
