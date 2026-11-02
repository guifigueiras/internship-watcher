"""
notify.py — envia mensagens para os canais configurados.

Cada canal só é usado se as respetivas variáveis de ambiente existirem.
No GitHub Actions essas variáveis vêm dos "Secrets" do repositório, por isso
podes ligar Telegram, Discord e/ou email sem mexer no código.

  Telegram : TELEGRAM_TOKEN + TELEGRAM_CHAT_ID
  Discord  : DISCORD_WEBHOOK_URL
  Email    : EMAIL_USER + EMAIL_APP_PASSWORD (+ EMAIL_TO, opcional)
"""
from __future__ import annotations

import os
import smtplib
import time
from email.message import EmailMessage

import requests

TIMEOUT = 20  # segundos que esperamos por cada pedido HTTP antes de desistir

# Limites oficiais: Telegram 4096 e Discord 2000 caracteres por mensagem.
# Usamos um pouco menos porque alguns emojis contam como 2 caracteres.
LIMITE_TELEGRAM = 4000
LIMITE_DISCORD = 1900

# Variáveis cujo valor nunca deve aparecer nos logs.
_SEGREDOS = ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL",
             "EMAIL_USER", "EMAIL_APP_PASSWORD", "EMAIL_TO")


def dividir_em_blocos(texto: str, limite: int) -> list[str]:
    """Parte `texto` em blocos com no máximo `limite` caracteres,
    cortando de preferência nas quebras de linha."""
    blocos: list[str] = []
    atual = ""
    for linha in texto.splitlines(keepends=True):
        # Uma linha sozinha maior do que o limite é cortada à força.
        while len(linha) > limite:
            if atual:
                blocos.append(atual)
                atual = ""
            blocos.append(linha[:limite])
            linha = linha[limite:]
        if len(atual) + len(linha) > limite:
            blocos.append(atual)
            atual = ""
        atual += linha
    if atual:
        blocos.append(atual)
    return [b.rstrip("\n") for b in blocos if b.strip()]


def _limpar_segredos(mensagem: str) -> str:
    """Substitui por *** qualquer segredo que apareça numa mensagem de erro."""
    for nome in _SEGREDOS:
        valor = os.environ.get(nome)
        if valor:
            mensagem = mensagem.replace(valor, "***")
    return mensagem


def _post_json(url: str, dados: dict, canal: str) -> None:
    """POST com JSON. Se o serviço pedir para abrandar (HTTP 429), espera e tenta de novo."""
    for _ in range(3):
        r = requests.post(url, json=dados, timeout=TIMEOUT)
        if r.status_code == 429:
            try:
                corpo = r.json()
            except ValueError:
                corpo = {}
            espera = corpo.get("retry_after") or corpo.get("parameters", {}).get("retry_after") or 5
            time.sleep(min(float(espera), 60))
            continue
        if r.status_code >= 300:
            raise RuntimeError(f"{canal} respondeu {r.status_code}: {r.text[:300]}")
        return
    raise RuntimeError(f"{canal}: demasiados pedidos (429), mesmo depois de esperar")


def _telegram(texto: str, assunto: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False  # canal não configurado
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for bloco in dividir_em_blocos(texto, LIMITE_TELEGRAM):
        _post_json(url, {
            "chat_id": chat_id,
            "text": bloco,
            "link_preview_options": {"is_disabled": True},  # sem pré-visualização de links
        }, "Telegram")
        time.sleep(0.5)
    return True


def _discord(texto: str, assunto: str) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return False
    for bloco in dividir_em_blocos(texto, LIMITE_DISCORD):
        # flags=4 -> SUPPRESS_EMBEDS: não gera cartões de pré-visualização para cada link
        _post_json(url, {"content": bloco, "flags": 4}, "Discord")
        time.sleep(0.5)
    return True


def _email(texto: str, assunto: str) -> bool:
    utilizador = os.environ.get("EMAIL_USER")
    palavra_passe = os.environ.get("EMAIL_APP_PASSWORD")
    if not (utilizador and palavra_passe):
        return False
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = utilizador
    msg["To"] = os.environ.get("EMAIL_TO") or utilizador
    msg.set_content(texto)
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    porta = int(os.environ.get("SMTP_PORT") or "465")
    with smtplib.SMTP_SSL(host, porta, timeout=TIMEOUT) as servidor:
        servidor.login(utilizador, palavra_passe)
        servidor.send_message(msg)
    return True


CANAIS = {"Telegram": _telegram, "Discord": _discord, "Email": _email}


def enviar(texto: str, assunto: str = "Vigilante de estágios") -> dict[str, str]:
    """Envia `texto` para todos os canais configurados.

    Falha (lança RuntimeError) se nenhum canal estiver configurado ou se todos
    falharem: assim a execução no GitHub fica a vermelho e o GitHub avisa-te por email.
    """
    resultados: dict[str, str] = {}
    for nome, funcao in CANAIS.items():
        try:
            if funcao(texto, assunto):
                resultados[nome] = "enviado"
        except Exception as erro:  # um canal a falhar não impede os outros
            resultados[nome] = f"falhou: {_limpar_segredos(str(erro))}"

    for nome, estado in resultados.items():
        print(f"[notificações] {nome}: {estado}")

    if not resultados:
        raise RuntimeError(
            "Nenhum canal configurado. Define TELEGRAM_TOKEN + TELEGRAM_CHAT_ID, "
            "ou DISCORD_WEBHOOK_URL, ou EMAIL_USER + EMAIL_APP_PASSWORD."
        )
    if "enviado" not in resultados.values():
        raise RuntimeError("Todos os canais configurados falharam (ver linhas acima).")
    return resultados
