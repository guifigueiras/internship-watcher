"""
estado.py: a "memória" do vigilante, guardada em data/estado.json.

O GitHub Actions começa cada execução numa máquina vazia: tudo o que o programa
precisa de lembrar entre execuções tem de estar num ficheiro do repositório.
Depois de cada execução normal, o workflow faz commit deste ficheiro.

Estrutura (JSON):
{
  "versao": 1,
  "criado_em": "2026-10-30",
  "ofertas": {                       # só as ofertas que passaram nos filtros
    "greenhouse:Feedzai:123": {"empresa": ..., "titulo": ..., "url": ...,
                               "visto_em": "2026-10-30", "ultimo_visto": "2026-11-02"}
  },
  "fontes": {                        # saúde de cada empresa/fonte
    "Feedzai": {"ultimo_ok": "2026-11-02", "falhas_seguidas": 0, "ultimo_erro": "", "ofertas_lidas": 57}
  }
}
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from vigilante.config import FICHEIRO_ESTADO

# Ofertas que não aparecem há este tempo são apagadas (o ficheiro não cresce para sempre).
# Se uma oferta desaparecer e voltar antes disso, não é avisada outra vez.
DIAS_ESQUECER = 180


def carregar(caminho: Path | None = None) -> dict | None:
    """Devolve o estado, ou None se ainda não existe (primeira execução)."""
    caminho = caminho or FICHEIRO_ESTADO
    if not caminho.exists():
        return None
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def novo(hoje: date) -> dict:
    return {"versao": 1, "criado_em": hoje.isoformat(), "ofertas": {}, "fontes": {}}


def guardar(estado: dict, caminho: Path | None = None) -> None:
    caminho = caminho or FICHEIRO_ESTADO
    caminho.parent.mkdir(parents=True, exist_ok=True)
    # Escrevemos primeiro num ficheiro temporário e só depois substituímos o verdadeiro:
    # se o programa for interrompido a meio, o estado antigo fica intacto.
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(estado, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    temporario.replace(caminho)


def registar_ofertas(estado: dict, ofertas: list, hoje: date) -> list:
    """Marca as ofertas (ou textos de páginas, ou notícias) como vistas hoje e devolve as
    que nunca tinham sido vistas."""
    novas = []
    for oferta in ofertas:
        registo = estado["ofertas"].get(oferta.chave)
        if registo is None:
            novas.append(oferta)
            registo = estado["ofertas"][oferta.chave] = {
                "empresa": oferta.empresa, "titulo": oferta.titulo, "url": oferta.url,
                "local": oferta.local, "visto_em": hoje.isoformat(),
            }
        registo["ultimo_visto"] = hoje.isoformat()
        registo["fonte"] = oferta.fonte                      # greenhouse, pagina, noticia, ...
        registo["informatica"] = bool(getattr(oferta, "informatica", False))
    return novas


def marcar_primeira_leitura(estado: dict, chaves: set) -> None:
    """Itens que já existiam quando a fonte foi lida pela 1.ª vez: não contam como
    "novidades da semana" no resumo de sexta."""
    for chave in chaves:
        if chave in estado["ofertas"]:
            estado["ofertas"][chave]["primeira_leitura"] = True


def registar_execucao(estado: dict, hoje: date) -> None:
    """Guarda as datas das últimas execuções (para o resumo dizer quantos dias correu)."""
    datas = [d for d in estado.get("execucoes", []) if d != hoje.isoformat()] + [hoje.isoformat()]
    estado["execucoes"] = datas[-60:]


def esquecer_antigas(estado: dict, hoje: date) -> int:
    limite = (hoje - timedelta(days=DIAS_ESQUECER)).isoformat()
    antigas = [k for k, r in estado["ofertas"].items() if r.get("ultimo_visto", "") < limite]
    for chave in antigas:
        del estado["ofertas"][chave]
    return len(antigas)


def canal_novo(estado: dict, chave: str, assinatura: str) -> bool:
    """Um canal é "novo" se nunca foi lido com sucesso, ou se a configuração mudou desde a
    última leitura (ex.: a EDP passou do site principal para jobs.edp.com). O que se lê
    num canal novo já existia antes: não é novidade."""
    registo = estado["fontes"].get(chave, {})
    return not registo.get("ultimo_ok") or registo.get("assinatura") != assinatura


def registar_fonte(estado: dict, empresa: str, hoje: date, erro: str | None = None, lidas: int = 0,
                   assinatura: str = "") -> int:
    """Atualiza a saúde da fonte. Devolve o número de falhas seguidas (0 se correu bem)."""
    registo = estado["fontes"].setdefault(empresa, {"falhas_seguidas": 0})
    if erro:
        registo["falhas_seguidas"] = registo.get("falhas_seguidas", 0) + 1
        registo["ultimo_erro"] = erro[:300]
    else:
        registo["falhas_seguidas"] = 0
        registo["ultimo_ok"] = hoje.isoformat()
        registo["ultimo_erro"] = ""
        registo["ofertas_lidas"] = lidas
        registo["assinatura"] = assinatura   # só depois de uma leitura com sucesso
    return registo["falhas_seguidas"]
