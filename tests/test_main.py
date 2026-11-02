"""Simula vários dias seguidos do vigilante, sem rede, para testar memória e mensagens."""
from datetime import date, datetime, timezone

import pytest

from vigilante import estado as memoria
from vigilante import leitores, main, paginas
from vigilante.leitores import Oferta
from vigilante.noticias import ItemNoticia


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    """Leitores, páginas e notícias falsos; estado num ficheiro temporário; 'enviar' que só
    guarda as mensagens numa lista."""
    ofertas = {}     # empresa -> lista de (id, título, local), ou uma Exception
    htmls = {}       # url -> html (páginas vigiadas)
    noticias = []    # ItemNoticia devolvidas pelo Google News falso
    enviadas = []

    def leitor_falso(sessao, ident, fonte):
        dados = ofertas.get(fonte["nome"], [])
        if isinstance(dados, Exception):
            raise dados
        return [Oferta(fonte["nome"], "greenhouse", i, t, f"https://x/{i}", local=l) for i, t, l in dados]

    def renderizar_falso(urls):
        return {u: paginas.Pagina(u, u, 200, htmls[u]) if u in htmls else paginas.Pagina(u, erro="Timeout")
                for u in urls}

    monkeypatch.setitem(leitores.LEITORES, "greenhouse", leitor_falso)
    monkeypatch.setattr(paginas, "renderizar", renderizar_falso)
    monkeypatch.setattr(paginas.Robots, "permite", lambda self, url: "proibido" not in url)
    monkeypatch.setattr(main, "ler_noticias", lambda s, q, e, h: (list(noticias), []))
    monkeypatch.setattr(memoria, "FICHEIRO_ESTADO", tmp_path / "estado.json")
    monkeypatch.setattr(main, "enviar", lambda texto, assunto="": enviadas.append(texto))
    return ofertas, htmls, noticias, enviadas


def fonte(nome, prioridade=1, plataforma="greenhouse", **extra):
    return {"nome": nome, "prioridade": prioridade, "site": f"https://{nome.lower()}.pt",
            "plataforma": {"tipo": plataforma, "id": nome.lower()} if plataforma else None, **extra}


GERAIS = {"agregadores": [], "noticias": ["estágios de verão"]}
FONTES = [fonte("Feedzai"), fonte("Galp", 2), fonte("Sonae", 1, plataforma=None, carreiras="https://sonae.pt/carreiras")]


def correr(dia, fontes=FONTES, gerais=GERAIS):
    return main.modo_normal(hoje=date(2026, 11, dia), fontes=fontes, gerais=gerais)


def test_varios_dias(ambiente):
    ofertas, htmls, noticias, enviadas = ambiente
    ofertas["Feedzai"] = [("1", "Software Engineer Intern", "Lisbon, Portugal"),
                          ("2", "Senior Engineer", "Lisbon"),               # não é estágio
                          ("3", "Data Intern", "Madrid, Spain")]             # fora de Portugal
    ofertas["Galp"] = [("9", "Summer Internship Finance", "Lisboa")]
    htmls["https://sonae.pt/carreiras"] = "<h2>Programa Contacto</h2><p>Estágios de verão 2026: candidaturas fechadas</p>"
    noticias.append(ItemNoticia("Empresa X abre estágios de verão", "https://n/1", "ECO", None))
    noticias.append(ItemNoticia("Galp abre programa de estágios", "https://n/2", "Expresso", None, empresa_citada="Galp"))

    # Dia 1: primeira execução = resumo inicial com o que está aberto
    correr(2)
    assert len(enviadas) == 1
    m = enviadas[0]
    assert "resumo inicial" in m and "2 ofertas de estágio abertas agora em 2 empresas" in m
    assert m.index("💻 Feedzai") < m.index("📌 Galp")                      # informática primeiro
    assert "📌 Sonae (1 texto), ex.: «Estágios de verão 2026: candidaturas fechadas»" in m   # 1.ª leitura: compacta
    assert "⭐ Galp" in m and "Empresa X" not in m                          # 1.ª leitura: só notícias com ⭐

    # Dia 2: nada mudou -> não envia nada
    correr(3)
    assert len(enviadas) == 1

    # Dia 3: uma oferta nova, um texto novo na página e uma notícia nova
    ofertas["Galp"].append(("10", "Estágio de Verão em Dados", "Lisboa"))
    htmls["https://sonae.pt/carreiras"] += "<p>Estágios de verão 2027: candidaturas abertas!</p>"
    noticias.append(ItemNoticia("Feedzai lança summer internship", "https://n/3", "Observador",
                                datetime(2026, 11, 4, tzinfo=timezone.utc), empresa_citada="Feedzai"))
    correr(4)
    m = enviadas[1]
    assert m.startswith("🆕 3 novidades (04/11/2026)")
    assert "💻 Galp: Estágio de Verão em Dados" in m
    assert "«Estágios de verão 2027: candidaturas abertas!»" in m and "2026" not in m.split("📄")[1].split("📰")[0]
    assert "📰 ⭐ Feedzai · Feedzai lança summer internship (Observador, 04/11)" in m

    # Dia 4: empresa nova na lista -> as ofertas dela vão numa secção à parte
    ofertas["Nokia"] = [("50", "R&D Intern", "Amadora")]
    correr(5, FONTES + [fonte("Nokia")])
    assert "Já estavam visíveis, em fontes que li pela primeira vez (1)" in enviadas[2]
    assert "🆕" not in enviadas[2]


def test_leitor_nao_suportado_passa_para_pagina(ambiente, monkeypatch):
    _, htmls, _, enviadas = ambiente

    def sf(sessao, ident, fonte):
        raise leitores.NaoSuportado("não parece um site SuccessFactors")
    monkeypatch.setitem(leitores.LEITORES, "successfactors", sf)
    bpi = fonte("BPI", plataforma="successfactors", carreiras="https://bpi.pt/trabalhar-no-bpi")
    htmls["https://bpi.pt/trabalhar-no-bpi"] = '<a href="/summer-boost">Summer Boost 2026</a>'
    correr(2, [bpi])
    assert "BPI (1 texto), ex.: «Summer Boost 2026»\nhttps://bpi.pt/trabalhar-no-bpi" in enviadas[0]


def test_robots_e_vigiar_pagina_false(ambiente):
    _, _, _, enviadas = ambiente
    fontes = [fonte("A", plataforma=None, carreiras="https://a.pt/proibido"),
              fonte("Millennium", plataforma=None, vigiar_pagina=False)]
    canais = main.recolher(fontes, {"agregadores": [], "noticias": []}, date(2026, 11, 2))
    por_nome = {c.nome: c for c in canais}
    assert "robots.txt" in por_nome["A"].pendente
    assert por_nome["Millennium"].pendente.startswith("só notícias")


def test_fonte_avariada_avisa_ao_terceiro_dia(ambiente):
    ofertas, _, _, enviadas = ambiente
    ofertas["Feedzai"] = [("1", "Intern", "Lisboa")]
    correr(1, FONTES[:1])
    ofertas["Feedzai"] = RuntimeError("API em baixo")
    for dia in (2, 3):
        correr(dia, FONTES[:1])
    assert len(enviadas) == 1          # 2 falhas: ainda não avisa
    correr(4, FONTES[:1])
    assert "Não consigo ler há 3 execuções seguidas: Feedzai" in enviadas[-1]


def test_limite_por_empresa(ambiente):
    ofertas, _, _, enviadas = ambiente
    ofertas["Feedzai"] = [(str(i), f"Intern {i}", "Lisboa") for i in range(8)]
    correr(1, [fonte("Feedzai", carreiras="https://feedzai.com/careers")])
    assert "… e mais 5 de Feedzai: https://feedzai.com/careers" in enviadas[0]   # 1.ª leitura: 3 por empresa


def test_se_o_envio_falhar_o_estado_nao_e_guardado(ambiente, monkeypatch):
    ofertas, _, _, _ = ambiente
    ofertas["Feedzai"] = [("1", "Intern", "Lisboa")]

    def falha(texto, assunto=""):
        raise RuntimeError("Telegram em baixo")
    monkeypatch.setattr(main, "enviar", falha)
    with pytest.raises(RuntimeError):
        correr(1, FONTES[:1])
    assert memoria.carregar() is None   # amanhã volta a tentar o resumo inicial


def test_simulacao_nao_envia_nem_guarda(ambiente):
    ofertas, _, _, enviadas = ambiente
    ofertas["Feedzai"] = [("1", "Intern", "Lisboa")]
    texto = main.modo_normal(simular=True, hoje=date(2026, 11, 1), fontes=FONTES[:1], gerais=GERAIS)
    assert "resumo inicial" in texto and enviadas == [] and memoria.carregar() is None


def test_esquecer_ofertas_antigas():
    e = memoria.novo(date(2026, 1, 1))
    memoria.registar_ofertas(e, [Oferta("A", "greenhouse", "1", "Intern", "u")], date(2026, 1, 1))
    assert memoria.esquecer_antigas(e, date(2026, 12, 31)) == 1 and e["ofertas"] == {}


def test_mudar_configuracao_conta_como_primeira_leitura(ambiente):
    # O caso da EDP: lia 0 ofertas com o endereço errado; com o endereço certo, as ofertas
    # que aparecem já existiam: vão para "Já estavam visíveis", não para "🆕".
    ofertas, _, _, enviadas = ambiente
    edp = fonte("EDP", carreiras="https://www.edp.com")
    correr(2, [edp])                                  # resumo inicial, 0 ofertas
    ofertas["EDP"] = [("1", "EDP Summer Internship", "Lisboa")]
    correr(3, [dict(edp, carreiras="https://jobs.edp.com")])
    assert "Já estavam visíveis" in enviadas[-1] and "🆕" not in enviadas[-1]
    ofertas["EDP"].append(("2", "Estágio Profissional", "Lisboa"))
    correr(4, [dict(edp, carreiras="https://jobs.edp.com")])
    assert enviadas[-1].startswith("🆕 1 novidade")


def test_resumo_semanal(ambiente):
    ofertas, htmls, _, enviadas = ambiente
    ofertas["Feedzai"] = [("1", "Software Engineer Intern", "Lisboa")]
    htmls["https://sonae.pt/carreiras"] = "<p>Programa de estágios de verão: candidaturas abertas até 6 de abril</p>"
    correr(2)                                         # segunda 02/11: resumo inicial
    ofertas["Feedzai"].append(("2", "Data Intern", "Lisboa"))
    correr(4)                                         # quarta: 1 novidade
    correr(5)                                         # quinta: nada
    assert len(enviadas) == 2
    correr(6)                                         # sexta: dia de resumo (a diária não tem nada)
    assert len(enviadas) == 3
    r = enviadas[-1]
    assert r.startswith("📅 Resumo semanal (06/11/2026)") and "correu 4 de 7 dias" in r
    assert "💼 Estágios abertos agora: 2 em 1 empresa" in r
    assert "💻 Feedzai (2): Software Engineer Intern · Data Intern" in r
    assert "📣 Páginas que dizem \"candidaturas abertas\" (1)" in r
    assert "🆕 Novidades desta semana: 1 oferta · 0 textos" in r          # as do resumo inicial não contam
    assert "Todas as fontes leram sem erros" in r and "Próximo resumo: 13/11" in r


def test_resumo_semanal_mostra_fontes_avariadas(ambiente):
    ofertas, _, _, enviadas = ambiente
    correr(2, FONTES[:1])
    ofertas["Feedzai"] = RuntimeError("API em baixo")
    main.modo_normal(hoje=date(2026, 11, 3), fontes=FONTES[:1], gerais=GERAIS, forcar_resumo=True)
    assert "• Feedzai: 1× seguidas · RuntimeError: API em baixo" in enviadas[-1]
