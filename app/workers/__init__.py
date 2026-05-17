"""Workers dramatiq do domínio payments.

Cada submódulo declara actors `@dramatiq.actor`. O dramatiq CLI descobre
todos os actors importáveis a partir do módulo passado:

    dramatiq app.workers --processes 1 --threads 4

Em Fase 0 só temos o healthcheck — workers reais (extração PDF, ingestão,
reconciliação) entram nas Fases 1/2/4.
"""

# Garante que o broker é inicializado quando o CLI do dramatiq importa este
# pacote. `dramatiq_setup` registra o broker global via `set_broker()`.
from app.adapters.queue import dramatiq_setup  # noqa: F401

# Importação de módulos com actors — cada import registra os @dramatiq.actor
# no broker. Ordem não importa, mas listar explicitamente facilita auditoria.
from app.workers import healthcheck  # noqa: F401, E402
