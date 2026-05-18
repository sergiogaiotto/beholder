"""Importações laterais que populam ANALYTICS_REGISTRY.

Chamar este módulo (ou importar) garante que os handlers R7 estão registrados
antes de tentar resolver via ANALYTICS_REGISTRY[code]. Pattern espelha
`rules/_register_all.py`.

Detectores ainda não implementados estão comentados; descomenta conforme
os blocos C/D entregam.
"""

from __future__ import annotations

# Bloco B — 4 detectores estatísticos.
from app.core.services.payments.analytics import (  # noqa: F401
    r7_fixo_variavel,
    r7_lag_pagto,
    r7_lpu_outlier,
    r7_qtd_quebrada,
)

# Bloco C — 3 detectores temporais.
from app.core.services.payments.analytics import (  # noqa: F401
    r7_pico_fim_periodo,
    r7_periodos_atipicos,
    r7_validade_vencida,
)

# Bloco D — 4 detectores complexos.
from app.core.services.payments.analytics import (  # noqa: F401
    r7_consumo_perfil,
    r7_empreiteira_padrao,
    r7_lpu_padrao_servico,
    r7_recorr_variavel,
)
