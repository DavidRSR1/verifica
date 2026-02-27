"""
consulta_venda.py
-----------------
Fluxo central de consulta das vendas diárias por posto.

# COMENTÁRIOS PARA EQUIPE:
# - Cada posto usa sua própria API Key (está no Supabase).
# - Se o JWT expira ou é renovado (quando chega em 50% da vida útil), o header 'renovacao-automatica-jwt'
#   vem na resposta e a rotina já atualiza a chave pra gente automaticamente (não precisa se preocupar).
# - Esse módulo cuida de:
#     - Baixar todas as vendas autorizadas do(s) dia(s) (com paginação, não perde venda de volume alto)
#     - Aplicar mapeamento padronizado pros campos (nome, NF, produto, litros, arla, etc)
#     - Persistir o resultado pronto no Supabase
# - Organização:
#     - Foque em praticidade. Se precisar fazer manutenção/use essas funções básicas:
#         - processar_vendas_posto: roda tudo para 1 posto (recebe o CNPJ).
#         - mapear_venda: transforma o dicionário original da API no padrão do banco.
"""

import database
import helpers
from config import POSTOS_ALVO, BASE_URL, ENDPOINT_VENDAS

from playwright.sync_api import sync_playwright
from tenacity import retry, stop_after_attempt, wait_exponential

def safe_float(val):
    """Converte para float ou retorna None em caso de erro."""
    try:
        return float(val)
    except Exception:
        return None

# ============================================================================================
# PARSER & MAPEAMENTO DE CAMPOS DA VENDA PADRAO (utilizar SEMPRE para inserir no Supabase)
# ============================================================================================
def mapear_venda(r: dict, cnpj_posto: str, posto_uuid) -> dict:
    """
    Recebe dict da resposta da API de vendas, aplica regras/extrações, 
    retorna dict padronizado para uso na base.
    ---
    Pontos importantes:
      - Aplica lógica de isenção de NF diretamente aqui
      - Separa quantidades/valores de combustível normal vs. ARLA
      - Faz ROUND nos campos numéricos (padronização)
    """
    import helpers
    data_bruta = r.get("dataAbastecimento") or ""
    data_iso = data_bruta[:10] if len(data_bruta) >= 10 else None
    hora = data_bruta[11:19] if len(data_bruta) >= 19 else None

    frota     = r.get("frota") or {}
    motorista = r.get("motorista") or {}
    veiculo   = r.get("veiculo") or {}
    ciclo     = r.get("ciclo") or {}

    nome_frota = frota.get("razaoSocial") or ""
    status_nf = r.get("statusEmissaoNotaFiscal") or ""
    
    # Regra: ISENÇÃO de NF (confere direto pelo helpers, copiar se precisar em outro lugar)
    if helpers.verificar_isencao(nome_frota) and (not status_nf or "pendent" in status_nf.lower() or "não" in status_nf.lower()):
        status_nf = "Isenta"

    itens = r.get("itens") or []
    
    nome_combustivel_lista = []
    nome_servico_lista = []
    litros_combustivel = 0.0
    valor_combustivel = 0.0
    litros_arla = 0.0
    valor_arla = 0.0

    for item in itens:
        desc = item.get("descricao") or ""
        qtd = helpers.safe_float(item.get("quantidade")) or 0.0
        v_unit = helpers.safe_float(item.get("valorUnitario")) or 0.0
        v_tot_item = helpers.safe_float(item.get("valorTotal")) or (qtd * v_unit)

        if "arla" in desc.lower():
            nome_servico_lista.append(desc)
            litros_arla += qtd
            valor_arla += v_tot_item
        else:
            nome_combustivel_lista.append(desc)
            litros_combustivel += qtd
            valor_combustivel += v_tot_item

    valor_total_transacao = helpers.safe_float(r.get("valorTotal")) or 0.0
    if valor_total_transacao <= 0.0:
        valor_total_transacao = valor_combustivel + valor_arla

    litros_totais = litros_combustivel + litros_arla

    return {
        "id_autorizacao":       r.get("idAutorizacaoPagamento") or r.get("idAutorizacaoPagamento"),
        "posto_id":             posto_uuid,
        "cnpj_posto":           cnpj_posto,
        "data_abastecimento":   data_iso,
        "hora_abastecimento":   hora,
        "nome_frota":           nome_frota,
        "cnpj_frota":           frota.get("cnpj") or "",
        "nome_motorista":       motorista.get("nome") or "",
        "cpf_motorista":        motorista.get("cpf") or "",
        "placa_veiculo":        veiculo.get("placa") or "",
        
        "produto":              " + ".join(nome_combustivel_lista),  # todos combustíveis (ex: Diesel + Gasolina)
        "servico":              " + ".join(nome_servico_lista),      # todos serviços/arla

        "quantidade_litros":    round(litros_totais, 3), 
        "valor_unitario":       itens[0].get("valorUnitario") if itens else None,
        "valor_total":          round(valor_total_transacao, 2),
        
        "status_autorizacao":   r.get("statusAutorizacaoPagamento") or "",
        "status_nota_fiscal":   status_nf,
        "ciclo_inicio":         (ciclo.get("dataInicio") or "")[:10] or None,
        "ciclo_fim":            (ciclo.get("dataFim") or "")[:10] or None,
        "ciclo_limite_emissao": (ciclo.get("dataLimiteEmissao") or "")[:10] or None,
        
        "litros_combustivel":   round(litros_combustivel, 3),
        "valor_combustivel":    round(valor_combustivel, 2),
        "litros_arla":          round(litros_arla, 3),
        "valor_arla":           round(valor_arla, 2),
    }


# ============================================================================================
# CONSULTA COM PAGINAÇÃO (USAR SEMPRE para garantir todos resultados de postos com muito volume)
# ============================================================================================
def _buscar_vendas_paginado(
    request_context,
    token: str,
    data_ini_api: str,
    data_fim_api: str,
) -> list[dict]:
    """
    Busca TODOS os registros de vendas para o período. Loop de paginação garantido!
    DICA: Não chame a API por conta própria, use esta função pra não perder vendas!
    """
    todos = []
    pagina = 1
    tamanho_pagina = 200  # Limite prático (API aceita até 200 normalmente!)

    while True:
        payload = {
            "pagina": pagina,
            "tamanhoPagina": tamanho_pagina,
            "idAutorizacaoPagamentoInicial": 0,
            "idAutorizacaoPagamentoExato": False,
            "dataInicial": data_ini_api,
            "dataFinal": data_fim_api,
        }

        response = request_context.post(
            ENDPOINT_VENDAS,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            data=payload,
            timeout=30000,
        )

        if not response.ok:
            print(f"[vendas] Erro na API (Status {response.status}): {response.text()}")
            break

        dados = response.json()
        registros = dados.get("registros", [])
        if not registros:
            break

        todos.extend(registros)

        total_items = dados.get("totalItems", len(todos))
        if len(todos) >= total_items or len(registros) < tamanho_pagina:
            break

        pagina += 1

    return todos


# ============================================================================================
# CONSULTA PRINCIPAL — use processar_vendas_posto para rodar toda a cadeia de um CNPJ de posto
# ============================================================================================
def processar_vendas_posto(cnpj_posto: str):
    """
    Função principal. Para rodar vendas de 1 posto:
        chama helpers.obter_periodo_daily (define janelinha do dia)
        busca key do posto
        executa a consulta na API (detectando e persistindo renovação JWT se vier no header)
        filtra + mapeia os dados e grava já no Supabase
    - ATENÇÃO: Chame UMA por vez (thread seguro, mas respeite API rate limit!)
    """
    data_ini, data_fim, datas_validas = helpers.obter_periodo_daily()
    token_atual = database.obter_api_key_posto(cnpj_posto)

    if not token_atual:
        print(f"AVISO: Posto {cnpj_posto} não possui API Key cadastrada.")
        return

    nome_posto = POSTOS_ALVO.get(cnpj_posto, cnpj_posto)
    print(f"Iniciando consulta: {nome_posto} | {data_ini[:10]} a {data_fim[:10]}")

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            request_context = p.request.new_context(base_url=BASE_URL)

            # RENOVAÇÃO AUTOMÁTICA
            # AO CONSULTAR A PRIMEIRA PÁGINA, PODE VIR UM NOVO JWT NO HEADER.
            # Se vier, persiste direto no Supabase. É transparente pro resto do fluxo!
            nova_chave_capturada = None

            payload_p1 = {
                "pagina": 1,
                "tamanhoPagina": 1,
                "idAutorizacaoPagamentoInicial": 0,
                "idAutorizacaoPagamentoExato": False,
                "dataInicial": data_ini,
                "dataFinal": data_fim,
            }
            probe = request_context.post(
                ENDPOINT_VENDAS,
                headers={"Authorization": f"Bearer {token_atual}", "Content-Type": "application/json"},
                data=payload_p1,
                timeout=30000,
            )
            nova_chave_capturada = probe.headers.get("renovacao-automatica-jwt")
            if nova_chave_capturada:
                print(f"Nova chave detectada para {cnpj_posto}. Atualizando...")
                database.atualizar_api_key_posto(cnpj_posto, nova_chave_capturada)
                token_atual = nova_chave_capturada

            if not probe.ok:
                print(f"Erro na API (Status {probe.status}): {probe.text()}")
                return

            # Busca todas as páginas do(s) dia(s)
            todos_registros = _buscar_vendas_paginado(request_context, token_atual, data_ini, data_fim)

            # Filtra apenas vendas AUTORIZADAS e que estejam no(s) dia(s) válido(s)
            vendas_filtradas = [
                r for r in todos_registros
                if r.get("dataAbastecimento", "")[:10] in datas_validas
                and r.get("statusAutorizacaoPagamento") == "Autorizado"
            ]

            print(f"{len(vendas_filtradas)} abastecimentos autorizados para {nome_posto}.")

            # Envia para base apenas se existir algum registro
            if vendas_filtradas:
                posto_uuid = database.get_posto_id(cnpj_posto)
                registros_mapeados = [mapear_venda(r, cnpj_posto, posto_uuid) for r in vendas_filtradas]
                database.enviar_para_supabase(registros_mapeados, "vendas_diarias")

    except Exception as e:
        print(f"Erro inesperado ao processar posto {cnpj_posto}: {e}")
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


# ============================================================================================
# ENTRY POINT PADRÃO (roda todas as vendas de todos os postos do config)
# ============================================================================================
if __name__ == "__main__":
    for cnpj in POSTOS_ALVO:
        processar_vendas_posto(cnpj)  # Para rodar só um, basta filtrar aqui!
# End of bloco
