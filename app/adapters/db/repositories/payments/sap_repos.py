"""Implementação asyncpg dos 5 repos SAP:
PgPurchaseOrderHeader, PgPurchaseOrderItem, PgServicePackage,
PgPurchaseOrderGc, PgCostCenterAccount.
"""

from __future__ import annotations

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.payments._helpers import record_to_dict
from app.core.domain.payments import (
    CostCenterAccount,
    PurchaseOrderGc,
    PurchaseOrderHeader,
    PurchaseOrderItem,
    ServicePackage,
)
from app.core.ports.payments.repositories import (
    CostCenterAccountRepository,
    PurchaseOrderGcRepository,
    PurchaseOrderHeaderRepository,
    PurchaseOrderItemRepository,
    ServicePackageRepository,
)


# ---------------------------------------------------------------------------
# PurchaseOrderHeader (EKKO)
# ---------------------------------------------------------------------------


class PgPurchaseOrderHeaderRepository(PurchaseOrderHeaderRepository):

    async def bulk_insert(self, items: list[PurchaseOrderHeader]) -> int:
        if not items:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                """
                INSERT INTO payments.purchase_order_header (
                    id, documento_compras, empresa, categoria_doc, tipo_doc,
                    fornecedor, contrato_basico, data_documento,
                    inicio_validade, fim_validade, val_fix_cab, moeda, status,
                    raw_extra, ingestion_run_id, imported_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16
                )
                ON CONFLICT (documento_compras) DO NOTHING
                """,
                [
                    (
                        h.id, h.documento_compras, h.empresa, h.categoria_doc,
                        h.tipo_doc, h.fornecedor, h.contrato_basico,
                        h.data_documento, h.inicio_validade, h.fim_validade,
                        h.val_fix_cab, h.moeda, h.status, h.raw_extra,
                        h.ingestion_run_id, h.imported_at,
                    )
                    for h in items
                ],
            )
        return len(items)

    async def get_by_documento(
        self, documento_compras: str
    ) -> PurchaseOrderHeader | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT * FROM payments.purchase_order_header
                WHERE documento_compras = $1
                """,
                documento_compras,
            )
            return (
                PurchaseOrderHeader.model_validate(record_to_dict(row))
                if row else None
            )

    async def list_for_fornecedor(
        self, fornecedor: str
    ) -> list[PurchaseOrderHeader]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.purchase_order_header
                WHERE fornecedor = $1
                ORDER BY data_documento DESC NULLS LAST
                """,
                fornecedor,
            )
            return [
                PurchaseOrderHeader.model_validate(record_to_dict(r)) for r in rows
            ]

    async def count(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.purchase_order_header")
            return int(n or 0)


# ---------------------------------------------------------------------------
# PurchaseOrderItem (EKPO)
# ---------------------------------------------------------------------------


class PgPurchaseOrderItemRepository(PurchaseOrderItemRepository):

    async def bulk_insert(self, items: list[PurchaseOrderItem]) -> int:
        if not items:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                """
                INSERT INTO payments.purchase_order_item (
                    id, documento_compras, item, texto_breve, material,
                    grupo_mercadorias, quantidade, unidade_medida,
                    preco_liquido, valor_liquido, centro, categoria_item,
                    raw_extra, ingestion_run_id, imported_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15
                )
                ON CONFLICT (documento_compras, item) DO NOTHING
                """,
                [
                    (
                        i.id, i.documento_compras, i.item, i.texto_breve,
                        i.material, i.grupo_mercadorias, i.quantidade,
                        i.unidade_medida, i.preco_liquido, i.valor_liquido,
                        i.centro, i.categoria_item, i.raw_extra,
                        i.ingestion_run_id, i.imported_at,
                    )
                    for i in items
                ],
            )
        return len(items)

    async def get(
        self, documento_compras: str, item: str
    ) -> PurchaseOrderItem | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT * FROM payments.purchase_order_item
                WHERE documento_compras = $1 AND item = $2
                """,
                documento_compras, item,
            )
            return (
                PurchaseOrderItem.model_validate(record_to_dict(row))
                if row else None
            )

    async def list_for_documento(
        self, documento_compras: str
    ) -> list[PurchaseOrderItem]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.purchase_order_item
                WHERE documento_compras = $1
                ORDER BY item
                """,
                documento_compras,
            )
            return [
                PurchaseOrderItem.model_validate(record_to_dict(r)) for r in rows
            ]

    async def count(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.purchase_order_item")
            return int(n or 0)


# ---------------------------------------------------------------------------
# ServicePackage (ESLL)
# ---------------------------------------------------------------------------


class PgServicePackageRepository(ServicePackageRepository):

    async def bulk_insert(self, items: list[ServicePackage]) -> int:
        if not items:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                """
                INSERT INTO payments.service_package (
                    id, pacote, linha, numero_servico, texto_breve,
                    preco_bruto, qtd_solicitada, valor_solicitado,
                    ekpo_documento, ekpo_item, raw_extra,
                    ingestion_run_id, imported_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13
                )
                ON CONFLICT (pacote, linha) DO NOTHING
                """,
                [
                    (
                        s.id, s.pacote, s.linha, s.numero_servico,
                        s.texto_breve, s.preco_bruto, s.qtd_solicitada,
                        s.valor_solicitado, s.ekpo_documento, s.ekpo_item,
                        s.raw_extra, s.ingestion_run_id, s.imported_at,
                    )
                    for s in items
                ],
            )
        return len(items)

    async def get(self, pacote: str, linha: int) -> ServicePackage | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT * FROM payments.service_package
                WHERE pacote = $1 AND linha = $2
                """,
                pacote, linha,
            )
            return ServicePackage.model_validate(record_to_dict(row)) if row else None

    async def list_for_servico(self, numero_servico: str) -> list[ServicePackage]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.service_package
                WHERE numero_servico = $1
                ORDER BY pacote, linha
                """,
                numero_servico,
            )
            return [ServicePackage.model_validate(record_to_dict(r)) for r in rows]

    async def count(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.service_package")
            return int(n or 0)


# ---------------------------------------------------------------------------
# PurchaseOrderGc (Guarda Chuvas)
# ---------------------------------------------------------------------------


class PgPurchaseOrderGcRepository(PurchaseOrderGcRepository):

    async def bulk_insert(self, items: list[PurchaseOrderGc]) -> int:
        if not items:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                """
                INSERT INTO payments.purchase_order_gc (
                    id, documento_compras, item, ult_modif_dia, texto_breve,
                    empresa, numero_pacote_ekpo, pacote_esll,
                    inicio_validade, fim_validade, val_fix_cab,
                    preco_bruto_lpu, numero_servico, texto_breve_servico,
                    raw_extra, ingestion_run_id, imported_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16, $17
                )
                ON CONFLICT (documento_compras, item) DO NOTHING
                """,
                [
                    (
                        g.id, g.documento_compras, g.item, g.ult_modif_dia,
                        g.texto_breve, g.empresa, g.numero_pacote_ekpo,
                        g.pacote_esll, g.inicio_validade, g.fim_validade,
                        g.val_fix_cab, g.preco_bruto_lpu, g.numero_servico,
                        g.texto_breve_servico, g.raw_extra,
                        g.ingestion_run_id, g.imported_at,
                    )
                    for g in items
                ],
            )
        return len(items)

    async def get(
        self, documento_compras: str, item: str
    ) -> PurchaseOrderGc | None:
        async with connect_payments() as c:
            row = await c.fetchrow(
                """
                SELECT * FROM payments.purchase_order_gc
                WHERE documento_compras = $1 AND item = $2
                """,
                documento_compras, item,
            )
            return (
                PurchaseOrderGc.model_validate(record_to_dict(row))
                if row else None
            )

    async def list_for_servico(self, numero_servico: str) -> list[PurchaseOrderGc]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.purchase_order_gc
                WHERE numero_servico = $1
                ORDER BY documento_compras, item
                """,
                numero_servico,
            )
            return [PurchaseOrderGc.model_validate(record_to_dict(r)) for r in rows]

    async def count(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.purchase_order_gc")
            return int(n or 0)


# ---------------------------------------------------------------------------
# CostCenterAccount (CC + CONTA)
# ---------------------------------------------------------------------------


class PgCostCenterAccountRepository(CostCenterAccountRepository):

    async def bulk_upsert(self, items: list[CostCenterAccount]) -> int:
        if not items:
            return 0
        async with connect_payments() as c:
            await c.executemany(
                """
                INSERT INTO payments.cost_center_account (
                    centro_de_custo, conta_razao, ingestion_run_id, imported_at
                ) VALUES ($1, $2, $3, $4)
                ON CONFLICT (centro_de_custo, conta_razao) DO NOTHING
                """,
                [
                    (
                        cca.centro_de_custo, cca.conta_razao,
                        cca.ingestion_run_id, cca.imported_at,
                    )
                    for cca in items
                ],
            )
        return len(items)

    async def list_all(self) -> list[CostCenterAccount]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT * FROM payments.cost_center_account
                ORDER BY centro_de_custo, conta_razao
                """
            )
            return [CostCenterAccount.model_validate(record_to_dict(r)) for r in rows]

    async def get_contas_for_cc(self, centro_de_custo: str) -> list[str]:
        async with connect_payments() as c:
            rows = await c.fetch(
                """
                SELECT conta_razao FROM payments.cost_center_account
                WHERE centro_de_custo = $1
                ORDER BY conta_razao
                """,
                centro_de_custo,
            )
            return [r["conta_razao"] for r in rows]

    async def count(self) -> int:
        async with connect_payments() as c:
            n = await c.fetchval("SELECT COUNT(*) FROM payments.cost_center_account")
            return int(n or 0)
