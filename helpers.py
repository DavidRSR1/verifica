from datetime import datetime, timedelta
from typing import Optional


# ============================================================
# NUMÉRICO — funções centralizadas (evita duplicação entre módulos)
# ============================================================

def safe_float(v) -> Optional[float]:
    """Converte qualquer valor para float, retorna None se inválido."""
    if v is None or v == "" or v == "None":
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None


def safe_sum(records: list[dict], key: str) -> float:
    return sum(safe_float(r.get(key)) or 0.0 for r in records)


# ============================================================
# DATAS
# ============================================================

def obter_periodo_daily():
    hoje = datetime.now()

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
    hoje = datetime.now()
    data_fim = hoje
    data_inicio = hoje - timedelta(days=dias_historico)

    return (
        data_inicio.strftime("%Y-%m-%dT00:00:00.000Z"),
        data_fim.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    )