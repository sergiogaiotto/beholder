"""MaritacaExtractionClient — cliente real Maritaca sabia-4 + Instructor (Fase 4).

Pré-C empiricamente validou em 5 PDFs reais:
  - 86% campos populados (supera target SDD G2 de 85%)
  - R$0,37/PDF médio (40× abaixo do budget R$15/PDF)
  - 17s/PDF latência (extract + LLM)
  - Truncate de 60k chars no texto não impactou qualidade

Mecânica:
  - `instructor.from_openai(AsyncOpenAI)` aponta para endpoint Maritaca
    OpenAI-compatible (`/v1/chat/completions`); `instructor.Mode.JSON`
    força `response_format={"type":"json_object"}` que Maritaca suporta.
  - Pydantic coage o JSON para `ExtractedContractFields` — campos faltantes
    viram None (não erro), seguindo as defaults do schema.
  - Custo calculado a partir de `usage.prompt_tokens / completion_tokens`
    devolvidos pela API.

Issues conhecidas do spike (mitigadas via prompt + UI HITL, não validators
rígidos que rejeitariam linhas):
  - I1: LLM eventualmente devolve UF inválido ("NO" em vez de "PA"/"AM").
    Prompt pede 27 estados ISO; UI HITL permite correção manual.
  - I2: `contrato_num_sap` às vezes recebe REF WS por engano. Prompt
    distingue explicitamente os dois.
  - I4/I5: `val_fix_cab` e `contratante_cnpj` faltam em maioria dos
    contratos sob-demanda — None é legítimo, não disparar erro.

Os campos `ref_ws` e `contrato_num_sap` aparecem no Pré-C mas NÃO no
schema atual `ExtractedContractFields`. Ficam fora do retorno por ora;
quando o schema evoluir, basta acrescentar aqui no prompt.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import instructor
from openai import AsyncOpenAI

from app.config import get_settings
from app.core.services.payments.extraction._client import ExtractionResult
from app.core.services.payments.extraction.schemas import ExtractedContractFields

logger = logging.getLogger(__name__)


# Preços Maritaca sabia-4 (R$ por 1k tokens). Confere com `maritaca_adapter.py`.
COST_PER_1K_INPUT_BRL = Decimal("0.0008")
COST_PER_1K_OUTPUT_BRL = Decimal("0.0024")

# Pré-C R6: truncate de 60k chars é suficiente — todos os 5 contratos
# couberam mesmo o maior (CW28648 60 pgs / 144k chars) com 100% extração.
MAX_CHARS_PROMPT = 60_000

# Folha de rosto sozinha cabe em ~600-800 tokens (vide Pré-C: 233-351 out).
# 2k dá folga generosa. LPU items NÃO são extraídos nesta chamada (vide
# system prompt) — uma LPU completa estoura facilmente 8k tokens; quando
# a Fase 4 Bloco C entregar extração de LPU, vira chamada separada com
# schema dedicado e paginação.
MAX_OUTPUT_TOKENS = 2_000

# Determinismo: temperatura baixa pra reduzir variação entre execuções.
TEMPERATURE = 0.1


SYSTEM_PROMPT = """Você é um extrator forense de contratos jurídicos da Claro com fornecedores empreiteiras de telecom.

Sua única função é ler o texto bruto de um contrato em PT-BR e devolver um objeto JSON estruturado com os campos solicitados, respeitando estritamente o schema fornecido.

Regras de extração:

1. **CNPJ**: devolva apenas dígitos (14 caracteres). Remova pontos, barras e hífens. Se ausente, devolva null.
2. **Datas (`valid_from`, `valid_to`)**: devolva em formato ISO 8601 (YYYY-MM-DD). Se o contrato diz "vigência de 12 meses a partir de 01/01/2024", então valid_from=2024-01-01 e valid_to=2024-12-31. Se ausente ou ambíguo, devolva null.
3. **`val_fix_cab`**: valor fixo mensal de cabeçalho em reais (decimal). Só preencha se o contrato é tipo "FIXO MENSAL" com valor explícito. Contratos sob-demanda têm val_fix_cab=null — isso é legítimo.
4. **`uf`**: lista de UFs (siglas oficiais de 2 letras dos 27 estados brasileiros: AC, AL, AP, AM, BA, CE, DF, ES, GO, MA, MT, MS, MG, PA, PB, PR, PE, PI, RJ, RN, RS, RO, RR, SC, SP, SE, TO). NUNCA invente siglas. Se o contrato cobre "Norte", expanda para as UFs da região (AC, AM, AP, PA, RO, RR, TO).
5. **`cidade`**: lista de cidades específicas. Se o contrato cobre "todo o estado de SP" sem listar cidades específicas, devolva lista vazia [].
6. **`categoria`**: classifique em uma destas (case-sensitive): "FIXO MENSAL", "SOB DEMANDA", "MANUTENÇÃO", "RECUPERAÇÃO", "OBRA PONTUAL".
7. **`tecnologia`**: ex. "FIBRA", "HFC", "GPON", "INFRAESTRUTURA", "COAXIAL". Frase descritiva se for combinação.
8. **`atividade`**: descrição livre da atividade principal (ex. "MANUTENÇÃO PREVENTIVA E CORRETIVA", "OBRAS PONTUAIS").
9. **`empreiteira_nome`**: razão social da empresa contratada (a fornecedora, não a Claro).
10. **`empreiteira_cnpj`**: CNPJ da empreiteira.
11. **`contratante_cnpj`**: CNPJ da Claro. Comum no preâmbulo. Se não estiver no texto, devolva null (não invente).
12. **`objeto_contrato`**: frase curta descritiva do objeto (1-2 linhas), copiada/adaptada da cláusula "OBJETO" do contrato.
13. **`lpu_items`**: SEMPRE devolva lista vazia `[]`. A Lista de Preços Unitários é extraída em chamada separada (escopo Fase 4 Bloco C); nesta chamada o foco é exclusivamente a folha de rosto. Mesmo que o contrato contenha tabela LPU, ignore-a aqui.

NUNCA invente dados. Se você não tem certeza, devolva null. É melhor um campo null que será revisado manualmente do que um valor errado que será aceito sem checagem.
"""


USER_PROMPT_TEMPLATE = """Texto do contrato (filename: {filename}):

```
{pdf_text}
```

Extraia os campos no schema. Lembre: null > inventar.
"""


class MaritacaExtractionClient:
    """Cliente real Maritaca sabia-4 com structured output via Instructor.

    Construção é fail-fast: se MARITACA_API_KEY não existir, levanta
    RuntimeError no construtor. Permite o worker decidir entre Maritaca
    e Mock baseado na presença da chave.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.maritaca_api_key
        if not self.api_key:
            raise RuntimeError(
                "MARITACA_API_KEY não configurada — não é possível criar "
                "MaritacaExtractionClient. Use MockExtractionClient em dev."
            )
        self.model = model or settings.maritaca_model
        self.base_url = (base_url or settings.maritaca_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds

        # Cliente OpenAI-compatible. Maritaca expõe `/v1/chat/completions`
        # sob o `base_url` configurado. Instructor envelopa para devolver
        # objetos Pydantic em vez de dict cru.
        self._raw_client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=f"{self.base_url}/v1",
            timeout=timeout_seconds,
        )
        # Mode.MD_JSON: pede JSON dentro de ```json``` codeblock via prompt,
        # NÃO envia `response_format`. Necessário pra Maritaca: ela aceita
        # `response_format={"type":"json_object"}` mas faz validação
        # server-side bugada que rejeita arrays legítimos (e.g. uf=['SP'])
        # com 'is not of type object'. MD_JSON contorna entregando JSON puro
        # via prompt; Instructor parseia o codeblock e coage para Pydantic.
        self._client = instructor.from_openai(
            self._raw_client, mode=instructor.Mode.MD_JSON,
        )

    async def extract(
        self,
        *,
        pdf_text: str,
        pdf_filename: str,
    ) -> ExtractionResult:
        """Extrai folha de rosto + LPU items via 1 chamada Maritaca.

        Truncate de 60k chars conforme Pré-C R6.
        """
        truncated = pdf_text[:MAX_CHARS_PROMPT]
        truncated_size = len(truncated)
        user_msg = USER_PROMPT_TEMPLATE.format(
            filename=pdf_filename, pdf_text=truncated,
        )

        # Instructor faz retry interno se o JSON não casa com o schema.
        # Default é 3 tentativas; pra contratos longos isso pode somar
        # custo, então limito em 2.
        fields, raw_response = await self._client.chat.completions.create_with_completion(
            model=self.model,
            response_model=ExtractedContractFields,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=TEMPERATURE,
            max_retries=2,
        )

        usage = getattr(raw_response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cost = self._compute_cost(prompt_tokens, completion_tokens)

        logger.info(
            "maritaca extract OK filename=%s chars=%d in_tok=%d out_tok=%d cost=R$%.4f",
            pdf_filename, truncated_size, prompt_tokens, completion_tokens, float(cost),
        )

        return ExtractionResult(
            fields=fields,
            cost_brl=cost,
            llm_model_used=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    @staticmethod
    def _compute_cost(prompt_tokens: int, completion_tokens: int) -> Decimal:
        """Cost em BRL conforme tabela Maritaca sabia-4.

        Devolve Decimal arredondado a 6 casas pra caber em
        `extraction_job.cost_brl NUMERIC(10,6)`.
        """
        input_cost = (Decimal(prompt_tokens) / 1000) * COST_PER_1K_INPUT_BRL
        output_cost = (Decimal(completion_tokens) / 1000) * COST_PER_1K_OUTPUT_BRL
        return (input_cost + output_cost).quantize(Decimal("0.000001"))


def build_maritaca_client_or_none() -> MaritacaExtractionClient | None:
    """Factory que retorna o cliente real se MARITACA_API_KEY existe,
    senão None. Worker usa para decidir entre Maritaca e Mock sem
    explodir em dev/CI onde a chave não está setada."""
    try:
        return MaritacaExtractionClient()
    except RuntimeError as exc:
        logger.info("Maritaca client unavailable, fallback to Mock: %s", exc)
        return None
