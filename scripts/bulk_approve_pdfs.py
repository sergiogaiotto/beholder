"""Bulk-aprova todos os ExtractionJobs em status='review', usando
`extracted_fields` direto (sem edição) — equivalente a "aceitar tudo
como Maritaca extraiu".

Cada aprovação materializa ContractMaster + ContractVersion. Necessário
pra que as regras R1-R6.9 tenham `contract_version` vigente pra comparar
contra wf_payment.

Em prod a controladoria edita os campos antes de aprovar — esse script
existe pra UAT/E2E acelerar o caminho. Falhas individuais (CNPJ
ausente, etc.) são logadas mas não param o batch.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.adapters.db.postgres_payments import connect_payments
from app.adapters.db.repositories.user_repo import PgUserRepository
from app.core.services.payments.extraction.service import (
    PaymentsExtractionService,
)


async def _resolve_user_id(username: str) -> UUID:
    repo = PgUserRepository()
    user = await repo.get_by_username(username)
    if user is None:
        raise SystemExit(f"usuário não encontrado: {username}")
    return user.id


def _to_decimal(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


async def main() -> int:
    user_id = await _resolve_user_id("sergio.gaiotto")
    svc = PaymentsExtractionService()

    async with connect_payments() as c:
        rows = await c.fetch(
            "SELECT id, pdf_filename, extracted_fields FROM payments.extraction_job "
            "WHERE status='review' ORDER BY pdf_filename"
        )

    print(f"\n=== Bulk approve: {len(rows)} jobs em 'review' ===\n")
    approved = 0
    failed = 0
    for row in rows:
        job_id = row["id"]
        fname = row["pdf_filename"]
        raw = row["extracted_fields"]
        if raw is None:
            print(f"[SKIP] {fname}: extracted_fields vazio")
            continue
        fields = raw if isinstance(raw, dict) else json.loads(raw)
        # Coage tipos pra Pydantic aceitar.
        edited = {
            "empreiteira_nome": fields.get("empreiteira_nome"),
            "empreiteira_cnpj": fields.get("empreiteira_cnpj"),
            "contratante_cnpj": fields.get("contratante_cnpj"),
            "objeto_contrato": fields.get("objeto_contrato"),
            "categoria": fields.get("categoria"),
            "tecnologia": fields.get("tecnologia"),
            "atividade": fields.get("atividade"),
            "uf": fields.get("uf") or [],
            "cidade": fields.get("cidade") or [],
            "val_fix_cab": _to_decimal(fields.get("val_fix_cab")),
            "valid_from": _to_date(fields.get("valid_from")),
            "valid_to": _to_date(fields.get("valid_to")),
            "lpu_items": fields.get("lpu_items") or [],
        }
        try:
            cm_id = await svc.approve_job(
                job_id, edited_fields=edited, approved_by_id=user_id,
            )
            approved += 1
            print(f"[OK]   {fname:75s} → contract_master={cm_id}")
        except ValueError as exc:
            failed += 1
            print(f"[FAIL] {fname:75s} {exc}")

    print(f"\n[DONE] approved={approved} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
