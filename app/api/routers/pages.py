"""Router de páginas HTML (template engine Jinja2)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import (
    current_user_optional,
    get_auth_service,
    get_finops_service,
    get_prompt_service,
    get_registry_service,
    get_skill_service,
    get_user_admin_service,
)
from app.core.domain.entities import User
from app.core.services.auth_service import AuthService
from app.core.services.finops_service import FinOpsService
from app.core.services.prompt_service import PromptService
from app.core.services.registry_service import RegistryService
from app.core.services.skill_service import SkillService
from app.core.services.user_admin_service import UserAdminService

BASE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


async def _visible_features_for(user: User | None) -> set[str]:
    """Resolve o set de `feature_key`s (domínio da matriz, ex.: 'vozcliente',
    'raiox') que o user enxerga.

    - Sem usuário: set vazio.
    - Com root: TODAS as features (bypass).
    - Demais: consulta o service.

    Use `_visible_nav_keys_for` se você quer o set traduzido para as keys
    de entrada do menu (ex.: 'radar' em vez de 'vozcliente'), que é o que
    `nav_left.html` espera.
    """
    if not user:
        return set()
    from app.core.services.feature_access_service import (
        CONTROLLED_FEATURES,
        FeatureAccessService,
    )
    if "root" in (user.roles or []):
        return set(CONTROLLED_FEATURES)
    svc = FeatureAccessService()
    return await svc.visible_features(user.roles or [], user.department or "")


async def _visible_nav_keys_for(user: User | None) -> set[str]:
    """Mesma resolução de `_visible_features_for`, mas traduzida para as
    keys de entrada do menu (`nav_left.html`).

    O nav usa keys internas (ex.: 'radar' para a entry "Voz do Cliente"),
    mantidas estáveis pra continuidade de UI (highlight via active_module,
    nome de pasta `app/templates/radar/`, rotas `/radar`). A matriz
    administrativa usa nomes de produto (ex.: 'vozcliente'). O mapeamento
    `NAV_ENTRY_TO_FEATURE` em FeatureAccessService liga os dois mundos.
    """
    from app.core.services.feature_access_service import NAV_ENTRY_TO_FEATURE
    feats = await _visible_features_for(user)
    return {
        nav_key
        for nav_key, feat_key in NAV_ENTRY_TO_FEATURE.items()
        if feat_key in feats
    }


async def _ctx(request: Request, user: User | None, **extras):
    """Contexto base do Jinja. Injeta dois sets relacionados:

      - ``visible_features``: chaves da matriz (`vozcliente`, `raiox`).
        Útil pra qualquer template que queira mostrar ao usuário "que
        features de produto ele tem acesso".
      - ``visible_nav_keys``: chaves de entrada do menu (`radar`, `raiox`).
        Usado por `nav_left.html` para filtrar o grupo "Funcionalidade"
        sem ter que conhecer o mapping entry→feature.

    Async porque consulta a matriz "Funcionalidades por Perfil" (1 query).
    Caller que NÃO renderiza o nav (respostas JSON, redirects) não passa
    por aqui.
    """
    return {
        "request": request,
        "user": user,
        "active_module": extras.pop("active_module", None),
        "visible_features": await _visible_features_for(user),
        "visible_nav_keys": await _visible_nav_keys_for(user),
        **extras,
    }


def _require_any_role(user: User | None, allowed: list[str]) -> User:
    """Bloqueia acesso à página se o usuário não tem ao menos um dos roles.

    Usado como gate no servidor para os grupos Configurações/Monitoramento/
    Administrativo.

    **Root supremacy**: ``root`` é papel supremo e SEMPRE passa, mesmo que
    não esteja listado em ``allowed`` — corrige o sintoma de root tomar 403
    nas próprias telas administrativas. Sem este bypass, todo gate precisaria
    listar ``root`` explicitamente; o bypass evita a duplicação.

    analista_n3 só passa em rotas com role 'analista_n3' OR sem gate.
    """
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "autenticação requerida")
    user_roles = user.roles or []
    # Bypass do root — corolário da política "root tem todos os poderes".
    if "root" in user_roles:
        return user
    if not any(r in allowed for r in user_roles):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"acesso restrito · requer um dos papéis: {', '.join(allowed)}"
        )
    return user


@router.get("/", response_class=HTMLResponse)
async def cockpit(
    request: Request,
    user: User | None = Depends(current_user_optional),
    reg: RegistryService = Depends(get_registry_service),
):
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    from datetime import datetime
    from app.core.services.cockpit_service import CockpitService

    # Atividade pessoal do usuário logado (KPIs, heatmap, timeline, top módulos)
    cockpit_svc = CockpitService()
    activity = await cockpit_svc.user_activity(user_id=str(user.id), days=30)

    # Módulos disponíveis (catálogo, sem custos) — só para mostrar atalhos
    modules_all = await reg.list_all()
    modules_catalog = [
        {
            "id": str(m.id),
            "name": m.name,
            "description": m.description,
            "status": m.status.value,
        }
        for m in modules_all if m.status.value == "active"
    ]

    return templates.TemplateResponse(
        "cockpit/index.html",
        await _ctx(
            request, user,
            active_module="cockpit",
            activity=activity,
            modules_catalog=modules_catalog,
            now=datetime.now().strftime("%d/%m/%Y %H:%M"),
        ),
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, auth: AuthService = Depends(get_auth_service)):
    setup_mode = not await auth.has_any_user()
    return templates.TemplateResponse("auth/login.html", {"request": request, "error": None, "setup_mode": setup_mode})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    auth: AuthService = Depends(get_auth_service),
):
    if not await auth.has_any_user():
        try:
            user = await auth.bootstrap_root(username, password)
        except ValueError:
            user = None
        if user:
            token = auth.issue_token(user)
            request.session["token"] = token
            request.session["username"] = user.username
            return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    user = await auth.authenticate(username, password)
    if not user:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Credenciais inválidas.", "setup_mode": False},
            status_code=401,
        )
    token = auth.issue_token(user)
    request.session["token"] = token
    request.session["username"] = user.username
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


@router.get("/logout")
async def logout_page(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)


# ---------- Radar (BKO Inteligente) ----------

async def _assert_feature_access(user: User, feature_key: str) -> None:
    """Bloqueia acesso à página se a matriz "Funcionalidades por Perfil"
    nega a feature. Root sempre passa (bypass dentro de `can_access`).

    Levanta 403 quando o user não tem acesso. Usado por /radar e /raiox.
    """
    from app.core.services.feature_access_service import FeatureAccessService
    svc = FeatureAccessService()
    allowed = await svc.can_access(
        user.roles or [], user.department or "", feature_key
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"acesso à funcionalidade '{feature_key}' negado pela política "
            f"de Funcionalidades por Perfil",
        )


# ---------- Prompts ----------

@router.get("/prompts", response_class=HTMLResponse)
async def prompts_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: PromptService = Depends(get_prompt_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor'])
    all_prompts = await svc.list_all()
    return templates.TemplateResponse(
        "prompts/index.html",
        await _ctx(
            request, user,
            active_module="prompts",
            prompts=all_prompts,
        ),
    )


# ---------- FinOps ----------

@router.get("/finops", response_class=HTMLResponse)
async def finops_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: FinOpsService = Depends(get_finops_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'finops'])
    by_module = await svc.by_module()
    by_model = await svc.by_model()
    total_cost = sum(r["cost"] for r in by_model)
    total_calls = sum(r["calls"] for r in by_model)

    # Tarifas vivas de cada adapter, para a seção "Como o custo é calculado".
    from app.adapters.llm.factory import build_clients
    rates = []
    for model_name, client in build_clients().items():
        in_rate = float(getattr(client, "cost_per_1k_input", 0.0) or 0.0)
        cached_in_rate = float(getattr(client, "cost_per_1k_cached_input", 0.0) or 0.0)
        rates.append({
            "model": model_name,
            "in_per_1k": in_rate,
            "out_per_1k": float(getattr(client, "cost_per_1k_output", 0.0) or 0.0),
            "cached_in_per_1k": cached_in_rate,
            # economia percentual de usar cache vs input cobrado normalmente
            "cache_savings_pct": (
                round((1 - cached_in_rate / in_rate) * 100, 1)
                if in_rate > 0 and cached_in_rate < in_rate
                else 0.0
            ),
            "is_mock": client.__class__.__name__ == "MockLLMClient",
        })
    rates.sort(key=lambda r: r["model"])

    # Orçamentos avaliados (com gasto corrente vs limite + severidade).
    from app.adapters.db.repositories.finops_repo import (
        PgFinOpsBudgetRepository, PgFinOpsModelPolicyRepository,
    )
    from app.core.services.finops_service import (
        FinOpsBudgetService, FinOpsPolicyService,
    )
    budget_svc = FinOpsBudgetService(
        PgFinOpsBudgetRepository(), svc.repo,
    )
    policy_svc = FinOpsPolicyService(PgFinOpsModelPolicyRepository())
    budget_statuses = await budget_svc.evaluate_all()
    recent_alerts = await budget_svc.recent_alerts(10)
    policies = await policy_svc.list()

    # Showback multi-dimensional. Não falha a página se uma dimensão der erro.
    breakdowns: dict[str, list[dict]] = {}
    for dim in ("domain", "agent", "environment"):
        try:
            breakdowns[dim] = (await svc.by_dimension(dim))[:8]
        except ValueError:
            breakdowns[dim] = []

    # Conhecidos pela plataforma (vão alimentar selects de scope_value).
    known_models = sorted({m for m in (build_clients() or {}).keys()})
    known_modules = sorted({r["module"] for r in by_module if r["module"]})

    return templates.TemplateResponse(
        "finops/index.html",
        await _ctx(
            request, user,
            active_module="finops",
            by_module=by_module, by_model=by_model,
            total_cost=total_cost, total_calls=total_calls,
            model_rates=rates,
            budget_statuses=budget_statuses,
            recent_alerts=recent_alerts,
            policies=policies,
            breakdowns=breakdowns,
            known_models=known_models,
            known_modules=known_modules,
        ),
    )


# ---------- Audit (Rastreabilidade) ----------

@router.get("/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'finops'])
    return templates.TemplateResponse(
        "audit/index.html",
        await _ctx(request, user, active_module="audit"),
    )


# ---------- Skills/Modules ----------

@router.get("/modules", response_class=HTMLResponse)
async def modules_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: RegistryService = Depends(get_registry_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor'])
    raw = await svc.list_all()
    modules = [
        {
            "id": str(m.id),
            "name": m.name,
            "endpoint_url": m.endpoint_url,
            "status": m.status.value,
            "config_params": m.config_params,
            "description": m.description,
            "skill_path": m.skill_path,
            "response_type": getattr(m, "response_type", "text") or "text",
            "response_config": getattr(m, "response_config", {}) or {},
        }
        for m in raw
    ]
    return templates.TemplateResponse(
        "modules/index.html",
        await _ctx(request, user, active_module="modules", modules=modules),
    )


# ---------- Users ----------

@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    svc: UserAdminService = Depends(get_user_admin_service),
):
    """Página /users — gerência de usuários.

    Acesso por role:
      - root:        vê todos os usuários (lista completa do banco).
      - admin:       vê todos.
      - supervisor:  vê APENAS usuários analista_n* do MESMO departamento;
                     sem dept preenchido recebe lista vazia + aviso na UI.
      - demais:      403.

    O backend (users_router) reaplica essas regras nas APIs — esta página
    apenas serve o HTML inicial com o subset visível ao actor.
    """
    if not user:
        return RedirectResponse("/login")
    actor_roles = set(user.roles or [])
    is_root = "root" in actor_roles
    is_admin = "admin" in actor_roles
    is_supervisor = "supervisor" in actor_roles
    if not (is_root or is_admin or is_supervisor):
        raise HTTPException(403, "apenas root/admin/supervisor pode gerenciar usuários")

    users_raw = await svc.list_all()

    # Supervisor: filtra só analistas do próprio dept.
    if is_supervisor and not is_admin and not is_root:
        actor_dept = (user.department or "").strip()
        if not actor_dept:
            users_raw = []
        else:
            users_raw = [
                u for u in users_raw
                if u.roles
                and all(r.startswith("analista_") for r in u.roles)
                and (u.department or "").strip() == actor_dept
            ]

    users = [
        {
            "id": str(u.id),
            "username": u.username,
            "full_name": getattr(u, "full_name", "") or "",
            "email": getattr(u, "email", "") or "",
            "phone": getattr(u, "phone", "") or "",
            "department": getattr(u, "department", "") or "",
            "title": getattr(u, "title", "") or "",
            "roles": u.roles,
            "is_active": u.is_active,
        }
        for u in users_raw
    ]
    return templates.TemplateResponse(
        "users/index.html",
        await _ctx(request, user, active_module="users", users=users),
    )


# ---------- Galeria de Apresentações ----------

@router.get("/apis", response_class=HTMLResponse)
async def apis_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin'])
    return templates.TemplateResponse(
        "apis/index.html",
        await _ctx(request, user, active_module="apis"),
    )


# ---------- Funcionalidades por Perfil (matriz) ----------

@router.get("/access", response_class=HTMLResponse)
async def access_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    """Tela administrativa da matriz "Funcionalidades por Perfil".

    Acesso: root vê e edita; admin vê em modo read-only (decisão de
    política — root é o ator supremo que define quem vê o quê). A API
    `/api/access/rule` rejeita PUT/DELETE de admin com 403, então o
    read-only é enforced no backend e replicado no frontend para UX.
    """
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin'])
    is_root = "root" in (user.roles or [])
    return templates.TemplateResponse(
        "access/index.html",
        await _ctx(
            request, user,
            active_module="access",
            can_edit_matrix=is_root,
        ),
    )


# ---------- Skills ----------

@router.get("/skills", response_class=HTMLResponse)
async def skills_page(
    request: Request,
    name: str | None = None,
    user: User | None = Depends(current_user_optional),
    svc: SkillService = Depends(get_skill_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor'])
    raw = svc.list_all()
    skills = [
        {
            "name": s.name, "title": s.title, "path": s.path,
            "sections": list(s.sections.keys()),
            "updated_at": s.updated_at.isoformat(),
            "size_bytes": s.size_bytes,
        }
        for s in raw
    ]
    selected_obj = svc.get(name) if name else (raw[0] if raw else None)
    selected = None
    if selected_obj:
        selected = {
            "name": selected_obj.name, "title": selected_obj.title, "path": selected_obj.path,
            "content": selected_obj.content, "sections": selected_obj.sections,
            "updated_at": selected_obj.updated_at.isoformat(),
            "size_bytes": selected_obj.size_bytes,
        }
    return templates.TemplateResponse(
        "skills/index.html",
        await _ctx(request, user, active_module="skills", skills=skills, selected=selected),
    )


# ---------- Pagamentos → Empreiteiras WF (Fase 3) ----------

@router.get("/payments/empreiteiras-wf", response_class=HTMLResponse)
async def payments_empreiteiras_wf_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    search: str | None = None,
    uf: str | None = None,
    tipo: str | None = None,
):
    """Dashboard de Monitoramento de Pagamentos para Empreiteiras-WF.

    Acesso: root/admin/supervisor/controladoria. A role `controladoria` é
    nova (Fase 3) — o gate aceita strings livres, não exige migration de
    enum. Outras roles tomam 403 via `_require_any_role`.

    Query params filtram apenas a tabela "Visão por Fornecedor"; KPIs e
    charts mostram sempre o panorama global. Estados ficam refletidos nos
    inputs via `dashboard.active_filters` (state-aware UI).
    """
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.dashboard_service import PaymentsDashboardService

    svc = PaymentsDashboardService()
    dashboard = await svc.dashboard_payload(search=search, uf=uf, tipo=tipo)

    return templates.TemplateResponse(
        "payments/empreiteiras_wf/index.html",
        await _ctx(
            request, user,
            active_module="empreiteiras_wf",
            dashboard=dashboard,
        ),
    )


@router.get("/payments/empreiteiras-wf/alertas", response_class=HTMLResponse)
async def payments_empreiteiras_wf_alertas_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    severity: str | None = None,
    rule_code: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
):
    """Inbox /alertas — lista paginada de findings com filtros.

    Mesma matriz de acesso do dashboard. Severity aceita 'high'/'medium'/'low';
    UI passa o label visível (ex.: 'Alerta Op.') que o service converte
    antes de chamar o repo.
    """
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.dashboard_service import PaymentsDashboardService

    svc = PaymentsDashboardService()
    # severity da UI vem como label — converte aqui.
    sev_map = dict(svc.TIPOS_ALERTA)
    severity_internal = sev_map.get(severity, severity if severity else None)

    inbox = await svc.inbox_payload(
        severity=severity_internal,
        rule_code=rule_code or None,
        status=status or None,
        search=search or None,
        page=max(1, page),
    )

    return templates.TemplateResponse(
        "payments/empreiteiras_wf/alertas.html",
        await _ctx(
            request, user,
            active_module="empreiteiras_wf",
            inbox=inbox,
            # Reverse map p/ exibir o label nos selects sem extra Jinja logic.
            severity_label_active=severity or "",
        ),
    )


@router.get("/payments/empreiteiras-wf/ingestao", response_class=HTMLResponse)
async def payments_empreiteiras_wf_ingestao_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    just_uploaded: str | None = None,
):
    """Tela de ingestão XLSX/MSRV5 (Fase 3.5).

    `just_uploaded` é o run_id após PRG do upload — UI exibe banner
    confirmando a fila e destaca a linha correspondente na tabela.
    """
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.ingestion_service import PaymentsIngestionService

    svc = PaymentsIngestionService()
    projections = svc.list_projections()
    runs = await svc.list_recent_runs(limit=20)

    return templates.TemplateResponse(
        "payments/empreiteiras_wf/ingestao.html",
        await _ctx(
            request, user,
            active_module="empreiteiras_wf",
            projections=projections,
            runs=runs,
            just_uploaded=just_uploaded or "",
        ),
    )


@router.post("/payments/empreiteiras-wf/ingestao/upload")
async def payments_empreiteiras_wf_ingestao_upload(
    request: Request,
    user: User | None = Depends(current_user_optional),
    projection_name: str = Form(...),
    file: UploadFile = File(...),
):
    """Recebe upload, enfileira no dramatiq, redireciona pra /ingestao
    com o run_id na query (UI mostra banner)."""
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.ingestion_service import PaymentsIngestionService

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "arquivo vazio")

    svc = PaymentsIngestionService()
    try:
        run_id = await svc.queue_upload(
            file_bytes=file_bytes,
            filename=file.filename or "upload.bin",
            projection_name=projection_name,
            user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return RedirectResponse(
        f"/payments/empreiteiras-wf/ingestao?just_uploaded={run_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/payments/empreiteiras-wf/ingestao/runs",
    response_class=HTMLResponse,
)
async def payments_empreiteiras_wf_ingestao_runs_partial(
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    """Partial HTMX da tabela de runs — pra refresh manual ou polling
    do dashboard quando há run em execução."""
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.ingestion_service import PaymentsIngestionService

    svc = PaymentsIngestionService()
    runs = await svc.list_recent_runs(limit=20)
    return templates.TemplateResponse(
        "payments/empreiteiras_wf/_ingestao_runs.html",
        {"request": request, "runs": runs, "just_uploaded": ""},
    )


@router.get(
    "/payments/empreiteiras-wf/ingestao/runs/{run_id}",
    response_class=JSONResponse,
)
async def payments_empreiteiras_wf_ingestao_run_status(
    run_id: str,
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    """Status JSON de 1 run específico — alimenta o polling HTMX/JS do
    Bloco C."""
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "autenticação requerida")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.ingestion_service import PaymentsIngestionService

    try:
        run_uuid = __import__("uuid").UUID(run_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "run_id inválido") from exc

    svc = PaymentsIngestionService()
    run = await svc.get_run(run_uuid)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run não encontrado")

    # JSON-safe: converte datetimes em ISO 8601.
    run_jsonable = {
        **run,
        "started_at": run["started_at"].isoformat() if run["started_at"] else None,
        "finished_at": run["finished_at"].isoformat() if run["finished_at"] else None,
    }
    return JSONResponse(run_jsonable)


@router.get("/payments/empreiteiras-wf/desvios", response_class=HTMLResponse)
async def payments_empreiteiras_wf_desvios_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    severity: str | None = None,
    detector_code: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
):
    """Inbox /desvios — lista paginada de analytic_finding (R7).

    Mesma matriz de acesso do dashboard. Severity vem como código interno
    ('high'/'medium'/'low') no querystring; labels são resolvidos no
    service via _SEVERITY_LABELS_DESVIOS.
    """
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.dashboard_service import PaymentsDashboardService

    svc = PaymentsDashboardService()
    inbox = await svc.desvios_payload(
        severity=severity or None,
        detector_code=detector_code or None,
        status=status or None,
        search=search or None,
        page=max(1, page),
    )
    return templates.TemplateResponse(
        "payments/empreiteiras_wf/desvios.html",
        await _ctx(
            request, user,
            active_module="empreiteiras_wf_desvios",
            inbox=inbox,
        ),
    )


@router.get("/payments/empreiteiras-wf/desvios/{finding_id}", response_class=HTMLResponse)
async def payments_empreiteiras_wf_desvio_detalhe(
    finding_id: str,
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    """Detalhe de 1 analytic_finding: contexto (detector/score/range) +
    workflow HITL."""
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.dashboard_service import PaymentsDashboardService

    svc = PaymentsDashboardService()
    desvio = await svc.desvio_detail(finding_id)
    if desvio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "desvio não encontrado")

    return templates.TemplateResponse(
        "payments/empreiteiras_wf/desvio_detalhe.html",
        await _ctx(
            request, user,
            active_module="empreiteiras_wf_desvios",
            desvio=desvio,
        ),
    )


@router.post("/payments/empreiteiras-wf/desvios/{finding_id}/decide")
async def payments_empreiteiras_wf_desvio_decide(
    finding_id: str,
    request: Request,
    new_status: str = Form(...),
    decision_reason: str = Form(""),
    user: User | None = Depends(current_user_optional),
):
    """Aplica transição de status no analytic_finding (HITL R7).

    Form-encoded + 303 redirect — segue PRG pattern do /alertas/decide.
    """
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.dashboard_service import PaymentsDashboardService

    svc = PaymentsDashboardService()
    ok, error = await svc.transition_desvio(
        finding_id,
        new_status=new_status,
        decision_reason=(decision_reason.strip() or None),
        decided_by_user_id=str(user.id),
    )
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, error or "falha na transição")
    return RedirectResponse(
        f"/payments/empreiteiras-wf/desvios/{finding_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/payments/empreiteiras-wf/alertas/{finding_id}", response_class=HTMLResponse)
async def payments_empreiteiras_wf_alerta_detalhe(
    finding_id: str,
    request: Request,
    user: User | None = Depends(current_user_optional),
):
    """Detalhe de 1 finding: contexto (regra/contrato/OS) e ações (workflow)."""
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.dashboard_service import PaymentsDashboardService

    svc = PaymentsDashboardService()
    finding = await svc.finding_detail(finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alerta não encontrado")

    return templates.TemplateResponse(
        "payments/empreiteiras_wf/alerta_detalhe.html",
        await _ctx(
            request, user,
            active_module="empreiteiras_wf",
            finding=finding,
        ),
    )


@router.post("/payments/empreiteiras-wf/alertas/{finding_id}/decide")
async def payments_empreiteiras_wf_alerta_decide(
    finding_id: str,
    request: Request,
    new_status: str = Form(...),
    decision_reason: str = Form(""),
    user: User | None = Depends(current_user_optional),
):
    """Aplica transição de status no finding (HITL workflow).

    Form-encoded para que `<form method=POST>` funcione sem JS extra.
    Após decidir, redireciona pro próprio detalhe (PRG pattern — evita
    repost no F5)."""
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.dashboard_service import PaymentsDashboardService

    svc = PaymentsDashboardService()
    ok, error = await svc.transition_finding(
        finding_id,
        new_status=new_status,
        decision_reason=(decision_reason.strip() or None),
        decided_by_user_id=str(user.id),
    )
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, error or "falha na transição")
    return RedirectResponse(
        f"/payments/empreiteiras-wf/alertas/{finding_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/payments/empreiteiras-wf/fornecedores", response_class=HTMLResponse)
async def payments_empreiteiras_wf_fornecedores_partial(
    request: Request,
    user: User | None = Depends(current_user_optional),
    search: str | None = None,
    uf: str | None = None,
    tipo: str | None = None,
):
    """Partial HTMX da tabela 'Visão por Fornecedor'. Retorna SÓ o HTML
    da tabela para target=#fornecedores-table no dashboard. Não recarrega
    KPIs/charts."""
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor', 'controladoria'])

    from app.core.services.payments.dashboard_service import PaymentsDashboardService

    svc = PaymentsDashboardService()
    fornecedores = await svc.fornecedores(search=search, uf=uf, tipo=tipo)

    return templates.TemplateResponse(
        "payments/empreiteiras_wf/_fornecedores_table.html",
        {"request": request, "fornecedores": fornecedores},
    )


# ---------- Building Blocks (catálogo) ----------

@router.get("/blocks", response_class=HTMLResponse)
async def blocks_page(
    request: Request,
    user: User | None = Depends(current_user_optional),
    reg: RegistryService = Depends(get_registry_service),
    skills: SkillService = Depends(get_skill_service),
    prompts: PromptService = Depends(get_prompt_service),
):
    if not user:
        return RedirectResponse("/login")
    _require_any_role(user, ['admin', 'supervisor'])
    modules = await reg.list_all()
    all_prompts = await prompts.list_all()
    blocks = []
    for m in modules:
        skill_dict = None
        if m.skill_path:
            stem = m.skill_path.rsplit("/", 1)[-1].replace(".md", "")
            skill_obj = skills.get(stem)
            if skill_obj:
                skill_dict = {
                    "name": skill_obj.name,
                    "title": skill_obj.title,
                    "path": skill_obj.path,
                }
        cnt = sum(1 for p in all_prompts if p.module_name == m.name)
        blocks.append({
            "id": str(m.id),
            "name": m.name,
            "title": m.name.replace("_", " ").title(),
            "description": m.description or "Sem descrição.",
            "status": m.status.value,
            "skill_obj": skill_dict,
            "prompts_count": cnt,
            "config_params": m.config_params,
        })
    return templates.TemplateResponse(
        "blocks/index.html",
        await _ctx(request, user, active_module="blocks", blocks=blocks),
    )


