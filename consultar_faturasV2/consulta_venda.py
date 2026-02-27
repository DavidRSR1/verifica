"""
consulta_venda.py
-----------------
Fluxo de consulta de vendas diárias por posto.

Como funciona:
  - Cada posto possui sua própria API Key armazenada no Supabase.
  - A API Profrotas renova automaticamente o JWT quando ele atinge 50% da
    vida útil, enviando a nova chave no header 'renovacao-automatica-jwt'.
  - Este módulo detecta essa renovação e persiste a nova chave no Supabase
    de forma transparente.
"""

import database
import helpers
from config import POSTOS_ALVO, BASE_URL, ENDPOINT_VENDAS

from playwright.sync_api import sync_playwright
from tenacity import retry, stop_after_attempt, wait_exponential


# ==========================================
# PARSER & MAPEAMENTO
# ==========================================
def mapear_venda(r: dict, cnpj_posto: str, posto_uuid) -> dict:
    """Achata o JSON aninhado da API para o formato esperado pelo Supabase."""

    data_bruta = r.get("dataAbastecimento") or ""
    data_iso = data_bruta[:10] if len(data_bruta) >= 10 else None
    hora = data_bruta[11:19] if len(data_bruta) >= 19 else None

    frota     = r.get("frota") or {}
    motorista = r.get("motorista") or {}
    veiculo   = r.get("veiculo") or {}
    ciclo     = r.get("ciclo") or {}

    itens = r.get("itens") or []
    item  = itens[0] if itens else {}

    return {
        "id_autorizacao":       r.get("idAutorizacaoPagamento"),
        "posto_id":             posto_uuid,
        "cnpj_posto":           cnpj_posto,
        "data_abastecimento":   data_iso,
        "hora_abastecimento":   hora,
        "nome_frota":           frota.get("razaoSocial") or "",
        "cnpj_frota":           frota.get("cnpj") or "",
        "nome_motorista":       motorista.get("nome") or "",
        "cpf_motorista":        motorista.get("cpf") or "",
        "placa_veiculo":        veiculo.get("placa") or "",
        "produto":              item.get("descricao") or "",
        "quantidade_litros":    item.get("quantidade"),
        "valor_unitario":       item.get("valorUnitario"),
        "valor_total":          r.get("valorTotal"),
        "status_autorizacao":   r.get("statusAutorizacaoPagamento") or "",
        "status_nota_fiscal":   r.get("statusEmissaoNotaFiscal") or "",
        "ciclo_inicio":         (ciclo.get("dataInicio") or "")[:10] or None,
        "ciclo_fim":            (ciclo.get("dataFim") or "")[:10] or None,
        "ciclo_limite_emissao": (ciclo.get("dataLimiteEmissao") or "")[:10] or None,
    }


# ==========================================
# CONSULTA COM PAGINAÇÃO COMPLETA
# ==========================================
def _buscar_vendas_paginado(
    request_context,
    token: str,
    data_ini_api: str,
    data_fim_api: str,
) -> list[dict]:
    """
    Itera todas as páginas da API de vendas e retorna todos os registros
    autorizados. Garante que nenhum registro seja perdido em postos de
    alto volume.
    """
    todos = []
    pagina = 1
    tamanho_pagina = 200  # máximo seguro por chamada

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


# ==========================================
# CONSULTA PRINCIPAL (rotina diária)
# ==========================================
def processar_vendas_posto(cnpj_posto: str):
    """Executa a consulta de vendas para um posto específico."""

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

            # Renovação automática de token
            nova_chave_capturada = None

            # Busca primeira página para detectar header de renovação
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

            # Busca completa com paginação
            todos_registros = _buscar_vendas_paginado(request_context, token_atual, data_ini, data_fim)

            vendas_filtradas = [
                r for r in todos_registros
                if r.get("dataAbastecimento", "")[:10] in datas_validas
                and r.get("statusAutorizacaoPagamento") == "Autorizado"
            ]

            print(f"{len(vendas_filtradas)} abastecimentos autorizados para {nome_posto}.")

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


# ==========================================
# ENTRY POINT
# ==========================================
if __name__ == "__main__":
    for cnpj in POSTOS_ALVO:
        processar_vendas_posto(cnpj)