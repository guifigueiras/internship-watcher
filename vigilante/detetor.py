"""
detetor.py: descobre, para cada empresa da lista, a página de carreiras e a
plataforma de recrutamento que ela usa (Greenhouse, Lever, Workday, ...).

Corre-se à mão (workflow "Detetor de páginas de carreiras"), normalmente uma ou duas vezes:
    python -m vigilante.detetor                       # todas as empresas, com browser
    python -m vigilante.detetor --sem-browser         # só pedidos HTTP simples (mais rápido)
    python -m vigilante.detetor --apenas "OutSystems, Feedzai"

Funciona em duas fases:
  1. HTTP: descarrega o site, segue os links que parecem "carreiras" e procura no
     código das páginas endereços de plataformas conhecidas (as "assinaturas").
  2. Browser (Playwright): para as empresas onde a fase 1 não encontrou plataforma,
     abre a página num Chromium a sério. Assim o JavaScript corre, e também se apanham
     os pedidos que a página faz em segundo plano (muitas vezes vão direto à plataforma).

Resultados na pasta saida/:
    relatorio_detetor.md   tabela para leres (também aparece na página da execução)
    plataformas.yaml       configuração gerada, que o passo 3 vai usar
    detetor.json           todos os detalhes, para depuração
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import yaml
from bs4 import BeautifulSoup

from vigilante.config import FICHEIRO_EMPRESAS, FICHEIRO_PLATAFORMAS, LISBOA, RAIZ, carregar_empresas, carregar_plataformas
from vigilante.notify import enviar
from vigilante.rede import CABECALHOS, TIMEOUT, UA
from vigilante.leitores import e_site_rmk

# ---------------------------------------------------------------- configuração

MAX_BYTES = 3_000_000       # não lemos mais do que 3 MB de cada página
MAX_PAGINAS = 6             # páginas visitadas por empresa na fase HTTP
TRABALHADORES = 8           # empresas analisadas em paralelo na fase HTTP

# Palavras que indicam um link de carreiras, com os respetivos pontos.
# Procuram-se no texto do link e no endereço (href).
PALAVRAS_CARREIRAS = {
    "carreiras": 5, "carreira": 5, "careers": 5, "career": 4,
    "trabalha connosco": 5, "trabalhe connosco": 5, "trabalhar connosco": 5,
    "trabalha-connosco": 5, "trabalhe-connosco": 5,
    "trabalhar no": 5, "trabalhar na": 5, "trabalhar nos": 5,          # "Trabalhar no BPI"
    "trabalhar-no": 5, "trabalhar-na": 5, "trabalhar-nos": 5,
    "junte-se": 4, "open positions": 4, "open-positions": 4, "vacancies": 3, "candidaturas": 3,
    "work with us": 4, "work-with-us": 4, "join us": 4, "join-us": 4,
    "join our team": 4, "junta-te": 4, "recrutamento": 4, "recruitment": 4,
    "jobs": 4, "vagas": 3, "emprego": 3, "estágios": 3, "internship": 3,
    "job": 2, "oportunidades": 2, "talento": 2, "talent": 2, "estudantes": 1, "students": 1,
}
PONTOS_MINIMOS = 3
# Links para estes sites nunca são a página de carreiras da empresa.
DOMINIOS_IGNORADOS = ("linkedin.", "facebook.", "instagram.", "twitter.", "x.com",
                      "youtube.", "tiktok.", "glassdoor.", "indeed.", "whatsapp.", "t.me")
EXTENSOES_IGNORADAS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".mp4", ".doc", ".docx")

# Assinaturas: (plataforma, expressão regular). O grupo entre parênteses é o
# identificador da empresa dentro da plataforma (ex.: boards.greenhouse.io/outsystems).
_ASSINATURAS = [
    ("greenhouse", r"greenhouse\.io/embed/job_board(?:/js)?\?for=([a-z0-9_-]+)"),
    ("greenhouse", r"boards-api(?:\.eu)?\.greenhouse\.io/v1/boards/([a-z0-9_-]+)"),
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([a-z0-9_-]+)"),
    ("lever", r"api(?:\.eu)?\.lever\.co/v0/postings/([a-z0-9_.-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([a-z0-9_.-]+)"),
    ("workable", r"apply\.workable\.com/(?:api/v\d+/(?:widget/)?accounts/)?([a-z0-9_-]+)"),
    ("smartrecruiters", r"api\.smartrecruiters\.com/v1/companies/([a-z0-9_-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([a-z0-9_-]+)"),
    ("workday", r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[a-z0-9_-]+/)?(?:[a-z]{2}-[a-z]{2}/)?([a-z0-9_-]+)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("personio", r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),
    ("ashby", r"(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/([a-z0-9_.-]+)"),
    ("teamtailor", r"([a-z0-9-]+)\.teamtailor\.com"),
    # Plataformas sem API pública simples: só registamos que existem.
    # SuccessFactors "clássico": career5.successfactors.eu/career?company=XXX. Os sites "RMK"
    # (com domínio próprio, ex.: jobs.galp.com) são detetados à parte, em procurar_rmk().
    ("successfactors", r"(career\d*\.successfactors\.(?:eu|com))/career\?[^\"'\s<>]*?company=([A-Za-z0-9_-]+)"),
    ("taleo", r"([a-z0-9-]+)\.taleo\.net"),
    ("icims", r"([a-z0-9-]+)\.icims\.com"),
    ("oracle", r"([a-z0-9-]+)\.fa\.[a-z0-9-]+\.oraclecloud\.com/hcmui/candidateexperience"),
    ("eightfold", r"([a-z0-9-]+)\.eightfold\.ai"),
    ("avature", r"([a-z0-9-]+)\.avature\.net"),
    ("phenom", r"cdn\.phenompeople\.com"),
    ("jobvite", r"jobs\.jobvite\.com/([a-z0-9_-]+)"),
    ("bamboohr", r"([a-z0-9-]+)\.bamboohr\.com"),
    ("breezy", r"([a-z0-9-]+)\.breezy\.hr"),
]
ASSINATURAS = [(tipo, re.compile(padrao, re.IGNORECASE)) for tipo, padrao in _ASSINATURAS]

# Plataformas cujo identificador conseguimos confirmar numa API pública.
VERIFICAVEIS = {"greenhouse", "lever", "workable", "smartrecruiters", "workday",
                "recruitee", "personio", "ashby", "teamtailor"}
# Identificadores que são partes genéricas do endereço, não o nome da empresa.
IDS_GENERICOS = {"www", "api", "apply", "jobs", "careers", "app", "cdn", "static", "assets",
                 "embed", "boards", "job-boards", "js", "widget", "scripts", "help", "support",
                 "login", "wday", "v0", "v1", "accounts", "posting-api"}
# Para estas plataformas o identificador diferencia maiúsculas de minúsculas.
IDS_COM_MAIUSCULAS = {"smartrecruiters", "lever", "ashby", "workday"}

# Grandes empresas com sistema próprio: tratadas à parte no passo 3.
PROPRIAS = [
    ("amazon", re.compile(r"amazon\.jobs", re.I)),
    ("google", re.compile(r"google\.com/about/careers|careers\.google\.com", re.I)),
    ("microsoft", re.compile(r"careers\.microsoft\.com", re.I)),
]


# ---------------------------------------------------------------- resultado

@dataclass
class Resultado:
    nome: str
    setor: str
    prioridade: int
    site: str
    estado: str = "erro"            # plataforma | propria | pagina | sem_carreiras | erro
    codigo_http: int | None = None  # resposta ao pedido simples
    codigo_browser: int | None = None
    url_final: str | None = None    # endereço depois de redirecionamentos
    carreiras: str | None = None    # página de carreiras encontrada
    plataforma: str | None = None
    plataforma_id: str | None = None
    ofertas: int | None = None      # nº de ofertas na API da plataforma (todas as localizações)
    via_browser: bool = False
    manual: bool = False            # plataforma escrita à mão em empresas.yaml
    carreiras_manual: str | None = None  # página de carreiras escrita à mão em empresas.yaml
    visitadas: list = field(default_factory=list)
    assinaturas: dict = field(default_factory=dict)  # "tipo|id" -> nº de vezes encontrada
    erros: list = field(default_factory=list)

    def site_respondeu(self) -> bool:
        return any(c is not None and c < 400 for c in (self.codigo_http, self.codigo_browser))


# ---------------------------------------------------------------- utilitários

def limpar_url(url: str) -> str:
    """Remove o fragmento (#...) e a barra final, para comparar endereços."""
    p = urlparse(url)
    return urlunparse(p._replace(fragment="")).rstrip("/")


def obter(sessao: requests.Session, url: str) -> tuple[int | None, str, str, str | None]:
    """Descarrega `url`. Devolve (código HTTP, endereço final, html, erro)."""
    try:
        r = sessao.get(url, headers=CABECALHOS, timeout=TIMEOUT, stream=True, allow_redirects=True)
        conteudo = r.raw.read(MAX_BYTES, decode_content=True)
        r.close()
        tipo = r.headers.get("content-type", "").lower()
        if tipo and "html" not in tipo and "xml" not in tipo and "text" not in tipo:
            return r.status_code, r.url, "", None
        codificacao = r.encoding if "charset" in tipo else "utf-8"
        return r.status_code, r.url, conteudo.decode(codificacao or "utf-8", errors="replace"), None
    except requests.RequestException as erro:
        return None, url, "", f"{type(erro).__name__}: {str(erro)[:160]}"


def parece_carreiras(url: str) -> bool:
    """True se o próprio endereço já parece uma página de carreiras."""
    p = urlparse(url)
    host = p.netloc.lower()
    if host.startswith(("careers.", "career.", "jobs.", "carreiras.", "emprego.", "recrutamento.")):
        return True
    partes = {s.lower() for s in p.path.split("/") if s}
    return bool(partes & {"careers", "career", "carreiras", "carreira", "jobs", "recrutamento",
                          "trabalha-connosco", "trabalhe-connosco", "join-us", "work-with-us"})


def pontuar_link(texto: str, href: str) -> int:
    """Pontos de um link: melhor palavra no texto + melhor palavra no endereço (+ bónus)."""
    texto, href = texto.lower(), href.lower()
    no_texto = max((p for w, p in PALAVRAS_CARREIRAS.items() if w in texto), default=0)
    no_href = max((p for w, p in PALAVRAS_CARREIRAS.items() if w in href), default=0)
    bonus = 3 if urlparse(href).netloc.startswith(("careers.", "jobs.", "carreiras.")) else 0
    return no_texto + no_href + bonus


def links_carreiras(html: str, base: str) -> list[tuple[int, str]]:
    """Lista de (pontos, endereço) dos links que parecem de carreiras, do melhor para o pior."""
    sopa = BeautifulSoup(html, "html.parser")
    melhores: dict[str, int] = {}
    for a in sopa.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base, href)
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            continue
        if any(d in p.netloc.lower() for d in DOMINIOS_IGNORADOS):
            continue
        if p.path.lower().endswith(EXTENSOES_IGNORADAS):
            continue
        texto = " ".join(a.get_text(" ", strip=True).split()) + " " + (a.get("title") or "") + " " + (a.get("aria-label") or "")
        pontos = pontuar_link(texto, url)
        if pontos >= PONTOS_MINIMOS:
            chave = limpar_url(url)
            melhores[chave] = max(pontos, melhores.get(chave, 0))
    return sorted(((p, u) for u, p in melhores.items()), key=lambda x: -x[0])


def construir_id(tipo: str, m: re.Match) -> str | None:
    """Transforma uma correspondência da expressão regular no identificador da empresa."""
    if tipo == "workday":
        inquilino, servidor, site = m.group(1), m.group(2), m.group(3)
        # No Workday, "careers" é um nome de site legítimo; só rejeitamos "wday" e códigos
        # de idioma como "en-US", que aparecem no endereço antes do nome do site.
        if (site.lower() == "wday" or inquilino.lower() in IDS_GENERICOS
                or re.fullmatch(r"[a-z]{2}-[a-z]{2}", site, re.I)):
            return None
        return f"{inquilino.lower()}.{servidor.lower()}/{site}"
    if tipo == "successfactors":
        return f"{m.group(1).lower()}/{m.group(2)}"
    if not m.groups():
        return ""  # plataforma reconhecida, mas sem identificador
    ident = m.group(1).strip(".")
    if not ident or ident.lower() in IDS_GENERICOS:
        return None
    return ident if tipo in IDS_COM_MAIUSCULAS else ident.lower()


# Endereços típicos de ofertas num site RMK: https://jobs.galp.com/job/<titulo>/1260946001/
LINK_RMK = re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})/(?:([a-z0-9-]{2,12})/)?(?:job|go)/[^\"'\s<>/]+/\d{6,}", re.I)


def procurar_rmk(textos: list[str], pares: bool = True) -> Counter:
    """Sites SuccessFactors "RMK": páginas com os vestígios do RMK, ou links no formato RMK.
    Com pares=True, `textos` alterna endereço e html: [url1, html1, url2, html2, ...]."""
    encontrados: Counter = Counter()
    for url, html in (zip(textos[0::2], textos[1::2]) if pares else []):
        if url and html and e_site_rmk(html):
            encontrados[f"successfactors|{urlparse(url).netloc.lower()}"] += 3
    for texto in textos:
        for m in LINK_RMK.finditer((texto or "").replace("\\/", "/")):
            host = m.group(1).lower()
            if "successfactors." not in host and not any(d in host for d in DOMINIOS_IGNORADOS):
                encontrados[f"successfactors|{host}"] += 1
    return encontrados


def procurar_assinaturas(textos: list[str], pares: bool = True) -> Counter:
    """Conta, em todos os textos (html e endereços), as assinaturas de plataformas."""
    encontrados: Counter = procurar_rmk(textos, pares)
    for texto in textos:
        if not texto:
            continue
        texto = texto.replace("\\/", "/")  # endereços dentro de JSON vêm com "\/"
        for tipo, padrao in ASSINATURAS:
            for m in padrao.finditer(texto):
                ident = construir_id(tipo, m)
                if ident is not None:
                    encontrados[f"{tipo}|{ident}"] += 1
    return encontrados


# ---------------------------------------------------------------- verificação nas APIs

def _json(sessao, url, **kw):
    r = sessao.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def verificar(sessao: requests.Session, tipo: str, ident: str) -> int | None:
    """Pergunta à API pública da plataforma quantas ofertas a empresa tem.
    Devolve o número, ou None se não foi possível confirmar."""
    try:
        if tipo == "greenhouse":
            return len(_json(sessao, f"https://boards-api.greenhouse.io/v1/boards/{ident}/jobs")["jobs"])
        if tipo == "lever":
            for dominio in ("api.lever.co", "api.eu.lever.co"):
                try:
                    return len(_json(sessao, f"https://{dominio}/v0/postings/{ident}", params={"mode": "json"}))
                except requests.HTTPError:
                    continue
            return None
        if tipo == "workable":
            return len(_json(sessao, f"https://apply.workable.com/api/v1/widget/accounts/{ident}")["jobs"])
        if tipo == "smartrecruiters":
            return int(_json(sessao, f"https://api.smartrecruiters.com/v1/companies/{ident}/postings",
                             params={"limit": 1})["totalFound"])
        if tipo == "workday":
            inquilino_servidor, site = ident.split("/", 1)
            inquilino = inquilino_servidor.split(".")[0]
            url = f"https://{inquilino_servidor}.myworkdayjobs.com/wday/cxs/{inquilino}/{site}/jobs"
            r = sessao.post(url, json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                            headers={"User-Agent": UA, "Accept": "application/json"}, timeout=TIMEOUT)
            r.raise_for_status()
            return int(r.json()["total"])
        if tipo == "recruitee":
            return len(_json(sessao, f"https://{ident}.recruitee.com/api/offers/")["offers"])
        if tipo == "ashby":
            return len(_json(sessao, f"https://api.ashbyhq.com/posting-api/job-board/{ident}")["jobs"])
        if tipo in ("personio", "teamtailor"):
            urls = ([f"https://{ident}.jobs.personio.de/xml", f"https://{ident}.jobs.personio.com/xml"]
                    if tipo == "personio" else [f"https://{ident}.teamtailor.com/jobs.rss"])
            marca = "<position>" if tipo == "personio" else "<item>"
            for url in urls:
                r = sessao.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
                if r.ok:
                    return r.text.count(marca)
            return None
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None
    return None


def finalizar(res: Resultado, sessao: requests.Session, encontrados: Counter,
              manual: dict | None = None) -> None:
    """Escolhe a plataforma mais provável, confirma-a na API e decide o estado final.
    `manual` é a plataforma escrita à mão em empresas.yaml: tem sempre prioridade."""
    res.assinaturas = dict(encontrados.most_common())

    if manual:
        res.plataforma, res.plataforma_id = manual["tipo"], str(manual.get("id") or "")
        res.ofertas = verificar(sessao, res.plataforma, res.plataforma_id) if res.plataforma in VERIFICAVEIS else None
        res.estado, res.manual = "plataforma", True
        return

    for nome, padrao in PROPRIAS:
        if any(u and padrao.search(u) for u in (res.site, res.url_final, res.carreiras)):
            res.plataforma, res.plataforma_id, res.estado = nome, "", "propria"
            return

    # Ordem de tentativa: primeiro as verificáveis, depois as mais frequentes.
    candidatos = sorted(encontrados.items(),
                        key=lambda kv: (kv[0].split("|")[0] not in VERIFICAVEIS, -kv[1]))
    escolhido = None
    for chave, _ in candidatos[:4]:
        tipo, ident = chave.split("|", 1)
        if tipo in VERIFICAVEIS and ident:
            n = verificar(sessao, tipo, ident)
            if n is not None:
                escolhido = (tipo, ident, n)
                break
    if escolhido is None and candidatos:
        tipo, ident = candidatos[0][0].split("|", 1)
        escolhido = (tipo, ident, None)

    if escolhido:
        res.plataforma, res.plataforma_id, res.ofertas = escolhido
        res.estado = "plataforma"
    elif res.carreiras:
        res.estado = "pagina"
    elif res.site_respondeu():
        res.estado = "sem_carreiras"
    else:
        res.estado = "erro"


# ---------------------------------------------------------------- fase 1: HTTP

def analisar_http(emp: dict) -> Resultado:
    res = Resultado(nome=emp["nome"], setor=emp["setor"], prioridade=emp["prioridade"], site=emp["site"],
                    carreiras_manual=emp.get("carreiras"))
    sessao = requests.Session()
    textos: list[str] = []
    vistos: set[str] = set()
    # Se a página de carreiras foi escrita à mão, começamos por ela em vez do site principal.
    inicio = emp.get("carreiras") or emp["site"]

    codigo, final, html, erro = obter(sessao, inicio)
    res.codigo_http, res.url_final = codigo, final
    res.visitadas.append(f"{codigo or 'erro'} {inicio}")
    vistos.update({limpar_url(inicio), limpar_url(final)})
    if erro:
        res.erros.append(erro)
    textos += [final, html]

    if codigo is not None and codigo < 400 and html:
        if emp.get("carreiras") or parece_carreiras(final):
            res.carreiras = final
        # Fila de páginas a visitar: (endereço, profundidade). Profundidade 1 = links da
        # página inicial; profundidade 2 = links da página de carreiras (ex.: "ver vagas").
        fila = [(u, 1) for _, u in links_carreiras(html, final)[:3]]
        while fila and len(res.visitadas) < MAX_PAGINAS:
            url, profundidade = fila.pop(0)
            if limpar_url(url) in vistos:
                continue
            vistos.add(limpar_url(url))
            c, f, h, e = obter(sessao, url)
            res.visitadas.append(f"{c or 'erro'} {url}")
            textos += [f, h]
            if e:
                res.erros.append(e)
            if c is not None and c < 400:
                if res.carreiras is None:
                    res.carreiras = f
                if profundidade == 1 and h:
                    novos = [(u, 2) for p, u in links_carreiras(h, f) if p >= 4 and limpar_url(u) not in vistos]
                    fila += novos[:2]

    finalizar(res, sessao, procurar_assinaturas(textos), manual=emp.get("plataforma"))
    return res


# ---------------------------------------------------------------- fase 2: browser

def analisar_browser(resultados: list[Resultado]) -> None:
    """Volta a analisar, com um browser a sério, as empresas sem plataforma detetada."""
    from playwright.sync_api import Error as ErroPlaywright
    from playwright.sync_api import TimeoutError as TempoEsgotado
    from playwright.sync_api import sync_playwright

    def renderizar(pagina, url):
        resposta = pagina.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            pagina.wait_for_load_state("networkidle", timeout=10_000)  # espera o JavaScript acalmar
        except TempoEsgotado:
            pass
        return (resposta.status if resposta else None), pagina.url, pagina.content()

    sessao = requests.Session()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for i, res in enumerate(resultados, start=1):
            contexto = browser.new_context(user_agent=UA, locale="pt-PT")
            pagina = contexto.new_page()
            pedidos: list[str] = []
            pagina.on("request", lambda pedido, lista=pedidos: lista.append(pedido.url))
            textos: list[str] = []
            try:
                inicio = res.carreiras or res.carreiras_manual or res.site
                codigo, final, html = renderizar(pagina, inicio)
                res.visitadas.append(f"[browser] {codigo or 'erro'} {inicio}")
                textos += [final, html]
                if res.carreiras is None:
                    res.codigo_browser = codigo
                if res.carreiras is None and codigo is not None and codigo < 400:
                    if res.carreiras_manual or parece_carreiras(final):
                        res.carreiras = final
                    else:
                        links = links_carreiras(html, final)
                        if links:
                            c2, f2, h2 = renderizar(pagina, links[0][1])
                            res.visitadas.append(f"[browser] {c2 or 'erro'} {links[0][1]}")
                            textos += [f2, h2]
                            if c2 is not None and c2 < 400:
                                res.carreiras = f2
            except ErroPlaywright as erro:
                res.erros.append(f"[browser] {str(erro).splitlines()[0][:160]}")
            finally:
                contexto.close()

            res.via_browser = True
            encontrados = Counter(res.assinaturas) + procurar_assinaturas(textos) + procurar_assinaturas(pedidos, pares=False)
            finalizar(res, sessao, encontrados)
            print(f"  [browser {i}/{len(resultados)}] {res.nome}: {descrever(res)}", flush=True)
        browser.close()


# ---------------------------------------------------------------- relatórios

ICONES = {"plataforma": "✅", "propria": "🏢", "pagina": "📄", "sem_carreiras": "⚠️", "erro": "❌"}
TITULOS = {
    "plataforma": "Plataforma de recrutamento detetada",
    "propria": "Plataforma própria (tratada à parte)",
    "pagina": "Página de carreiras sem plataforma conhecida (vigiada pelo texto no passo 4)",
    "sem_carreiras": "Site responde, mas não encontrei link de carreiras",
    "erro": "Site com erro ou bloqueado",
}


def descrever(res: Resultado) -> str:
    if res.estado == "plataforma":
        extra = f", {res.ofertas} ofertas" if res.ofertas is not None else ", não confirmada"
        return f"{res.plataforma} ({res.plataforma_id or '-'}{extra})"
    if res.estado == "pagina":
        return f"página {res.carreiras}"
    if res.estado == "erro":
        return f"erro ({res.codigo_http or (res.erros[0] if res.erros else '?')})"
    return res.estado


def _celula(valor) -> str:
    return str(valor if valor not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")


def relatorio_markdown(resultados: list[Resultado], duracao: float) -> str:
    contagem = Counter(r.estado for r in resultados)
    linhas = [
        "# Relatório do detetor",
        "",
        f"{datetime.now(LISBOA):%d/%m/%Y %H:%M} · {len(resultados)} empresas · {duracao / 60:.1f} min",
        "",
    ]
    for estado in ICONES:
        linhas.append(f"- {ICONES[estado]} {TITULOS[estado]}: **{contagem.get(estado, 0)}**")
    for estado in ICONES:
        grupo = [r for r in resultados if r.estado == estado]
        if not grupo:
            continue
        linhas += ["", f"## {ICONES[estado]} {TITULOS[estado]} ({len(grupo)})", "",
                   "| Empresa | Prio | Plataforma | ID | Ofertas | Página de carreiras | Notas |",
                   "|---|---|---|---|---|---|---|"]
        for r in sorted(grupo, key=lambda x: (x.prioridade, x.nome.lower())):
            notas = []
            if r.codigo_http and r.codigo_http >= 400:
                notas.append(f"HTTP {r.codigo_http}")
            if r.manual or r.carreiras_manual:
                notas.append("link manual")
            if r.via_browser:
                notas.append("browser")
            if r.erros and r.estado in ("erro", "sem_carreiras"):
                notas.append(r.erros[0][:80])
            linhas.append("| " + " | ".join(_celula(v) for v in (
                r.nome, r.prioridade, r.plataforma, r.plataforma_id, r.ofertas,
                r.carreiras or r.url_final, "; ".join(notas))) + " |")
    return "\n".join(linhas) + "\n"


def _entradas(resultados: list[Resultado]) -> dict:
    dados = {}
    for r in resultados:
        entrada = {"estado": r.estado, "carreiras": r.carreiras}
        if r.plataforma:
            entrada["plataforma"] = {"tipo": r.plataforma, "id": r.plataforma_id}
            entrada["ofertas_na_detecao"] = r.ofertas
        if r.manual or r.carreiras_manual:
            entrada["manual"] = True
        dados[r.nome] = entrada
    return dados


def _yaml(dados: dict) -> str:
    cabecalho = (f"# Gerado pelo detetor em {datetime.now(LISBOA):%d/%m/%Y %H:%M}. Não editar à mão:\n"
                 "# para corrigir uma empresa, escreve carreiras/plataforma em config/empresas.yaml.\n")
    return cabecalho + yaml.safe_dump({"empresas": dados}, allow_unicode=True, sort_keys=False, width=200)


def plataformas_yaml(resultados: list[Resultado]) -> str:
    return _yaml(_entradas(resultados))


def gravar_config(resultados: list[Resultado], todas: list[dict], caminho: Path = FICHEIRO_PLATAFORMAS) -> None:
    """Junta os resultados a config/plataformas.yaml. Numa execução parcial (--apenas) só
    as empresas analisadas são atualizadas; as outras ficam como estavam."""
    dados = carregar_plataformas(caminho)
    dados.update(_entradas(resultados))
    # Mesma ordem que empresas.yaml; empresas apagadas da lista desaparecem.
    ordenado = {e["nome"]: dados[e["nome"]] for e in todas if e["nome"] in dados}
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(_yaml(ordenado), encoding="utf-8")
    print(f"Configuração atualizada: {caminho} ({len(ordenado)} empresas)")


def resumo_telegram(resultados: list[Resultado], duracao: float) -> str:
    contagem = Counter(r.estado for r in resultados)
    confirmadas = sum(1 for r in resultados if r.estado == "plataforma" and r.ofertas is not None)
    linhas = [f"🔎 Detetor terminado: {len(resultados)} empresas em {duracao / 60:.0f} min", ""]
    for estado in ICONES:
        extra = f" ({confirmadas} confirmadas)" if estado == "plataforma" else ""
        linhas.append(f"{ICONES[estado]} {TITULOS[estado].split(' (')[0]}: {contagem.get(estado, 0)}{extra}")
    problemas = [r.nome for r in resultados if r.estado in ("erro", "sem_carreiras")]
    if problemas:
        linhas += ["", "A corrigir: " + ", ".join(problemas)]
    if os.environ.get("GITHUB_RUN_ID"):
        url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
               f"{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
        linhas += ["", f"Relatório completo: {url}"]
    return "\n".join(linhas)


# ---------------------------------------------------------------- programa

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detetor de páginas de carreiras")
    parser.add_argument("--sem-browser", action="store_true", help="não usar o Playwright")
    parser.add_argument("--apenas", default="", help="nomes separados por vírgulas")
    parser.add_argument("--empresas", type=Path, default=FICHEIRO_EMPRESAS)
    parser.add_argument("--saida", type=Path, default=RAIZ / "saida")
    parser.add_argument("--sem-notificar", action="store_true")
    parser.add_argument("--gravar-config", action="store_true",
                        help="atualizar config/plataformas.yaml (o workflow faz depois o commit)")
    args = parser.parse_args(argv)

    empresas = todas = carregar_empresas(args.empresas)
    if args.apenas.strip():
        pedidos = {n.strip().lower() for n in args.apenas.split(",") if n.strip()}
        desconhecidas = pedidos - {e["nome"].lower() for e in empresas}
        if desconhecidas:
            print("Aviso: não estão na lista:", ", ".join(sorted(desconhecidas)))
        empresas = [e for e in empresas if e["nome"].lower() in pedidos]

    inicio = time.monotonic()
    print(f"Fase 1 (HTTP): {len(empresas)} empresas", flush=True)
    resultados: list[Resultado] = []
    with ThreadPoolExecutor(max_workers=TRABALHADORES) as executor:
        for i, res in enumerate(executor.map(analisar_http, empresas), start=1):
            resultados.append(res)
            print(f"  [{i}/{len(empresas)}] {res.nome}: {descrever(res)}", flush=True)

    if not args.sem_browser:
        por_rever = [r for r in resultados if r.estado in ("pagina", "sem_carreiras", "erro")]
        print(f"Fase 2 (browser): {len(por_rever)} empresas sem plataforma detetada", flush=True)
        if por_rever:
            analisar_browser(por_rever)

    duracao = time.monotonic() - inicio
    args.saida.mkdir(parents=True, exist_ok=True)
    md = relatorio_markdown(resultados, duracao)
    (args.saida / "relatorio_detetor.md").write_text(md, encoding="utf-8")
    (args.saida / "plataformas.yaml").write_text(plataformas_yaml(resultados), encoding="utf-8")
    (args.saida / "detetor.json").write_text(
        json.dumps([asdict(r) for r in resultados], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRelatórios escritos em {args.saida}/")
    if args.gravar_config:
        gravar_config(resultados, todas)

    # No GitHub Actions, este ficheiro especial aparece como resumo na página da execução.
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md)

    if not args.sem_notificar:
        try:
            enviar(resumo_telegram(resultados, duracao), assunto="Vigilante: relatório do detetor")
        except RuntimeError as erro:  # o relatório já está guardado; não falhamos por isto
            print(f"Aviso: resumo não enviado ({erro})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
