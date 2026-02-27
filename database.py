import traceback
from supabase import create_client, Client
from config import URL_SUPABASE, CHAVE_SUPABASE

# ==========================================
# CONEXÃO SUPABASE
# Este bloco centraliza toda a criação/conexão com o Supabase.
# Se mudar as credenciais ou endpoint, ajuste no .env e config.py
# ==========================================
def get_supabase_client() -> Client | None:
    """
    Cria e retorna cliente Supabase usando variáveis do .env.
    Lembre: conectar 1x só e reutilizar a instância!
    """
    try:
        cliente = create_client(URL_SUPABASE, CHAVE_SUPABASE)
        print("Supabase conectado.")
        return cliente
    except Exception as e:
        print(f"Erro ao conectar com o Supabase: {e}")
        return None

# Instância global do cliente para este módulo (evita reconectar toda hora)
supabase = get_supabase_client()

# Cache in-memory (evita consultas repetidas para o mesmo CNPJ)
_cache_postos = {}

# Defina a(s) chave(s) de upsert de cada tabela (aplica regra de conflict do Supabase)
_CONFLICT_KEYS = {
    "relatorio_abastecimentos": "empresa,data_bruta,valor_total",
    "vendas_diarias":           "id_autorizacao",
}

# ==========================================
# CONSULTA DE POSTOS (tudo relacionado a tabela de Postos)
# ==========================================

def get_posto_id(cnpj: str) -> str | None:
    """
    Busca o UUID (id) do posto pelo CNPJ via Supabase - com cache local.
    Prático: evita múltiplas consultas para o mesmo CNPJ durante o processamento!
    Retorna None se não encontrar.
    """
    if not supabase:
        return None

    if cnpj in _cache_postos:
        return _cache_postos[cnpj]

    try:
        resposta = supabase.table("postos").select("id").eq("cnpj", cnpj).limit(1).execute()
        if resposta.data:
            _cache_postos[cnpj] = resposta.data[0]["id"]
        else:
            _cache_postos[cnpj] = None
        return _cache_postos[cnpj]
    except Exception as e:
        print(f"Erro ao buscar posto_id para {cnpj}: {e}")
        return None

def obter_api_key_posto(cnpj: str) -> str | None:
    """
    Busca a API Key salva para o posto do CNPJ informado.
    Use sempre que for consultar vendas desse posto.
    """
    if not supabase:
        return None
    try:
        resposta = supabase.table("postos").select("api_key").eq("cnpj", cnpj).limit(1).execute()
        if resposta.data:
            return resposta.data[0].get("api_key")
        return None
    except Exception as e:
        print(f"Erro ao buscar chave no banco: {e}")
        return None

def atualizar_api_key_posto(cnpj: str, nova_chave: str):
    """
    Atualiza a API Key do posto especificado quando o JWT for renovado.
    Prático: chamada automática, mantém o banco sempre com o token válido.
    """
    if not supabase:
        return
    try:
        supabase.table("postos").update({"api_key": nova_chave}).eq("cnpj", cnpj).execute()
        print(f"[Token] Chave do posto {cnpj} renovada com sucesso no Supabase!")
    except Exception as e:
        print(f"Erro ao persistir nova chave: {e}")

# ==========================================
# ESCRITA (Upsert genérico para qualquer tabela)
# Use SEMPRE este método para inserir/atualizar dados!
# ==========================================
def enviar_para_supabase(dados: list[dict], nome_tabela: str) -> None:
    """
    Insere (ou atualiza - upsert) lista de registros em uma tabela do Supabase.
    - Regra: usa upsert se houver chave de conflito configurada, senão faz insert.
    - Prático: já lida com situações de duplicidade!
    - DICA: sempre envie uma lista, mesmo com 1 registro (supabase espera isso).
    Param:
      dados        Lista de dicts para inserir/atualizar.
      nome_tabela  Nome da tabela alvo no Supabase.
    """
    if not dados:
        print("Nenhum dado para enviar ao Supabase.")
        return
    if not supabase:
        print("Cliente Supabase não inicializado.")
        return

    on_conflict = _CONFLICT_KEYS.get(nome_tabela)

    try:
        query = supabase.table(nome_tabela)
        # upsert: insere ou atualiza, conforme conflito configurado (_CONFLICT_KEYS)
        resposta = (
            query.upsert(dados, on_conflict=on_conflict).execute()
            if on_conflict
            else query.insert(dados).execute()
        )
        print(f"{len(resposta.data)} registros inseridos/atualizados em '{nome_tabela}'.")
    except Exception as e:
        print(f"Falha ao inserir no Supabase: {e}")
        print(traceback.format_exc())
