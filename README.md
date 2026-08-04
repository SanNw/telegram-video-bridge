# Telegram Video Bridge

Transmite vídeos autorizados para uma chamada de vídeo/grupo do Telegram via
MTProto, rodando inteiramente em background — sem interface gráfica de
terceiros, sem automação de tela. Controlado por comandos de um bot do
Telegram (Pyrogram).

**Fora de escopo (v1):** captura de conteúdo protegido por DRM ou sem
autorização de distribuição, dashboard web, múltiplas contas/sessões
simultâneas.

## Stack

| Camada | Escolha | Por quê |
|---|---|---|
| Runtime | Python 3.13 + `asyncio` + `uvloop` (Linux) | pedido no escopo |
| Cliente MTProto | [Kurigram](https://pypi.org/project/kurigram/) | ver nota abaixo |
| Chamada de vídeo | [py-tgcalls](https://pypi.org/project/py-tgcalls/) 2.3.x | ver nota abaixo |
| Mídia | FFmpeg via `asyncio.subprocess` (nunca binding não oficial) | pedido no escopo |
| Config | Pydantic Settings | pedido no escopo |
| Logs | Loguru + Rich | pedido no escopo |
| Gerenciador de pacotes | [uv](https://docs.astral.sh/uv/) | ver nota abaixo |
| Deploy | Docker + Docker Compose | pedido no escopo |

### Nota: Kurigram em vez do pacote `pyrogram` oficial

O pacote **`pyrogram`** oficial no PyPI não recebe release desde abril/2023.
Isso não é só uma preocupação teórica: **testado neste projeto**, o `pyrogram`
oficial (2.0.106) é incompatível com o `py-tgcalls` atual — falta
`pyrogram.errors.GroupcallForbidden`, usado pelo adapter interno do
`py-tgcalls`, e a aplicação nem inicializa (`ImportError`).

A correção foi trocar para **[Kurigram](https://pypi.org/project/kurigram/)**,
um fork ativamente mantido (releases mensais) que instala no **mesmo
namespace de import `pyrogram`** — ou seja, é um drop-in: nenhuma linha de
código do projeto muda, só a dependência em `pyproject.toml`. Todo o código
usa `from pyrogram import ...` normalmente.

Não instale o pacote `pyrogram` oficial junto com `kurigram` — os dois
escrevem no mesmo caminho `pyrogram/` dentro de `site-packages` e vão
conflitar. Por isso a dependência do `py-tgcalls` está declarada **sem** o
extra `[pyrogram]` (que puxaria o pacote oficial): o `py-tgcalls` importa
`pyrogram.*` de forma preguiçosa, então o namespace fornecido pelo Kurigram já
é suficiente.

### Nota: py-tgcalls, não `pytgcalls`

O nome do pacote no PyPI é **`py-tgcalls`** (pacote `pytgcalls`, sem hífen, é
outro projeto — abandonado desde 2023). `py-tgcalls` está ativamente mantido,
migrando para o core nativo `ntgcalls` (C++) com compatibilidade retroativa.

### Nota: uv em vez de Poetry

Resolução de dependências mais rápida, um binário só (sem depender de
`python -m venv` + `pip` prévios), lockfile determinístico (`uv.lock`,
versionado) e melhor cache de camada Docker via `uv sync --frozen`. Poetry
seria uma escolha igualmente válida; `uv` foi preferido por já ser o padrão de
fato em projetos Python novos e por integrar bem com multi-stage builds.

## Arquitetura

```
telegram-video-bridge/
├── app/
│   ├── bot/          # comandos e camada de apresentação do Telegram
│   │   └── handlers/  # um módulo por grupo de comandos
│   ├── player/        # fila de reprodução (FIFO, loop, persistência)
│   ├── streaming/      # pipeline FFmpeg (FFmpegStreamer)
│   ├── telegram/       # gerenciamento da chamada (TelegramCallManager)
│   ├── addon_system/   # núcleo do sistema de plugins (ver seção própria)
│   ├── config/         # Settings centralizadas (Pydantic)
│   ├── services/       # orquestração entre camadas (PlaybackService, AddonService)
│   └── utils/          # logging, sanitização, retry, contrato de mídia
├── addons/              # addons instalados, um por subpasta (ver "Sistema de addons")
│   └── archive_org/      # addon oficial: Internet Archive
├── data/                # fila serializada (queue.json), estado de addons
├── logs/                # bot.log, stream.log, ffmpeg.log, errors.log
├── media/               # arquivos locais para /play
├── docker/              # Dockerfile
├── tests/
└── docker-compose.yml
```

**Regra de dependência:** `bot/` nunca importa de `streaming/`, `telegram/`
ou `addon_system/` diretamente — só de `services/`. Handlers de comando não
têm lógica de negócio: chamam um método de `PlaybackService`/`AddonService` e
formatam a resposta. `addon_system/` também não conhece `streaming/` nem
`telegram/`: um addon devolve uma URL reproduzível, e é `AddonService` quem
entrega essa URL ao `PlaybackService` — o mesmo caminho que `/play` usa.

### Fluxo de dados

```
Operador (Telegram)
      │  /play, /pause, /resume, /stop, /skip, /queue, /clear, /status, ...
      ▼
   app/bot/                (autorização, parsing, formatação de resposta)
      │  chama métodos de PlaybackService
      ▼
   app/services/            (único ponto de orquestração)
      │                 │                    │
      ▼                 ▼                    ▼
 app/player/       app/streaming/       app/telegram/
 (fila FIFO,       (FFmpegStreamer:     (TelegramCallManager:
  persistência)     inicia/monitora/     entra/sai/reconecta a
                     reinicia o FFmpeg,   chamada via Pyrogram +
                     escreve em pipes     py-tgcalls, lê dos
                     nomeados)            mesmos pipes)
```

`streaming/` e `telegram/` não se importam entre si — são independentes,
conectadas apenas pelos dois named pipes (`media/pipes/video.pipe` e
`audio.pipe`) que `FFmpegStreamer` escreve e `TelegramCallManager` lê via
`InputDevice` do `py-tgcalls`. É isso que permite trocar de fonte ou
sobreviver a uma falha do FFmpeg **sem** derrubar a chamada em andamento: a
chamada continua lendo dos mesmos pipes, só o processo que escreve neles é
reiniciado. O formato exato (resolução, fps, sample rate) é a única fonte de
verdade compartilhada, em `app/utils/media_contract.py` — evita que as duas
camadas fiquem fora de sincronia.

## Comandos

Autorização: comandos de controle exigem que o `user_id` esteja na whitelist
`AUTHORIZED_USER_IDS` — ou, se `AUTHORIZED_USER_IDS=all`, que o usuário seja
membro atual do grupo em `CHAT_ID` (checado em tempo real via
`get_chat_member`); fora disso, a resposta é sempre "Você não tem permissão
para usar este comando." `/start`, `/help`, `/ping` e `/version` são públicos
(somente leitura, sem dado sensível).

| Comando | Autorização | Resposta |
|---|---|---|
| `/start` | pública | Mensagem de boas-vindas |
| `/help` | pública | Lista de comandos |
| `/ping` | pública | `Pong! <latência>ms` |
| `/version` | pública | Versão em execução |
| `/play <fonte>` | whitelist | Enfileira; inicia a reprodução se ociosa. Sem argumento: uso. Fonte inválida: motivo. Fila cheia: motivo. |
| `/pause` | whitelist | Pausa a chamada (FFmpeg continua rodando, bloqueado no pipe). "Nada está tocando" se ociosa. |
| `/resume` | whitelist | Retoma uma chamada pausada. |
| `/stop` | whitelist | Para a reprodução e sai da chamada. Não limpa a fila pendente. |
| `/skip` | whitelist | Pula para o próximo item (ignora loop de item). Fila vazia: encerra. |
| `/restart` | whitelist | Reinicia o item atual do zero (mesma fonte). "Nada está tocando" se ociosa. |
| `/volume <0-200>` | whitelist | Ajusta o volume da chamada. Sem argumento: uso. Fora do intervalo ou não numérico: motivo. |
| `/queue` | whitelist | Lista o item atual + itens pendentes. |
| `/remove <posição>` | whitelist | Remove um item pendente da fila. Posição inválida: motivo. |
| `/loop <off\|item\|queue>` | whitelist | Define o modo de repetição da fila. Modo desconhecido: valores aceitos. |
| `/clear` | whitelist | Esvazia a fila pendente (não afeta o item em reprodução). |
| `/status` | whitelist | Estado do streaming, da chamada, da fila e sinal de degradação. |
| `/nowplaying` | whitelist | Item em reprodução, quem pediu e há quanto tempo toca. "Nada está tocando" se ociosa. |
| `/uptime` | whitelist | Há quanto tempo o processo está em execução. |
| `/find <busca>` | whitelist | Busca em todos os addons habilitados; lista resultados numerados. Sem argumento: uso. |
| `/pick <número>` | whitelist | Resolve o resultado `<número>` da última `/find` e enfileira. Posição inválida, sem fonte reproduzível, fonte inválida ou fila cheia: motivo. |
| `/addons` | whitelist | Lista addons instalados (habilitado/desabilitado, versão). |
| `/addon <info\|enable\|disable\|reload\|uninstall> <nome>` | whitelist | Gerencia um addon (ver "Sistema de addons"). Ação/nome ausente ou addon inexistente: motivo. |

Fontes suportadas em `/play` (e no destino final de `/pick`): arquivo local
em `media/` (`.mp4`, `.mkv`, `.avi`, `.mov`), URL HTTP/HTTPS direta, HLS
(`.m3u8`), RTMP, RTSP.

## Como adicionar vídeos

**Local (sem Docker):** copie o arquivo para a pasta `media/` do projeto e
use `/play nome-do-arquivo.mp4`.

**Docker:** `media/` é um volume nomeado (não um bind mount), então copie o
arquivo para dentro do container:

```bash
docker compose cp ./meu-video.mp4 bridge:/app/media/meu-video.mp4
```

Depois use `/play meu-video.mp4` normalmente. URLs HTTP/HTTPS, HLS, RTMP e
RTSP não precisam desse passo — funcionam direto: `/play https://...`.

## Sistema de addons

Um addon resolve **fontes de mídia**: recebe uma busca em texto livre
(`/find`) e devolve candidatos que, uma vez escolhidos (`/pick`), viram a
`<fonte>` de um `/play` comum — mesma fila, mesmo `PlaybackService`, mesmas
regras de sanitização. Um addon não sabe nada sobre `streaming/`, `telegram/`
ou FFmpeg; só fala `search` → `get_streams` → uma URL HTTP(S)/HLS/RTMP/RTSP.

### Arquitetura

```
/find <busca>  ──▶  AddonService.find()  ──▶  AddonManager.search()
                                                    │
                                    asyncio.gather em paralelo,
                                    timeout + isolamento de falha
                                    por addon, resultado cacheado (TTL)
                                                    ▼
                                        addon.search() de cada
                                        addon habilitado

/pick <número> ──▶  AddonService.pick()  ──▶  AddonManager.get_streams()
                                                    │
                                                    ▼
                                        addon.get_streams(media_id)
                                                    │
                                    melhor StreamCandidate.url
                                                    ▼
                                        PlaybackService.play(url, ...)
                                        (mesmo caminho do /play manual)
```

Cada addon implementa a interface `BaseAddon`
(`app/addon_system/base.py`): `search(query)`, `get_metadata(media_id)`,
`get_streams(media_id)` (obrigatórios) e `health()`/`close()` (opcionais, com
implementação padrão). `AddonManager` carrega addons dinamicamente via
`importlib` — cada carga/recarga importa `plugin.py` sob um nome de módulo
único (não reusa `importlib.reload`), então um `/addon reload` nunca deixa
classes antigas penduradas em memória.

**Isolamento de falha** é o requisito central: um addon que trava, estoura
timeout (`ADDON_SEARCH_TIMEOUT_SECONDS`/`ADDON_STREAMS_TIMEOUT_SECONDS`) ou
levanta exceção nunca derruba o processo nem os outros addons — vira log +
resultado vazio para aquele addon específico, os demais respondem
normalmente.

### Estrutura de um addon

```
addons/<nome>/
  manifest.json   # metadados: name, version, description, entrypoint, min_core_version
  plugin.py        # implementação de BaseAddon (classe indicada em "entrypoint")
  README.md         # o que o addon faz, limitações, configuração
```

`entrypoint` no `manifest.json` é `"<módulo>:<Classe>"` (ex.: `"plugin:Addon"`
— o padrão, raramente precisa mudar). Config opcional por addon vai em
`config/addons/<nome>.{json,yaml,yml}` (caminho configurável via
`ADDONS_CONFIG_PATH`) e chega no addon como `dict` no construtor
(`BaseAddon.__init__(self, config=None)`) — nunca por variável de ambiente
própria, para não duplicar o mecanismo de `Settings`.

### Gerenciando addons

- `/addons` — lista os instalados, com estado (habilitado/desabilitado).
- `/addon info <nome>` — versão, descrição, estado, healthcheck ao vivo.
- `/addon enable <nome>` / `/addon disable <nome>` — não afeta addons já
  carregados, só se participam de `/find`. Estado sobrevive a restart
  (`ADDONS_STATE_PATH`).
- `/addon reload <nome>` — recarrega `plugin.py` do disco sem reiniciar o
  processo. Se o reload falhar (erro de sintaxe, exceção no construtor), o
  addon anterior continua carregado e ativo.
- `/addon uninstall <nome>` — descarrega e **apaga a pasta do addon do
  disco**. Ação destrutiva, sem confirmação adicional no chat.

Instalar um addon novo hoje é manual: colocar a pasta em `addons/<nome>/`
(local ou via volume Docker) e reiniciar o processo (ou, se `addons_path` já
existia, o addon só é descoberto em `AddonManager.discover()`, chamado na
inicialização).

### Addon oficial: `archive_org`

Busca filmes de domínio público / licença aberta no
[Internet Archive](https://archive.org), usando só a API pública
(`advancedsearch.php` + `/metadata/`), sem scraping e sem chave. Filtra por
`mediatype:(movies)` e `licenseurl:(*publicdomain* OR *creativecommons*)` —
um filtro de curadoria dos próprios metadados do archive.org, não uma
garantia legal absoluta (itens mal categorizados podem escapar do filtro).
Detalhes em `addons/archive_org/README.md`.

### Addon oficial: `stremio`

Ponte para addons Stremio externos de terceiros (protocolo HTTP/JSON:
`/manifest.json`, `/catalog`, `/stream`, `/meta`), consumidos via
`StremioAddonClient` (`app/services/stremio_client.py`). Não baixa nem
executa código de terceiros — só fala HTTP com URLs que o operador já
configurou em `config/addons/stremio.json`. Sem upstreams configurados, o
addon fica inerte (`/find` não retorna nada dele, `health()` não saudável).
Só resolve streams com URL HTTP(S)/HLS/RTMP/RTSP direta — magnet
links/torrents não são suportados. Detalhes e exemplo de configuração em
`addons/stremio/README.md`.

### O que **não** existe (de propósito)

Não há "loja de addons" (`/addon store`, `/addon install <nome>` baixando de
um índice remoto) nem instalação por chat a partir de zip/URL/repositório
Git. Isso foi cogitado no design original, mas **deliberadamente adiado**:
instalar e executar código de terceiros a partir de um comando de chat é a
parte de maior risco de todo o sistema de addons (execução arbitrária de
código no mesmo processo que tem a `SESSION_STRING`), e não deve ser
implementado sem uma decisão explícita de modelo de confiança (índice
controlado só por mim vs. aceitar PRs de terceiros com checksum obrigatório
vs. não ter índice remoto nenhum, só deploy manual). Hoje, instalar um addon
é sempre manual (seção acima) — nunca a partir de um comando enviado no
Telegram.

## Configuração

Copie `.env.example` para `.env` e preencha:

```bash
cp .env.example .env
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `API_ID`, `API_HASH` | sim | De https://my.telegram.org |
| `SESSION_STRING` | sim | String de sessão Pyrogram/Kurigram (ver abaixo) |
| `CHAT_ID` | sim | Chat/grupo onde a chamada acontece |
| `AUTHORIZED_USER_IDS` | não (mas fica inutilizável sem) | `user_id` separados por vírgula, ou `all` para qualquer membro atual do grupo em `CHAT_ID` |
| `LOG_LEVEL`, `LOG_DIR`, `LOG_ROTATION`, `LOG_RETENTION` | não | ver seção Logs |
| `FFMPEG_PATH`, `MEDIA_PATH` | não | padrão: `ffmpeg` no PATH, pasta `media/` |
| `FFMPEG_MAX_CONCURRENT` | não | máx. processos FFmpeg simultâneos (v1: 1) |
| `FFMPEG_HEALTHCHECK_INTERVAL_SECONDS` | não | intervalo do log de healthcheck |
| `FFMPEG_TERMINATE_TIMEOUT_SECONDS` | não | prazo p/ SIGTERM antes de SIGKILL |
| `QUEUE_MAX_ITEMS` | não | limite de itens na fila (padrão 50) |
| `QUEUE_DATA_PATH` | não | onde persistir a fila (padrão `data/queue.json`) |
| `RETRY_BASE_DELAY_SECONDS`, `RETRY_MAX_DELAY_SECONDS`, `RETRY_MAX_ATTEMPTS`, `RETRY_JITTER_SECONDS` | não | ver Política de retry |
| `ADDONS_PATH` | não | pasta com os addons instalados (padrão `addons/`) |
| `ADDONS_STATE_PATH` | não | onde persistir habilitado/desabilitado por addon (padrão `data/addons_state.json`) |
| `ADDONS_CONFIG_PATH` | não | pasta com config própria de cada addon (padrão `config/addons/`) |
| `ADDON_SEARCH_TIMEOUT_SECONDS`, `ADDON_STREAMS_TIMEOUT_SECONDS` | não | timeout por addon em `/find`/`/pick` (padrão 10s cada) |
| `ADDON_SEARCH_CACHE_TTL_SECONDS` | não | TTL do cache de resultados de `/find` (padrão 300s) |

Nunca versione `.env` (já está no `.gitignore`). `API_HASH` e
`SESSION_STRING` são mascarados em todo log (`***MASKED***`) e nunca aparecem
em texto plano em nenhum arquivo de log.

### Gerando o `SESSION_STRING`

Com Kurigram instalado localmente (`uv sync` já traz), rode uma vez:

```python
from pyrogram import Client

with Client("gerar-sessao", api_id=SEU_API_ID, api_hash="SEU_API_HASH", in_memory=True) as app:
    print(app.export_session_string())
```

Use uma conta com permissão para participar de chamadas no grupo/canal alvo.
Essa é a **mesma conta** que recebe os comandos do bot (ver arquitetura —
`bot/` e `telegram/` compartilham a mesma sessão Pyrogram, não abrem duas).

## Logs

Quatro arquivos em `LOG_DIR` (padrão `logs/`), rotacionados por
`LOG_ROTATION`/retidos por `LOG_RETENTION` (padrão: 10 MB / 7 dias):

- `bot.log` — comandos e respostas
- `stream.log` — player, streaming (FFmpeg) e telegram (chamada)
- `ffmpeg.log` — saída bruta do processo FFmpeg (stdout/stderr)
- `errors.log` — todo registro ERROR+ agregado, qualquer componente

## Resiliência

- **Processo principal nunca morre por exceção não tratada**: exceções de
  handler são capturadas pelo próprio Pyrogram; exceções em tasks de
  background são capturadas por um `loop.set_exception_handler` e logadas em
  `errors.log`.
- **FFmpeg cai** → `FFmpegStreamer` detecta a saída inesperada e reinicia
  automaticamente com a mesma fonte, com backoff exponencial + jitter.
- **Chamada cai** (kick, saída forçada, fim de stream) → `TelegramCallManager`
  detecta via update do `py-tgcalls` e reconecta automaticamente com a última
  fonte conhecida, mesma política de retry.
- **Falha permanente** (esgotou as tentativas): loga em `errors.log`, marca
  `/status` como degradado com o motivo, e — no caso do FFmpeg — pausa a
  chamada em vez de deixá-la travada numa imagem congelada. Não crasha o
  processo; requer intervenção do operador (`/skip`, `/play` outra fonte, ou
  reiniciar o processo).

### Política de retry

Backoff exponencial com jitter, mesma política para reconexão de chamada e
de FFmpeg (`app/utils/retry.py`): `delay = min(RETRY_BASE_DELAY_SECONDS *
2^(tentativa-1), RETRY_MAX_DELAY_SECONDS) + jitter_aleatório(0,
RETRY_JITTER_SECONDS)`. Após `RETRY_MAX_ATTEMPTS` tentativas sem sucesso,
marca falha permanente e alerta (ver acima). Padrões: 2s base, 60s teto, 8
tentativas, 1s de jitter.

## Segurança

- Nenhuma credencial hardcoded — tudo via `.env`/`Settings`.
- `SESSION_STRING`/`API_HASH` mascarados em logs.
- Entrada de `/play` validada antes de chegar ao FFmpeg
  (`app/utils/sanitize.py`): rejeita entradas que comecem com `-` (evita
  injeção de flags), caracteres de controle, esquemas de URL não suportados,
  e caminhos locais fora de `MEDIA_PATH`. O processo FFmpeg é sempre criado
  via `asyncio.create_subprocess_exec` com uma lista de argumentos — nunca
  `shell=True`, nunca uma string concatenada.
- Limite configurável de itens na fila (`QUEUE_MAX_ITEMS`) e de processos
  FFmpeg simultâneos (`FFMPEG_MAX_CONCURRENT`).
- Whitelist de `user_id` (`AUTHORIZED_USER_IDS`) para todo comando de
  controle — ver tabela de comandos.
- Addons rodam no mesmo processo (sem sandbox) e são código Python arbitrário
  — por isso não há instalação de addon via chat (ver "O que não existe, de
  propósito" na seção de addons). Instalar um addon hoje exige acesso ao
  filesystem/deploy, o mesmo nível de confiança já exigido para editar
  `.env` ou o código do bot.

## Execução local

Requer Python 3.13+, [uv](https://docs.astral.sh/uv/) e FFmpeg instalado no
PATH.

```bash
cp .env.example .env   # preencha as variáveis
uv sync
uv run python -m app.main
```

## Execução via Docker

```bash
cp .env.example .env   # preencha as variáveis
docker compose up -d
docker compose logs -f
```

`docker compose up` sobe a aplicação funcional sem passos manuais além de
preencher o `.env` — a imagem já traz FFmpeg instalado. Volumes nomeados
(`logs`, `media`, `data`) persistem entre restarts do container.

## Deploy / atualização

```bash
git pull
docker compose up -d --build   # reconstrói a imagem e reinicia
```

A fila (`data/queue.json`) sobrevive ao restart do container — o processo
carrega a fila persistida ao subir, mas **não retoma a reprodução
automaticamente** (decisão deliberada: evita rejuntar uma chamada sem o
operador pedir). Use `/play` ou `/skip` para retomar manualmente.

## Solução de problemas

**`/status` mostra "Degradado"** — FFmpeg ou a chamada esgotaram as
tentativas de reconexão automática. Veja o motivo na própria resposta e em
`errors.log`. Tente `/skip` (próximo item), `/play` (nova fonte) ou `/stop` +
`/play` de novo.

**`/play` responde "Fonte inválida"** — a URL usa um esquema não suportado
(só http/https/hls/rtmp/rtsp), o arquivo local não existe em `MEDIA_PATH`,
ou a extensão não é uma das suportadas (`.mp4`, `.mkv`, `.avi`, `.mov`). Com
Docker, lembre que `media/` é um volume nomeado — veja "Como adicionar
vídeos".

**Nenhum comando responde** — confira se o `user_id` está em
`AUTHORIZED_USER_IDS` (comandos de controle) e se `docker compose logs`
mostra o processo rodando sem traceback na inicialização (geralmente
`SESSION_STRING`/`API_ID`/`API_HASH` inválidos ou expirados).

**`ImportError` envolvendo `pyrogram`** — confirme que só `kurigram` está
instalado (`uv pip list | grep -i gram`), não o pacote `pyrogram` oficial
junto — os dois conflitam no mesmo caminho de import. `uv sync` a partir do
`uv.lock` deste repositório já resolve isso corretamente.

**FFmpeg não encontrado** — confirme `FFMPEG_PATH` (padrão: espera `ffmpeg`
no `PATH`) e que o binário está instalado. Na imagem Docker já vem incluso.

## FAQ

**Por que não usar a API de Bot padrão do Telegram (BotFather) em vez de uma
conta de usuário?** Chamadas de grupo (MTProto group calls) exigem uma sessão
de usuário — bots comuns não conseguem participar de chamadas de vídeo.

**Por que os pipes nomeados em vez de deixar o `py-tgcalls` rodar o FFmpeg
dele mesmo internamente?** O `py-tgcalls` sabe fazer isso (modo "shell"), mas
aí perderíamos controle direto do processo — sem `healthcheck()` nosso, sem
`ffmpeg.log` próprio, sem reinício sob nossa política de retry. Os pipes
mantêm `streaming/` e `telegram/` desacopladas como a arquitetura pede, com
`FFmpegStreamer` sendo o dono real do processo.

**Dá pra rodar mais de uma chamada ao mesmo tempo?** Não na v1 — decisão
deliberada de escopo (`FFMPEG_MAX_CONCURRENT` existe para o futuro, mas hoje
só uma sessão é suportada).

**Por que Kurigram e não Hydrogram, já que os dois são forks mantidos do
Pyrogram?** Hydrogram foi cogitado primeiro (também citado como extra oficial
do `py-tgcalls`), mas ele instala sob o namespace `hydrogram`, não
`pyrogram` — e o adapter interno do `py-tgcalls` (`mtproto_client.py`)
detecta o cliente pelo nome do módulo (`pyrogram` ou `telethon`); um cliente
Hydrogram cai no `else` e levanta `InvalidMTProtoClient`, testado neste
projeto. Kurigram resolve isso por reusar o namespace `pyrogram` de verdade.
