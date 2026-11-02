"""
semanal.py: o resumo de sexta-feira.

Enquanto a mensagem diária só diz o que é NOVO, o resumo semanal diz o que está ABERTO
e como está o próprio bot. Serve também de "sinal de vida": se numa sexta não chegar
nada, é porque o vigilante parou (ver o separador Actions no GitHub).

Secções:
  💼 Estágios abertos agora      ofertas das plataformas vistas na execução de hoje
  📣 Páginas a dizer "candidaturas abertas"   textos de páginas vistos hoje
  🆕 Novidades desta semana      contagem do que foi avisado nos últimos 7 dias
  🔧 Fontes com problemas         canais que falharam na última leitura, e sites sem acesso
"""
from __future__ import annotations

from datetime import date, timedelta

from vigilante.filtros import diz_abertas, normalizar

DIA_DA_SEMANA = 4          # 0 = segunda ... 4 = sexta
TITULOS_POR_EMPRESA = 2
MAX_PAGINAS_ABERTAS = 10


def e_dia_de_resumo(hoje: date) -> bool:
    return hoje.weekday() == DIA_DA_SEMANA


def _n(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _curto(texto: str, n: int = 60) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


def montar_resumo(estado: dict, hoje_itens: list, canais: list, hoje: date, links: dict[str, str]) -> str:
    """`hoje_itens` = itens relevantes lidos nesta execução; `canais` = resultado de main.recolher()."""
    blocos = [f"📅 Resumo semanal ({hoje:%d/%m/%Y})"]

    # Sinal de vida: quantos dias correu nos últimos 7.
    semana = {(hoje - timedelta(days=d)).isoformat() for d in range(7)}
    correu = len(semana & set(estado.get("execucoes", [])))
    blocos[0] += f"\n✅ O vigilante correu {correu} de 7 dias esta semana."
    if correu < 6:
        blocos[0] += " (O GitHub às vezes salta execuções; se forem muitas, vê o separador Actions.)"

    # 💼 Estágios abertos agora
    ofertas = [i for i in hoje_itens if i.fonte not in ("pagina", "sitemap", "noticia")]
    por_empresa: dict[str, list] = {}
    for o in sorted(ofertas, key=lambda o: (not o.informatica, o.prioridade, normalizar(o.empresa))):
        por_empresa.setdefault(o.empresa, []).append(o)
    if por_empresa:
        linhas = [f"💼 Estágios abertos agora: {len(ofertas)} em {_n(len(por_empresa), 'empresa', 'empresas')}"]
        for empresa, grupo in por_empresa.items():
            icone = "💻" if any(o.informatica for o in grupo) else "📌"
            titulos = " · ".join(_curto(o.titulo) for o in grupo[:TITULOS_POR_EMPRESA])
            mais = f" · +{len(grupo) - TITULOS_POR_EMPRESA}" if len(grupo) > TITULOS_POR_EMPRESA else ""
            link = links.get(empresa) or grupo[0].url
            linhas.append(f"{icone} {empresa} ({len(grupo)}): {titulos}{mais}\n{link}")
        blocos.append("\n\n".join(linhas))
    else:
        blocos.append("💼 Nenhuma oferta de estágio aberta nas plataformas lidas.")

    # 📣 Páginas que dizem "candidaturas abertas"
    abertas = [i for i in hoje_itens if i.fonte == "pagina" and diz_abertas(i.titulo)]
    if abertas:
        linhas = [f"📣 Páginas que dizem \"candidaturas abertas\" ({len(abertas)})"]
        for i in abertas[:MAX_PAGINAS_ABERTAS]:
            linhas.append(f"{'💻' if i.informatica else '📌'} {i.empresa}: «{_curto(i.titulo, 120)}»\n{i.url}")
        if len(abertas) > MAX_PAGINAS_ABERTAS:
            linhas.append(f"… e mais {len(abertas) - MAX_PAGINAS_ABERTAS}")
        blocos.append("\n\n".join(linhas))

    # 🆕 Novidades da semana (o que foi avisado nas mensagens diárias)
    inicio = (hoje - timedelta(days=6)).isoformat()
    da_semana = [r for r in estado["ofertas"].values()
                 if r.get("visto_em", "") >= inicio and not r.get("primeira_leitura")]
    n_ofertas = sum(1 for r in da_semana if r.get("fonte") not in ("pagina", "sitemap", "noticia"))
    n_paginas = sum(1 for r in da_semana if r.get("fonte") in ("pagina", "sitemap"))
    n_noticias = sum(1 for r in da_semana if r.get("fonte") == "noticia")
    blocos.append(f"🆕 Novidades desta semana: {_n(n_ofertas, 'oferta', 'ofertas')} · "
                  f"{_n(n_paginas, 'texto', 'textos')} em páginas · {_n(n_noticias, 'notícia', 'notícias')}"
                  + ("" if da_semana else " (semana calma)"))

    # 🔧 Fontes com problemas
    avariadas = sorted((chave, r) for chave, r in estado["fontes"].items() if r.get("falhas_seguidas", 0) > 0)
    sem_acesso = sorted(c.nome for c in canais if c.pendente and ("robots" in c.pendente or "só notícias" in c.pendente))
    linhas = []
    if avariadas:
        linhas.append(f"🔧 Fontes com erro na última leitura ({len(avariadas)}):")
        linhas += [f"• {chave}: {r['falhas_seguidas']}× seguidas · {_curto(r.get('ultimo_erro', ''), 80)}"
                   for chave, r in avariadas]
    if sem_acesso:
        linhas.append(f"🚫 Sem acesso à página (robots.txt ou bloqueio), só por notícias: {', '.join(sem_acesso)}")
    blocos.append("\n".join(linhas) if linhas else "🔧 Todas as fontes leram sem erros.")

    proxima = hoje + timedelta(days=(DIA_DA_SEMANA - hoje.weekday()) % 7 or 7)
    blocos.append(f"Próximo resumo: {proxima:%d/%m}.")
    return "\n\n".join(blocos)
