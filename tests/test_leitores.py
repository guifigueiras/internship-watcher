"""Cada leitor é testado com uma resposta inventada no formato da API verdadeira."""
import pytest

from vigilante import leitores as L
from vigilante.rede import pedir
from tests.conftest import SessaoFalsa, resposta

FONTE = {"nome": "Empresa", "carreiras": None}


def test_pedir_tenta_de_novo_em_429():
    respostas = iter([resposta(429, headers={"Retry-After": "1"}), resposta(200, json={"ok": True})])
    sessao = SessaoFalsa(lambda *a: next(respostas))
    assert pedir(sessao, "GET", "https://x.test/api").json() == {"ok": True}
    assert len(sessao.pedidos) == 2


def test_pedir_nao_insiste_em_404():
    sessao = SessaoFalsa(lambda *a: resposta(404))
    with pytest.raises(Exception):
        pedir(sessao, "GET", "https://x.test/api")
    assert len(sessao.pedidos) == 1


def test_greenhouse():
    def rotas(m, url, q, corpo):
        if url == "https://boards-api.greenhouse.io/v1/boards/feedzai/jobs":
            return resposta(json={"jobs": [
                {"id": 1, "title": "Software Engineer Intern", "absolute_url": "https://boards.greenhouse.io/feedzai/jobs/1",
                 "location": {"name": "Lisbon, Portugal"}, "first_published": "2026-10-01T10:00:00Z"},
                {"id": 2, "title": "Senior Data Scientist", "absolute_url": "u2", "location": {"name": "Coimbra"}},
            ]})
    ofertas = L.ler_greenhouse(SessaoFalsa(rotas), "feedzai", FONTE)
    assert [o.titulo for o in ofertas] == ["Software Engineer Intern", "Senior Data Scientist"]
    assert ofertas[0].local == "Lisbon, Portugal" and ofertas[0].chave == "greenhouse:Empresa:1"


def test_greenhouse_tenta_servidor_europeu():
    def rotas(m, url, q, corpo):
        if url == "https://boards-api.eu.greenhouse.io/v1/boards/x/jobs":
            return resposta(json={"jobs": [{"id": 5, "title": "Intern", "absolute_url": "u", "location": {"name": "Lisboa"}}]})
    assert [o.id for o in L.ler_greenhouse(SessaoFalsa(rotas), "x", FONTE)] == ["5"]


def test_lever_tenta_servidor_europeu():
    def rotas(m, url, q, corpo):
        if url == "https://api.eu.lever.co/v0/postings/unbabel":
            return resposta(json=[{"id": "abc", "text": "Backend Developer", "hostedUrl": "https://jobs.eu.lever.co/unbabel/abc",
                                   "createdAt": 1761900000000, "workplaceType": "hybrid",
                                   "categories": {"location": "Lisbon", "allLocations": ["Lisbon", "Porto"],
                                                  "team": "Engineering", "commitment": "Intern"}}])
    [o] = L.ler_lever(SessaoFalsa(rotas), "unbabel", FONTE)
    assert o.local == "Lisbon · Porto" and o.tipo_contrato == "Intern" and o.departamento == "Engineering"
    assert o.publicada.startswith("2025-")


def test_workable():
    def rotas(m, url, q, corpo):
        if url == "https://apply.workable.com/api/v1/widget/accounts/sword":
            return resposta(json={"jobs": [{"title": "Data Intern", "shortcode": "AB12", "url": "https://apply.workable.com/sword/j/AB12/",
                                            "employment_type": "Internship", "department": "Data",
                                            "locations": [{"city": "Lisboa", "region": "Lisboa", "country": "Portugal"}]}]})
    [o] = L.ler_workable(SessaoFalsa(rotas), "sword", FONTE)
    assert o.id == "AB12" and o.local == "Lisboa, Lisboa, Portugal" and o.tipo_contrato == "Internship"


def test_smartrecruiters_pagina():
    def rotas(m, url, q, corpo):
        offset = int(q["offset"])
        n = 100 if offset == 0 else 50
        return resposta(json={"totalFound": 150, "content": [
            {"id": str(offset + i), "name": f"Job {offset + i}", "location": {"city": "Lisboa", "country": "pt"},
             "typeOfEmployment": {"label": "Internship"}} for i in range(n)]})
    sessao = SessaoFalsa(rotas)
    ofertas = L.ler_smartrecruiters(sessao, "BoschGroup", FONTE)
    assert len(ofertas) == 150 and len(sessao.pedidos) == 2
    assert ofertas[0].url == "https://jobs.smartrecruiters.com/BoschGroup/0" and ofertas[0].tipo_contrato == "Internship"


FACETAS = [{"facetParameter": "locationMainGroup", "values": [
    {"facetParameter": "locationCountry", "descriptor": "Country", "values": [
        {"descriptor": "Spain", "id": "es1", "count": 40}, {"descriptor": "Portugal", "id": "pt1", "count": 12}]}]}]


def test_workday_filtro_portugal():
    assert __import__("vigilante.leitores", fromlist=["x"]).filtro_portugal_workday(FACETAS) == {"locationCountry": ["pt1"]}
    sem_pais = [{"facetParameter": "locations", "values": [{"descriptor": "Lisbon - Av. Liberdade", "id": "l1"},
                                                           {"descriptor": "Madrid", "id": "m1"}]}]
    assert L.filtro_portugal_workday(sem_pais) == {"locations": ["l1"]}


def test_workday_com_filtro_e_detalhe():
    api = "https://nokia.wd3.myworkdayjobs.com/wday/cxs/nokia/careers"

    def rotas(m, url, q, corpo):
        if m == "POST" and url == f"{api}/jobs":
            if not corpo["appliedFacets"]:
                return resposta(json={"total": 900, "facets": FACETAS, "jobPostings": []})
            assert corpo["appliedFacets"] == {"locationCountry": ["pt1"]} and corpo["searchText"] == ""
            return resposta(json={"total": 2, "jobPostings": [
                {"title": "R&D Intern", "externalPath": "/job/Amadora/RD-Intern_R1", "locationsText": "Amadora"},
                {"title": "Software Intern", "externalPath": "/job/X/Software-Intern_R2", "locationsText": "2 Locations"},
            ]})
        if m == "GET" and url == f"{api}/job/X/Software-Intern_R2":
            return resposta(json={"jobPostingInfo": {"location": "Lisbon", "additionalLocations": ["Porto"]}})
    ofertas = {o.id: o for o in L.ler_workday(SessaoFalsa(rotas), "nokia.wd3/careers", FONTE)}
    assert ofertas["/job/X/Software-Intern_R2"].local == "Lisbon · Porto"
    assert ofertas["/job/Amadora/RD-Intern_R1"].url == "https://nokia.wd3.myworkdayjobs.com/careers/job/Amadora/RD-Intern_R1"


def test_workday_sem_filtro_pesquisa_termos():
    api = "https://x.wd1.myworkdayjobs.com/wday/cxs/x/ext"
    termos = []

    def rotas(m, url, q, corpo):
        termos.append(corpo["searchText"])
        return resposta(json={"total": 0, "facets": [], "jobPostings": []})
    L.ler_workday(SessaoFalsa(rotas), "x.wd1/ext", FONTE)
    assert termos[0] == "" and "intern" in termos and "estágio" in termos


def test_recruitee_ashby():
    def rotas(m, url, q, corpo):
        if url == "https://empresa.recruitee.com/api/offers/":
            return resposta(json={"offers": [{"id": 7, "title": "Estágio", "careers_url": "https://empresa.recruitee.com/o/estagio",
                                              "city": "Lisboa", "country": "Portugal"}]})
        if url == "https://api.ashbyhq.com/posting-api/job-board/empresa":
            return resposta(json={"jobs": [{"id": "j1", "title": "AI Intern", "location": "Lisbon", "isRemote": False,
                                            "secondaryLocations": [{"location": "Porto"}], "jobUrl": "https://jobs.ashbyhq.com/empresa/j1",
                                            "employmentType": "Intern"}]})
    s = SessaoFalsa(rotas)
    [r] = L.ler_recruitee(s, "empresa", FONTE)
    [a] = L.ler_ashby(s, "empresa", FONTE)
    assert r.local == "Lisboa, Portugal" and a.local == "Lisbon · Porto" and a.tipo_contrato == "Intern"


def test_personio_xml():
    xml = """<?xml version="1.0"?><workzag-jobs><position><id>42</id><office>Lisboa</office>
             <name>Summer Intern</name><department>Tech</department><employmentType>intern</employmentType>
             <createdAt>2026-10-01</createdAt></position></workzag-jobs>"""
    s = SessaoFalsa(lambda m, url, q, c: resposta(text=xml) if url == "https://empresa.jobs.personio.de/xml" else None)
    [o] = L.ler_personio(s, "empresa", FONTE)
    assert (o.id, o.local, o.url) == ("42", "Lisboa", "https://empresa.jobs.personio.de/job/42")


def test_teamtailor_rss():
    rss = """<rss xmlns:tt="https://teamtailor.com/locations"><channel><item><title>Estágio Dev</title>
             <link>https://x.teamtailor.com/jobs/1</link><guid>1</guid>
             <tt:locations><tt:location><tt:city>Lisboa</tt:city><tt:country>Portugal</tt:country></tt:location></tt:locations>
             </item></channel></rss>"""
    s = SessaoFalsa(lambda m, url, q, c: resposta(text=rss))
    [o] = L.ler_teamtailor(s, "x", FONTE)
    assert o.titulo == "Estágio Dev" and o.local == "Lisboa, Portugal"


RMK = """<table><tr class="data-row">
  <td><a class="jobTitle-link" href="/job/Lisboa-Summer-Internship/111/">Summer Internship</a>
      <a class="jobTitle-link" href="/job/Lisboa-Summer-Internship/111/">Summer Internship</a></td>
  <td><span class="jobLocation"> Lisboa, PT </span></td><td><span class="jobDate">29/10/2026</span></td></tr>
<tr class="data-row"><td><a class="jobTitle-link" href="/job/Porto-Analyst/222/">Analyst</a></td><td></td></tr></table>"""


RMK_VAZIO = '<link href="https://rmkcdn.successfactors.com/a.css"><div id="searchResultsShell"></div>'


def test_successfactors_rmk():
    def rotas(m, url, q, corpo):
        if url == "https://recrutamento.tap.pt/go/Internships-&-Trainee/742402/":
            return resposta(text=RMK)
        if url == "https://recrutamento.tap.pt/search/":
            return resposta(text=RMK if q.get("q") == "intern" else RMK_VAZIO)
    fonte = {"nome": "TAP", "carreiras": "https://recrutamento.tap.pt/go/Internships-&-Trainee/742402/"}
    ofertas = {o.id: o for o in L.ler_successfactors(SessaoFalsa(rotas), "recrutamento.tap.pt", fonte)}
    assert set(ofertas) == {"111", "222"}
    assert ofertas["111"].local == "Lisboa, PT" and ofertas["111"].url == "https://recrutamento.tap.pt/job/Lisboa-Summer-Internship/111/"
    assert ofertas["111"].empresa == "TAP"


def test_successfactors_classico_vai_para_pagina():
    with pytest.raises(L.NaoSuportado):
        L.ler_successfactors(SessaoFalsa(lambda *a: None), "career2.successfactors.eu/bancodepor", FONTE)


def test_successfactors_site_errado_vai_para_pagina():
    # O caso do BPI: o detetor guardou o site principal, que não tem /search/ (404)
    with pytest.raises(L.NaoSuportado):
        L.ler_successfactors(SessaoFalsa(lambda *a: resposta(404)), "", {"nome": "BPI", "carreiras": "https://www.bancobpi.pt/x"})
    # O caso da EDP: o site principal tem uma pesquisa, mas não é do SuccessFactors
    with pytest.raises(L.NaoSuportado):
        L.ler_successfactors(SessaoFalsa(lambda *a: resposta(text="<h1>Pesquisa no site</h1>")), "www.edp.com", FONTE)


def test_successfactors_prefixo():
    assert L.base_rmk("careers.ey.com/ey", "") == "https://careers.ey.com/ey"
    assert L.base_rmk("", "https://careers.ey.com/ey/search/?q=") == "https://careers.ey.com/ey"
    assert L.base_rmk("jobs.galp.com", "https://jobs.galp.com/search/?q=") == "https://jobs.galp.com"
    assert L.base_rmk("", "https://career2.successfactors.eu/career?company=x") is None


def test_amazon():
    def rotas(m, url, q, corpo):
        assert q["country"] == "PRT"
        return resposta(json={"jobs": [{"id_icims": "99", "title": "Software Dev Engineer Intern", "job_path": "/en/jobs/99",
                                        "normalized_location": "Lisbon, PRT"}]})
    [o] = L.ler_amazon(SessaoFalsa(rotas), "", FONTE)
    assert o.url == "https://www.amazon.jobs/en/jobs/99" and o.local == "Lisbon, PRT"


def test_breezy():
    def rotas(m, url, q, corpo):
        if url == "https://critical.breezy.hr/json":
            return resposta(json=[{"id": "p1", "friendly_id": "abc-summer-camp", "name": "Summer Camp 2027",
                                   "url": "https://critical.breezy.hr/p/abc-summer-camp", "type": {"name": "Internship"},
                                   "location": {"name": "Lisboa, Portugal", "is_remote": False}}])
    [o] = L.ler_breezy(SessaoFalsa(rotas), "critical", FONTE)
    assert (o.titulo, o.local, o.tipo_contrato) == ("Summer Camp 2027", "Lisboa, Portugal", "Internship")


def test_itjobs():
    def rotas(m, url, q, corpo):
        assert q["contract"] == "3,5" and q["api_key"] == "CHAVE"
        return resposta(json={"total": 1, "results": [{"id": 9, "title": "Estágio Java", "slug": "estagio-java",
                                                         "company": {"name": "Bee Engineering"},
                                                         "locations": [{"id": 14, "name": "Lisboa"}]}]})
    [o] = L.ler_itjobs(SessaoFalsa(rotas), "CHAVE", {"nome": "ITJobs.pt"})
    assert o.empresa == "Bee Engineering (via ITJobs)" and o.url == "https://www.itjobs.pt/oferta/9/estagio-java"
