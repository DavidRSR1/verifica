from datetime import datetime, timedelta
from typing import Optional
import re

# ============================================================
# EMPRESAS ISENTAS DE NF
# ============================================================
# Adicione aqui partes/nome(s) de empresas isentas de emissão de Nota Fiscal
# Pode ser fragmento: se o nome da empresa contém algum dos textos abaixo, consideramos isenta.
EMPRESAS_ISENTAS_NF = [
    "Sr Transportadora",
    "Maria Das Dores",
    "Transportes Gabardo"
    # DICA: Só colocar o pedaço do nome da empresa, não precisa ser completo!
]

def verificar_isencao(empresa: str) -> bool:
    """
    Verifica se a empresa está na lista de isentas de NF.
    Facilita: se algum fragmento do nome estiver em EMPRESAS_ISENTAS_NF, retorna True.
    Use sempre que precisar aplicar regra de isenção de nota.
    """
    if not empresa:
        return False
    emp = empresa.lower()
    # Busca case-insensitive se fragmento existe no nome
    return any(e.lower() in emp for e in EMPRESAS_ISENTAS_NF)

# ============================================================
# NUMÉRICO — Funções de Limpeza Agressiva (Tratamento BR/US)
# ============================================================
# ATENÇÃO: Estas funções tratam campos numéricos que podem vir em qualquer formato BR/US
# (“1.234,56”, “1234.56”, “R$1.000,00”, etc). Use SEMPRE safe_float ao puxar qualquer número!

def safe_float(v) -> Optional[float]:
    """
    Converte valor para float, aceitando formatos brasileiros e americanos, além de strings bagunçadas.
    Exemplos tratados:
      - "R$ 1.234,56"      => 1234.56
      - "1000"             => 1000.0
      - "None" ou "" ou {} => None
    Não lança exceção. Retorna None caso impossível converter.
    """
    if v is None or v == "" or str(v).lower() == "none":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    
    # Remove símbolos comuns, espaços, etc
    s = str(v).upper().replace("R$", "").replace("RS", "").replace("L", "").strip()
    if not s:
        return None

    # Se contiver ',', assume que ',' é decimal (BR): remove pontos (milhar), troca vírgula por ponto
    if "," in s:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    else:
        # Se mais de um ponto, tudo antes do último é milhar (ex: "1.234.567.89" -> "1234567.89")
        if s.count(".") > 1:
            partes = s.split(".")
            s = "".join(partes[:-1]) + "." + partes[-1]
    
    try:
        return float(s)
    except ValueError:
        # Última tentativa: extrai apenas o primeiro número padrão que encontrar na string
        match = re.search(r"[-+]?\d*\.?\d+", s)
        return float(match.group()) if match else None

def safe_sum(records: list[dict], key: str) -> float:
    """
    Soma segura: soma valores da lista de dicionários, usando safe_float para cada item.
    Se chave não existe ou não é número, considera 0.0.
    Use SEMPRE esta ao somar valores de tabela!
    """
    return sum(safe_float(r.get(key)) or 0.0 for r in records)

# ============================================================
# DATAS — Utilidades para range diário e janelas de reembolso
# ============================================================
# Centraliza geração de períodos de consulta sempre no formato ISO (string padrão API Profrotas).

def obter_periodo_daily():
    """
    Gera o período de consulta de vendas 'padrão': se hoje é segunda, pega sexta a domingo;
    nos outros dias, sempre o último dia útil (ontem).
    Retorna:
      - data_ini_api: string no formato "%Y-%m-%dT00:00:00Z" (início do período)
      - data_fim_api: string no formato "%Y-%m-%dT23:59:59Z" (fim do período)
      - datas_validas: lista de strings dos dias individuais (ex: ["2024-05-17","2024-05-18"])
    Use SEMPRE para construir payload/data de consulta!
    """
    hoje = datetime.now()

    # Se for segunda-feira, pega 3 dias atrás (sexta a domingo), senão pega só ontem
    if hoje.weekday() == 0:
        inicio = hoje - timedelta(days=3)
        fim = hoje - timedelta(days=1)
    else:
        inicio = fim = hoje - timedelta(days=1)

    data_ini_api = inicio.strftime("%Y-%m-%dT00:00:00Z")
    data_fim_api = fim.strftime("%Y-%m-%dT23:59:59Z")

    datas_validas = []
    delta = (fim - inicio).days
    for i in range(delta + 1):
        dia = inicio + timedelta(days=i)
        datas_validas.append(dia.strftime("%Y-%m-%d"))

    return data_ini_api, data_fim_api, datas_validas

def calcular_janela_reembolso(dias_historico: int = 7) -> tuple[str, str]:
    """
    Retorna tupla com range de datas para pesquisar reembolsos:
      - data_inicial: {dias_historico} dias atrás, às 00:00:00.000Z
      - data_final: hoje, hora minuto segundo atual, em formato .000Z
    Útil para pegar histórico proporcional ao cron/agendamento.
    """
    hoje = datetime.now()
    data_fim = hoje
    data_inicio = hoje - timedelta(days=dias_historico)
    return (
        data_inicio.strftime("%Y-%m-%dT00:00:00.000Z"),
        data_fim.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    )
