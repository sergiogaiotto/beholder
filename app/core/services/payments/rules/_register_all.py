"""Side-effect imports: importa todos os 20 handlers para popular RULES_REGISTRY.

Quem precisar do registry completo (engine, scripts, tests E2E) faz:

    import app.core.services.payments.rules._register_all  # noqa: F401

Isso garante que os handlers estejam registrados antes de tentar resolver
codes via RULES_REGISTRY[code].
"""

from __future__ import annotations

# R1-R4
from app.core.services.payments.rules import regra_1_cnpj  # noqa: F401
from app.core.services.payments.rules import regra_2_validade  # noqa: F401
from app.core.services.payments.rules import regra_3_texto_preco  # noqa: F401
from app.core.services.payments.rules import regra_4_cobertura  # noqa: F401

# R5 família
from app.core.services.payments.rules import regra_5_uf  # noqa: F401
from app.core.services.payments.rules import regra_5_cidade  # noqa: F401
from app.core.services.payments.rules import regra_5_tecnologia  # noqa: F401
from app.core.services.payments.rules import regra_5_atividade  # noqa: F401
from app.core.services.payments.rules import regra_5_categoria  # noqa: F401
from app.core.services.payments.rules import regra_5_objeto  # noqa: F401

# R6 família
from app.core.services.payments.rules import regra_6_1_pedido  # noqa: F401
from app.core.services.payments.rules import regra_6_2_data  # noqa: F401
from app.core.services.payments.rules import regra_6_3_contrato  # noqa: F401
from app.core.services.payments.rules import regra_6_4_item  # noqa: F401
from app.core.services.payments.rules import regra_6_5_valor  # noqa: F401
from app.core.services.payments.rules import regra_6_6_gc_contrato  # noqa: F401
from app.core.services.payments.rules import regra_6_7_gc_item  # noqa: F401
from app.core.services.payments.rules import regra_6_8_gc_descricao  # noqa: F401
from app.core.services.payments.rules import regra_6_9_gc_preco  # noqa: F401

# LPU
from app.core.services.payments.rules import regra_lpu_preco  # noqa: F401

ALL_RULE_CODES: tuple[str, ...] = (
    "REGRA_1", "REGRA_2", "REGRA_3", "REGRA_4",
    "REGRA_5_UF", "REGRA_5_CIDADE", "REGRA_5_TECNOLOGIA",
    "REGRA_5_ATIVIDADE", "REGRA_5_CATEGORIA", "REGRA_5_OBJETO",
    "REGRA_6_1", "REGRA_6_2", "REGRA_6_3", "REGRA_6_4", "REGRA_6_5",
    "REGRA_6_6", "REGRA_6_7", "REGRA_6_8", "REGRA_6_9",
    "REGRA_LPU",
)
"""20 códigos oficiais (seed migration 007)."""
