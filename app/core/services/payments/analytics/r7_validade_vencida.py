"""R7_VALIDADE_VENCIDA — pagamentos após validade do contrato (heurística SQL).

Flag pagamentos cuja `data_pedido` é posterior a `contract_version.valid_to`
da versão corrente do contrato (current_version_id). Não usa o filtro
universal — interesse aqui é justamente o que está fora do operacional
ativo (ignore_universe_filter na seed seria adequado).

Parâmetros:
  grace_days: int   — dias após valid_to ainda tolerados (default 0)
  lookback_days: int — janela retroativa pra evitar ruído (default 365)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.analytics import register
from app.core.services.payments.analytics._base import (
    AnalyticContext,
    AnalyticFindingDraft,
)


@register("R7_VALIDADE_VENCIDA")
async def r7_validade_vencida(ctx: AnalyticContext) -> AsyncIterator[AnalyticFindingDraft]:
    grace_days = int(ctx.detector.threshold_params.get("grace_days", 0))
    lookback_days = int(ctx.detector.threshold_params.get("lookback_days", 365))

    async with connect_payments() as c:
        rows = await c.fetch(
            """
            SELECT
                wp.id, wp.data_pedido, wp.os_num, wp.empreiteira,
                wp.valor_total_final,
                cv.valid_to,
                cm.id  AS contract_master_id,
                sb.id  AS supplier_id
            FROM payments.wf_payment wp
            JOIN payments.supplier_bridge sb
              ON sb.empreiteira = wp.empreiteira
            JOIN payments.contract_master cm
              ON cm.supplier_bridge_id = sb.id AND cm.is_monitored = TRUE
            JOIN payments.contract_version cv
              ON cv.id = cm.current_version_id
            WHERE wp.data_pedido > cv.valid_to + ($1 || ' days')::interval
              AND wp.data_pedido <= cv.valid_to + ($2 || ' days')::interval
            """,
            str(grace_days),
            str(lookback_days),
        )

    for r in rows:
        days_over = (r["data_pedido"] - r["valid_to"]).days
        yield AnalyticFindingDraft(
            detector_code="R7_VALIDADE_VENCIDA",
            severity=Severity(ctx.detector.severity.value),
            score=float(days_over),
            expected_range={
                "valid_to": r["valid_to"].isoformat(),
                "grace_days": grace_days,
                "method": "validity_check",
            },
            actual_value={
                "data_pedido": r["data_pedido"].isoformat(),
                "days_over": days_over,
                "empreiteira": r["empreiteira"],
                "os_num": r["os_num"],
                "valor_total_final": float(r["valor_total_final"] or 0),
            },
            wf_payment_id=int(r["id"]),
            wf_payment_data_pedido=r["data_pedido"],
            supplier_id=r["supplier_id"],
            reason=(
                f"OS {r['os_num']} pago {days_over}d após validade "
                f"({r['valid_to']}) do contrato {r['contract_master_id']}"
            ),
        )
