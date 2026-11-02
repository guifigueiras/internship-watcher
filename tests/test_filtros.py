import pytest

from vigilante.filtros import classificar_local, e_estagio, e_informatica, normalizar


def test_normalizar():
    assert normalizar("Estágio de VERÃO") == "estagio de verao"


@pytest.mark.parametrize("titulo, tipo, esperado", [
    ("Software Engineer Intern", "", True),
    ("Summer Internship Programme", "", True),
    ("Estágio de Verão - Sistemas de Informação", "", True),
    ("Estagiário(a) de Dados", "", True),
    ("Trainee Program 2027", "", True),
    ("Technology Summer Camp", "", True),
    ("Graduate Software Engineer", "", True),
    ("Programa Jovem Talento", "", True),
    ("Backend Developer", "Intern", True),                 # tipo de contrato da Lever
    ("Senior Backend Engineer", "", False),
    ("Internal Auditor", "", False),                       # "internal" não é "intern"
    ("International Sales Manager", "", False),
    ("Student Recruitment Manager", "", False),            # palavra fraca + cargo sénior
    ("Graduate Recruiter", "", False),
])
def test_e_estagio(titulo, tipo, esperado):
    assert e_estagio(titulo, tipo) is esperado


@pytest.mark.parametrize("local, esperado", [
    ("Lisbon, Portugal", "lisboa"),
    ("Lisboa", "lisboa"),
    ("Oeiras, Lisboa, PT", "lisboa"),
    ("Madrid, Spain · Lisbon, Portugal", "lisboa"),
    ("Porto Salvo", "lisboa"),                 # é Oeiras, não Porto
    ("Linda-a-Velha", "lisboa"),
    ("Portugal", "portugal"),
    ("PT", "portugal"),
    ("", "desconhecido"),
    ("3 Locations", "desconhecido"),
    ("Remote - Europe", "remoto"),
    ("Porto, Portugal", None),                 # outra cidade: excluído por omissão
    ("Coimbra", None),
    ("Madrid, Spain", None),
    ("Remote - US", None),
    ("New York, NY", None),
])
def test_classificar_local(local, esperado):
    assert classificar_local(local) == esperado


@pytest.mark.parametrize("texto, esperado", [
    ("Software Engineer Intern", True),
    ("Data Analyst Trainee", True),
    ("Estágio em Engenharia Informática", True),
    ("IT Support Intern", True),
    ("Youth Internship Technology", True),
    ("Cybersecurity Summer Intern", True),
    ("Marketing Intern", False),
    ("Finance Summer Internship", False),
    ("Mechanical Engineering Intern", False),
])
def test_e_informatica(texto, esperado):
    assert e_informatica(texto) is esperado
