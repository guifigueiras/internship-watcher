"""
paginas.py: vigia de páginas de carreiras (para as empresas sem leitor de plataforma).

Ideia: abrir a página num browser a sério (Playwright, para o JavaScript correr), guardar
os pequenos textos que falam de estágios ("Summer Internship 2027", "Programa de Estágios
de Verão"...) e avisar quando aparece um texto que nunca tinha sido visto.

Em cada empresa:
  1. abre a página de carreiras (ou o site, se não houver página de carreiras);
  2. abre até 2 sub-páginas promissoras ("Estudantes", "Estágios", "Vagas"...);
  3. de todas, extrai os textos curtos que passam em filtros.e_programa().

Cada texto vira um ItemPagina, com a mesma "forma" de uma Oferta (empresa, titulo, url,
chave), para a memória (estado.py) o tratar da mesma maneira.

Respeitamos o robots.txt: se o site pedir aos robôs para não abrirem uma página, não abrimos.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from vigilante.filtros import e_informatica, e_programa, local_estrangeiro, normalizar
from vigilante.rede import UA

MIN_CHARS, MAX_CHARS = 6, 140      # textos mais curtos ou mais longos (parágrafos) são ignorados
MAX_SUBPAGINAS = 2
CONCORRENCIA = 4                    # páginas abertas ao mesmo tempo
TEMPO_CARREGAR = 30_000             # ms para a página abrir
TEMPO_ACALMAR = 8_000               # ms à espera que o JavaScript termine
DOMINIOS_IGNORADOS = ("linkedin.", "facebook.", "instagram.", "twitter.", "x.com", "youtube.",
                      "tiktok.", "glassdoor.", "indeed.", "whatsapp.", "t.me", "google.com/maps")

# Palavras que tornam um link digno de ser seguido, com pontos.
SEGUIR = [
    (re.compile(r"\b(?:estagi\w*|intern\w*|trainee\w*|summer|verao)\b"), 3),
    (re.compile(r"\b(?:estudantes?|students?|graduates?|recem[- ]licenciad\w*|early[- ]careers?|"
                r"universit\w*|jovens?|young|talento jovem|start your career|inicia\w* (?:a tua )?carreira)\b"), 2),
    (re.compile(r"\b(?:vagas|oportunidades|ofertas|open positions|job openings|vacancies|jobs|"
                r"see all jobs|ver todas)\b"), 1),
    (re.compile(r"\b(?:carreiras?|careers?|recrutamento|recursos humanos|junte-se|junta-te|"
                r"trabalh\w* (?:no|na|nos|connosco)|join us|work with us)\b"), 1),
]
OFERTA_INDIVIDUAL = re.compile(r"/(?:job|jobs|oferta|ofertas|vaga|posting|position|p)/[^/]*\d{3,}", re.I)
# Ex.: "há 3 dias", "2 days ago", "Posted 5 hours ago": mudam todos os dias e não interessam.
TEMPO_RELATIVO = re.compile(r"\b(?:ha|posted|publicad\w*|ha cerca de)?\s*\d+\s*(?:dias?|days?|horas?|hours?|"
                            r"semanas?|weeks?|meses|months?|minutos?|minutes?)\s*(?:ago|atras)?\b")


@dataclass
class ItemPagina:
    empresa: str
    titulo: str          # o texto encontrado na página
    url: str             # link do texto (ou a própria página)
    pagina: str          # página onde foi encontrado
    fonte: str = "pagina"
    local: str = ""
    zona: str = ""
    informatica: bool = False
    prioridade: int = 3

    @property
    def chave(self) -> str:
        texto = TEMPO_RELATIVO.sub(" ", normalizar(self.titulo))
        texto = " ".join(re.sub(r"[^\w\s]", " ", texto).split())
        return f"{self.fonte}:{self.empresa}:{hashlib.sha1(texto.encode()).hexdigest()[:16]}"


@dataclass
class Pagina:
    url: str
    final: str = ""
    estado: int | None = None
    html: str = ""
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return self.erro is None and self.estado is not None and self.estado < 400 and bool(self.html)


@dataclass
class Vigia:
    fonte: dict
    itens: list = field(default_factory=list)
    visitadas: list = field(default_factory=list)
    erro: str | None = None
    ignorada: str | None = None   # ex.: robots.txt não permite

    @property
    def nome(self) -> str:
        return self.fonte["nome"]


# ---------------------------------------------------------------- extração (funções puras)

def _limpar(texto: str) -> str:
    return " ".join(texto.split())


def extrair_itens(html: str, url: str, empresa: str, padrao_link: str | None = None,
                  prioridade: int = 3) -> list[ItemPagina]:
    """Textos curtos sobre estágios numa página. Se `padrao_link` for dado (agregadores),
    também conta qualquer link cujo endereço tenha esse padrão, seja qual for o texto."""
    sopa = BeautifulSoup(html, "html.parser")
    for lixo in sopa(["script", "style", "noscript", "template", "svg"]):
        lixo.decompose()
    padrao = re.compile(padrao_link) if padrao_link else None

    itens: dict[str, ItemPagina] = {}

    def juntar(texto, href):
        texto = _limpar(texto)
        if not (MIN_CHARS <= len(texto) <= MAX_CHARS) or local_estrangeiro(texto):
            return
        item = ItemPagina(empresa, texto, href or url, url, prioridade=prioridade,
                          informatica=e_informatica(texto))
        itens.setdefault(item.chave, item)

    # 1) links: o texto completo do link (um cartão de oferta costuma ser um link)
    for a in sopa.find_all("a", href=True):
        href = urljoin(url, a["href"])
        texto = a.get_text(" ", strip=True) or a.get("title") or a.get("aria-label") or ""
        if e_programa(texto) or (padrao and padrao.search(href)):
            juntar(texto or urlparse(href).path.strip("/").split("/")[-1].replace("-", " "), href)

    # 2) blocos de texto "folha" (títulos, parágrafos, itens de lista, células) que não têm
    #    outros blocos lá dentro. Assim "<h3>Summer <b>Internship</b></h3>" dá um só texto.
    for bloco in sopa.find_all(BLOCOS):
        if bloco.find(BLOCOS):
            continue
        texto = bloco.get_text(" ", strip=True)
        if e_programa(texto):
            link = bloco.find("a", href=True) or bloco.find_parent("a", href=True)
            juntar(texto, urljoin(url, link["href"]) if link else None)
    return sem_repetidos(list(itens.values()))


def sem_repetidos(itens: list[ItemPagina]) -> list[ItemPagina]:
    """Um cartão costuma dar dois textos: o título ("Accenture - Summer Internship 2026") e o
    cartão inteiro (título + descrição). Fica só o mais curto, que está contido no outro."""
    normais = [(" ".join(normalizar(i.titulo).split()), i) for i in itens]
    curtos = sorted((n for n, _ in normais if len(n) >= 12), key=len)
    resultado = []
    for n, item in normais:
        if any(c != n and len(c) < len(n) and c in n for c in curtos):
            continue
        resultado.append(item)
    return resultado


BLOCOS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "dt", "dd", "label", "button",
          "figcaption", "caption", "summary", "div", "section", "article", "header", "footer", "blockquote"]


def links_a_seguir(html: str, url: str, limite: int = MAX_SUBPAGINAS) -> list[str]:
    """As sub-páginas mais promissoras (ex.: "Estudantes", "Estágios", "Vagas")."""
    sopa = BeautifulSoup(html, "html.parser")
    atual = url.split("#")[0].rstrip("/")
    pontuados: dict[str, int] = {}
    for a in sopa.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0]
        p = urlparse(href)
        if p.scheme not in ("http", "https") or href.rstrip("/") == atual:
            continue
        if any(d in href.lower() for d in DOMINIOS_IGNORADOS) or p.path.lower().endswith((".pdf", ".jpg", ".png", ".zip")):
            continue
        if OFERTA_INDIVIDUAL.search(p.path) or re.search(r"\d{6,}", p.path):
            continue  # página de uma só oferta: o título já foi apanhado como item
        texto = normalizar(a.get_text(" ", strip=True) + " " + p.path.replace("-", " ").replace("/", " "))
        pontos = sum(peso for regex, peso in SEGUIR if regex.search(texto))
        if pontos:
            pontuados[href] = max(pontos, pontuados.get(href, 0))
    melhores = sorted(pontuados.items(), key=lambda kv: (-kv[1], kv[0]))
    return [u for u, _ in melhores[:limite]]


# ---------------------------------------------------------------- robots.txt

class Robots:
    """Lê o robots.txt de cada site uma vez por execução."""

    def __init__(self):
        self._cache: dict[str, RobotFileParser | None] = {}

    def permite(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._cache:
            regras = None
            try:
                r = requests.get(f"{base}/robots.txt", headers={"User-Agent": UA}, timeout=10)
                if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                    regras = RobotFileParser()
                    regras.parse(r.text.splitlines())
            except requests.RequestException:
                pass  # sem robots.txt acessível: pode-se abrir
            self._cache[base] = regras
        regras = self._cache[base]
        return regras is None or regras.can_fetch("*", url)


# ---------------------------------------------------------------- browser

async def _renderizar(urls: list[str]) -> dict[str, Pagina]:
    from playwright.async_api import async_playwright

    resultados: dict[str, Pagina] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        limite = asyncio.Semaphore(CONCORRENCIA)

        async def uma(url):
            async with limite:
                contexto = await browser.new_context(user_agent=UA, locale="pt-PT")
                # Não descarregar imagens, vídeos e tipos de letra: mais rápido.
                await contexto.route("**/*", lambda rota: rota.abort()
                                     if rota.request.resource_type in ("image", "media", "font")
                                     else rota.continue_())
                pagina = await contexto.new_page()
                res = Pagina(url)
                try:
                    resposta = await pagina.goto(url, wait_until="domcontentloaded", timeout=TEMPO_CARREGAR)
                    try:
                        await pagina.wait_for_load_state("networkidle", timeout=TEMPO_ACALMAR)
                    except Exception:
                        pass
                    res.estado = resposta.status if resposta else None
                    res.final, res.html = pagina.url, await pagina.content()
                except Exception as erro:
                    res.erro = f"{type(erro).__name__}: {str(erro).splitlines()[0][:150]}"
                finally:
                    await contexto.close()
                resultados[url] = res

        await asyncio.gather(*(uma(u) for u in dict.fromkeys(urls)))
        await browser.close()
    return resultados


def renderizar(urls: list[str]) -> dict[str, Pagina]:
    """Abre todas as páginas (várias ao mesmo tempo). Os testes substituem esta função."""
    if not urls:
        return {}
    return asyncio.run(_renderizar(urls))


# ---------------------------------------------------------------- sitemap (agregadores)

def _xml_locs(xml: bytes) -> list[str]:
    from xml.etree import ElementTree
    raiz = ElementTree.fromstring(xml)
    return [el.text.strip() for el in raiz.iter() if el.tag.split("}")[-1] == "loc" and el.text]


def ler_sitemap(sessao, fonte: dict) -> list[ItemPagina]:
    """Lê o índice do site (sitemap) e devolve as páginas cujo endereço bate com `filtro`.
    Cada página nova de um agregador (ex.: um programa de estágio novo no Talent Portugal)
    é um endereço novo no sitemap."""
    from vigilante.rede import pedir
    filtro = re.compile(fonte["filtro"])
    raiz = pedir(sessao, "GET", fonte["url"]).content
    locs = _xml_locs(raiz)
    # Um "índice de sitemaps" aponta para outros sitemaps: lemos só os que batem com o filtro.
    filhos = [l for l in locs if l.endswith(".xml")]
    paginas = [l for l in locs if not l.endswith(".xml")]
    # Os nomes dos sitemaps às vezes usam "_" (ex.: estagio_de_verao-sitemap.xml). Se nenhum
    # bater com o filtro, lemos todos (no máximo 40) e filtramos os endereços.
    escolhidos = [f for f in filhos if filtro.search(f.replace("_", "-"))] or filhos[:40]
    for filho in escolhidos:
        paginas += [l for l in _xml_locs(pedir(sessao, "GET", filho).content) if not l.endswith(".xml")]
    itens = []
    for url in dict.fromkeys(paginas):
        if not filtro.search(url):
            continue
        titulo = urlparse(url).path.rstrip("/").split("/")[-1].replace("-", " ")
        item = ItemPagina(fonte["nome"], titulo, url, fonte["url"], fonte="sitemap",
                          informatica=e_informatica(titulo), prioridade=fonte.get("prioridade", 3))
        itens.append(item)
    return itens


# ---------------------------------------------------------------- vigia

def vigiar(fontes: list[dict], robots: Robots | None = None) -> list[Vigia]:
    """Vigia a página de cada fonte. Cada fonte é uma empresa (ou agregador) com
    "carreiras" ou "site"/"url", e opcionalmente "padrao_link" e "seguir_links"."""
    robots = robots or Robots()
    vigias = [Vigia(f) for f in fontes]
    inicio: dict[str, Vigia] = {}
    for v in vigias:
        url = v.fonte.get("carreiras") or v.fonte.get("url") or v.fonte.get("site")
        if not url:
            v.ignorada = "sem endereço"
        elif not robots.permite(url):
            v.ignorada = "o robots.txt do site não permite robôs nesta página"
        else:
            inicio[url] = v

    # Ronda 1: páginas principais
    paginas = renderizar(list(inicio))
    segunda: dict[str, Vigia] = {}
    for url, v in inicio.items():
        pag = paginas.get(url) or Pagina(url, erro="não aberta")
        v.visitadas.append(url)
        if not pag.ok:
            v.erro = pag.erro or f"HTTP {pag.estado}"
            continue
        v.itens += extrair_itens(pag.html, pag.final or url, v.nome, v.fonte.get("padrao_link"),
                                 v.fonte.get("prioridade", 3))
        if v.fonte.get("seguir_links", True):
            for sub in links_a_seguir(pag.html, pag.final or url):
                if sub not in inicio and sub not in segunda and robots.permite(sub):
                    segunda[sub] = v

    # Ronda 2: sub-páginas (um erro aqui não conta como falha da empresa)
    paginas = renderizar(list(segunda))
    for url, v in segunda.items():
        pag = paginas.get(url)
        v.visitadas.append(url)
        if pag and pag.ok:
            v.itens += extrair_itens(pag.html, pag.final or url, v.nome, v.fonte.get("padrao_link"),
                                     v.fonte.get("prioridade", 3))

    for v in vigias:  # sem repetidos (o mesmo texto em várias páginas da empresa)
        v.itens = list({i.chave: i for i in v.itens}.values())
    return vigias
