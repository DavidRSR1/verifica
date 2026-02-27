import traceback
from supabase import create_client, Client
from config import URL_SUPABASE, CHAVE_SUPABASE


# ==========================================
# CONEXÃO
# ==========================================
def get_supabase_client() -> Client | None:
    try:
        cliente = create_client(URL_SUPABASE, CHAVE_SUPABASE)
        print("Supabase conectado.")
        return cliente
    except Exception as e:
        print(f"Erro ao conectar com o Supabase: {e}")
        return None


# Instância global do cliente para este módulo
supabase = get_supabase_client()

_cache_postos = {}

# Chaves de upsert por tabela
_CONFLICT_KEYS = {
    "relatorio_abastecimentos": "empresa,data_bruta,valor_total",
    "vendas_diarias":           "id_autorizacao",
}


# ==========================================
# POSTOS
# ==========================================
def get_posto_id(cnpj: str) -> str | None:
    """Busca o UUID do posto pelo CNPJ no Supabase (com cache em memória)."""
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
    """Recupera a API Key atual de um posto específico no Supabase."""
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
    """Atualiza a API Key no banco quando a renovação automática é disparada."""
    if not supabase:
        return
    try:
        supabase.table("postos").update({"api_key": nova_chave}).eq("cnpj", cnpj).execute()
        print(f"[Token] Chave do posto {cnpj} renovada com sucesso no Supabase!")
    except Exception as e:
        print(f"Erro ao persistir nova chave: {e}")


# ==========================================
# ESCRITA
# ==========================================
def enviar_para_supabase(dados: list[dict], nome_tabela: str) -> None:
    """Envia os dados para a tabela do Supabase via upsert."""
    if not dados:
        print("Nenhum dado para enviar ao Supabase.")
        return
    if not supabase:
        print("Cliente Supabase não inicializado.")
        return

    on_conflict = _CONFLICT_KEYS.get(nome_tabela)

    try:
        query = supabase.table(nome_tabela)
        resposta = (
            query.upsert(dados, on_conflict=on_conflict).execute()
            if on_conflict
            else query.insert(dados).execute()
        )
        print(f"{len(resposta.data)} registros inseridos/atualizados em '{nome_tabela}'.")
    except Exception as e:
        print(f"Falha ao inserir no Supabase: {e}")
        print(traceback.format_exc())