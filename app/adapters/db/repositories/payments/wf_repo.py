"""Implementação asyncpg de WFPaymentRepository.

869k rows iniciais. Particionada por trimestre em data_pedido.

list_universe aplica o filtro universal SDD §9 v1.1.1 — mesmo predicado
do idx_wf_universe (índice parcial). PK composta (id, data_pedido) por
causa do particionamento.
"""

from __future__ import annotations

from datetime import date

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments._helpers import record_to_dict
from app.core.domain.payments import WFPayment
from app.core.ports.payments.repositories import WFPaymentRepository


# Predicado-base reutilizado em list_universe e count_universe.
# Bate exatamente com idx_wf_universe (migration 004).
_UNIVERSE_PREDICATE = """
    status_os IN ('EXECUTADO', 'EM EXECUÇÃO')
    AND nivel_gerencial IN ('Em Pagamento', 'Medido')
    AND malogro <> 'ERROR'
"""


class PgWFPaymentRepository(WFPaymentRepository):

    async def bulk_insert(self, items: list[WFPayment]) -> int:
        if not items:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                """
                INSERT INTO payments.wf_payment (
                    os_num, sistema, pedido_num, contrato_num, item_num,
                    item_descricao, material_servico_num,
                    data_pedido, data_execucao,
                    valor_total_final, valor_unitario, valor_unitario_para,
                    categoria, uf, cidade, tecnologia, atividade,
                    objeto_do_contrato, tipo_de_lpu, tipo_de_despesa,
                    empreiteira, fase_atual, status_os, nivel_gerencial,
                    malogro, mes_medicao, regional_soe_nova, centro_de_custo,
                    raw_extra, ingestion_run_id, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16, $17,
                    $18, $19, $20, $21, $22, $23, $24, $25,
                    $26, $27, $28, $29, $30, $31
                )
                """,
                [
                    (
                        w.os_num,
                        w.sistema.value if w.sistema else None,
                        w.pedido_num, w.contrato_num, w.item_num,
                        w.item_descricao, w.material_servico_num,
                        w.data_pedido, w.data_execucao,
                        w.valor_total_final, w.valor_unitario, w.valor_unitario_para,
                        w.categoria, w.uf, w.cidade, w.tecnologia, w.atividade,
                        w.objeto_do_contrato, w.tipo_de_lpu,
                        w.tipo_de_despesa.value if w.tipo_de_despesa else None,
                        w.empreiteira, w.fase_atual, w.status_os, w.nivel_gerencial,
                        w.malogro, w.mes_medicao, w.regional_soe_nova,
                        w.centro_de_custo, w.raw_extra, w.ingestion_run_id,
                        w.created_at,
                    )
                    for w in items
                ],
            )
        return len(items)

    async def get(self, wf_id: int, data_pedido: date) -> WFPayment | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT * FROM payments.wf_payment
                WHERE id = $1 AND data_pedido = $2
                """,
                wf_id, data_pedido,
            )
            return WFPayment.model_validate(record_to_dict(row)) if row else None

    async def list_universe(
        self,
        *,
        since: date,
        until: date,
        empreiteira: str | None = None,
        limit: int = 1000,
    ) -> list[WFPayment]:
        sql = f"""
            SELECT * FROM payments.wf_payment
            WHERE data_pedido >= $1 AND data_pedido < $2
              AND ($3::text IS NULL OR empreiteira = $3)
              AND {_UNIVERSE_PREDICATE}
            ORDER BY data_pedido DESC
            LIMIT $4
        """
        async with connect_payments() as c:
            rows = await c.fetch(sql, since, until, empreiteira, limit)
            return [WFPayment.model_validate(record_to_dict(r)) for r in rows]

    async def count_universe(
        self,
        *,
        since: date,
        until: date,
        empreiteira: str | None = None,
    ) -> int:
        sql = f"""
            SELECT COUNT(*) FROM payments.wf_payment
            WHERE data_pedido >= $1 AND data_pedido < $2
              AND ($3::text IS NULL OR empreiteira = $3)
              AND {_UNIVERSE_PREDICATE}
        """
        async with connect_payments() as c:
            n = await c.fetchval(sql, since, until, empreiteira)
            return int(n or 0)

    async def count_total(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.wf_payment")
            return int(n or 0)
