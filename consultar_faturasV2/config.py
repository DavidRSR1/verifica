import os
from dotenv import load_dotenv

load_dotenv()

# Credenciais de Login (Usadas no consulta_reembolso.py)
USUARIO = os.getenv("PROFROTAS_USER")
SENHA = os.getenv("PROFROTAS_PASS")

# URLs da API (Usadas no consulta_venda.py)
BASE_URL = os.getenv("PROFROTAS_BASE_URL", "https://api-portal.profrotas.com.br")
ENDPOINT_VENDAS = os.getenv("ENDPOINT_VENDAS", "/api/revenda/autorizacao/pesquisa")

# Credenciais Supabase (Usadas no database.py)
URL_SUPABASE = os.getenv("SUPABASE_URL")
CHAVE_SUPABASE = os.getenv("SUPABASE_KEY")

# Lista de Postos Alvo
POSTOS_ALVO = {
    #----------------SQUAD 7-------------------------
    "03.951.672/0001-70": "Auto Posto Sof Norte Ltda",
    "36.203.543/0001-53": "Mg Comercio De Combustiveis Ltda",
    "43.288.248/0001-02": "Posto De Combustiveis Correa 020 Ltda",
    "43.153.039/0001-51": "Posto de Combustíveis Divisão Ltda",
    "23.049.249/0001-97": "Posto Sao Roque Alianca Ltda",
    #"57.460.770/0001-34": "Posto São Roque Brazlandia Ltda",           # não está no nosso acesso
    "40.806.619/0001-02": "Auto Posto Pro Trok Rio Preto Ltda",
    "01.427.744/0001-50": "Sao Bernardo Servicos Automotivos Ltda",
    
    #----------------SQUAD X-------------------------
    "31.160.539/0001-31": "Posto Sao Roque Cerradao Ltda",     # chaves até aqui

}
