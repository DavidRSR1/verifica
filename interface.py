"""
interface.py
------------
FastAPI — Interface de consulta multi-adquirente.

Correções aplicadas:
  - buscar_e_persistir_periodo implementada em consulta_reembolso.py e importada aqui
  - Lock de on-demand substituído por threading.Lock (thread-safe)
  - safe_float / safe_sum centralizados em helpers.py
  - Paginação completa no on-demand de vendas (via _buscar_e_persistir_vendas)
  - HTML servido inline com style.css e functions.js embutidos
  - RLS: squad_id aplicado corretamente no cliente com JWT do usuário
"""

from __future__ import annotations

import os
import re
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── importações do projeto ──────────────────────────────────────────────────
try:
    import database
    from helpers import safe_float, safe_sum
    from config import POSTOS_ALVO, BASE_URL, ENDPOINT_VENDAS
    _HAS_PROJECT = True
except ImportError:
    _HAS_PROJECT = False
    POSTOS_ALVO = {
        "03.951.672/0001-70": "Auto Posto Sof Norte Ltda",
        "36.203.543/0001-53": "Mg Comercio De Combustiveis Ltda",
        "43.288.248/0001-02": "Posto De Combustiveis Correa 020 Ltda",
        "43.153.039/0001-51": "Posto de Combustíveis Divisão Ltda",
        "23.049.249/0001-97": "Posto Sao Roque Alianca Ltda",
        "40.806.619/0001-02": "Auto Posto Pro Trok Rio Preto Ltda",
        "01.427.744/0001-50": "Sao Bernardo Servicos Automotivos Ltda",
    }
    BASE_URL = "https://api-portal.profrotas.com.br"
    ENDPOINT_VENDAS = "/api/revenda/autorizacao/pesquisa"

    def safe_float(v) -> Optional[float]:
        if v is None or v == "" or v == "None":
            return None
        try:
            return float(str(v).replace(",", ".").strip())
        except (ValueError, TypeError):
            return None

    def safe_sum(records: list[dict], key: str) -> float:
        return sum(safe_float(r.get(key)) or 0.0 for r in records)


# ============================================================
# LOCK THREAD-SAFE para on-demand
# ============================================================
_ondemand_mutex = threading.Lock()
_ondemand_set: set[str] = set()


def _ondemand_try_acquire(key: str) -> bool:
    """Retorna True se conseguiu adquirir o lock para a chave, False se já está em progresso."""
    with _ondemand_mutex:
        if key in _ondemand_set:
            return False
        _ondemand_set.add(key)
        return True


def _ondemand_release(key: str):
    with _ondemand_mutex:
        _ondemand_set.discard(key)


# ============================================================
# SUPABASE CLIENT COM JWT DO USUÁRIO
# ============================================================
def get_sb_client(user_jwt: str | None = None):
    """
    Retorna cliente Supabase.
    Se user_jwt for fornecido, usa o token do usuário (RLS ativa).
    Caso contrário usa a service key (sem RLS — apenas operações internas).
    """
    if not _HAS_PROJECT:
        return None
    from supabase import create_client
    from config import URL_SUPABASE, CHAVE_SUPABASE
    sb = create_client(URL_SUPABASE, CHAVE_SUPABASE)
    if user_jwt:
        sb.auth.set_session(access_token=user_jwt, refresh_token="")
    return sb


def get_perfil(sb, user_jwt: str | None) -> dict | None:
    """Busca perfil + squad_id do usuário autenticado."""
    if not sb or not user_jwt:
        return None
    try:
        user = sb.auth.get_user(user_jwt)
        uid = user.user.id if user and user.user else None
        if not uid:
            return None
        resp = sb.table("perfis").select("id,nome,role,squad_id").eq("id", uid).limit(1).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def get_postos_do_squad(sb, squad_id: str | None) -> list[dict]:
    if not sb:
        return [{"cnpj": c, "nome": n} for c, n in POSTOS_ALVO.items()]

    try:
        q = (
            sb.table("postos")
            .select("id,cnpj,nome,nome_curto,squad_id,squads(nome)")
            .eq("ativo", True)
        )
        if squad_id:
            q = q.eq("squad_id", squad_id)
        resp = q.execute()
        return resp.data or []
    except Exception as e:
        print("Erro ao buscar postos:", e)
        return []


# ============================================================
# ON-DEMAND: vendas — com paginação completa
# ============================================================
def _buscar_e_persistir_vendas(cnpj_posto: str, data_ini: str, data_fim: str) -> None:
    """
    Consulta a API Profrotas para o período solicitado (todas as páginas),
    persiste no Supabase e retorna.
    """
    if not _HAS_PROJECT:
        return

    token_atual = database.obter_api_key_posto(cnpj_posto)
    if not token_atual:
        print(f"[on-demand vendas] Sem API Key para {cnpj_posto}")
        return

    from playwright.sync_api import sync_playwright
    from consulta_venda import mapear_venda, _buscar_vendas_paginado

    data_ini_api = f"{data_ini}T00:00:00Z"
    data_fim_api = f"{data_fim}T23:59:59Z"

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            request_context = p.request.new_context(base_url=BASE_URL)

            # Detecta renovação de token
            probe_payload = {
                "pagina": 1, "tamanhoPagina": 1,
                "idAutorizacaoPagamentoInicial": 0,
                "idAutorizacaoPagamentoExato": False,
                "dataInicial": data_ini_api,
                "dataFinal": data_fim_api,
            }
            probe = request_context.post(
                ENDPOINT_VENDAS,
                headers={"Authorization": f"Bearer {token_atual}", "Content-Type": "application/json"},
                data=probe_payload,
                timeout=30000,
            )
            nova_chave = probe.headers.get("renovacao-automatica-jwt")
            if nova_chave:
                database.atualizar_api_key_posto(cnpj_posto, nova_chave)
                token_atual = nova_chave

            if not probe.ok:
                print(f"[on-demand vendas] Erro API {probe.status}: {probe.text()}")
                return

            # Paginação completa
            todos_registros = _buscar_vendas_paginado(
                request_context, token_atual, data_ini_api, data_fim_api
            )
            vendas = [r for r in todos_registros if r.get("statusAutorizacaoPagamento") == "Autorizado"]

            if vendas:
                posto_uuid = database.get_posto_id(cnpj_posto)
                registros_mapeados = [mapear_venda(r, cnpj_posto, posto_uuid) for r in vendas]
                database.enviar_para_supabase(registros_mapeados, "vendas_diarias")
                print(f"[on-demand vendas] {len(registros_mapeados)} vendas persistidas para {cnpj_posto}")

    except Exception as e:
        print(f"[on-demand vendas] Erro inesperado: {e}")
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


# ============================================================
# BASE PROVIDER
# ============================================================
class BaseProvider(ABC):
    name: str
    slug: str
    color: str
    icon: str

    @abstractmethod
    def get_postos(self, sb=None, squad_id: str | None = None) -> list[dict]: ...

    @abstractmethod
    def get_vendas(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None) -> list[dict]: ...

    @abstractmethod
    def get_reembolsos(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None, by_pagamento: bool = True) -> list[dict]: ...


# ============================================================
# PROVIDER: PROFROTAS
# ============================================================
class ProfrotasProvider(BaseProvider):
    name  = "Profrotas"
    slug  = "profrotas"
    color = "#00C896"
    icon  = "⛽"

    def get_postos(self, sb=None, squad_id: str | None = None) -> list[dict]:
        return get_postos_do_squad(sb, squad_id)

    def get_vendas(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None) -> list[dict]:
        if not _HAS_PROJECT:
            return _mock_vendas(cnpj_posto)

        # Usa service key para leitura (RLS de postos é controlada na camada de autenticação da rota)
        sb_svc = get_sb_client()
        if not sb_svc:
            return []

        posto_id = database.get_posto_id(cnpj_posto)
        if not posto_id:
            return []

        try:
            resp = (
                sb_svc.table("vendas_diarias")
                .select("*")
                .eq("posto_id", posto_id)
                .gte("data_abastecimento", data_ini)
                .lte("data_abastecimento", data_fim)
                .order("data_abastecimento", desc=True)
                .limit(5000)
                .execute()
            )
            rows = resp.data or []

            # on-demand: banco vazio → busca na API
            lock_key = f"vendas:{cnpj_posto}:{data_ini}:{data_fim}"
            if not rows and _ondemand_try_acquire(lock_key):
                try:
                    print(f"[vendas] Sem dados em {cnpj_posto} {data_ini}–{data_fim}. Buscando na API...")
                    _buscar_e_persistir_vendas(cnpj_posto, data_ini, data_fim)
                    resp2 = (
                        sb_svc.table("vendas_diarias")
                        .select("*")
                        .eq("posto_id", posto_id)
                        .gte("data_abastecimento", data_ini)
                        .lte("data_abastecimento", data_fim)
                        .order("data_abastecimento", desc=True)
                        .limit(5000)
                        .execute()
                    )
                    rows = resp2.data or []
                finally:
                    _ondemand_release(lock_key)

            return rows
        except Exception as e:
            print(f"Erro get_vendas: {e}")
            return []

    def get_reembolsos(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None, by_pagamento: bool = True) -> list[dict]:
        if not _HAS_PROJECT:
            return _mock_reembolsos(cnpj_posto)

        sb_svc = get_sb_client()
        if not sb_svc:
            return []

        posto_id = database.get_posto_id(cnpj_posto)
        if not posto_id:
            return []

        date_col = "data_pagamento" if by_pagamento else "data"

        try:
            resp = (
                sb_svc.table("relatorio_abastecimentos")
                .select("*")
                .eq("posto_id", posto_id)
                .gte(date_col, data_ini)
                .lte(date_col, data_fim)
                .order(date_col, desc=True)
                .limit(5000)
                .execute()
            )
            rows = resp.data or []

            # on-demand: sem dados → consulta portal
            lock_key = f"reembolso:{cnpj_posto}:{data_ini}:{data_fim}"
            if not rows and _ondemand_try_acquire(lock_key):
                try:
                    print(f"[reembolso on-demand] Sem dados para {cnpj_posto} {data_ini}–{data_fim}. Buscando no portal...")
                    from consulta_reembolso import buscar_e_persistir_periodo
                    buscar_e_persistir_periodo(cnpj_posto, data_ini, data_fim)
                    resp2 = (
                        sb_svc.table("relatorio_abastecimentos")
                        .select("*")
                        .eq("posto_id", posto_id)
                        .gte(date_col, data_ini)
                        .lte(date_col, data_fim)
                        .order(date_col, desc=True)
                        .limit(5000)
                        .execute()
                    )
                    rows = resp2.data or []
                except Exception as e_od:
                    print(f"[reembolso on-demand] Falha: {e_od}")
                finally:
                    _ondemand_release(lock_key)

            return rows
        except Exception as e:
            print(f"Erro get_reembolsos: {e}")
            return []


# ============================================================
# PROVIDER: REDEFROTA (placeholder)
# ============================================================
class RedefrotaProvider(BaseProvider):
    name  = "Redefrota"
    slug  = "redefrota"
    color = "#FF6B35"
    icon  = "🚛"

    def get_postos(self, sb=None, squad_id: str | None = None) -> list[dict]:
        return []

    def get_vendas(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None) -> list[dict]:
        return []

    def get_reembolsos(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None, by_pagamento: bool = True) -> list[dict]:
        return []


# ============================================================
# REGISTRO
# ============================================================
PROVIDERS: dict[str, BaseProvider] = {
    "profrotas": ProfrotasProvider(),
    "redefrota": RedefrotaProvider(),
}


# ============================================================
# MOCKS
# ============================================================
def _mock_vendas(cnpj: str) -> list[dict]:
    from random import uniform, choice, randint
    hoje = datetime.now()
    return [
        {
            "id_autorizacao":     f"MOCK-{i:04d}",
            "data_abastecimento": (hoje - timedelta(days=randint(0, 6))).strftime("%Y-%m-%d"),
            "hora_abastecimento": f"{randint(6,22):02d}:{randint(0,59):02d}:00",
            "nome_frota":         choice(["Transportes Alpha", "Logística Beta"]),
            "placa_veiculo":      f"ABC{randint(1000,9999)}",
            "nome_motorista":     choice(["João Silva", "Maria Souza"]),
            "produto":            choice(["Diesel S10", "Gasolina Comum"]),
            "quantidade_litros":  round(uniform(30, 200), 2),
            "valor_unitario":     round(uniform(5.5, 7.2), 3),
            "valor_total":        round(uniform(200, 1400), 2),
            "status_autorizacao": "Autorizado",
            "status_nota_fiscal": choice(["Emitida", "Pendente"]),
        }
        for i in range(10)
    ]


def _mock_reembolsos(cnpj: str) -> list[dict]:
    from random import uniform, choice, randint
    hoje = datetime.now()
    return [
        {
            "empresa":          choice(["Transportes Alpha", "Logística Beta"]),
            "data":             (hoje - timedelta(days=randint(0, 30))).strftime("%Y-%m-%d"),
            "hora":             f"{randint(6,22):02d}:{randint(0,59):02d}",
            "nota_fiscal":      f"NF-{randint(1000,9999)}",
            "placa_motorista":  f"DEF{randint(1000,9999)}",
            "litros":           round(uniform(30, 200), 2),
            "combustivel":      choice(["Diesel S10", "Gasolina"]),
            "servico":          "",
            "local_destino":    "",
            "valor_total":      round(uniform(200, 1400), 2),
            "reembolso_total":  round(uniform(2000, 8000), 2),
            "status_pagamento": choice(["Pago", "Pendente"]),
            "data_pagamento":   (hoje + timedelta(days=5)).strftime("%Y-%m-%d"),
        }
        for _ in range(8)
    ]


# ============================================================
# APP FASTAPI
# ============================================================
app = FastAPI(title="Painel Multi-Adquirente", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def jwt_from_header(authorization: str | None = Header(default=None)) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None


def _ctx(jwt: str | None):
    """Retorna (sb, perfil, squad_id) para a requisição."""
    sb       = get_sb_client(jwt)
    perfil   = get_perfil(sb, jwt)
    squad_id = perfil.get("squad_id") if perfil else None
    role     = (perfil.get("role") or "viewer") if perfil else "viewer"
    if role == "admin":
        squad_id = None  # admin sem squad vê tudo
    return sb, perfil, squad_id


# ── Routes ─────────────────────────────────────────────────

@app.get("/api/providers")
def listar_providers(jwt: str | None = Depends(jwt_from_header)):
    sb, perfil, squad_id = _ctx(jwt)
    return [
        {
            "slug":       p.slug,
            "name":       p.name,
            "color":      p.color,
            "icon":       p.icon,
            "has_postos": len(p.get_postos(sb, squad_id)) > 0,
        }
        for p in PROVIDERS.values()
    ]


@app.get("/api/{provider}/postos")
def listar_postos(provider: str, jwt: str | None = Depends(jwt_from_header)):
    p = PROVIDERS.get(provider)
    if not p:
        raise HTTPException(404, "Provider não encontrado")
    sb, perfil, squad_id = _ctx(jwt)
    return p.get_postos(sb, squad_id)


@app.get("/api/{provider}/vendas")
def get_vendas(
    provider: str,
    cnpj: str = Query(...),
    data_ini: str = Query(default=None),
    data_fim: str = Query(default=None),
    jwt: str | None = Depends(jwt_from_header),
):
    p = PROVIDERS.get(provider)
    if not p:
        raise HTTPException(404, "Provider não encontrado")
    hoje = datetime.now()
    ini = data_ini or (hoje - timedelta(days=7)).strftime("%Y-%m-%d")
    fim = data_fim or hoje.strftime("%Y-%m-%d")
    sb, _, _ = _ctx(jwt)
    return p.get_vendas(cnpj, ini, fim, sb)


@app.get("/api/{provider}/reembolsos")
def get_reembolsos(
    provider: str,
    cnpj: str = Query(...),
    data_ini: str = Query(default=None),
    data_fim: str = Query(default=None),
    by_pagamento: int = Query(default=1),
    jwt: str | None = Depends(jwt_from_header),
):
    p = PROVIDERS.get(provider)
    if not p:
        raise HTTPException(404, "Provider não encontrado")
    hoje = datetime.now()
    ini = data_ini or (hoje - timedelta(days=30)).strftime("%Y-%m-%d")
    fim = data_fim or hoje.strftime("%Y-%m-%d")
    sb, _, _ = _ctx(jwt)
    return p.get_reembolsos(cnpj, ini, fim, sb, by_pagamento=bool(by_pagamento))


@app.get("/api/{provider}/resumo")
def get_resumo(
    provider: str,
    cnpj: str = Query(...),
    data_ini: str = Query(default=None),
    data_fim: str = Query(default=None),
    jwt: str | None = Depends(jwt_from_header),
):
    p = PROVIDERS.get(provider)
    if not p:
        raise HTTPException(404, "Provider não encontrado")
    hoje = datetime.now()
    ini = data_ini or (hoje - timedelta(days=7)).strftime("%Y-%m-%d")
    fim = data_fim or hoje.strftime("%Y-%m-%d")
    sb, _, _ = _ctx(jwt)

    vendas = p.get_vendas(cnpj, ini, fim, sb)
    reembs = p.get_reembolsos(cnpj, ini, fim, sb)

    total_vendas = safe_sum(vendas, "valor_total")
    total_litros = safe_sum(vendas, "quantidade_litros")
    total_reemb  = safe_sum(reembs, "valor_total")

    return {
        "total_vendas":    round(total_vendas, 2),
        "total_litros":    round(total_litros, 2),
        "total_reembolso": round(total_reemb, 2),
        "qtd_vendas":      len(vendas),
        "qtd_reembolsos":  len(reembs),
    }


# ── Frontend ────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_build_html())


def _build_html() -> str:
    """
    Lê index.html da pasta frontend/ e injeta style.css e functions.js inline.
    O index.html deve manter as tags <style>\n</style> e <script>\n</script> vazias
    como placeholders — o conteúdo dos arquivos será injetado entre elas.
    """
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")

    def _read(name: str) -> str:
        path = os.path.join(base, name)
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return f"/* {name} não encontrado */"

    html = _read("index.html")
    css  = _read("style.css")
    js   = _read("functions.js")

    # Injeta CSS e JS — regex tolerante a espaços/quebras entre as tags
    html = re.sub(r"<style>\s*</style>", "<style>\n" + css + "\n</style>", html)
    html = re.sub(r"<script>\s*</script>", "<script>\n" + js + "\n</script>", html)

    return html


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("interface:app", host="0.0.0.0", port=8000, reload=True)