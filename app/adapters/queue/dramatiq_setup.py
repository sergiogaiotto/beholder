"""Setup do dramatiq — broker Redis + middleware padrão.

Estratégia:
  - Único broker global por processo (importação de `app.adapters.queue` já
    o registra). Os módulos worker (`app.workers.*`) declaram actors com
    `@dramatiq.actor` que automaticamente se registram nesse broker.
  - Middleware default + AsyncIO (para actors que precisam de event loop
    — por exemplo, extração PDF que usa httpx async pra chamar Maritaca).

Ambiente:
  - Em dev (docker-compose.dev.yml), Redis está em `redis://redis:6379/0`.
  - Em testes, `dramatiq.brokers.stub.StubBroker` é usado via `conftest.py`
    para validar actors sem precisar de Redis rodando.

Não confundir broker do dramatiq com o pool PG: são canais ortogonais.
"""

from __future__ import annotations

import os
from typing import Optional

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AgeLimit, Callbacks, Pipelines, Retries, ShutdownNotifications, TimeLimit

from app.config import get_settings

_broker: Optional[dramatiq.Broker] = None


def _build_broker() -> dramatiq.Broker:
    """Constrói o broker Redis com middleware padrão.

    `AsyncIO` middleware NÃO está incluído por default em dramatiq — é opt-in
    via `dramatiq[asyncio]`. Como não há benefício imediato em Fase 0
    (workers são síncronos por padrão), começamos sem. Habilitar quando
    Fase 4 introduzir actors `async def` que chamem Maritaca via httpx.
    """
    s = get_settings()
    middleware = [
        AgeLimit(),
        TimeLimit(),
        ShutdownNotifications(),
        Callbacks(),
        Pipelines(),
        Retries(max_retries=3, min_backoff=1000, max_backoff=900000),
    ]
    return RedisBroker(url=s.redis_url, middleware=middleware)


def get_broker() -> dramatiq.Broker:
    """Retorna o broker global, criando se necessário.

    Idempotente — uvicorn pode importar este módulo várias vezes (cada
    worker process tem seu próprio import). `dramatiq.set_broker` é
    chamado uma vez por processo.
    """
    global _broker
    if _broker is None:
        _broker = _build_broker()
        dramatiq.set_broker(_broker)
    return _broker


# Inicializa broker no import — torna `@dramatiq.actor` utilizável diretamente
# em módulos `app.workers.*` sem precisar chamar `get_broker()` explicitamente.
# Pula em test mode (conftest.py substitui por StubBroker antes do import).
if os.environ.get("DRAMATIQ_TESTS") != "1":
    broker = get_broker()
else:
    broker = None  # conftest configura StubBroker
