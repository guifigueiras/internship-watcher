"""
config.py: caminhos dos ficheiros e leitura da configuração.

Há dois ficheiros de configuração:
  config/empresas.yaml     escrito à mão: a lista de empresas (e links que já se sabem)
  config/plataformas.yaml  gerado pelo detetor: página de carreiras e plataforma de cada empresa

carregar_fontes() junta os dois. O que estiver escrito à mão em empresas.yaml ganha sempre.
"""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

LISBOA = ZoneInfo("Europe/Lisbon")
RAIZ = Path(__file__).resolve().parent.parent          # pasta do repositório
FICHEIRO_EMPRESAS = RAIZ / "config" / "empresas.yaml"
FICHEIRO_PLATAFORMAS = RAIZ / "config" / "plataformas.yaml"
FICHEIRO_ESTADO = RAIZ / "data" / "estado.json"
FICHEIRO_FONTES = RAIZ / "config" / "fontes.yaml"
TIPOS_AGREGADOR = ("pagina", "sitemap", "itjobs")


def carregar_gerais(caminho: Path = FICHEIRO_FONTES) -> dict:
    """Lê config/fontes.yaml: {"agregadores": [...], "noticias": [pesquisas]}."""
    with open(caminho, encoding="utf-8") as f:
        dados = yaml.safe_load(f) or {}
    agregadores = dados.get("agregadores") or []
    problemas = []
    for a in agregadores:
        if a.get("tipo") not in TIPOS_AGREGADOR:
            problemas.append(f"{a.get('nome', '?')}: tipo tem de ser {', '.join(TIPOS_AGREGADOR)}")
        if a.get("tipo") in ("pagina", "sitemap") and not str(a.get("url", "")).startswith("http"):
            problemas.append(f"{a.get('nome', '?')}: falta url")
        if a.get("tipo") == "sitemap" and not a.get("filtro"):
            problemas.append(f"{a.get('nome', '?')}: sitemap precisa de filtro")
    if problemas:
        raise ValueError("Erros em config/fontes.yaml:\n  - " + "\n  - ".join(problemas))
    return {"agregadores": agregadores, "noticias": [str(q) for q in dados.get("noticias") or []]}

CAMPOS_OBRIGATORIOS = ("nome", "setor", "site", "prioridade")


def carregar_empresas(caminho: Path = FICHEIRO_EMPRESAS) -> list[dict]:
    """Lê a lista de empresas (por omissão config/empresas.yaml) e verifica os campos obrigatórios."""
    with open(caminho, encoding="utf-8") as f:
        dados = yaml.safe_load(f) or {}
    empresas = dados.get("empresas", [])
    problemas = []
    nomes_vistos = set()
    for i, emp in enumerate(empresas, start=1):
        em_falta = [c for c in CAMPOS_OBRIGATORIOS if not emp.get(c)]
        if em_falta:
            problemas.append(f"entrada {i} ({emp.get('nome', '?')}): falta {', '.join(em_falta)}")
        if emp.get("prioridade") not in (1, 2, 3):
            problemas.append(f"{emp.get('nome', i)}: prioridade tem de ser 1, 2 ou 3")
        # Campos opcionais escritos à mão: carreiras (endereço) e plataforma ({tipo, id}).
        if emp.get("carreiras") and not str(emp["carreiras"]).startswith(("http://", "https://")):
            problemas.append(f"{emp.get('nome')}: carreiras tem de começar por http:// ou https://")
        plataforma = emp.get("plataforma")
        if plataforma is not None and not (isinstance(plataforma, dict) and plataforma.get("tipo")):
            problemas.append(f"{emp.get('nome')}: plataforma tem de ser {{tipo: ..., id: ...}}")
        if "vigiar_pagina" in emp and not isinstance(emp["vigiar_pagina"], bool):
            problemas.append(f"{emp.get('nome')}: vigiar_pagina tem de ser true ou false")
        nome = (emp.get("nome") or "").lower()
        if nome in nomes_vistos:
            problemas.append(f"{emp.get('nome')}: aparece repetida")
        nomes_vistos.add(nome)
    if problemas:
        raise ValueError("Erros em config/empresas.yaml:\n  - " + "\n  - ".join(problemas))
    return empresas


def carregar_plataformas(caminho: Path = FICHEIRO_PLATAFORMAS) -> dict:
    """Lê o resultado do detetor: {nome da empresa: {estado, carreiras, plataforma, ...}}."""
    if not caminho.exists():
        return {}
    with open(caminho, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("empresas", {}) or {}


def carregar_fontes(empresas: list[dict] | None = None, plataformas: dict | None = None) -> list[dict]:
    """Junta empresas.yaml com plataformas.yaml. Os campos escritos à mão têm prioridade."""
    empresas = carregar_empresas() if empresas is None else empresas
    plataformas = carregar_plataformas() if plataformas is None else plataformas
    fontes = []
    for emp in empresas:
        detetado = plataformas.get(emp["nome"]) or {}
        fonte = dict(emp)
        fonte["carreiras"] = emp.get("carreiras") or detetado.get("carreiras")
        # "plataforma: null" escrito à mão também ganha (obriga a vigiar a página).
        fonte["plataforma"] = emp["plataforma"] if "plataforma" in emp else detetado.get("plataforma")
        fonte["estado_detecao"] = detetado.get("estado")
        fontes.append(fonte)
    return fontes
