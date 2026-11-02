"""
leitores.py: um leitor por plataforma de recrutamento.

Todos têm a mesma "assinatura" (os mesmos argumentos e o mesmo tipo de resultado):

    ler_x(sessao, ident, fonte) -> list[Oferta]

  sessao  requests.Session reutilizada nos pedidos
  ident   identificador da empresa na plataforma (ex.: "feedzai" em boards.greenhouse.io/feedzai)
  fonte   a entrada da empresa (nome, carreiras, ...) vinda de config.carregar_fontes()

Assim, main.py pode chamar qualquer leitor sem saber de que plataforma se trata:
    LEITORES["greenhouse"](sessao, "feedzai", fonte)

Os leitores devolvem TODAS as ofertas que conseguem ler; os filtros (filtros.py)
escolhem depois as que interessam.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from vigilante.filtros import e_estagio, normalizar
from vigilante.rede import pedir

JSON = {"Accept": "application/json"}


@dataclass
class Oferta:
    empresa: str
    fonte: str              # plataforma de onde veio (greenhouse, lever, ...)
    id: str                 # identificador da oferta dentro da plataforma
    titulo: str
    url: str
    local: str = ""
    departamento: str = ""
    tipo_contrato: str = ""  # ex.: "Intern" (Lever, Ashby) ou "Internship" (Workable)
    publicada: str = ""      # data em texto, quando a plataforma a dá
    # Preenchidos pelos filtros em main.py:
    zona: str = ""           # lisboa, portugal, desconhecido, remoto (ver filtros.classificar_local)
    informatica: bool = False
    prioridade: int = 3

    @property
    def chave(self) -> str:
        """Identifica a oferta de forma única no estado (data/estado.json)."""
        return f"{self.fonte}:{self.empresa}:{self.id}"


class NaoSuportado(Exception):
    """Plataforma sem leitor (fica para o passo 4, vigilância da página)."""


def _juntar(*partes) -> str:
    """Junta as partes não vazias com ", " (ex.: cidade, região, país)."""
    return ", ".join(str(p) for p in partes if p)


def _texto(valor) -> str:
    """Algumas APIs devolvem {"label": "..."} em vez de texto simples."""
    if isinstance(valor, dict):
        return str(valor.get("label") or valor.get("name") or "")
    return str(valor or "")


# ---------------------------------------------------------------- Greenhouse

def _primeiro_que_existe(sessao, urls: list[str], **kw):
    """Pede cada endereço por ordem e devolve a primeira resposta que não seja 404."""
    for url in urls:
        try:
            return pedir(sessao, "GET", url, **kw)
        except requests.HTTPError as erro:
            if erro.response is None or erro.response.status_code != 404 or url == urls[-1]:
                raise
    raise RuntimeError("lista de endereços vazia")


def ler_greenhouse(sessao, ident, fonte):
    # O Greenhouse também tem um servidor europeu (boards-api.eu.greenhouse.io).
    dados = _primeiro_que_existe(sessao, [f"https://boards-api.greenhouse.io/v1/boards/{ident}/jobs",
                                          f"https://boards-api.eu.greenhouse.io/v1/boards/{ident}/jobs"],
                                 headers=JSON).json()
    return [
        Oferta(fonte["nome"], "greenhouse", str(j["id"]), j.get("title", ""), j.get("absolute_url", ""),
               local=(j.get("location") or {}).get("name", ""),
               publicada=j.get("first_published") or j.get("updated_at") or "")
        for j in dados.get("jobs", [])
    ]


# ---------------------------------------------------------------- Lever

def ler_lever(sessao, ident, fonte):
    # A Lever tem dois servidores: o global e o europeu. Tentamos os dois.
    for dominio in ("api.lever.co", "api.eu.lever.co"):
        try:
            dados = pedir(sessao, "GET", f"https://{dominio}/v0/postings/{ident}",
                          params={"mode": "json"}, headers=JSON).json()
            break
        except requests.HTTPError as erro:
            if erro.response is None or erro.response.status_code != 404:
                raise
    else:  # o "else" de um for corre quando o ciclo acaba sem "break"
        raise RuntimeError(f"Lever: conta '{ident}' não encontrada")

    ofertas = []
    for p in dados:
        cat = p.get("categories") or {}
        locais = cat.get("allLocations") or [cat.get("location", "")]
        criada = p.get("createdAt")  # milissegundos desde 1970
        ofertas.append(Oferta(
            fonte["nome"], "lever", p["id"], p.get("text", ""), p.get("hostedUrl", ""),
            local=" · ".join(l for l in locais if l) + (" (remote)" if p.get("workplaceType") == "remote" else ""),
            departamento=cat.get("team") or cat.get("department") or "",
            tipo_contrato=cat.get("commitment") or "",
            publicada=datetime.fromtimestamp(criada / 1000, timezone.utc).date().isoformat() if criada else "",
        ))
    return ofertas


# ---------------------------------------------------------------- Workable

def ler_workable(sessao, ident, fonte):
    dados = pedir(sessao, "GET", f"https://apply.workable.com/api/v1/widget/accounts/{ident}", headers=JSON).json()
    ofertas = []
    for j in dados.get("jobs", []):
        locais = [_juntar(l.get("city"), l.get("region"), l.get("country")) for l in (j.get("locations") or [])]
        local = " · ".join(l for l in locais if l) or _juntar(j.get("city"), j.get("state"), j.get("country"))
        if j.get("telecommuting"):
            local += " (remote)"
        codigo = j.get("shortcode") or j.get("code") or j.get("url") or j.get("title")
        ofertas.append(Oferta(
            fonte["nome"], "workable", str(codigo), j.get("title", ""),
            j.get("url") or j.get("shortlink") or f"https://apply.workable.com/{ident}/j/{codigo}/",
            local=local, departamento=j.get("department") or "",
            tipo_contrato=j.get("employment_type") or "",
            publicada=j.get("published_on") or j.get("created_at") or "",
        ))
    return ofertas


# ---------------------------------------------------------------- SmartRecruiters

def ler_smartrecruiters(sessao, ident, fonte):
    url = f"https://api.smartrecruiters.com/v1/companies/{ident}/postings"
    ofertas, offset = [], 0
    for _ in range(20):  # no máximo 20 páginas de 100 (empresas enormes como a Bosch)
        dados = pedir(sessao, "GET", url, params={"limit": 100, "offset": offset, "country": "pt"},
                      headers=JSON).json()
        conteudo = dados.get("content") or []
        for p in conteudo:
            loc = p.get("location") or {}
            ofertas.append(Oferta(
                fonte["nome"], "smartrecruiters", str(p["id"]), p.get("name", ""),
                f"https://jobs.smartrecruiters.com/{ident}/{p['id']}",
                local=_juntar(loc.get("city"), loc.get("region"), loc.get("country")) + (" (remote)" if loc.get("remote") else ""),
                departamento=_texto(p.get("department")),
                tipo_contrato=_texto(p.get("typeOfEmployment")),
                publicada=p.get("releasedDate") or "",
            ))
        offset += len(conteudo)
        if not conteudo or offset >= int(dados.get("totalFound") or 0):
            break
    return ofertas


# ---------------------------------------------------------------- Workday

# Sem filtro de país, pesquisamos estes termos no mundo inteiro (plano B).
PESQUISAS_WORKDAY = ["intern", "internship", "estágio", "trainee", "graduate", "summer", "student"]
MAX_DETALHES_WORKDAY = 30
MAX_OFERTAS_WORKDAY = 600


def _folhas_facetas(no, parametro=""):
    """Percorre as "facetas" (filtros) do Workday, que vêm em árvore, e devolve as folhas
    como (parâmetro, descrição, id). Ex.: ("locationCountry", "Portugal", "a1b2...")."""
    if isinstance(no, list):
        for filho in no:
            yield from _folhas_facetas(filho, parametro)
    elif isinstance(no, dict):
        param = no.get("facetParameter") or parametro
        if isinstance(no.get("values"), list):
            yield from _folhas_facetas(no["values"], param)
        elif no.get("id"):
            yield param, str(no.get("descriptor") or ""), str(no["id"])


def filtro_portugal_workday(facetas) -> dict:
    """Escolhe o filtro "país = Portugal" ou, se não houver, "local = Lisboa/Portugal"."""
    folhas = list(_folhas_facetas(facetas))
    for param, descricao, ident in folhas:
        if "country" in param.lower() and normalizar(descricao).strip() == "portugal":
            return {param: [ident]}
    locais: dict[str, list[str]] = {}
    for param, descricao, ident in folhas:
        d = normalizar(descricao)
        if "location" in param.lower() and ("portugal" in d or "lisbo" in d or "lisboa" in d):
            locais.setdefault(param, []).append(ident)
    return locais


def ler_workday(sessao, ident, fonte):
    # ident = "inquilino.wdN/site", ex.: "nokia.wd3/careers"
    inquilino_servidor, site = ident.split("/", 1)
    inquilino = inquilino_servidor.split(".")[0]
    base = f"https://{inquilino_servidor}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{inquilino}/{site}"

    def pagina(filtros, termo, offset):
        return pedir(sessao, "POST", f"{api}/jobs", headers=JSON, json={
            "appliedFacets": filtros, "limit": 20, "offset": offset, "searchText": termo}).json()

    vistas: dict[str, Oferta] = {}

    def guardar(postings):
        for p in postings:
            caminho = p.get("externalPath")
            if caminho and caminho not in vistas:
                vistas[caminho] = Oferta(fonte["nome"], "workday", caminho, p.get("title", ""),
                                         f"{base}/{site}{caminho}", local=p.get("locationsText", ""))

    # 1.º pedido: sem pesquisa, só para ler os filtros disponíveis (vêm na resposta).
    primeira = pagina({}, "", 0)
    filtros = filtro_portugal_workday(primeira.get("facets"))
    if filtros:
        # Plano A: todas as ofertas em Portugal (normalmente algumas centenas no máximo).
        for offset in range(0, MAX_OFERTAS_WORKDAY, 20):
            dados = pagina(filtros, "", offset)
            postings = dados.get("jobPostings") or []
            guardar(postings)
            if len(postings) < 20 or offset + 20 >= int(dados.get("total") or 0):
                break
    else:
        # Plano B: pesquisa por termos no mundo inteiro; o filtro de local trata do resto.
        for termo in PESQUISAS_WORKDAY:
            for offset in range(0, 100, 20):
                dados = pagina({}, termo, offset)
                postings = dados.get("jobPostings") or []
                guardar(postings)
                if len(postings) < 20 or offset + 20 >= int(dados.get("total") or 0):
                    break

    # "3 Locations": para os títulos que parecem estágio, abrimos o detalhe para ver os locais reais.
    detalhes = 0
    for oferta in vistas.values():
        if detalhes >= MAX_DETALHES_WORKDAY:
            break
        if re.fullmatch(r"\d+\s+locations?", normalizar(oferta.local).strip()) and e_estagio(oferta.titulo):
            detalhes += 1
            try:
                info = pedir(sessao, "GET", f"{api}{oferta.id}", headers=JSON).json().get("jobPostingInfo", {})
                locais = [info.get("location", "")] + list(info.get("additionalLocations") or [])
                oferta.local = " · ".join(l for l in locais if l) or oferta.local
                oferta.publicada = info.get("startDate", "") or oferta.publicada
            except (requests.RequestException, ValueError):
                pass  # fica com "3 Locations"; o filtro trata como local não indicado
    return list(vistas.values())


# ---------------------------------------------------------------- Recruitee

def ler_recruitee(sessao, ident, fonte):
    dados = pedir(sessao, "GET", f"https://{ident}.recruitee.com/api/offers/", headers=JSON).json()
    return [
        Oferta(fonte["nome"], "recruitee", str(o["id"]), o.get("title", ""),
               o.get("careers_url") or f"https://{ident}.recruitee.com/o/{o.get('slug', '')}",
               local=o.get("location") or _juntar(o.get("city"), o.get("country")),
               departamento=o.get("department") or "",
               tipo_contrato=o.get("employment_type_code") or "",
               publicada=o.get("published_at") or o.get("created_at") or "")
        for o in dados.get("offers", [])
    ]


# ---------------------------------------------------------------- Personio (XML)

def ler_personio(sessao, ident, fonte):
    resposta = _primeiro_que_existe(sessao, [f"https://{ident}.jobs.personio.de/xml",
                                             f"https://{ident}.jobs.personio.com/xml"])
    dominio = urlparse(resposta.url).netloc.rsplit(".", 1)[-1] or "de"
    raiz = ElementTree.fromstring(resposta.content)
    ofertas = []
    for pos in raiz.iter("position"):
        pid = pos.findtext("id") or ""
        escritorios = [pos.findtext("office") or ""] + [o.text or "" for o in pos.iter("additionalOffice")]
        ofertas.append(Oferta(
            fonte["nome"], "personio", pid, pos.findtext("name") or "",
            f"https://{ident}.jobs.personio.{dominio}/job/{pid}",
            local=" · ".join(e for e in escritorios if e),
            departamento=pos.findtext("department") or "",
            tipo_contrato=pos.findtext("employmentType") or "",
            publicada=pos.findtext("createdAt") or "",
        ))
    return ofertas


# ---------------------------------------------------------------- Ashby

def ler_ashby(sessao, ident, fonte):
    dados = pedir(sessao, "GET", f"https://api.ashbyhq.com/posting-api/job-board/{ident}", headers=JSON).json()
    ofertas = []
    for j in dados.get("jobs", []):
        locais = [j.get("location", "")] + [s.get("location", "") for s in (j.get("secondaryLocations") or [])]
        if j.get("isRemote"):
            locais.append("remote")
        ofertas.append(Oferta(
            fonte["nome"], "ashby", str(j.get("id") or j.get("jobUrl")), j.get("title", ""),
            j.get("jobUrl") or j.get("applyUrl") or "",
            local=" · ".join(l for l in locais if l),
            departamento=j.get("department") or j.get("team") or "",
            tipo_contrato=j.get("employmentType") or "",
            publicada=j.get("publishedAt") or "",
        ))
    return ofertas


# ---------------------------------------------------------------- Teamtailor (RSS)

def ler_teamtailor(sessao, ident, fonte):
    resposta = pedir(sessao, "GET", f"https://{ident}.teamtailor.com/jobs.rss")
    raiz = ElementTree.fromstring(resposta.content)
    ofertas = []
    for item in raiz.iter("item"):
        link = item.findtext("link") or ""
        # As etiquetas próprias da Teamtailor vêm com prefixo (ex.: {..}city): olhamos só para o fim.
        locais = [el.text for el in item.iter() if el.tag.split("}")[-1] in ("city", "country") and el.text]
        ofertas.append(Oferta(fonte["nome"], "teamtailor", item.findtext("guid") or link,
                              item.findtext("title") or "", link, local=", ".join(locais),
                              publicada=item.findtext("pubDate") or ""))
    return ofertas


# ---------------------------------------------------------------- SuccessFactors (sites "RMK")

# Os sites de carreiras SuccessFactors "RMK" (ex.: recrutamento.tap.pt, careers.capgemini.com)
# têm uma página de pesquisa em HTML: /search/?q=TERMO&locationsearch=LOCAL&startrow=N
PESQUISAS_RMK = ["intern", "internship", "estágio", "trainee", "summer", "graduate"]
LOCAIS_RMK = ["Portugal", "Lisboa", "Lisbon"]
POR_PAGINA_RMK = 25


def extrair_rmk(html: str, base: str, local_pesquisado: str = "") -> list[Oferta]:
    """Lê as linhas de resultados de uma página RMK. Devolve ofertas com empresa vazia."""
    sopa = BeautifulSoup(html, "html.parser")
    resultados = []
    linhas = sopa.select("tr.data-row") or sopa.select("li.job-tile, div.job-tile")
    if linhas:
        for linha in linhas:
            a = linha.select_one("a.jobTitle-link") or linha.find("a", href=re.compile(r"/job/"))
            if not a:
                continue
            local_el = linha.select_one(".jobLocation")
            data_el = linha.select_one(".jobDate")
            resultados.append((a, local_el.get_text(" ", strip=True) if local_el else "",
                               data_el.get_text(" ", strip=True) if data_el else ""))
    else:  # página com outro desenho: qualquer link do tipo /job/<titulo>/<numero>/
        resultados = [(a, "", "") for a in sopa.find_all("a", href=re.compile(r"/job/[^/]+/\d+/?$"))]

    ofertas, vistos = [], set()
    for a, local, data in resultados:
        url = urljoin(base, a["href"])
        numero = re.search(r"/(\d+)/?$", urlparse(url).path)
        ident = numero.group(1) if numero else url
        if ident in vistos:
            continue  # o RMK repete cada link (versão para computador e para telemóvel)
        vistos.add(ident)
        ofertas.append(Oferta("", "successfactors", ident, a.get_text(" ", strip=True), url,
                              local=" ".join(local.split()) or local_pesquisado, publicada=data))
    return ofertas


# Vestígios que só existem em páginas de sites RMK (para não confundir com o site principal).
MARCAS_RMK = ("rmkcdn.successfactors.com", "jobTitle-link", "searchResultsShell", 'class="data-row',
              "createNewAlert", "jobLocation")


def e_site_rmk(html: str) -> bool:
    return any(marca in html for marca in MARCAS_RMK)


def base_rmk(ident: str, carreiras: str) -> str | None:
    """Endereço base do site RMK. `ident` pode ser "jobs.galp.com" ou "careers.ey.com/ey"
    (alguns sites têm um prefixo antes de /search/ e /job/)."""
    c = urlparse(carreiras or "")
    host = (ident or c.netloc).split("/")[0]
    if not host or "successfactors." in host:
        return None
    prefixo = ident.split("/", 1)[1] if "/" in ident else ""
    if not prefixo and c.netloc == host:
        m = re.match(r"(.*?)/(?:search|go|job)/", c.path)
        prefixo = m.group(1).strip("/") if m else ""
    return f"https://{host}" + (f"/{prefixo}" if prefixo else "")


def ler_successfactors(sessao, ident, fonte):
    carreiras = fonte.get("carreiras") or ""
    base = base_rmk(ident, carreiras)
    if base is None:
        raise NaoSuportado("SuccessFactors clássico (career*.successfactors.eu): vigiado como página")

    # (endereço da 1.ª página, local pesquisado, função que dá o endereço da página seguinte)
    pesquisas = []
    if "/go/" in carreiras:  # página de categoria (ex.: TAP "Internships & Trainee")
        pesquisas.append((carreiras, "", lambda n, u=carreiras: u.rstrip("/") + f"/{n}/"))
    for termo in PESQUISAS_RMK:
        for local in LOCAIS_RMK:
            u = f"{base}/search/?q={quote(termo)}&locationsearch={quote(local)}"
            pesquisas.append((u, local, lambda n, u=u: f"{u}&startrow={n}"))

    # Antes de tudo, confirmar que é mesmo um site RMK. Se não for (ex.: o detetor viu
    # vestígios do SuccessFactors no site principal da empresa), a empresa passa para a
    # vigia de páginas em vez de dar erro todos os dias.
    try:
        primeira = pedir(sessao, "GET", pesquisas[0][0]).text
    except requests.HTTPError as erro:
        if erro.response is not None and erro.response.status_code in (403, 404, 410):
            raise NaoSuportado(f"{base} não tem pesquisa SuccessFactors: vigiado como página") from erro
        raise
    if not e_site_rmk(primeira):
        raise NaoSuportado(f"{base} não parece um site SuccessFactors: vigiado como página")

    ofertas: dict[str, Oferta] = {}
    for n_pesquisa, (url, local, seguinte) in enumerate(pesquisas):
        for pagina in range(3):  # até 3 páginas de 25
            endereco = url if pagina == 0 else seguinte(pagina * POR_PAGINA_RMK)
            html = primeira if (n_pesquisa == 0 and pagina == 0) else pedir(sessao, "GET", endereco).text
            encontradas = extrair_rmk(html, base, local)
            for oferta in encontradas:
                oferta.empresa = fonte["nome"]
                ofertas.setdefault(oferta.id, oferta)
            if len(encontradas) < POR_PAGINA_RMK:
                break
    return list(ofertas.values())


# ---------------------------------------------------------------- Amazon

def ler_amazon(sessao, ident, fonte):
    ofertas = []
    for offset in range(0, 500, 100):
        dados = pedir(sessao, "GET", "https://www.amazon.jobs/en/search.json", headers=JSON, params={
            "base_query": "", "country": "PRT", "result_limit": 100, "offset": offset}).json()
        trabalhos = dados.get("jobs") or []
        for j in trabalhos:
            ofertas.append(Oferta(
                fonte["nome"], "amazon", str(j.get("id_icims") or j.get("id") or j.get("job_path")),
                j.get("title", ""), "https://www.amazon.jobs" + (j.get("job_path") or ""),
                local=j.get("normalized_location") or j.get("location") or "",
                departamento=j.get("job_category") or "", publicada=j.get("posted_date") or ""))
        if len(trabalhos) < 100:
            break
    return ofertas


# ---------------------------------------------------------------- Breezy HR

def ler_breezy(sessao, ident, fonte):
    dados = pedir(sessao, "GET", f"https://{ident}.breezy.hr/json", headers=JSON).json()
    ofertas = []
    for p in dados or []:
        loc = p.get("location") or {}
        local = loc.get("name") or _juntar(loc.get("city"), _texto(loc.get("country")))
        if loc.get("is_remote"):
            local += " (remote)"
        codigo = p.get("friendly_id") or p.get("id")
        ofertas.append(Oferta(
            fonte["nome"], "breezy", str(p.get("id") or codigo), p.get("name", ""),
            p.get("url") or f"https://{ident}.breezy.hr/p/{codigo}",
            local=local, departamento=_texto(p.get("department")),
            tipo_contrato=_texto(p.get("type")), publicada=p.get("published_date") or ""))
    return ofertas


# ---------------------------------------------------------------- ITJobs.pt (agregador)

# Na API do ITJobs, "contract" 3 = Estágio profissional e 5 = Estágio académico.
CONTRATOS_ESTAGIO_ITJOBS = "3,5"


def ler_itjobs(sessao, chave_api, fonte):
    """Estágios de informática publicados no ITJobs.pt. `chave_api` vem do segredo ITJOBS_API_KEY.
    Aqui a "empresa" de cada oferta é a empresa que publicou (não "ITJobs")."""
    ofertas = []
    for pagina in range(1, 6):
        dados = pedir(sessao, "GET", "https://api.itjobs.pt/job/list.json", headers=JSON, params={
            "api_key": chave_api, "contract": CONTRATOS_ESTAGIO_ITJOBS, "limit": 100, "page": pagina}).json()
        resultados = dados.get("results") or []
        for j in resultados:
            empresa = (j.get("company") or {}).get("name") or "?"
            ofertas.append(Oferta(
                f"{empresa} (via ITJobs)", "itjobs", str(j["id"]), j.get("title", ""),
                f"https://www.itjobs.pt/oferta/{j['id']}/{j.get('slug', '')}",
                local=" · ".join(l.get("name", "") for l in (j.get("locations") or [])),
                tipo_contrato="estágio", publicada=j.get("publishedAt") or ""))
        if len(resultados) < 100:
            break
    return ofertas


LEITORES = {
    "breezy": ler_breezy,
    "greenhouse": ler_greenhouse,
    "lever": ler_lever,
    "workable": ler_workable,
    "smartrecruiters": ler_smartrecruiters,
    "workday": ler_workday,
    "recruitee": ler_recruitee,
    "personio": ler_personio,
    "ashby": ler_ashby,
    "teamtailor": ler_teamtailor,
    "successfactors": ler_successfactors,
    "amazon": ler_amazon,
}
