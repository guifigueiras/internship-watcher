# Vigilante de estágios

Programa em Python que corre sozinho no GitHub Actions, procura programas de estágio
(sobretudo estágios de verão em Lisboa) e envia-te só o que é novo por Telegram,
Discord e/ou email. Não depende de nenhum modelo de IA: continua a funcionar sem o Claude.

## Estrutura

```
.github/workflows/vigilante.yml   agendamento diário + botão (simulacao / normal / teste)
.github/workflows/detetor.yml     botão para correr o detetor
.github/workflows/testes.yml      testes automáticos a cada git push
vigilante/main.py                 ponto de entrada: recolhe, filtra, compara, envia
vigilante/leitores.py             um leitor por plataforma (Greenhouse, Lever, Workday, ...)
vigilante/filtros.py              é estágio? é em Lisboa? é informática?
vigilante/estado.py               memória: o que já foi visto (data/estado.json)
vigilante/paginas.py              vigia de páginas de carreiras e agregadores (browser)
vigilante/noticias.py             notícias do Google News
vigilante/semanal.py              resumo de sexta-feira
vigilante/notify.py               envio de mensagens (Telegram, Discord, email)
vigilante/detetor.py              descobre página de carreiras + plataforma de cada empresa
vigilante/config.py, rede.py      leitura da configuração e pedidos HTTP com novas tentativas
config/empresas.yaml              lista de empresas vigiadas (edita à vontade)
config/plataformas.yaml           gerado pelo detetor (não editar à mão)
config/fontes.yaml                agregadores (Talent Portugal, Técnico, ITJobs) e pesquisas de notícias
data/estado.json                  gerado pelo vigilante (não editar à mão)
tests/                            testes automáticos (python -m pytest)
```

## Estado dos passos

- [x] **Passo 1** Esqueleto: agendamento + notificações + lista de empresas
- [x] **Passo 2** Detetor: descobre a página de carreiras e a plataforma de cada empresa
- [x] **Passo 3** Núcleo: leitores das plataformas, filtro, estado, primeira execução
- [x] **Passo 4** Páginas de carreiras genéricas, agregadores e notícias
- [x] **Passo 5** Resumo de sexta-feira + relatório de fontes avariadas
- [ ] **Passo 6** Registo de candidaturas com lembretes de follow-up
- [ ] **Passo 7** (opcional) servidor MCP

---

## Passo 1: configuração (cerca de 15 minutos)

### 1. Criar o repositório (privado)

GitHub → **New repository** → nome `vigilante-estagios` → **Private** → *Create*.

Privado porque: guarda a tua lista e, mais tarde, as tuas candidaturas; e em repositórios
públicos o GitHub desliga os agendamentos ao fim de 60 dias sem atividade.

### 2. Enviar estes ficheiros

```bash
git clone https://github.com/<o-teu-utilizador>/vigilante-estagios.git
cd vigilante-estagios
# copia para aqui o conteúdo do zip (incluindo a pasta .github, que pode estar oculta)
git add .
git commit -m "Passo 1: esqueleto do vigilante"
git push
```

Se o `git push` disser *refusing to allow a Personal Access Token to create or update workflow*,
o teu token não tem a permissão `workflow`. Usa o GitHub Desktop, SSH, ou cria um token com essa permissão.

### 3. Criar o canal de notificações

**Telegram (recomendado: notificação no telemóvel, mensagens longas)**

1. No Telegram, abre uma conversa com **@BotFather** e envia `/newbot`.
2. Escolhe um nome e um username terminado em `bot`. Recebes um **token** parecido com `123456789:AAH...`.
3. Abre o teu bot e envia-lhe uma mensagem qualquer (um bot não pode iniciar conversa contigo).
4. No browser, abre `https://api.telegram.org/bot<TOKEN>/getUpdates` (troca `<TOKEN>` pelo teu).
   Procura `"chat":{"id":` : o número a seguir é o teu **chat_id**.

**Discord (alternativa)**: no teu servidor → *Definições do servidor* → *Integrações* → *Webhooks*
→ *Novo webhook* → escolhe o canal → *Copiar URL do webhook*.

**Email (opcional)**: precisa de verificação em 2 passos no Gmail e de uma *app password*
(myaccount.google.com → Segurança → Palavras-passe de apps).

### 4. Guardar os segredos no GitHub

Repositório → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

| Nome do segredo        | Valor                                   |
|------------------------|-----------------------------------------|
| `TELEGRAM_TOKEN`       | token do BotFather                      |
| `TELEGRAM_CHAT_ID`     | o número do `getUpdates`                |
| `DISCORD_WEBHOOK_URL`  | (se usares Discord) URL do webhook      |
| `EMAIL_USER`           | (se usares email) o teu Gmail           |
| `EMAIL_APP_PASSWORD`   | (se usares email) a app password        |
| `EMAIL_TO`             | (opcional) destinatário, por omissão o próprio |

Basta configurar **um** canal. Os que não tiverem segredos são ignorados.

### 5. Testar

Repositório → **Actions** → *Vigilante de estágios* → **Run workflow** → modo `teste` → **Run**.
Em 1 a 2 minutos deves receber "✅ Vigilante de estágios: teste".

Se ficar a vermelho, abre a execução e vê a linha `[notificações] ...`: diz qual canal falhou e porquê.

## Passo 2: correr o detetor

1. Envia os ficheiros novos: `git add .`, `git commit -m "Passo 2: detetor"`, `git push`.
2. Repositório → **Actions** → *Detetor de páginas de carreiras* → **Run workflow**
   (deixa "Usar browser" ligado) → **Run**. Demora cerca de 10 a 25 minutos.
3. Recebes um resumo no Telegram. Na página da execução:
   - em cima aparece o relatório completo (tabela por categoria);
   - em baixo, em **Artifacts**, descarrega `relatorio-detetor` (zip com
     `relatorio_detetor.md`, `plataformas.yaml` e `detetor.json`).
4. Envia esse zip ao Claude para corrigir os links partidos e rever as plataformas.

Para voltar a testar só algumas empresas, escreve os nomes no campo "apenas"
(ex.: `OutSystems, Feedzai`).

| Ícone | Significado |
|---|---|
| ✅ | Plataforma de recrutamento detetada (ex.: Greenhouse). "N ofertas" = confirmada na API pública |
| 🏢 | Sistema próprio (Amazon, Google, Microsoft): tratado à parte |
| 📄 | Página de carreiras normal: será vigiada pelo texto (passo 4) |
| ⚠️ | O site responde mas não encontrei link de carreiras: falta o link certo |
| ❌ | Erro ou bloqueio: link errado ou site que recusa bots |

## Passo 3: pôr o vigilante a funcionar

1. Envia o código: `git add .`, `git commit -m "Passo 3: núcleo"`, `git push`.
   O workflow **Testes** corre sozinho e deve ficar verde.
2. **Actions → Detetor de páginas de carreiras → Run workflow** (browser ligado, "apenas" vazio).
   No fim, o próprio GitHub faz commit de `config/plataformas.yaml`.
3. No teu computador: **`git pull`**. Sempre que o bot faz commit (detetor ou estado diário),
   o teu repositório local fica atrasado; sem `git pull` antes de alterares algo, o próximo
   `git push` é recusado com "fetch first".
4. **Actions → Vigilante de estágios → Run workflow → `simulacao`**. Na página da execução vês
   a tabela das fontes e a mensagem que *seria* enviada. Nada é enviado nem guardado.
5. Se estiver bem: **Run workflow → `normal`**. Recebes o **resumo inicial** (tudo o que está
   aberto agora). A partir daí corre sozinho todos os dias e só avisa o que é novo.

### Como lê cada mensagem

```
🆕 2 estágios novos (04/11/2026)

💻 Feedzai: Software Engineer Intern        💻 = parece informática
📍 Lisbon, Portugal                         📌 = outra área ou por confirmar
https://boards.greenhouse.io/feedzai/...
```

- "📋 Já estavam abertos": ofertas de empresas que o bot leu pela primeira vez
  (ex.: depois de correres o detetor). Não são necessariamente recentes.
- "⚠️ Não consigo ler há 3 dias seguidos": essa fonte avariou; vê o log da execução.

### Regras dos filtros (vigilante/filtros.py)

- **Estágio:** intern, internship, estágio, trainee, summer intern/camp, verão, apprentice
  (sempre); graduate, student, academy, early careers... (só se o título não tiver
  manager, senior, head, lead...).
- **Local:** região de Lisboa (Lisboa, Oeiras, Amadora, Sintra, Cascais, Almada...), "Portugal"
  sem cidade, local não indicado, ou remoto na Europa. Outras cidades ficam de fora;
  para as incluir, muda `INCLUIR_RESTO_PORTUGAL = True`.
- **Informática (💻):** software, data, IT, cloud, cyber, developer, sistemas... Só muda o ícone:
  ofertas de outras áreas também são enviadas.

### Problemas comuns

| Sintoma | Solução |
|---|---|
| Passo "Guardar o estado" falha com 403 | Settings → Actions → General → Workflow permissions → **Read and write** |
| `git push` recusado ("fetch first") | `git pull` e depois `git push` |
| Uma empresa aparece com ❌ na tabela | Vê o erro; se persistir, corrige `carreiras`/`plataforma` em empresas.yaml |

## Passo 4: páginas, agregadores e notícias

O vigilante passa a ter três fontes, todas na mesma mensagem diária:

| Secção | De onde vem | Exemplo |
|---|---|---|
| 💼 Ofertas | APIs das plataformas (Greenhouse, Workday, SuccessFactors...) | "Feedzai: Software Engineer Intern" |
| 📄 Páginas | páginas de carreiras abertas num browser; textos novos sobre estágios | "BPI: «Summer Boost 2027»" |
| 📰 Notícias | Google News; ⭐ = menciona uma empresa da lista | "⭐ Galp · Galp abre Summer Break" |

**Como funciona a vigia de páginas.** Para cada empresa sem leitor de plataforma (ou cujo
leitor disse "isto não é SuccessFactors"), o bot abre a página de carreiras e até 2
sub-páginas promissoras ("Estudantes", "Estágios", "Vagas"). Guarda os textos curtos que falam
de estágios e avisa só os que nunca tinham aparecido. Ex.: se a página dizia
"Summer Internship 2026" e passa a dizer "Summer Internship 2027", recebes o texto novo.
Respeita o robots.txt de cada site.

**Primeira leitura.** O que já existia quando o bot leu uma fonte pela primeira vez vai para
"📋 Já estavam visíveis" (no máximo 3 linhas por empresa). As notícias antigas e os
índices de sites (sitemaps) ficam calados na primeira leitura, exceto notícias com ⭐.

### ITJobs.pt (opcional, 2 minutos)

1. Em https://itjobs.pt/api escreve o teu email: recebes uma chave de leitura (API key).
2. GitHub → Settings → Secrets and variables → Actions → New secret:
   nome `ITJOBS_API_KEY`, valor = a chave.

Sem a chave, o ITJobs fica "⏳ sem o segredo ITJOBS_API_KEY" e o resto funciona na mesma.

### Ajustar

- Uma empresa a dar ruído na vigia de páginas: `vigiar_pagina: false` em empresas.yaml.
- Uma empresa que o detetor classificou mal: escreve `carreiras` e `plataforma` à mão
  (ou `plataforma: null` para a vigiar só como página).
- Pesquisas de notícias: lista `noticias` em config/fontes.yaml.

## Passo 5: resumo de sexta-feira

Todas as sextas, além da mensagem diária (se houver novidades), recebes um **resumo semanal**:

```
📅 Resumo semanal (06/11/2026)
✅ O vigilante correu 7 de 7 dias esta semana.

💼 Estágios abertos agora: 12 em 7 empresas
💻 Deloitte (2): Deloitte Tech Experience FY26/27 · Hello World 26/27
https://jobs.deloitte.pt/...

📣 Páginas que dizem "candidaturas abertas" (1)
📌 Sonae: «Programa Contacto: candidaturas abertas até 6 de abril»

🆕 Novidades desta semana: 3 ofertas · 1 texto em páginas · 2 notícias

🔧 Fontes com erro na última leitura (1):
• página: OutSystems: 2× seguidas · Timeout
🚫 Sem acesso à página (robots.txt ou bloqueio), só por notícias: Banco de Portugal, ...

Próximo resumo: 13/11.
```

- O resumo chega **sempre**, mesmo numa semana calma: é o "sinal de vida" do bot. Se numa
  sexta não chegar nada, o vigilante parou (vê o separador Actions).
- **Ver o resumo noutro dia:** Run workflow → `resumo` (execução normal + resumo já).
  Na `simulacao` o resumo aparece sempre no log, sem ser enviado.
- Mudar o dia: `DIA_DA_SEMANA` em `vigilante/semanal.py` (0 = segunda ... 4 = sexta).

## Versão pública (para o CV)

```powershell
python ferramentas\exportar_publico.py ..\internship-watcher
cd ..\internship-watcher
git add .
git commit -m "Atualizar versão pública"
git push
```

O script copia o código e a configuração, mas não `data/` nem o histórico, e desliga o
agendamento. Usa `publico/README.md` (inglês) como README. Corre-o sempre que quiseres
mostrar melhorias na versão pública.

## Correr no teu computador (opcional)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt
export TELEGRAM_TOKEN="..." TELEGRAM_CHAT_ID="..."   # PowerShell: $env:TELEGRAM_TOKEN="..."
python -m vigilante.main --modo teste

# detetor (a primeira vez, instala o browser com: python -m playwright install chromium)
python -m vigilante.detetor --apenas "OutSystems, Feedzai"

# simulação do vigilante e testes
python -m vigilante.main --modo simulacao
pip install -r requirements-dev.txt
python -m pytest
```

## Custos e limites

- GitHub Actions num repositório privado (plano Free): 2.000 minutos/mês incluídos.
  O vigilante diário gasta 6 a 12 minutos com a vigia de páginas (até ~360 por mês); o
  detetor, 10 a 25 minutos por execução. Fica abaixo do limite.
- Os agendamentos do GitHub podem atrasar ou, raramente, falhar uma execução.
  Como corre todos os dias, um dia perdido não faz diferença.
- Se uma execução falhar, o GitHub envia-te um email automaticamente.
