from datetime import date

from vigilante import paginas
from vigilante.noticias import _nomes, citada, ler_rss, ler_noticias
from vigilante.filtros import e_programa, noticia_relevante
from tests.conftest import SessaoFalsa, resposta

HTML = """
<html><head><script>var x = "Summer Internship";</script></head><body>
<nav><a href="/carreiras/estudantes">Estudantes e recém-licenciados</a> <a href="/sobre">Sobre nós</a>
     <a href="https://www.linkedin.com/company/x">LinkedIn</a> <a href="/vagas">Ver todas as vagas</a></nav>
<div class="card"><h3>Summer <b>Internship</b> 2027</h3><p>Candidaturas abertas até 15 de março.</p></div>
<ul><li><a href="/job/123">Estágio Curricular - Data Science</a> <span>há 3 dias</span></li></ul>
<p>Somos uma empresa com mais de 5000 colaboradores, presentes em 20 países, e procuramos sempre
   pessoas com vontade de aprender e de crescer connosco, num ambiente de inovação.</p>
<footer><a href="/privacidade">Privacidade</a></footer></body></html>"""


def test_extrair_itens():
    itens = paginas.extrair_itens(HTML, "https://x.pt/carreiras", "X")
    textos = {i.titulo: i.url for i in itens}
    assert "Summer Internship 2027" in textos                      # texto partido em <b> volta a juntar-se
    assert textos["Estágio Curricular - Data Science"] == "https://x.pt/job/123"
    assert not any("var x" in t for t in textos)                    # scripts ignorados
    assert not any("5000 colaboradores" in t for t in textos)       # parágrafo sem palavras de estágio
    assert [i for i in itens if "Data Science" in i.titulo][0].informatica


def test_chave_ignora_tempo_relativo():
    a = paginas.ItemPagina("X", "Estágio em Dados · há 3 dias", "u", "p")
    b = paginas.ItemPagina("X", "Estágio em Dados · há 4 dias", "u", "p")
    c = paginas.ItemPagina("X", "Estágio em Dados 2027", "u", "p")
    assert a.chave == b.chave != c.chave


def test_padrao_link_agregador():
    html = '<a href="/en/summer-internship/worten/">Worten - BESUMMER</a><a href="/en/blog/x">Blog</a>'
    itens = paginas.extrair_itens(html, "https://talentportugal.com/en/", "Talent Portugal", padrao_link="/summer-internship/")
    assert [i.titulo for i in itens] == ["Worten - BESUMMER"]


def test_links_a_seguir():
    links = paginas.links_a_seguir(HTML, "https://x.pt/carreiras")
    assert links == ["https://x.pt/carreiras/estudantes", "https://x.pt/vagas"]


def test_vigiar_com_subpaginas(monkeypatch):
    htmls = {"https://x.pt/carreiras": HTML,
             "https://x.pt/carreiras/estudantes": "<h2>Programa de Trainees 2027</h2>"}
    monkeypatch.setattr(paginas, "renderizar", lambda urls: {
        u: paginas.Pagina(u, u, 200, htmls[u]) if u in htmls else paginas.Pagina(u, erro="Timeout") for u in urls})
    robots = paginas.Robots()
    robots._cache = {"https://x.pt": None}
    [v] = paginas.vigiar([{"nome": "X", "carreiras": "https://x.pt/carreiras"}], robots)
    assert v.erro is None and "Programa de Trainees 2027" in {i.titulo for i in v.itens}
    assert v.visitadas == ["https://x.pt/carreiras", "https://x.pt/carreiras/estudantes", "https://x.pt/vagas"]


def test_robots():
    from urllib.robotparser import RobotFileParser
    r = paginas.Robots()
    regras = RobotFileParser()
    regras.parse(["User-agent: *", "Disallow: /"])
    r._cache = {"https://millenniumbcp.pt": regras, "https://x.pt": None}
    assert not r.permite("https://millenniumbcp.pt/carreiras") and r.permite("https://x.pt/a")


def test_sitemap():
    indice = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://tp.com/post-sitemap.xml</loc></sitemap>
      <sitemap><loc>https://tp.com/summer-internship-sitemap.xml</loc></sitemap></sitemapindex>"""
    filho = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://tp.com/en/summer-internship/accenture-summer-internships/</loc></url>
      <url><loc>https://tp.com/en/summer-internship/</loc></url></urlset>"""

    def rotas(m, url, q, c):
        return resposta(text={"https://tp.com/sitemap_index.xml": indice,
                              "https://tp.com/summer-internship-sitemap.xml": filho}.get(url, ""))
    s = SessaoFalsa(rotas)
    itens = paginas.ler_sitemap(s, {"nome": "TP", "url": "https://tp.com/sitemap_index.xml", "filtro": "summer-internship"})
    assert [i.titulo for i in itens] == ["accenture summer internships", "summer internship"]
    assert all("post-sitemap" not in p[1] for p in s.pedidos)       # só lê os sitemaps que interessam


# ---------------------------------------------------------------- notícias

RSS = """<rss><channel>
<item><title>Galp abre candidaturas para estágios de verão - ECO</title><link>https://n/1</link>
  <pubDate>Mon, 02 Nov 2026 09:00:00 GMT</pubDate><source url="https://eco.sapo.pt">ECO</source></item>
<item><title>Seleção em estágio de verão antes do Mundial - Record</title><link>https://n/2</link>
  <pubDate>Mon, 02 Nov 2026 09:00:00 GMT</pubDate><source>Record</source></item>
<item><title>NOS lança programa de estágios para universitários - Expresso</title><link>https://n/3</link>
  <pubDate>Sun, 01 Nov 2026 09:00:00 GMT</pubDate><source>Expresso</source></item>
<item><title>Nos próximos meses há estágios de verão em várias empresas - Público</title><link>https://n/4</link>
  <pubDate>Sun, 01 Nov 2026 09:00:00 GMT</pubDate><source>Público</source></item>
<item><title>Antiga: estágios de verão 2025 - X</title><link>https://n/5</link>
  <pubDate>Mon, 02 Jun 2025 09:00:00 GMT</pubDate><source>X</source></item>
</channel></rss>"""


def test_ler_rss_tira_nome_do_jornal():
    titulo, link, origem, data = ler_rss(RSS.encode())[0]
    assert (titulo, link, origem, data.day) == ("Galp abre candidaturas para estágios de verão", "https://n/1", "ECO", 2)


def test_noticias():
    empresas = [{"nome": "Galp"}, {"nome": "NOS"}, {"nome": "MEO (Altice Portugal)"}]
    s = SessaoFalsa(lambda *a: resposta(text=RSS))
    itens, erros = ler_noticias(s, ["estágios de verão"], empresas, date(2026, 11, 2))
    por_titulo = {i.titulo: i for i in itens}
    assert erros == [] and "Seleção em estágio de verão antes do Mundial" not in por_titulo   # futebol
    assert not any("Antiga" in t for t in por_titulo)                                         # > 30 dias
    assert por_titulo["Galp abre candidaturas para estágios de verão"].empresa_citada == "Galp"
    assert por_titulo["NOS lança programa de estágios para universitários"].empresa_citada == "NOS"
    assert por_titulo["Nos próximos meses há estágios de verão em várias empresas"].empresa_citada == ""
    assert "when%3A30d" in s.pedidos[0][2]["q"] or "when:30d" in s.pedidos[0][2]["q"]


def test_citada_variantes():
    nomes = _nomes([{"nome": "MEO (Altice Portugal)"}, {"nome": "Jerónimo Martins"}, {"nome": "TAP Air Portugal"}])
    assert citada("Altice abre estágios", nomes) == "MEO (Altice Portugal)"
    assert citada("Jeronimo Martins lança Summer Internship", nomes) == "Jerónimo Martins"
    assert citada("TAP recruta estagiários", nomes) == "TAP Air Portugal"
    assert citada("Torneira e tap water", nomes) == ""


def test_filtros_pagina_e_noticia():
    assert e_programa("Graduate Program 2027") and e_programa("Summer Break Galp")
    assert not e_programa("Students")                       # palavra fraca sozinha: não
    assert noticia_relevante("Sonae abre 80 estágios para jovens")
    assert not noticia_relevante("Doença detetada em estágio avançado")


def test_locais_estrangeiros_e_repetidos():
    html = """<a href="/1">Software Engineering Intern 2027 Internship Markham, CA</a>
              <a href="/2">Unpaid Internship | Devoteam Creative Tech | Belgium</a>
              <a href="/3">Application Security | Curricular Internship · Lisbon, Porto</a>
              <a href="/4"><h3>Accenture - Technology Summer Internship 2026</h3>
                 <p>O Technology Summer Internship da Accenture é um programa de imersão.</p></a>"""
    titulos = [i.titulo for i in paginas.extrair_itens(html, "https://x.pt", "X")]
    assert "Application Security | Curricular Internship · Lisbon, Porto" in titulos
    assert not any("Markham" in t or "Belgium" in t for t in titulos)
    assert "Accenture - Technology Summer Internship 2026" in titulos
    assert not any("imersão" in t and "Accenture -" in t for t in titulos)      # o cartão inteiro sai


def test_diz_abertas():
    from vigilante.filtros import diz_abertas
    assert diz_abertas("Summer Internship 2027: candidaturas abertas até 15 de março")
    assert diz_abertas("Applications are open!")
    assert not diz_abertas("As candidaturas para o estágio de verão 2026 estão encerradas")
    assert not diz_abertas("Candidaturas abertas brevemente. Fica atento!")
