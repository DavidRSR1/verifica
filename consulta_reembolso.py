"""
consulta_reembolso.py
---------------------
Fluxo de extração PAI/FILHO de reembolsos do portal Profrotas.

Resumo do fluxo (leitura essencial para o time):
  - Não usamos API Key individual por posto!
  - **Autenticação híbrida**: fazemos login automático com o Playwright (browser headless)
    apenas 1x, capturando o JWT de autenticação das requests internas do portal.
    Isso evita login manual/repetido e permite automação completa.
  - Após obter o JWT, consumimos as APIs do portal:
      - Primeiro buscamos faturas (reembolsos) = nó PAI
      - Depois, detalhamos abastecimentos dentro dessas faturas = nó FILHO
      - Só são considerados os postos presentes no POSTOS_ALVO (ver config.py)

Funções principais expostas:
  - executar_rotina_diaria()        # Executa a extração "full" via cron (abrange todos os postos/datas)
  - buscar_e_persistir_periodo()    # Executa on-demand para um posto e período (interface.py chama quando não há dados)
"""

import time
import requests
import concurrent
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import database
from config import USUARIO, SENHA, POSTOS_ALVO
from helpers import calcular_janela_reembolso, safe_float

# ==========================================================================
# 1. AUTENTICAÇÃO HÍBRIDA: login automático e interceptação do JWT
# ==========================================================================
def obter_sessao_hibrida() -> requests.Session:
    """
    Automatiza login e captura do JWT do portal Profrotas.
    Retorna uma sessão requests já autenticada.
    """
    sessao = requests.Session()
    sessao.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://portal.profrotas.com.br",
        "Referer": "https://portal.profrotas.com.br/"
    })

    print("[Autenticação] Iniciando interceptação de rede...")
    token_jwt = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Função chamada a cada request de rede -- intercepta JWT
        def capturar_token(request):
            nonlocal token_jwt
            if "api-portal.profrotas.com.br" in request.url:
                auth_header = request.headers.get("authorization")
                if auth_header and "Bearer" in auth_header:
                    token_jwt = auth_header.strip()

        page.on("request", capturar_token)
        page.goto("https://portal.profrotas.com.br/")
        page.locator("#username").fill(USUARIO)
        page.get_by_role("button", name="Próximo").click()
        page.locator("input#password").wait_for(state="visible", timeout=10000)
        page.locator("input#password").fill(SENHA)
        page.get_by_role("button", name="Entrar").click()

        try:
            with page.expect_request(
                lambda req: (
                    "api-portal.profrotas.com.br" in req.url
                    and "authorization" in req.headers
                    and "Bearer" in req.headers["authorization"]
                ),
                timeout=15000,
            ):
                pass
        except Exception:
            # Se não interceptar de cara, aguarda 3s (login pode ser lento)
            page.wait_for_timeout(3000)

        # Recupera cookies da sessão do navegador
        for c in context.cookies():
            sessao.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])

        browser.close()

    if token_jwt:
        sessao.headers.update({"Authorization": token_jwt})
        print("Sessão estabelecida com sucesso.")
    else:
        raise RuntimeError("Falha na interceptação do Token JWT.")

    return sessao

# ==========================================================================
# 2. EXTRATORES DE DADOS DA API (faturas e abastecimentos)
# ==========================================================================
@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _post_com_retry(session: requests.Session, url: str, payload: dict) -> requests.Response:
    """
    Wrapper para chamadas POST com retry e backoff (evita falhas intermitentes).
    """
    r = session.post(url, json=payload, timeout=30)
    r.raise_for_status()
    return r

def buscar_resumo_financeiro(
    session: requests.Session,
    data_inicio: str,
    data_fim: str,
    cnpj_filtro: str | None = None,
) -> list[dict]:
    """
    Busca faturas (reembolsos) do portal (nó PAI). Pagina até buscar tudo.
    Se cnpj_filtro for informado, filtra apenas pelas faturas do posto.
    """
    url = "https://api-portal.profrotas.com.br/api/financeiroRevenda/pesquisa"
    faturas = []
    pagina = 1

    while True:
        payload = {
            "paginacao": {"pagina": pagina, "tamanhoPagina": 50},
            "frota": {"id": None, "nome": "Todas as frotas"},
            "empresaUnidade": {"id": None, "nome": "Todos"},
            "de": data_inicio,
            "ate": data_fim,
            "tipoFiltroData": {"value": 1, "label": "Prazo de Reembolso"},
            "pontoDeVenda": None,
        }

        try:
            r = _post_com_retry(session, url, payload)
        except Exception as e:
            print(f"Erro ao buscar resumos financeiros (página {pagina}): {e}")
            break

        dados = r.json()
        registros = dados.get("registros", [])
        if not registros:
            break

        faturas.extend(registros)

        total_items = dados.get("totalItems", 0)
        if len(faturas) >= total_items:
            break
        pagina += 1

    return faturas

def buscar_detalhes_abastecimentos(
    session: requests.Session,
    fatura: dict,
    cnpj_filtro: str | None = None,
) -> list[dict]:
    """
    Busca abastecimentos (filhos) de uma fatura. Pagina até buscar tudo.
    Se cnpj_filtro for informado, filtra só o posto específico.
    """
    url = "https://api-portal.profrotas.com.br/api/detalhamentoNotaFiscal/pesquisa"

    id_consolidado = str(fatura.get("id"))
    data_inicio = fatura.get("dataInicioPeriodo")
    data_fim = fatura.get("dataFimPeriodo")
    valor_reembolso = fatura.get("valorReembolso") or fatura.get("valorReembolsoComDescontoCredito") or 0.0

    frota_pv = fatura.get("frotaPontoVenda", {})
    frota = frota_pv.get("frota", {})
    frota_id = frota.get("id")
    frota_cnpj = frota.get("cnpj")
    pv_id = frota_pv.get("idPv")

    status_pag = fatura.get("statusPagamentoReembolso") or {}
    status_pagamento = status_pag.get("label") or "Pendente"
    status_label = status_pag.get("label") or ""

    if status_label == "Pago":
        data_pagamento_bruta = fatura.get("dataPagamento") or ""
    else:
        prazos = fatura.get("prazos") or {}
        data_pagamento_bruta = prazos.get("dataLimitePagamento") or ""

    data_pagamento = data_pagamento_bruta[:10] if len(data_pagamento_bruta) >= 10 else None

    if not all([id_consolidado, frota_id, pv_id]):
        return []

    abastecimentos_processados = []
    pagina = 1
    tamanho_pagina = 500

    while True:
        payload = {
            "semEstorno": False,
            "processamentoDe": data_inicio,
            "processamentoAte": data_fim,
            "frota": {"id": frota_id, "cnpj": frota_cnpj},
            "pontoDeVenda": {"id": pv_id},
            "agruparExibicao": {"name": "ABASTECIMENTO"},
            "idConsolidado": id_consolidado,
            "paginacao": {"pagina": pagina, "tamanhoPagina": tamanho_pagina},
        }

        try:
            r = _post_com_retry(session, url, payload)
        except Exception as e:
            print(f"Erro ao buscar detalhes da fatura {id_consolidado} (página {pagina}): {e}")
            break

        dados = r.json()
        registros_brutos = dados.get("registros", [])
        if not registros_brutos:
            break

        for registro in registros_brutos:
            processados = processar_registro_api(
                registro,
                reembolso_total=valor_reembolso,
                status_pagamento=status_pagamento,
                data_pagamento=data_pagamento,
                cnpj_filtro=cnpj_filtro,
            )
            abastecimentos_processados.extend(processados)

        total_items = dados.get("totalItems", 0)
        if len(abastecimentos_processados) >= total_items or len(registros_brutos) < tamanho_pagina:
            break
        pagina += 1

    return abastecimentos_processados

# ==========================================================================
# 3. PARSER DE REGISTRO (mapeia todos os campos úteis para a gente)
# ==========================================================================
def processar_registro_api(
    registro_bruto: dict,
    reembolso_total = "",
    status_pagamento: str = "",
    data_pagamento: str = "",
    cnpj_filtro = None,
) -> list[dict]:
    """
    "Normaliza" cada registro do abastecimento com todos os campos que queremos tratar/salvar.
    Aplica lógica de compensação de valor/litro, deduz tipo de isenção de NF etc.
    Só retorna abastecimentos cujos postos estão em POSTOS_ALVO e (opcionalmente) cnpj_filtro.
    """
    import database
    import helpers
    
    processados = []
    filhos = registro_bruto.get("abastecimentosFilhos") or []

    for abast in filhos:
        cnpj_posto_transacao = abast.get("cnpjPosto") or ""

        if cnpj_posto_transacao not in POSTOS_ALVO:
            continue
        if cnpj_filtro and cnpj_posto_transacao != cnpj_filtro:
            continue

        data_bruta = abast.get("dataTransacao") or ""
        data_iso = f"{data_bruta[6:10]}-{data_bruta[3:5]}-{data_bruta[0:2]}" if len(data_bruta) >= 10 else None
        hora = abast.get("horaTransacao") or "00:00"
        data_hora_bruta = f"{hora} {data_bruta}".strip()

        lista_nfs = abast.get("notasFiscaisEmitidas") or []
        nfs = ", ".join([nf.get("numero", "") for nf in lista_nfs if isinstance(nf, dict) and nf.get("numero")])

        empresa = abast.get("nomeFrota") or ""
        
        # Se empresa tem isenção e não veio NF, marca como "Isenta"
        if helpers.verificar_isencao(empresa) and not nfs:
            nfs = "Isenta"

        # --- extração dos detalhes ---
        combustivel_principal = abast.get("nomeItemAbastecimento") or ""
        litros_principal = helpers.safe_float(abast.get("totalLitrosAbastecimento")) or 0.0
        valor_total_transacao = helpers.safe_float(abast.get("valorTotal")) or 0.0

        lista_srv = abast.get("itensAbastecimento") or []
        
        nome_combustivel_lista = []
        nome_servico_lista = []
        litros_combustivel = 0.0
        valor_combustivel = 0.0
        litros_arla = 0.0
        valor_arla = 0.0

        # Separa itens combustíveis (diesel/gasolina) dos itens "serviço" (Arla etc.)
        for item in lista_srv:
            nome_item = item.get("nome") or item.get("descricao") or ""
            qtd_item = helpers.safe_float(item.get("quantidade")) or 0.0
            
            v_item = helpers.safe_float(item.get("valorTotal")) or helpers.safe_float(item.get("valor")) or 0.0
            if v_item <= 0.0:
                v_unit = helpers.safe_float(item.get("valorUnitario")) or 0.0
                v_item = qtd_item * v_unit
            
            if "arla" in nome_item.lower():
                nome_servico_lista.append(nome_item)
                litros_arla += qtd_item
                valor_arla += v_item
            else:
                nome_combustivel_lista.append(nome_item)
                litros_combustivel += qtd_item
                valor_combustivel += v_item

        # Ajustes/correções caso Profrotas informe combustivel só na raiz
        if valor_combustivel == 0.0 and combustivel_principal:
            if "arla" in combustivel_principal.lower():
                if combustivel_principal not in nome_servico_lista:
                    nome_servico_lista.insert(0, combustivel_principal)
                litros_arla += litros_principal
                if valor_total_transacao > 0:
                    valor_arla += (valor_total_transacao - valor_arla)
            else:
                if combustivel_principal not in nome_combustivel_lista:
                    nome_combustivel_lista.insert(0, combustivel_principal)
                litros_combustivel += litros_principal
                if valor_total_transacao > 0:
                    valor_combustivel += (valor_total_transacao - valor_arla)

        # Se valor total não veio, soma manualmente
        if valor_total_transacao <= 0.0:
            valor_total_transacao = valor_combustivel + valor_arla
            
        litros_total = litros_principal if litros_principal > 0 else (litros_combustivel + litros_arla)

        placa = abast.get("placaVeiculo") or ""
        motorista = abast.get("nomeMotorista") or ""
        posto_uuid = database.get_posto_id(cnpj_posto_transacao)

        # Adiciona o registro já pronto/normalizado pra salvar no Supabase
        processados.append({
            "posto_id": posto_uuid,
            "empresa": empresa,
            "reembolso_total": helpers.safe_float(reembolso_total),
            "data_bruta": data_hora_bruta,
            "data": data_iso,
            "hora": hora,
            "nota_fiscal": nfs,
            "placa_motorista": f"{placa}\n{motorista}".strip() if placa or motorista else "",
            
            "litros": round(litros_total, 3),
            "combustivel": " + ".join(nome_combustivel_lista) or combustivel_principal,
            "servico": " + ".join(nome_servico_lista),
            "local_destino": abast.get("nomeUnidade") or "",
            "valor_total": round(valor_total_transacao, 2),
            "qtd_nfs": int(abast.get("quantidadeNotasFiscais") or 0),
            "status_pagamento": status_pagamento,
            "data_pagamento": data_pagamento,
            
            "litros_combustivel": round(litros_combustivel, 3),
            "valor_combustivel": round(valor_combustivel, 2),
            "litros_arla": round(litros_arla, 3),
            "valor_arla": round(valor_arla, 2),
        })

    return processados

# ==========================================================================
# 4. ON-DEMAND POR POSTO E PERÍODO (acesso rápido)
# ==========================================================================
def buscar_e_persistir_periodo(cnpj_posto: str, data_ini: str, data_fim: str) -> None:
    """
    Busca reembolso no portal para o posto/período específico, 
    deduplica, e grava no Supabase.
    Chamada pelo interface.py quando não há dados ainda no banco.
    data_ini/data_fim: formato ISO (YYYY-MM-DD)
    """
    # Formato esperado pela API (ISO completo)
    data_inicio_api = f"{data_ini}T00:00:00.000Z"
    data_fim_api    = f"{data_fim}T23:59:59.000Z"

    print(f"[on-demand reembolso] Iniciando busca para {cnpj_posto} ({data_ini} -> {data_fim})")

    try:
        sessao = obter_sessao_hibrida()
        adaptador = requests.adapters.HTTPAdapter(pool_connections=5, pool_maxsize=5)
        sessao.mount("https://", adaptador)

        faturas = buscar_resumo_financeiro(sessao, data_inicio_api, data_fim_api, cnpj_filtro=cnpj_posto)
        print(f"[on-demand reembolso] {len(faturas)} fatura(s) encontrada(s) para {cnpj_posto}")

        todos_abastecimentos = []
        for fatura in faturas:
            detalhes = buscar_detalhes_abastecimentos(sessao, fatura, cnpj_filtro=cnpj_posto)
            todos_abastecimentos.extend(detalhes)

        if not todos_abastecimentos:
            print(f"[on-demand reembolso] Nenhum abastecimento encontrado para {cnpj_posto} no período.")
            return

        # Deduplicação (evita sobrescrita de registros idênticos)
        vistos: set = set()
        dados_limpos = []
        for item in todos_abastecimentos:
            chave = (item.get("empresa"), item.get("data_bruta"), item.get("valor_total"))
            if chave not in vistos:
                vistos.add(chave)
                dados_limpos.append(item)

        print(f"[on-demand reembolso] Persistindo {len(dados_limpos)} registro(s) para {cnpj_posto}...")
        database.enviar_para_supabase(dados_limpos, "relatorio_abastecimentos")

    except Exception as e:
        print(f"[on-demand reembolso] Erro para {cnpj_posto}: {e}")

# ==========================================================================
# 5. LOOP GERAL: rotina diária (cron) de integração
# ==========================================================================
def executar_rotina_diaria():
    """
    Executa o batch global diário. Faz login, extração paralela, deduplica
    e dispara lote para o Supabase.
    Ideal para rodar via cron/timer.
    """
    inicio_relogio = time.perf_counter()

    # Janela: extração dos últimos 90 dias (pode mudar caso precise)
    data_inicio_busca, data_fim_busca = calcular_janela_reembolso(dias_historico=90)

    sessao = obter_sessao_hibrida()

    adaptador = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
    sessao.mount("https://", adaptador)

    print(f"\nBuscando matriz financeira de {data_inicio_busca[:10]} até hoje...")
    faturas = buscar_resumo_financeiro(sessao, data_inicio_busca, data_fim_busca)
    total_faturas = len(faturas)
    print(f"Foram encontrados {total_faturas} grupos de reembolso (faturas).")

    todos_abastecimentos = []

    # Extração paralela para acelerar (define max_workers conforme hardware)
    max_workers = 5
    print(f"\nIniciando extração paralela com {max_workers} threads...")

    def processar_fatura_worker(indice, fatura_obj):
        empresa = fatura_obj.get("frotaPontoVenda", {}).get("frota", {}).get("nomeFantasia", "Desconhecida")
        print(f"  [processo] {indice}/{total_faturas} | Extraindo: {empresa} (ID: {fatura_obj.get('id')})...")
        return buscar_detalhes_abastecimentos(sessao, fatura_obj)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futuros = {
            executor.submit(processar_fatura_worker, i, fatura): fatura
            for i, fatura in enumerate(faturas, 1)
        }
        for futuro in concurrent.futures.as_completed(futuros):
            try:
                detalhes = futuro.result()
                if detalhes:
                    todos_abastecimentos.extend(detalhes)
            except Exception as e:
                print(f"  [erro] Falha crítica ao extrair detalhes de uma fatura: {e}")

    print(f"\nExtração concluída. Total de abastecimentos: {len(todos_abastecimentos)}")

    if todos_abastecimentos:
        # Deduplica antes de gravar (algumas faturas podem referenciar os mesmos registros)
        vistos: set = set()
        dados_limpos = []
        for item in todos_abastecimentos:
            chave = (item.get("empresa"), item.get("data_bruta"), item.get("valor_total"))
            if chave not in vistos:
                vistos.add(chave)
                dados_limpos.append(item)

        duplicatas = len(todos_abastecimentos) - len(dados_limpos)
        print(f"Análise: {duplicatas} registros sobrepostos descartados.")

        if dados_limpos:
            print("Disparando lote para o Supabase...")
            database.enviar_para_supabase(dados_limpos, "relatorio_abastecimentos")

    fim_relogio = time.perf_counter()
    print(f"\nTempo total: {fim_relogio - inicio_relogio:.2f}s")

# Para testes locais ou execução manual via terminal
if __name__ == "__main__":
    executar_rotina_diaria()
