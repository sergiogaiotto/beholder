"""R7_PICO_FIM_PERIODO — picos de pagamento nos últimos N dias antes do
fim da validade do contrato (técnica timeseries_outlier).

Compara `total pago nos últimos `last_n_days` antes de valid_to` vs a
média mensal histórica do contrato. Razão acima de `spike_threshold`
sinaliza concentração suspeita ("rush" no fim do contrato).

Parâmetros:
  last_n_days: int        — janela final (default 30)
  spike_threshold: float  — razão pico/média (default 2.0)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import date, timedelta

from app.adapters.db.postgres_payments import connect_payments
from app.core.domain.payments import Severity
from app.core.services.payments.analytics import register
from app.core.services.payments.analytics._base import (
    AnalyticContext,
    AnalyticFindingDraft,
    universe_filter_for_detector,
)


@register("R7_PICO_FIM_PERIODO")
async def r7_pico_fim_periodo(
    ctx: AnalyticContext,
) -> AsyncIterator[AnalyticFindingDraft]:
    last_n_days = int(ctx.detector.threshold_params.get("last_n_days", 30))
    spike_threshold = float(
        ctx.detector.threshold_params.get("spike_threshold", 2.0)
    )
    universe = universe_filter_for_detector(ctx.detector)
    universe_and = f" AND {universe}" if universe else ""

    async with connect_payments() as c:
        rows = await c.fetch(
            f"""
            SELECT
                cm.id  AS contract_master_id,
                cv.id  AS contract_version_id,
                cv.valid_from, cv.valid_to,
                sb.id  AS supplier_id,
                sb.empreiteira,
                wp.data_pedido,
                wp.valor_total_final,
                wp.id AS wf_payment_id
            FROM payments.contract_master cm
            JOIN payments.contract_version cv ON cv.id = cm.current_version_id
            JOIN payments.supplier_bridge sb  ON sb.id = cm.supplier_bridge_id
            JOIN payments.wf_payment wp       ON wp.empreiteira = sb.empreiteira
              AND wp.data_pedido BETWEEN cv.valid_from AND cv.valid_to
            WHERE cm.is_monitored = TRUE
              AND wp.valor_total_final IS NOT NULL
              {universe_and}
            ORDER BY cm.id, wp.data_pedido
            """
        )

    # Agrupa em memória por contract_version_id.
    by_version: dict = defaultdict(
        lambda: {
            "valid_from": None,
            "valid_to": None,
            "empreiteira": None,
            "supplier_id": None,
            "contract_master_id": None,
            "payments": [],
        }
    )
    for r in rows:
        bucket = by_version[r["contract_version_id"]]
        bucket["valid_from"] = r["valid_from"]
        bucket["valid_to"] = r["valid_to"]
        bucket["empreiteira"] = r["empreiteira"]
        bucket["supplier_id"] = r["supplier_id"]
        bucket["contract_master_id"] = r["contract_master_id"]
        bucket["payments"].append(
            (r["data_pedido"], float(r["valor_total_final"]), r["wf_payment_id"])
        )

    for version_id, bucket in by_version.items():
        valid_from = bucket["valid_from"]
        valid_to = bucket["valid_to"]
        payments = bucket["payments"]
        if not payments or not valid_to:
            continue
        end_window_start = valid_to - timedelta(days=last_n_days)
        end_total = sum(v for d, v, _id in payments if d > end_window_start)
        # Total histórico = todos pagamentos do contrato — janela final
        early_total = sum(v for d, v, _id in payments if d <= end_window_start)
        contract_days = (valid_to - valid_from).days or 1
        early_days = max(1, contract_days - last_n_days)
        # Médias diárias.
        end_daily = end_total / max(1, last_n_days)
        early_daily = early_total / early_days if early_days > 0 else 0.0
        if early_daily == 0.0:
            continue
        ratio = end_daily / early_daily
        if ratio < spike_threshold:
            continue
        # Pagamentos no fim como evidência.
        evidence_ids = [
            pid for d, _v, pid in payments if d > end_window_start
        ]
        yield AnalyticFindingDraft(
            detector_code="R7_PICO_FIM_PERIODO",
            severity=Severity(ctx.detector.severity.value),
            score=float(ratio),
            expected_range={
                "early_daily_avg": early_daily,
                "spike_threshold": spike_threshold,
                "last_n_days": last_n_days,
                "method": "ratio_end_vs_early",
            },
            actual_value={
                "end_daily_avg": end_daily,
                "end_total": end_total,
                "ratio": ratio,
                "empreiteira": bucket["empreiteira"],
                "contract_master_id": str(bucket["contract_master_id"]),
                "valid_to": valid_to.isoformat(),
            },
            evidence_payment_ids=evidence_ids,
            supplier_id=bucket["supplier_id"],
            reason=(
                f"{bucket['empreiteira']} contrato {bucket['contract_master_id']}: "
                f"média diária últimos {last_n_days}d = R$ {end_daily:.2f} "
                f"vs R$ {early_daily:.2f} histórica (ratio={ratio:.2f}x)"
            ),
        )
