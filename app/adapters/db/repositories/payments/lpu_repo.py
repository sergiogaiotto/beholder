"""Implementação asyncpg de LPUItemRepository.

3.1M rows iniciais (MSRV5). Particionada por ano em data_documento (2018-2026).
"""

from __future__ import annotations

from datetime import date

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments._helpers import record_to_dict
from app.core.domain.payments import LPUItem
from app.core.ports.payments.repositories import LPUItemRepository


class PgLPUItemRepository(LPUItemRepository):

    async def bulk_insert(self, items: list[LPUItem]) -> int:
        if not items:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                """
                INSERT INTO payments.lpu_item (
                    contract_version_id, documento_compras, item, numero_servico,
                    data_documento, preco_unitario, qtd_solicitada, moeda,
                    descricao, texto_breve, pagina_pdf, clausula_ref,
                    extracted_by_llm, confidence, source, raw_extra,
                    ingestion_run_id
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16, $17
                )
                """,
                [
                    (
                        i.contract_version_id, i.documento_compras, i.item,
                        i.numero_servico, i.data_documento, i.preco_unitario,
                        i.qtd_solicitada, i.moeda, i.descricao, i.texto_breve,
                        i.pagina_pdf, i.clausula_ref, i.extracted_by_llm,
                        i.confidence, i.source.value, i.raw_extra,
                        i.ingestion_run_id,
                    )
                    for i in items
                ],
            )
        return len(items)

    async def get(self, lpu_id: int) -> LPUItem | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                "SELECT * FROM payments.lpu_item WHERE id = $1", lpu_id
            )
            return LPUItem.model_validate(record_to_dict(row)) if row else None

    async def find_by_servico_e_data(
        self, numero_servico: str, at: date
    ) -> list[LPUItem]:
        """Join com ContractVersion para validar vigência.

        Limita ao período de validade do contrato — relevante pra REGRA_LPU.
        """
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT li.*
                FROM payments.lpu_item li
                JOIN payments.contract_version cv ON cv.id = li.contract_version_id
                WHERE li.numero_servico = $1
                  AND $2 BETWEEN cv.valid_from AND cv.valid_to
                ORDER BY li.data_documento DESC
                """,
                numero_servico, at,
            )
            return [LPUItem.model_validate(record_to_dict(r)) for r in rows]

    async def count_by_year(self, year: int) -> int:
        async with connect_payments() as c:
            n = await c.fetchval(
                """
                SELECT COUNT(*) FROM payments.lpu_item
                WHERE data_documento >= make_date($1, 1, 1)
                  AND data_documento <  make_date($1 + 1, 1, 1)
                """,
                year,
            )
            return int(n or 0)

    async def count_total(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.lpu_item")
            return int(n or 0)
