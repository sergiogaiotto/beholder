"""Smoke test do DomainTracer — auto-injeção do tag `domain=payments`."""

from __future__ import annotations

from typing import Any

from app.adapters.observability.domain_tracer import DomainTracer
from app.core.ports.observability import Tracer


class _RecorderTracer(Tracer):
    """Tracer fake que captura calls para asserção."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def trace(self, name, input_data, output_data, metadata=None):
        self.calls.append({
            "kind": "trace",
            "name": name,
            "input": input_data,
            "output": output_data,
            "metadata": metadata,
        })

    def event(self, name, payload):
        self.calls.append({"kind": "event", "name": name, "payload": payload})


def test_trace_injects_domain_tag():
    recorder = _RecorderTracer()
    payments_tracer = DomainTracer("payments", base=recorder)

    payments_tracer.trace(
        "wf_payment.ingest",
        input_data={"rows": 1000},
        output_data={"inserted": 1000},
        metadata={"phase": "1"},
    )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["metadata"] == {"phase": "1", "domain": "payments"}
    # Prefixa o nome do span com o domínio
    assert call["name"] == "payments.wf_payment.ingest"


def test_trace_preserves_existing_domain_metadata():
    recorder = _RecorderTracer()
    payments_tracer = DomainTracer("payments", base=recorder)

    # Caller pode override (e.g., evento que cruza domínios)
    payments_tracer.trace(
        "cross_domain.ping",
        input_data=None,
        output_data=None,
        metadata={"domain": "shared"},
    )

    assert recorder.calls[0]["metadata"]["domain"] == "shared"


def test_event_routes_through_trace_with_domain():
    recorder = _RecorderTracer()
    payments_tracer = DomainTracer("payments", base=recorder)

    payments_tracer.event("rule_executed", {"rule_code": "REGRA_1"})

    # event() é açúcar pra trace() com kind=event
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["metadata"]["domain"] == "payments"
    assert recorder.calls[0]["metadata"]["kind"] == "event"
