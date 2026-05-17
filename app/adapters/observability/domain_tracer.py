"""Wrapper de Tracer que injeta tag `domain=<x>` em todos os spans/eventos.

Por que existe?

Fase 0 do SDD pede "telemetria por domínio" — todas as métricas e traces de
queries/jobs do `payments` precisam ser segmentáveis no Grafana/LangFuse
pelo tag `domain=payments`, sem que cada call site precise lembrar de
incluir o tag no metadata.

Padrão de uso:

    from app.adapters.observability.domain_tracer import payments_tracer

    payments_tracer.trace(
        "wf_payment.ingestion_run",
        input_data={"rows": 869_663},
        output_data={"inserted": 869_663, "duration_s": 142},
    )
    # ↑ vira span com atributo `domain=payments` sem o caller precisar lembrar

Para outros domínios futuros: criar `radar_tracer = DomainTracer("radar")`.

Compatível com `Tracer` Protocol — pode ser injetado em qualquer lugar que
espere a interface base.
"""

from __future__ import annotations

from typing import Any

from app.adapters.observability.composite_tracer import CompositeTracer
from app.core.ports.observability import Tracer


class DomainTracer(Tracer):
    """Forward para o `CompositeTracer` global injetando tag `domain`."""

    def __init__(self, domain: str, base: Tracer | None = None) -> None:
        self.domain = domain
        self._base = base or CompositeTracer()

    def _merge(self, metadata: dict | None) -> dict:
        merged = dict(metadata or {})
        merged.setdefault("domain", self.domain)
        return merged

    def trace(
        self,
        name: str,
        input_data: Any,
        output_data: Any,
        metadata: dict | None = None,
    ) -> None:
        self._base.trace(
            name=f"{self.domain}.{name}" if not name.startswith(f"{self.domain}.") else name,
            input_data=input_data,
            output_data=output_data,
            metadata=self._merge(metadata),
        )

    def event(self, name: str, payload: dict) -> None:
        self.trace(
            name=name,
            input_data=None,
            output_data=payload,
            metadata={"kind": "event"},
        )


# Singletons por domínio. Importar diretamente.
payments_tracer = DomainTracer("payments")
