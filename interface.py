"""
====================================================================================
interface.py — API FastAPI do Painel Multi-Adquirente
====================================================================================

Este arquivo é a INTERFACE central das rotas da aplicação multi-adquirente Profrotas.

───────────────────────────── GUIA PRÁTICO PARA A EQUIPE ───────────────────────────

► VISÃO GERAL

- Aqui estão os endpoints REST para vendas/reembolsos dos postos (principalmente Profrotas, mas extensível).
- Toda requisição chega por aqui e é roteada p/ o provider correspondente.
- Endpoints principais:
    - /api/providers
    - /api/{provider}/postos
    - /api/{provider}/vendas
    - /api/{provider}/reembolsos
    - /api/{provider}/resumo
    - /         (serve o painel web já com CSS+JS embutidos)

► CONCEITOS IMPORTANTES

- "Provider" é uma classe que incorpóra a integração (ex: Profrotas, Redefrota etc)
    - Para adicionar/adaptar um novo, crie uma classe herdando BaseProvider e adicione em PROVIDERS.
- "Mock" automático: se não houver dependências importadas, cairá em mocks p/ facilitar dev/tst offline.
- Funcionalidades auxiliares de float/soma estão em helpers.py — USE SEMPRE safe_float/safe_sum para precisão!
- BLOQUEIOS PARALELOS: Locks via threading.Lock + set em _ondemand_mutex/_ondemand_set para evitar duas syncs simultâneas (vendas/reembolso) do mesmo posto/período (leitura de API é custosa).
- Toda obtenção de dados usa "on-demand": se não houver no banco, busca automático na API Profrotas.
- RLS/Squad: consultas consideram squad_id do usuário (extraído do JWT); admin (role=admin) acessa TODOS postos.
- Prints para debugging: busque por "[on-demand vendas]", "[vendas]", "[reembolso on-demand]" etc.

► FRONTEND

- index.html no frontend/ é servido com injeção automática dos arquivos style.css e functions.js.
- Para mudar aparência ou lógica, edite frontend/style.css ou functions.js.
- As tags <style></style> e <script></script> no index.html SÃO PLACEHOLDERS — não remova!

───────────────────────────── ATALHO RÁPIDO DO FLUXO ────────────────────────────────

- Cada método no ProfrotasProvider:
    - get_postos(): lista postos do squad conforme RLS, ou todos se admin.
    - get_vendas(): busca vendas do banco; se vazio, busca na API e persiste na base; retorna lista de dicts padronizados.
    - get_reembolsos(): igual aos vendas, mas filtrando por data ou data_pagamento.
- Funções "mock" para ambiente de desenvolvimento: _mock_vendas, _mock_reembolsos
    - São usadas se helpers.py/database/config NÃO puderem ser importados.

───────────────────────────── DICAS USUAIS/PERGUNTAS FREQUENTES ─────────────────────

- Para liberar dois syncs simultâneos de vendas/reembolso do MESMO posto/período: NÃO PERMITIDO (lock no _ondemand_set); para outro período/posto, OK.
- Erro em provider? Veja se seu provider está registrado em PROVIDERS (fim do arquivo).
- Dúvidas sobre helpers/config? padronize nomeações no helpers.py sempre que possível!

====================================================================================
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

# --------------------------------------------------------------------------
# Importações do projeto/mocks: garanta portabilidade p/ devs/offline
# --------------------------------------------------------------------------
try:
    import database
    from helpers import safe_float, safe_sum
    from config import POSTOS_ALVO, BASE_URL, ENDPOINT_VENDAS
    _HAS_PROJECT = True
except ImportError:
    _HAS_PROJECT = False
    # MOCKS para facilitar desenvolvimento sem dependências
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
        # Manter precisão! Evita crash em conversão value/None/vazio/etc.
        if v is None or v == "" or v == "None":
            return None
        try:
            return float(str(v).replace(",", ".").strip())
        except (ValueError, TypeError):
            return None

    def safe_sum(records: list[dict], key: str) -> float:
        # Soma padrão resiliente (usa safe_float)
        return sum(safe_float(r.get(key)) or 0.0 for r in records)

# --------------------------------------------------------------------------
# BLOQUEIO para on-demand (evita sync concorrente do mesmo recurso)
# --------------------------------------------------------------------------
_ondemand_mutex = threading.Lock()
_ondemand_set: set[str] = set()

def _ondemand_try_acquire(key: str) -> bool:
    """Tenta adquirir lock para o recurso. Se já em uso, retorna False."""
    with _ondemand_mutex:
        if key in _ondemand_set:
            return False
        _ondemand_set.add(key)
        return True

def _ondemand_release(key: str):
    """Libera o lock para novo uso no mesmo recurso (período+posto)."""
    with _ondemand_mutex:
        _ondemand_set.discard(key)

# --------------------------------------------------------------------------
# Instância Supabase (autenticação RLS se JWT; service key para tasks internas)
# --------------------------------------------------------------------------
def get_sb_client(user_jwt: str | None = None):
    """
    Cliente Supabase para uso de ORM de consultas.
    - user_jwt → RLS aplicada (usuário final)
    - None     → service key (apenas para tasks internas/sync)
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
    """
    Retorna perfil (id, squad_id, role) do usuário autenticado (JWT).
    Se não autorizado ou erro, retorna None.
    """
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
    """
    Busca postos habilitados para o squad atual.
    Se sem conexão com DB, retorna mock do config.
    """
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

# --------------------------------------------------------------------------
# VENDAS: on-demand (busca Profrotas — paginação completa — e persiste)
# --------------------------------------------------------------------------
def _buscar_e_persistir_vendas(cnpj_posto: str, data_ini: str, data_fim: str) -> list[dict]:
    """
    Consulta toda venda do período na Profrotas e grava no Supabase.
    Retorna os dados mapeados para uso imediato no front-end.
    """
    if not _HAS_PROJECT:
        return []

    token_atual = database.obter_api_key_posto(cnpj_posto)
    if not token_atual:
        print(f"[on-demand vendas] Sem API Key para {cnpj_posto}")
        return []

    from playwright.sync_api import sync_playwright
    from consulta_venda import mapear_venda, _buscar_vendas_paginado

    data_ini_api = f"{data_ini}T00:00:00Z"
    data_fim_api = f"{data_fim}T23:59:59Z"

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            request_context = p.request.new_context(base_url=BASE_URL)

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
                return []

            todos_registros = _buscar_vendas_paginado(
                request_context, token_atual, data_ini_api, data_fim_api
            )
            vendas = [r for r in todos_registros if r.get("statusAutorizacaoPagamento") == "Autorizado"]

            if vendas:
                posto_uuid = database.get_posto_id(cnpj_posto)
                registros_mapeados = [mapear_venda(r, cnpj_posto, posto_uuid) for r in vendas]
                database.enviar_para_supabase(registros_mapeados, "vendas_diarias")
                print(f"[on-demand vendas] {len(registros_mapeados)} vendas persistidas para {cnpj_posto}")
                return registros_mapeados # Devolve direto para não precisar ler do banco!

    except Exception as e:
        print(f"[on-demand vendas] Erro inesperado: {e}")
    finally:
        if browser:
            try: browser.close()
            except Exception: pass
            
    return []

# --------------------------------------------------------------------------
# Definição de Provider: padrão para integrar novos adquirentes
# --------------------------------------------------------------------------
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

# --------------------------------------------------------------------------
# Provider Profrotas: padrão/produção
# --------------------------------------------------------------------------
class ProfrotasProvider(BaseProvider):
    name  = "Profrotas"
    slug  = "profrotas"
    color = "#00C896"
    icon  = ""  # Removido emoji

    def get_postos(self, sb=None, squad_id: str | None = None) -> list[dict]:
        # Busca postos permitidos via RLS ou todos se admin
        return get_postos_do_squad(sb, squad_id)

    def get_vendas(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None) -> list[dict]:
        if not _HAS_PROJECT:
            return _mock_vendas(cnpj_posto)
            
        sb_svc = get_sb_client()
        if not sb_svc: return []
        
        posto_id = database.get_posto_id(cnpj_posto)
        if not posto_id: return []
        
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
            
            # Se não há dados, busca na API e DEVOLVE direto (fim do loop infinito!)
            lock_key = f"vendas:{cnpj_posto}:{data_ini}:{data_fim}"
            if not rows and _ondemand_try_acquire(lock_key):
                try:
                    print(f"[vendas] Sem dados em {cnpj_posto} {data_ini}–{data_fim}. Buscando na API...")
                    novas_vendas = _buscar_e_persistir_vendas(cnpj_posto, data_ini, data_fim)
                    if novas_vendas:
                        rows = novas_vendas # Associa o resultado mapeado diretamente
                finally:
                    _ondemand_release(lock_key)
            return rows
        except Exception as e:
            print(f"Erro get_vendas: {e}")
            return []

    def get_reembolsos(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None, by_pagamento: bool = True) -> list[dict]:
        # Mock local/offline
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
            # Se não há dados, busca on-demand no portal (com lock)
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

# --------------------------------------------------------------------------
# Provider Redefrota (placeholder — amplie aqui!)
# --------------------------------------------------------------------------
class RedefrotaProvider(BaseProvider):
    name  = "Redefrota"
    slug  = "redefrota"
    color = "#FF6B35"
    icon  = ""  # Removido emoji

    def get_postos(self, sb=None, squad_id: str | None = None) -> list[dict]:
        # Sem integração ainda
        return []

    def get_vendas(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None) -> list[dict]:
        # Sem integração ainda
        return []

    def get_reembolsos(self, cnpj_posto: str, data_ini: str, data_fim: str, sb=None, by_pagamento: bool = True) -> list[dict]:
        # Sem integração ainda
        return []

# --------------------------------------------------------------------------
# Registro de providers — só precisa adicionar aqui para liberar em rotas.
# --------------------------------------------------------------------------
PROVIDERS: dict[str, BaseProvider] = {
    "profrotas": ProfrotasProvider(),
    "redefrota": RedefrotaProvider(),
}

# --------------------------------------------------------------------------
# Mocks de vendas/reembolsos para dev/teste offline.
# --------------------------------------------------------------------------
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

# --------------------------------------------------------------------------
# Instancia APP FastAPI + CORS liberado (simples para API pública)
# --------------------------------------------------------------------------
app = FastAPI(title="Painel Multi-Adquirente", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def jwt_from_header(authorization: str | None = Header(default=None)) -> str | None:
    """
    Extrai o token Bearer do cabeçalho Authorization.
    Use nas rotas como Depends.
    """
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None

def _ctx(jwt: str | None):
    """
    Atalho: retorna (sb, perfil, squad_id) autenticados para uso nas rotas.
    - Se admin, ignora squad_id.
    """
    sb       = get_sb_client(jwt)
    perfil   = get_perfil(sb, jwt)
    squad_id = perfil.get("squad_id") if perfil else None
    role     = (perfil.get("role") or "viewer") if perfil else "viewer"
    if role == "admin":
        squad_id = None  # admin sem squad vê tudo
    return sb, perfil, squad_id

# --------------------------------------------------------------------------
# Rotas REST públicas (api central)
# --------------------------------------------------------------------------

@app.get("/api/providers")
def listar_providers(jwt: str | None = Depends(jwt_from_header)):
    """
    Lista todos os providers disponíveis (e se possuem postos cadastrados).
    """
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
    """
    Lista postos disponíveis ao usuário, respeitando RLS/squad.
    """
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
    """
    Busca todas as vendas de um CNPJ/posto em um período.
    - Se vazio no banco, força consulta on-demand na API e salva.
    """
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
    """
    Busca reembolsos por posto e período.
    - by_pagamento=1 consulta por data de pagamento (default).
    """
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
    """
    Gera resumo de vendas/litros/reembolso quantitativo do período consultado.
    """
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

# --------------------------------------------------------------------------
# Frontend: serve index.html já com CSS e JS injetados inline
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_build_html())

def _build_html() -> str:
    """
    Injeta frontend/style.css + functions.js no index.html.
    Atenção: os arquivos do frontend DEVEM estar na mesma pasta ("frontend/").
    No index.html, mantenha as tags <style></style> e <script></script> vazias!
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

    # Regex tolerante: injeta CSS e JS entre as tags vazias (<style/>, <script/>)
    html = re.sub(r"<style>\s*</style>", "<style>\n" + css + "\n</style>", html)
    html = re.sub(r"<script>\s*</script>", "<script>\n" + js + "\n</script>", html)
    return html

# --------------------------------------------------------------------------
# Atalho: executar local, dev/testes (ex: python interface.py)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("interface:app", host="0.0.0.0", port=8000, reload=True)
