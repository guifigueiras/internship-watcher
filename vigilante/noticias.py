"""
noticias.py: notícias sobre programas de estágio, via Google News (RSS).

RSS é um formato XML simples que muitos sites publicam com as últimas notícias.
O Google News gera um RSS para qualquer pesquisa:
    https://news.google.com/rss/search?q=<pesquisa>&hl=pt-PT&gl=PT&ceid=PT:pt-150
  hl = idioma, gl = país, ceid = edição. "when:30d" na pesquisa limita aos últimos 30 dias.

Cada notícia relevante vira um ItemNoticia; ⭐ quando menciona uma empresa da lista.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from xml.etree import ElementTree

from vigilante.filtros import e_informatica, normalizar, noticia_relevante
from vigilante.rede import pedir

DIAS = 30                # notícias mais antigas são ignoradas
URL = "https://news.google.com/rss/search?q={q}&hl=pt-PT&gl=PT&ceid=PT:pt-150"


@dataclass
class ItemNoticia:
    titulo: str
    url: str
    origem: str             # jornal / site
    publicada: datetime | None
    empresa: str = "Notícias"
    empresa_citada: str = ""  # empresa da lista mencionada no título (⭐)
    fonte: str = "noticia"
    local: str = ""
    zona: str = ""
    informatica: bool = False
    prioridade: int = 3

    @property
    def chave(self) -> str:
        # O mesmo artigo aparece em várias pesquisas: a chave usa só o título normalizado.
        texto = " ".join(re.sub(r"[^\w\s]", " ", normalizar(self.titulo)).split())
        return f"noticia:{hashlib.sha1(texto.encode()).hexdigest()[:16]}"


def _nomes(empresas: list[dict]) -> list[tuple[str, re.Pattern]]:
    """Para cada empresa, uma expressão regular que encontra o nome num título.
    Nomes curtos em maiúsculas (NOS, EY, BPI, CTT...) só contam escritos em maiúsculas,
    senão "nos" (= "em os") apareceria em todas as notícias."""
    resultado = []
    for emp in empresas:
        nome = emp["nome"]
        variantes = {nome, re.sub(r"\s*\(.*?\)", "", nome).strip()}
        variantes |= set(re.findall(r"\((.*?)\)", nome))              # "MEO (Altice Portugal)" -> "Altice Portugal"
        variantes |= {v.replace(" Portugal", "").strip() for v in list(variantes)}
        primeira = nome.split()[0]
        if primeira.isupper():                                         # "TAP Air Portugal" -> "TAP"
            variantes.add(primeira)
        variantes = {v for v in variantes if len(v) >= 2}
        partes = []
        for v in sorted(variantes, key=len, reverse=True):
            if len(v) <= 4 and v.isupper():
                partes.append(r"(?-i:\b" + re.escape(v) + r"\b)")
            else:
                partes.append(r"\b" + re.escape(normalizar(v)) + r"\b")
        resultado.append((nome, re.compile("|".join(partes), re.I)))
    return resultado


def citada(titulo: str, nomes) -> str:
    original, normal = titulo, normalizar(titulo)
    for nome, padrao in nomes:
        # As variantes em maiúsculas procuram no original; as outras no texto normalizado.
        if padrao.search(normal) or padrao.search(original):
            return nome
    return ""


def ler_rss(xml: bytes) -> list[tuple[str, str, str, datetime | None]]:
    raiz = ElementTree.fromstring(xml)
    itens = []
    for item in raiz.iter("item"):
        titulo = (item.findtext("title") or "").strip()
        origem = (item.findtext("source") or "").strip()
        if origem and titulo.endswith(" - " + origem):
            titulo = titulo[: -len(origem) - 3]          # o Google acrescenta " - Jornal" ao título
        try:
            publicada = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            publicada = None
        itens.append((titulo, (item.findtext("link") or "").strip(), origem, publicada))
    return itens


def ler_noticias(sessao, consultas: list[str], empresas: list[dict], hoje: date) -> tuple[list[ItemNoticia], list[str]]:
    """Devolve (notícias relevantes, erros). Um erro numa pesquisa não impede as outras."""
    nomes = _nomes(empresas)
    limite = datetime.combine(hoje - timedelta(days=DIAS), datetime.min.time(), timezone.utc)
    itens: dict[str, ItemNoticia] = {}
    erros = []
    for consulta in consultas:
        try:
            xml = pedir(sessao, "GET", URL.format(q=quote(f"{consulta} when:{DIAS}d"))).content
            for titulo, url, origem, publicada in ler_rss(xml):
                if publicada and publicada < limite:
                    continue
                if not noticia_relevante(titulo):
                    continue
                item = ItemNoticia(titulo, url, origem, publicada, empresa_citada=citada(titulo, nomes),
                                   informatica=e_informatica(titulo))
                itens.setdefault(item.chave, item)
        except Exception as erro:
            erros.append(f"{consulta}: {type(erro).__name__}: {erro}"[:200])
    return list(itens.values()), erros
