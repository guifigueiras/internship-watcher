"""
filtros.py: decide se uma oferta interessa.

Três perguntas, todas feitas sobre texto "normalizado" (minúsculas e sem acentos,
para "Estágio", "estagio" e "ESTÁGIO" serem iguais):

  1. e_estagio()        parece um estágio / programa para estudantes?
  2. classificar_local() é em Lisboa (ou pode ser)?
  3. e_informatica()    parece da área de informática? (só para pôr 💻; não exclui nada)

Expressões regulares usadas aqui:
  \\b       fronteira de palavra: \\bintern\\b apanha "intern" mas não "internal"
  (?:a|b)  "a ou b", sem guardar o grupo
  s?       o "s" é opcional (intern / interns)
"""
from __future__ import annotations

import re
import unicodedata

# Se True, também envia estágios noutras cidades portuguesas (Porto, Braga...).
INCLUIR_RESTO_PORTUGAL = False


def normalizar(texto: str | None) -> str:
    """'Estágio de Verão' -> 'estagio de verao'."""
    decomposto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


def _regex(padroes: list[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(padroes) + r")\b")


# ---------------------------------------------------------------- 1. é estágio?

# Palavras fortes: chegam sozinhas.
ESTAGIO_FORTE = _regex([
    r"intern(?:s|ship|ships)?", r"estagi(?:o|os|ario|aria|arios|arias)", r"trainee(?:s|ship)?",
    r"summer (?:intern\w*|camp|program\w*|experience|boost|break|academy|school|trainee\w*)", r"verao",
    r"apprentice(?:ship)?s?", r"working student", r"werkstudent",
])
# Palavras fracas: valem só se o título não for de um cargo sénior
# (ex.: "Student Recruitment Manager" não é um estágio).
ESTAGIO_FRACO = _regex([
    r"graduates?", r"recem[- ]licenciad[oa]s?", r"recem[- ]formad[oa]s?", r"early[- ]careers?",
    r"students?", r"estudantes?", r"jovens? talentos?", r"young talents?", r"academy", r"academia",
    r"bootcamp", r"summer", r"universit\w+", r"entry[- ]level", r"junior program\w*", r"programa jovem",
])
SENIOR = _regex([
    r"senior", r"manager", r"gestor[a]?", r"head", r"lead", r"director", r"diretor[a]?", r"coordinator",
    r"coordenador[a]?", r"principal", r"chief", r"vp", r"recruiter", r"recrutador[a]?",
])


# Frases de programas para estudantes, usadas nas páginas e notícias (onde as palavras
# fracas sozinhas, como "students" num menu, dariam demasiado ruído).
PROGRAMA = _regex([
    r"graduate (?:program\w*|scheme|academy)", r"programas? de (?:trainees?|graduados|talento\w*|jovens\w*)",
    r"early[- ]careers?", r"jovens? talentos?", r"young talents?", r"recem[- ]licenciad[oa]s?",
    r"talento jovem", r"summer \w+ program\w*",
])
# Notícias de desporto usam "estágio" no sentido de concentração da equipa.
DESPORTO = _regex([
    r"futebol", r"selecao", r"selecionador", r"benfica", r"sporting", r"fc porto", r"braga", r"plantel",
    r"treinador", r"jogador\w*", r"pre-epoca", r"pre-temporada", r"liga", r"mundial", r"euro \d{4}",
    r"campeonato", r"atleta\w*", r"clube", r"futsal", r"andebol", r"basquetebol", r"voleibol", r"hoquei",
])
CONTEXTO_EMPREGO = _regex([
    r"candidatura\w*", r"vagas?", r"programas?", r"universitari\w*", r"estudantes?", r"recruta\w*",
    r"jovens?", r"talento\w*", r"empresa\w*", r"emprego", r"remunerad\w*", r"carreiras?",
    r"internships?", r"interns?", r"trainees?", r"summer", r"verao",
    # "estágios" (plural) e "estagiários" quase sempre são estágios profissionais;
    # "estágio" (singular) também pode ser "fase" ("estágio avançado").
    r"estagios", r"estagiari\w+",
])


def e_programa(texto: str) -> bool:
    """Para textos de páginas: só palavras fortes ou frases de programa."""
    t = normalizar(texto)
    return bool(ESTAGIO_FORTE.search(t) or PROGRAMA.search(t))


def noticia_relevante(titulo: str) -> bool:
    t = normalizar(titulo)
    return e_programa(titulo) and bool(CONTEXTO_EMPREGO.search(t)) and not DESPORTO.search(t)


def e_estagio(titulo: str, tipo_contrato: str = "") -> bool:
    titulo_n = normalizar(titulo)
    if ESTAGIO_FORTE.search(titulo_n):
        return True
    # Algumas plataformas dizem o tipo de contrato (Lever "Intern", Workable "Internship").
    if ESTAGIO_FORTE.search(normalizar(tipo_contrato)):
        return True
    return bool(ESTAGIO_FRACO.search(titulo_n)) and not SENIOR.search(titulo_n)


# ---------------------------------------------------------------- 2. onde é?

LISBOA_REGIAO = _regex([
    "lisboa", "lisbon", "lisbonne", "lissabon", "oeiras", "amadora", "sintra", "cascais", "carcavelos",
    "estoril", "almada", "loures", "odivelas", "linda-a-velha", "linda a velha", "paco de arcos",
    "porto salvo", "alfragide", "taguspark", "tagus park", "barcarena", "queluz", "seixal", "barreiro",
    "setubal", "palmela", "montijo", "vila franca de xira", "sacavem", "parque das nacoes", "mafra",
    "alges", "miraflores", "benfica", "moscavide", "agualva", "cacem",
])
OUTRAS_CIDADES = _regex([
    "porto", "oporto", "braga", "coimbra", "aveiro", "leiria", "matosinhos", "maia", "viseu", "funchal",
    "madeira", "acores", "azores", "ponta delgada", "evora", "faro", "guimaraes", "vila nova de gaia",
    "gaia", "covilha", "castelo branco", "viana do castelo", "santarem", "tomar", "fundao", "famalicao",
])
PORTUGAL = re.compile(r"\bportugal\b|\bprt\b|(?:^|[\s,(/-])pt(?:$|[\s,)/-])")
REMOTO = re.compile(r"\bremot[eo]\b|\bremotely\b|teletrabalho|\banywhere\b")
EUROPA = re.compile(r"\beurope\b|\beuropa\b|\bemea\b|\beu\b|\bportugal\b")


def classificar_local(local: str | None) -> str | None:
    """Devolve a zona da oferta, ou None se for claramente fora (ex.: só Madrid).

    "lisboa"       uma das localizações é na região de Lisboa
    "portugal"     diz só "Portugal", sem cidade (pode ser Lisboa)
    "desconhecido" a plataforma não indica o local
    "remoto"       remoto na Europa / Portugal
    "outra_cidade" noutra cidade portuguesa (só aceite se INCLUIR_RESTO_PORTUGAL)
    """
    texto = normalizar(local).strip()
    if not texto:
        return "desconhecido"
    if LISBOA_REGIAO.search(texto):
        return "lisboa"
    if OUTRAS_CIDADES.search(texto):
        return "outra_cidade" if INCLUIR_RESTO_PORTUGAL else None
    if REMOTO.search(texto) and EUROPA.search(texto):
        return "remoto"
    if PORTUGAL.search(texto):
        return "portugal"
    # "3 Locations" (Workday) quando não foi possível ver o detalhe
    if re.fullmatch(r"\d+\s+(?:locations|locais|localizacoes)", texto):
        return "desconhecido"
    return None


ZONAS_TEXTO = {
    "lisboa": "Lisboa",
    "portugal": "Portugal",
    "desconhecido": "local não indicado",
    "remoto": "remoto",
    "outra_cidade": "fora de Lisboa",
}


# ---------------------------------------------------------------- 3. é informática?

INFORMATICA = _regex([
    r"software", r"developers?", r"desenvolvedor\w*", r"desenvolvimento", r"programador\w*", r"programming",
    r"programacao", r"engenharia informatica", r"informatica", r"computer science", r"computacao", r"computing",
    r"data", r"dados", r"data science", r"ciencia de dados", r"ai", r"ia", r"inteligencia artificial",
    r"artificial intelligence", r"machine learning", r"ml", r"cloud", r"devops", r"sre", r"back[- ]?end",
    r"front[- ]?end", r"full[- ]?stack", r"it", r"ti", r"tecnologias? de informacao", r"technology",
    r"tecnologias?", r"tech", r"cyber\w*", r"ciberseguranca", r"seguranca informatica", r"security",
    r"sistemas de informacao", r"information systems", r"systems", r"sistemas", r"networks?", r"networking",
    r"redes", r"qa", r"testing", r"tester", r"automation", r"automacao", r"analytics", r"bi",
    r"business intelligence", r"sap", r"outsystems", r"java", r"python", r"web", r"mobile", r"ios",
    r"android", r"infraestrutura", r"infrastructure", r"databases?", r"sql", r"api", r"robotics",
    r"robotica",
    # "engineer" sozinho fica de fora de propósito: "Mechanical Engineering Intern" não é informática.
])


def e_informatica(*textos: str) -> bool:
    return bool(INFORMATICA.search(normalizar(" ".join(t or "" for t in textos))))


# ---------------------------------------------------------------- 4. textos de páginas

ESTRANGEIRO = _regex([
    "spain", "espanha", "madrid", "barcelona", "france", "franca", "paris", "germany", "alemanha", "berlin",
    "munich", "frankfurt", "united kingdom", "reino unido", "london", "londres", "ireland", "dublin",
    "belgium", "belgica", "brussels", "bruxelas", "netherlands", "amsterdam", "italy", "italia", "milan",
    "rome", "poland", "warsaw", "india", "bangalore", "bengaluru", "pune", "hyderabad", "philippines",
    "quezon city", "manila", "united states", "estados unidos", "new york", "san francisco", "canada",
    "toronto", "markham", "mexico", "mexico city", "brazil", "brasil", "sao paulo", "dubai", "singapore",
    "tokyo", "japan", "seoul", "korea", "china", "shanghai", "australia", "sydney", "denmark", "copenhagen",
    "sweden", "stockholm", "norway", "oslo", "finland", "helsinki", "switzerland", "zurich", "geneva",
    "austria", "vienna", "czech", "prague", "romania", "bucharest", "morocco", "egypt", "south africa",
])
# Códigos de país no fim de um local ("University Park, US"); só em maiúsculas.
CODIGO_PAIS = re.compile(r",\s*(?:US|USA|UK|CA|PH|IN|ES|FR|DE|IT|NL|BE|PL|BR|MX|AE|SG|JP|KR|CN|AU|IE|CH|AT|DK|SE|NO|FI|CZ|RO|HU|GR|TR|IL|ZA|AR|CL|CO|MA)\b")


def local_estrangeiro(texto: str) -> bool:
    """True se o texto indica um local fora de Portugal e nenhum em Portugal
    (ex.: "Software Engineering Intern 2027 Internship Markham, CA")."""
    t = normalizar(texto)
    if LISBOA_REGIAO.search(t) or OUTRAS_CIDADES.search(t) or PORTUGAL.search(t):
        return False
    return bool(ESTRANGEIRO.search(t) or CODIGO_PAIS.search(texto or ""))


ABERTAS = re.compile(r"candidaturas? (?:ja )?(?:estao |vao estar )?abert\w*|inscricoes (?:estao )?abertas|"
                     r"applications? (?:are |now )?open|open for applications|apply now|candidata-te ja|"
                     r"candidaturas ate|candidaturas a decorrer|accepting applications")
ENCERRADAS = re.compile(r"encerrad\w*|encerraram|fechad\w*|closed|terminad\w*|proximo ano|next year|"
                        r"proxima edicao|next edition|brevemente|coming soon|stay tuned|fica atent\w*|mantem-te atent\w*")


def diz_abertas(texto: str) -> bool:
    t = normalizar(texto)
    return bool(ABERTAS.search(t)) and not ENCERRADAS.search(t)
