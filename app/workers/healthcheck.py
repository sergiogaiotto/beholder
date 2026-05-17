"""Actor de healthcheck — valida que o worker dramatiq está vivo + Redis OK.

Fase 0: este é o ÚNICO actor declarado. Serve dois propósitos:
  1. Acceptance gate da Fase 0 — comprova que worker recebe jobs via Redis
     e executa código no processo separado (vs FastAPI).
  2. Probe genérico — UI/admin pode disparar `healthcheck.send(payload)` e
     conferir que volta em <1s.

Implementação simples e síncrona — não toca DB nem rede externa. Útil para
medir latência baseline do broker.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime, timezone

import dramatiq

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="payments_default", max_retries=0, time_limit=10_000)
def healthcheck(payload: dict | None = None) -> dict:
    """Echo + metadata do processo worker.

    Args:
        payload: opcional — qualquer dict serializável.

    Returns:
        Dict com hostname, pid, timestamp, payload echoado. NÃO é retornado
        ao caller via dramatiq (actors são fire-and-forget); fica em log
        para inspeção.
    """
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "timestamp": now,
        "echo": payload or {},
        "monotonic_ms": int(time.monotonic() * 1000),
    }
    logger.info("healthcheck OK %s", result)
    return result
