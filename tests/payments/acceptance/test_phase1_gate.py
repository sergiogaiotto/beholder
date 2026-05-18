"""Wrapper pytest do gate de aceitação Fase 1.

Marker `acceptance` skipa por default — rodar com:
    pytest tests/payments/acceptance -v -m acceptance

Útil pra rodar manualmente após mudanças significativas no loader,
parsers ou repos.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.acceptance.run_phase1_gate import (
    DEFAULT_DATA_DIR,
    SLA_SECONDS,
    SOURCES,
    run_gate,
)


pytestmark = pytest.mark.acceptance


async def test_phase1_full_load_meets_sla():
    """Carrega os 8 sources reais e verifica SLA <5min + counts esperados.

    Requer BEHOLDER_DATA_DIR existente. Skipped via marker em runs normais.
    """
    data_dir = Path(
        os.environ.get("BEHOLDER_DATA_DIR", str(DEFAULT_DATA_DIR))
    )
    if not data_dir.is_dir():
        pytest.skip(f"BEHOLDER_DATA_DIR não existe: {data_dir}")

    # Sanidade: arquivos esperados estão lá
    missing = [s.filename for s in SOURCES if not (data_dir / s.filename).exists()]
    if missing:
        pytest.skip(f"arquivos faltando em {data_dir}: {missing}")

    exit_code = await run_gate(data_dir)
    assert exit_code == 0, (
        f"Gate Fase 1 FAILED — SLA <{SLA_SECONDS}s ou algum source quebrou."
    )
