"""Registry: target_entity (str) → repository class com bulk_insert / bulk_upsert.

Inclui os 8 repos que o loader pode invocar para ingestão batch da Fase 1.
Demais repos (catálogos, workflow, single-row creates) não estão aqui —
usam-se diretamente via importação específica.
"""

from __future__ import annotations

import re
from typing import Any

from app.adapters.db.repositories.payments import (
    PgCostCenterAccountRepository,
    PgLPUItemRepository,
    PgPurchaseOrderGcRepository,
    PgPurchaseOrderHeaderRepository,
    PgPurchaseOrderItemRepository,
    PgServicePackageRepository,
    PgSupplierBridgeRepository,
    PgWFPaymentRepository,
)

REPO_REGISTRY: dict[str, type] = {
    "SupplierBridge": PgSupplierBridgeRepository,
    "PurchaseOrderHeader": PgPurchaseOrderHeaderRepository,
    "PurchaseOrderItem": PgPurchaseOrderItemRepository,
    "ServicePackage": PgServicePackageRepository,
    "PurchaseOrderGc": PgPurchaseOrderGcRepository,
    "CostCenterAccount": PgCostCenterAccountRepository,
    "WFPayment": PgWFPaymentRepository,
    "LPUItem": PgLPUItemRepository,
}


def resolve_repo(target_entity: str) -> Any:
    """Retorna uma instância fresh do repo capaz de bulk_insert/bulk_upsert.

    Levanta ValueError se target_entity não estiver no registry — sinal
    explícito de que o domain quer ingestão batch mas o repo não foi mapeado.
    """
    cls = REPO_REGISTRY.get(target_entity)
    if cls is None:
        raise ValueError(
            f"no bulk-capable repo registered for {target_entity!r}; "
            f"add to REPO_REGISTRY in app.core.services.payments.ingestion.registry"
        )
    return cls()


def target_table_for(target_entity: str) -> str:
    """CamelCase → fully-qualified PG table name."""
    return f"payments.{_camel_to_snake(target_entity)}"


_CAMEL_BOUNDARY_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_BOUNDARY_2 = re.compile(r"([a-z0-9])([A-Z])")


def _camel_to_snake(name: str) -> str:
    """CamelCase → snake_case, respeitando siglas (LPUItem → lpu_item).

    Não quebra siglas em letras individuais: 'WFPayment' → 'wf_payment'
    (não 'w_f_payment'); 'LPUItem' → 'lpu_item'.
    """
    s = _CAMEL_BOUNDARY_1.sub(r"\1_\2", name)
    s = _CAMEL_BOUNDARY_2.sub(r"\1_\2", s)
    return s.lower()
