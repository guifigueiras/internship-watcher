"""Ferramentas partilhadas pelos testes: uma sessão HTTP falsa que nunca vai à internet."""
from __future__ import annotations

import json as jsonlib
from urllib.parse import parse_qs, urlparse

import pytest
import requests


def resposta(status: int = 200, json=None, text: str = "", headers: dict | None = None, url: str = "") -> requests.Response:
    """Cria uma resposta HTTP verdadeira do requests, mas com conteúdo inventado."""
    r = requests.Response()
    r.status_code = status
    r.url = url
    r.reason = "OK" if status < 400 else "Erro"
    corpo = jsonlib.dumps(json) if json is not None else text
    r._content = corpo.encode("utf-8")
    r.headers.update(headers or {})
    r.encoding = "utf-8"
    return r


class SessaoFalsa:
    """Substitui requests.Session. `rotas` é uma função (metodo, url, query, corpo_json) -> resposta."""

    def __init__(self, rotas):
        self.rotas = rotas
        self.pedidos = []

    def request(self, metodo, url, params=None, json=None, **kw):
        alvo = urlparse(url)
        query = {k: v[0] for k, v in parse_qs(alvo.query).items()}
        query.update({k: str(v) for k, v in (params or {}).items()})
        base = f"{alvo.scheme}://{alvo.netloc}{alvo.path}"
        self.pedidos.append((metodo, base, query, json))
        r = self.rotas(metodo, base, query, json)
        if r is None:
            r = resposta(404, text="não encontrado")
        r.url = url
        return r


@pytest.fixture(autouse=True)
def sem_esperas(monkeypatch):
    """As novas tentativas em rede.py esperam alguns segundos; nos testes não."""
    monkeypatch.setattr("vigilante.rede.time.sleep", lambda s: None)
