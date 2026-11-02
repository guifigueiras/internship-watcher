"""
rede.py: pedidos HTTP partilhados pelo detetor e pelos leitores.
"""
from __future__ import annotations

import time

import requests

# Identificamo-nos como um Chrome normal: muitos sites recusam (403) pedidos
# que não pareçam vir de um browser.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CABECALHOS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}
TIMEOUT = 20  # segundos por pedido

# Códigos HTTP que costumam ser temporários: vale a pena tentar outra vez.
TEMPORARIOS = {429, 500, 502, 503, 504}


def nova_sessao() -> requests.Session:
    sessao = requests.Session()
    sessao.headers.update(CABECALHOS)
    return sessao


def _espera(resposta: requests.Response | None, tentativa: int) -> float:
    """Segundos a esperar antes de tentar outra vez (usa o cabeçalho Retry-After se existir)."""
    if resposta is not None:
        try:
            return min(float(resposta.headers.get("Retry-After", "")), 30)
        except ValueError:
            pass
    return 2 * (tentativa + 1)  # 2 s, 4 s, ...


def pedir(sessao: requests.Session, metodo: str, url: str, tentativas: int = 3, **kw) -> requests.Response:
    """Faz um pedido HTTP. Em erros temporários (rede, 429, 5xx) espera e tenta de novo.
    Erros definitivos (ex.: 404) lançam requests.HTTPError logo à primeira."""
    kw.setdefault("timeout", TIMEOUT)
    for tentativa in range(tentativas):
        ultima = tentativa == tentativas - 1
        try:
            resposta = sessao.request(metodo, url, **kw)
        except (requests.ConnectionError, requests.Timeout):
            if ultima:
                raise
            time.sleep(_espera(None, tentativa))
            continue
        if resposta.status_code in TEMPORARIOS and not ultima:
            time.sleep(_espera(resposta, tentativa))
            continue
        resposta.raise_for_status()
        return resposta
    raise RuntimeError("inalcançável")  # o ciclo devolve ou lança antes de chegar aqui
