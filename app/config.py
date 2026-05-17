"""Configurações centralizadas via pydantic-settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Beholder"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "change-me"
    app_base_url: str = "http://localhost:8000"

    # DB — PostgreSQL via asyncpg
    # Aceita tanto DSN puro (postgresql://user:pass@host:port/db) quanto a
    # forma SQLAlchemy (postgresql+asyncpg://...). O método `pg_dsn` normaliza
    # para o formato esperado por asyncpg (sem o sufixo +asyncpg).
    database_url: str = "postgresql://beholder:beholder@localhost:5432/beholder"

    # Pool de conexões — calibrado para throughput.
    # min_size: conexões "warm" mantidas no pool (latência baixa em pico)
    # max_size: teto. Em produção, ajustar conforme `max_connections` do PG
    # (ver: SHOW max_connections; típico 100). Cada worker uvicorn carrega
    # o seu próprio pool — dimensionar como pool_max * num_workers <= 80%
    # de max_connections deixando folga para conexões administrativas.
    pg_pool_min_size: int = 5
    pg_pool_max_size: int = 20
    pg_pool_max_inactive_connection_lifetime: float = 300.0  # 5 min — recicla conexões ociosas
    pg_command_timeout: float = 30.0                          # timeout default por query
    pg_statement_cache_size: int = 1024                       # cache de prepared statements por conexão

    # Auth
    # Bootstrap do primeiro usuário: NÃO há credenciais default. Quando a
    # tabela `users` está vazia, a primeira submissão em /login (qualquer
    # username/senha que o operador escolher) cria o usuário ROOT.
    # Fluxo em app/api/routers/pages.py:login_submit.
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 480

    # LLMs — ClaroHub (Hub GPU interno, OpenAI-compatible) e Maritaca (Sabia-4)
    # ClaroHub: endpoint https://hub-gpus.claro.com.br/gpt20, modelo
    # openai/gpt-oss-20b (reasoning). Proxy corporativo obrigatório.
    claro_hub_api_key: str = ""
    claro_hub_endpoint: str = "https://hub-gpus.claro.com.br/gpt20"
    claro_hub_model: str = "openai/gpt-oss-20b"

    maritaca_api_key: str = ""
    maritaca_model: str = "sabia-4"
    maritaca_base_url: str = "https://chat.maritaca.ai/api"

    # Router — política padrão de seleção de modelo.
    # Beholder roda em ambiente Claro: Maritaca como default (cloud, baixo
    # custo, ótimo PT-BR), ClaroHub como fallback/cheap (on-prem, sem
    # custo direto por chamada, ideal para volumes altos de extração PDF).
    router_default_model: str = "sabia-4"
    router_fallback_model: str = "openai/gpt-oss-20b"
    router_cheap_model: str = "openai/gpt-oss-20b"

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    mlflow_tracking_uri: str = ""
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "beholder"

    # Policy
    opa_url: str = ""

    # Proxy para acesso externo (Hub GPU Claro)
    http_proxy: str = ""
    https_proxy: str = ""

    # Guardrails
    guardrail_input_max_chars: int = 20000
    guardrail_injection_block: bool = True
    guardrail_pii_redact: bool = True

    # ============================================================
    # Payments domain (Fase 0+ Empreiteiras-WF)
    # ============================================================

    # Pool PG dedicado ao schema `payments`. Isolamento de carga:
    # cargas batch (XLSX → wf_payment 869k linhas) não roubam conexões dos
    # endpoints existentes (Radar/Raio-X). Habilitado em produção; em dev
    # default usa o mesmo banco mas dois pools (limita disputa).
    payments_pool_min_size: int = 2
    payments_pool_max_size: int = 10
    payments_pool_max_inactive_connection_lifetime: float = 300.0
    payments_pool_command_timeout: float = 60.0  # batch jobs precisam de mais

    # Redis (broker dramatiq + cache). Em dev: docker-compose.yml oferece.
    redis_url: str = "redis://localhost:6379/0"

    # DocumentStore — armazenamento de PDFs/anexos.
    # Modo: "filesystem" (dev) | "s3" (prod). MinIO local é compatible com s3.
    document_store_mode: str = "filesystem"
    document_store_fs_root: str = ""  # default: <repo>/data/documents
    # S3 config (também usado com MinIO em dev)
    s3_endpoint_url: str = ""  # vazio = AWS S3 real; preencher para MinIO
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "beholder-documents"
    s3_region: str = "us-east-1"

    # Dramatiq worker — concurrency por processo
    worker_processes: int = 1
    worker_threads_per_process: int = 4

    # Diretório de dados brutos (XLSX/PDFs/TXT — fora do repo). Usado pelos
    # scripts de ingestão (Pré-A/B/C + Fase 1).
    beholder_data_dir: str = r"C:\_PERSONAL\beholder_data"

    @property
    def pg_dsn(self) -> str:
        """Normaliza o DSN para o formato aceito por `asyncpg.connect`/`create_pool`.

        - `postgresql+asyncpg://...`  → `postgresql://...`  (asyncpg não usa o sufixo)
        - `postgres://...`            → `postgresql://...`  (alias compatível)
        """
        url = self.database_url.strip()
        if url.startswith("postgresql+asyncpg://"):
            url = "postgresql://" + url[len("postgresql+asyncpg://"):]
        elif url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
