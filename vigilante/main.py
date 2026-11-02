"""
Ponto de entrada do vigilante de estágios.

Uso:
    python -m vigilante.main --modo teste      # envia só uma mensagem de teste
    python -m vigilante.main --modo normal     # execução diária: procura, avisa o que é novo, guarda o estado
    python -m vigilante.main --modo simulacao  # faz tudo, mas não envia nem guarda (só mostra no log)

O que acontece numa execução normal:
  1. plataformas: lê as ofertas das empresas com plataforma conhecida (leitores.py), 8 em paralelo,
     e fica com os estágios em Lisboa ou possivelmente em Lisboa (filtros.py);
  2. páginas: as empresas sem leitor (e os agregadores) são abertas num browser e
     guardam-se os textos sobre estágios (paginas.py);
  3. notícias: pesquisas no Google News (noticias.py);
  4. compara tudo com data/estado.json para saber o que é novo (estado.py);
  5. envia a mensagem (notify.py) e só depois guarda o estado
     (se o envio falhar, o estado não é guardado e na próxima execução volta a tentar).

Cada "canal" (a plataforma de uma empresa, a página de uma empresa, as notícias...) tem a
sua entrada em estado["fontes"]. A primeira vez que um canal é lido com sucesso, o que lá
está não é "novo": já existia antes de o bot o conhecer. Vai para uma secção à parte.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

from vigilante import estado as memoria
from vigilante import paginas, semanal
from vigilante.config import LISBOA, carregar_empresas, carregar_fontes, carregar_gerais
from vigilante.filtros import ENCERRADAS, ZONAS_TEXTO, classificar_local, diz_abertas, e_estagio, e_informatica, normalizar
from vigilante.leitores import LEITORES, NaoSuportado, Oferta, ler_itjobs
from vigilante.noticias import ler_noticias
from vigilante.notify import enviar
from vigilante.rede import nova_sessao

TRABALHADORES = 8          # empresas lidas em paralelo
FALHAS_PARA_AVISAR = 3     # avisa quando um canal falha 3 execuções seguidas
POR_EMPRESA = 5            # máximo de linhas por empresa em cada secção da mensagem
MAX_NOTICIAS = 10


@dataclass
class Canal:
    """Resultado de ler uma fonte numa execução."""
    chave: str                      # entrada em estado["fontes"] (ex.: "Feedzai", "página: BPI")
    nome: str
    tipo: str                       # greenhouse, ..., pagina, sitemap, noticias
    fonte: dict = field(default_factory=dict)
    lidas: int = 0                  # quantas coisas foram lidas (ofertas, textos, notícias)
    itens: list = field(default_factory=list)   # o que passou nos filtros
    erro: str | None = None
    pendente: str | None = None     # não foi lido (ex.: sem leitor -> passa para a vigia de páginas)
    silencioso: bool = False        # na 1.ª leitura não mostra os itens (sitemaps, notícias sem ⭐)
    assinatura: str = ""            # configuração usada; se mudar, a leitura conta como 1.ª leitura


# ---------------------------------------------------------------- 1. plataformas

def ler_plataforma(fonte: dict) -> Canal:
    plataforma = fonte.get("plataforma") or {}
    tipo = plataforma.get("tipo")
    canal = Canal(fonte["nome"], fonte["nome"], tipo or "-", fonte,
                  assinatura=f"{tipo}|{plataforma.get('id') or ''}|{fonte.get('carreiras') or ''}")
    if not tipo:
        canal.pendente = "sem plataforma"
        return canal
    leitor = LEITORES.get(tipo)
    if leitor is None:
        canal.pendente = f"{tipo}: sem leitor"
        return canal
    try:
        ofertas = leitor(nova_sessao(), str(plataforma.get("id") or ""), fonte)
    except NaoSuportado as aviso:
        canal.pendente = str(aviso)
        return canal
    except Exception as erro:  # uma fonte avariada não pode parar as outras
        canal.erro = f"{type(erro).__name__}: {erro}"[:300]
        return canal
    canal.lidas = len(ofertas)
    canal.itens = filtrar(ofertas, fonte.get("prioridade", 3))
    return canal


def filtrar(ofertas: list[Oferta], prioridade: int, ja_sao_estagios: bool = False) -> list[Oferta]:
    relevantes = []
    for oferta in ofertas:
        if not ja_sao_estagios and not e_estagio(oferta.titulo, oferta.tipo_contrato):
            continue
        zona = classificar_local(oferta.local)
        if zona is None:
            continue
        oferta.zona = zona
        oferta.informatica = ja_sao_estagios or e_informatica(oferta.titulo, oferta.departamento)
        oferta.prioridade = prioridade
        relevantes.append(oferta)
    return relevantes


def ler_itjobs_canal(agregador: dict) -> Canal:
    canal = Canal("ITJobs", agregador["nome"], "itjobs", agregador, assinatura="itjobs|contratos-3-5")
    chave = os.environ.get("ITJOBS_API_KEY")
    if not chave:
        canal.pendente = "sem o segredo ITJOBS_API_KEY"
        return canal
    try:
        ofertas = ler_itjobs(nova_sessao(), chave, agregador)
    except Exception as erro:
        canal.erro = f"{type(erro).__name__}: {erro}"[:300].replace(chave, "***")
        return canal
    canal.lidas = len(ofertas)
    canal.itens = filtrar(ofertas, 3, ja_sao_estagios=True)  # o ITJobs já filtra por contrato de estágio
    return canal


# ---------------------------------------------------------------- 2. páginas

def vigiar_paginas(fontes_pagina: list[dict]) -> list[Canal]:
    canais = []
    for v in paginas.vigiar(fontes_pagina):
        url = v.fonte.get("carreiras") or v.fonte.get("url") or v.fonte.get("site") or ""
        c = Canal(f"página: {v.nome}", v.nome, "pagina", v.fonte, lidas=len(v.visitadas), itens=v.itens,
                  erro=v.erro, pendente=v.ignorada, assinatura=f"pagina|{url}")
        canais.append(c)
    return canais


def ler_sitemap_canal(agregador: dict) -> Canal:
    canal = Canal(f"sitemap: {agregador['nome']}", agregador["nome"], "sitemap", agregador, silencioso=True,
                  assinatura=f"sitemap|{agregador['url']}|{agregador['filtro']}")
    try:
        canal.itens = paginas.ler_sitemap(nova_sessao(), agregador)
        canal.lidas = len(canal.itens)
    except Exception as erro:
        canal.erro = f"{type(erro).__name__}: {erro}"[:300]
    return canal


# ---------------------------------------------------------------- 3. notícias

def ler_noticias_canal(consultas: list[str], empresas: list[dict], hoje: date) -> Canal:
    canal = Canal("notícias", "Notícias (Google News)", "noticias", silencioso=True, assinatura="noticias")
    if not consultas:
        canal.pendente = "sem pesquisas em config/fontes.yaml"
        return canal
    itens, erros = ler_noticias(nova_sessao(), consultas, empresas, hoje)
    canal.itens, canal.lidas = itens, len(itens)
    if erros and len(erros) == len(consultas):
        canal.erro = erros[0]
    for e in erros:
        print(f"  [notícias] {e}")
    return canal


# ---------------------------------------------------------------- mensagem

def _tipo(item) -> str:
    if item.fonte in ("pagina", "sitemap"):
        return "pagina"
    return "noticia" if item.fonte == "noticia" else "oferta"


def _linha(item) -> str:
    icone = "💻" if item.informatica else "📌"
    tipo = _tipo(item)
    if tipo == "oferta":
        if item.zona == "desconhecido":
            onde = ZONAS_TEXTO["desconhecido"] + (f" ({item.local})" if item.local else "")
        else:
            onde = item.local[:80] or ZONAS_TEXTO[item.zona]
        return f"{icone} {item.empresa}: {item.titulo}\n📍 {onde}\n{item.url}"
    if tipo == "pagina":
        return f"{icone} {item.empresa}: «{item.titulo}»\n{item.url}"
    estrela = f"⭐ {item.empresa_citada} · " if item.empresa_citada else ""
    data = f", {item.publicada:%d/%m}" if item.publicada else ""
    return f"📰 {estrela}{item.titulo} ({item.origem or 'notícia'}{data})\n{item.url}"


def _ordenar(itens: list) -> list:
    def chave(i):
        if _tipo(i) == "noticia":
            return (not i.empresa_citada, -(i.publicada.timestamp() if i.publicada else 0), "", "")
        return (not i.informatica, i.prioridade, normalizar(i.empresa), normalizar(i.titulo))
    return sorted(itens, key=chave)


def _listar(itens: list, links: dict[str, str], por_empresa: int = POR_EMPRESA) -> list[str]:
    """Linhas da mensagem, no máximo `por_empresa` por empresa (+ um link para o resto)."""
    grupos: dict[str, list] = {}
    for item in _ordenar(itens):
        grupos.setdefault(item.empresa, []).append(item)
    linhas = []
    for empresa, grupo in grupos.items():
        linhas += [_linha(i) for i in grupo[:por_empresa]]
        if len(grupo) > por_empresa:
            linhas.append(f"… e mais {len(grupo) - por_empresa} de {empresa}: {links.get(empresa, '')}".rstrip(": "))
    return linhas


def _exemplo(grupo: list):
    """O texto mais informativo de uma empresa: o que fala de candidaturas/ano, ou o mais curto."""
    def pontos(i):
        t = normalizar(i.titulo)
        return (not (diz_abertas(i.titulo) or ENCERRADAS.search(t) or any(a in t for a in ("2026", "2027", "2028"))),
                len(i.titulo))
    return min(grupo, key=pontos)


def _listar_compacto(itens: list, links: dict[str, str]) -> list[str]:
    """1.ª leitura de páginas: uma linha por empresa (quantos textos + um exemplo)."""
    grupos: dict[str, list] = {}
    for item in _ordenar(itens):
        grupos.setdefault(item.empresa, []).append(item)
    linhas = []
    for empresa, grupo in grupos.items():
        icone = "💻" if any(i.informatica for i in grupo) else "📌"
        n = f"{len(grupo)} {'texto' if len(grupo) == 1 else 'textos'}"
        linhas.append(f"{icone} {empresa} ({n}), ex.: «{_exemplo(grupo).titulo[:120]}»\n"
                      f"{links.get(empresa) or grupo[0].url}")
    return linhas


def _secoes(itens: list, links: dict[str, str], por_empresa: int = POR_EMPRESA,
            compacto: bool = False) -> list[str]:
    blocos = []
    ofertas = [i for i in itens if _tipo(i) == "oferta"]
    paginas_ = [i for i in itens if _tipo(i) == "pagina"]
    noticias = [i for i in itens if _tipo(i) == "noticia"]
    if ofertas:
        blocos.append(f"💼 Ofertas de estágio ({len(ofertas)})")
        blocos += _listar(ofertas, links, por_empresa)
    if paginas_ and compacto:
        n_emp = len({i.empresa for i in paginas_})
        blocos.append(f"📄 Páginas que já falam de estágios: {n_emp} (a partir de agora avisa só textos novos)")
        blocos += _listar_compacto(paginas_, links)
    elif paginas_:
        blocos.append(f"📄 Textos sobre estágios em páginas de carreiras ({len(paginas_)})")
        blocos += _listar(paginas_, links, por_empresa)
    if noticias:
        blocos.append(f"📰 Notícias ({len(noticias)})")
        escolhidas = _ordenar(noticias)[:MAX_NOTICIAS]
        blocos += [_linha(n) for n in escolhidas]
        if len(noticias) > MAX_NOTICIAS:
            blocos.append(f"… e mais {len(noticias) - MAX_NOTICIAS} notícias")
    return blocos


def montar_mensagem(novas: list, primeira: list, hoje: date, inicial: bool, avisos: list[str],
                    resumo: dict, links: dict[str, str]) -> str | None:
    """Devolve o texto a enviar, ou None se não houver nada a dizer.
    novas = nunca vistas em canais já conhecidos; primeira = vistas pela 1.ª vez num canal novo."""
    data = hoje.strftime("%d/%m/%Y")
    blocos = []
    if inicial:
        ofertas = [i for i in primeira if _tipo(i) == "oferta"]
        blocos.append(
            f"📋 Vigilante de estágios: resumo inicial ({data})\n"
            f"{len(ofertas)} ofertas de estágio abertas agora em {len({o.empresa for o in ofertas})} empresas.\n"
            f"Plataformas lidas: {resumo['plataformas']}"
            + (f" (+{resumo['erros']} com erro hoje, ver log)" if resumo["erros"] else "")
            + f" · páginas vigiadas: {resumo['paginas']} · sem acesso: {resumo['sem_acesso']}.\n"
            "A partir de agora só recebes o que for novo.")
        blocos += _secoes(primeira, links, por_empresa=3, compacto=True)
    else:
        if novas:
            blocos.append(f"🆕 {len(novas)} {'novidade' if len(novas) == 1 else 'novidades'} ({data})")
            blocos += _secoes(novas, links)
        if primeira:
            blocos.append(f"📋 Já estavam visíveis, em fontes que li pela primeira vez ({len(primeira)})")
            blocos += _secoes(primeira, links, por_empresa=3, compacto=True)
    if avisos:
        blocos.append(f"⚠️ Não consigo ler há {FALHAS_PARA_AVISAR} execuções seguidas: {', '.join(avisos)}. "
                      "Vê o log da execução no GitHub.")
    if not blocos:
        return None
    if novas or primeira:
        blocos.append("💻 parece informática · 📌 outra área ou por confirmar (vê na mesma) · ⭐ empresa da lista")
    return "\n\n".join(blocos)


def resumo_markdown(canais: list[Canal]) -> str:
    linhas = ["# Vigilante: fontes desta execução", "",
              "| Fonte | Tipo | Lidas | Relevantes | Estado |", "|---|---|---|---|---|"]
    ordem = {"pagina": 1, "sitemap": 2, "noticias": 3}
    for c in sorted(canais, key=lambda x: (ordem.get(x.tipo, 0), x.erro is None, x.pendente is not None, x.nome.lower())):
        if c.erro:
            situacao = "❌ " + c.erro.replace("|", "/")[:120]
        elif c.pendente:
            situacao = "⏳ " + c.pendente
        else:
            situacao = "✅"
        lidas = "-" if c.pendente else c.lidas
        linhas.append(f"| {c.nome} | {c.tipo} | {lidas} | {len(c.itens)} | {situacao} |")
    return "\n".join(linhas) + "\n"


# ---------------------------------------------------------------- modos

def modo_teste() -> None:
    agora = datetime.now(LISBOA).strftime("%d/%m/%Y %H:%M")
    n = len(carregar_empresas())
    texto = (
        "✅ Vigilante de estágios: teste\n"
        f"Enviado a {agora} (hora de Lisboa).\n"
        f"Lista de empresas carregada: {n} empresas.\n"
        "Se estás a ler isto, o GitHub Actions e as notificações estão a funcionar."
    )
    enviar(texto, assunto="Vigilante de estágios: teste")


def recolher(fontes: list[dict], gerais: dict, hoje: date) -> list[Canal]:
    agregadores = gerais.get("agregadores", [])
    print(f"1/3 Plataformas: {len(fontes)} empresas...", flush=True)
    with ThreadPoolExecutor(max_workers=TRABALHADORES) as executor:
        canais = list(executor.map(ler_plataforma, fontes))
    canais += [ler_itjobs_canal(a) for a in agregadores if a["tipo"] == "itjobs"]

    # Empresas sem leitor (ou cujo leitor disse "não suportado") passam para a vigia de páginas.
    para_vigiar = [c.fonte for c in canais if c.pendente and c.tipo != "itjobs"
                   and c.fonte.get("vigiar_pagina", True)]
    vigiadas = {f["nome"] for f in para_vigiar}
    for c in canais:
        if c.pendente and c.fonte.get("vigiar_pagina") is False:
            c.pendente = "só notícias (vigiar_pagina: false)"
    # Na tabela, a linha "⏳ sem leitor" é substituída pela linha da vigia de página.
    canais = [c for c in canais if not (c.pendente and c.nome in vigiadas and c.tipo != "itjobs")]
    para_vigiar += [dict(a, prioridade=a.get("prioridade", 3)) for a in agregadores if a["tipo"] == "pagina"]
    print(f"2/3 Páginas: {len(para_vigiar)} (browser)...", flush=True)
    canais += vigiar_paginas(para_vigiar)
    canais += [ler_sitemap_canal(a) for a in agregadores if a["tipo"] == "sitemap"]

    print("3/3 Notícias...", flush=True)
    canais.append(ler_noticias_canal(gerais.get("noticias", []), fontes, hoje))
    for c in canais:
        situacao = f"ERRO {c.erro}" if c.erro else (f"({c.pendente})" if c.pendente else f"{c.lidas} lidas, {len(c.itens)} relevantes")
        print(f"  [{c.tipo}] {c.nome}: {situacao}", flush=True)
    return canais


def modo_normal(simular: bool = False, hoje: date | None = None, fontes: list[dict] | None = None,
                gerais: dict | None = None, forcar_resumo: bool = False) -> str | None:
    hoje = hoje or datetime.now(LISBOA).date()
    fontes = carregar_fontes() if fontes is None else fontes
    gerais = carregar_gerais() if gerais is None else gerais
    canais = recolher(fontes, gerais, hoje)

    estado = memoria.carregar()
    inicial = estado is None
    if inicial:
        estado = memoria.novo(hoje)

    avisos, todos, chaves_primeira, chaves_silenciosas = [], [], set(), set()
    for c in canais:
        if c.pendente:
            continue
        novo = memoria.canal_novo(estado, c.chave, c.assinatura)
        falhas = memoria.registar_fonte(estado, c.chave, hoje, c.erro, c.lidas, c.assinatura)
        if falhas == FALHAS_PARA_AVISAR:
            avisos.append(c.chave)
        if c.erro:
            continue
        todos += c.itens
        if inicial or novo:
            for i in c.itens:
                # Na 1.ª leitura, sitemaps e notícias ficam calados, exceto notícias com ⭐.
                silencioso = c.silencioso and not getattr(i, "empresa_citada", "")
                (chaves_silenciosas if silencioso else chaves_primeira).add(i.chave)

    nunca_vistas = memoria.registar_ofertas(estado, todos, hoje)
    memoria.marcar_primeira_leitura(estado, {i.chave for i in nunca_vistas} & (chaves_primeira | chaves_silenciosas))
    memoria.esquecer_antigas(estado, hoje)
    memoria.registar_execucao(estado, hoje)
    primeira = [i for i in nunca_vistas if i.chave in chaves_primeira]
    novas = [i for i in nunca_vistas if i.chave not in chaves_primeira and i.chave not in chaves_silenciosas]

    resumo = {
        "plataformas": sum(1 for c in canais if c.tipo not in ("pagina", "sitemap", "noticias", "itjobs")
                           and not c.pendente and not c.erro),
        "erros": sum(1 for c in canais if c.tipo not in ("pagina", "sitemap", "noticias") and c.erro),
        "paginas": sum(1 for c in canais if c.tipo == "pagina" and not c.pendente and not c.erro),
        "sem_acesso": sum(1 for c in canais if c.tipo == "pagina" and (c.pendente or c.erro))
                      + sum(1 for f in fontes if f.get("vigiar_pagina") is False),
    }
    links = {f["nome"]: f.get("carreiras") or f.get("site", "") for f in fontes}
    links.update({a["nome"]: a.get("url", "") for a in gerais.get("agregadores", [])})
    texto = montar_mensagem(novas, primeira, hoje, inicial, avisos, resumo, links)

    # Resumo de sexta (na simulação aparece sempre, para o poderes ver em qualquer dia).
    resumo_semanal = None
    if not inicial and (simular or forcar_resumo or semanal.e_dia_de_resumo(hoje)):
        resumo_semanal = semanal.montar_resumo(estado, todos, canais, hoje, links)

    md = resumo_markdown(canais)
    print(f"\n{len(todos)} itens relevantes; {len(novas)} novos; {len(primeira)} de fontes lidas pela primeira vez.")
    print("\n--- mensagem ---\n" + (texto or "(nada a enviar hoje)") + "\n----------------")
    if resumo_semanal:
        print("\n--- resumo semanal ---\n" + resumo_semanal + "\n----------------")
    if os.environ.get("GITHUB_STEP_SUMMARY"):  # aparece na página da execução no GitHub
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md + "\n## Mensagem\n\n```\n" + (texto or "(nada a enviar hoje)") + "\n```\n")
            if resumo_semanal:
                f.write("\n## Resumo semanal\n\n```\n" + resumo_semanal + "\n```\n")

    if simular:
        print("[simulação] mensagens não enviadas e estado não guardado.")
        return texto
    if texto:
        enviar(texto, assunto="Vigilante de estágios: novidades")
    if resumo_semanal:
        enviar(resumo_semanal, assunto="Vigilante de estágios: resumo semanal")
    memoria.guardar(estado)  # só depois de enviar com sucesso
    return texto


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vigilante de estágios")
    parser.add_argument("--modo", choices=["teste", "normal", "simulacao", "resumo"], default="normal",
                        help="resumo = normal + envia já o resumo semanal (sem esperar pela sexta)")
    args = parser.parse_args(argv)
    if args.modo == "teste":
        modo_teste()
    else:
        modo_normal(simular=args.modo == "simulacao", forcar_resumo=args.modo == "resumo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
