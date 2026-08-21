# Telerion

Telerion é um sistema de cinema para Telegram. Administradores controlam o bot
por uma conversa privada e os espectadores assistem à transmissão ao vivo no
canal configurado.

O projeto pesquisa filmes, apresenta catálogo enriquecido pelo TMDB, resolve
fontes por addons, reproduz arquivos já publicados no canal, baixa torrents de
forma progressiva e adiciona legendas em português. A transmissão prioriza RTMP
e usa PyTgCalls como fallback automático.

> Use apenas mídias que você tem autorização para armazenar e transmitir. O
> projeto não contorna DRM e não determina a situação jurídica das fontes
> configuradas pelo operador.

## Funcionalidades

- controle privado por administradores do canal;
- catálogo interativo com pôsteres e informações do TMDB;
- busca de fontes por addons Stremio e Internet Archive;
- reprodução de vídeos já publicados no próprio canal com `/canal`;
- transmissão RTMP H.264/AAC em 720p, com fallback PyTgCalls;
- fila persistente, pausa, retomada, volume, repetição e avanço automático;
- download progressivo por qBittorrent, sem esperar o arquivo inteiro;
- armazenamento de mídia fora do disco do sistema;
- limpeza automática dos arquivos temporários após a reprodução;
- legendas automáticas em português por OpenSubtitles/Stremio;
- ajuste de sincronização e ativação/desativação de legendas em tempo real;
- isolamento de falhas por addon, retries e logs rotativos;
- onboarding diferente para administradores e usuários não autorizados.

## Como funciona

Telerion utiliza duas identidades do Telegram:

1. **Conta comum dedicada**: autentica por `SESSION_STRING`, gerencia a live
   RTMP, participa da chamada no fallback e acessa os arquivos publicados no
   canal.
2. **Bot do BotFather**: recebe comandos privados, envia o catálogo rico e
   apresenta botões interativos.

```text
Administrador -> bot privado -> handlers -> serviços -> fila
                                               |       |
                         TMDB/addons/torrent/canal      v
                                               FFmpeg -> RTMP
                                                  \----> PyTgCalls (fallback)

Espectadores ----------------------------------> live do canal
```

Os handlers apenas validam comandos e formatam respostas. A orquestração fica
em `app/services/`, o estado da fila em `app/player/`, o FFmpeg em
`app/streaming/` e os transportes Telegram em `app/telegram/`.

## Requisitos

- conta comum do Telegram dedicada ao Telerion;
- aplicação Telegram com `API_ID` e `API_HASH` de
  [my.telegram.org](https://my.telegram.org);
- bot criado no [@BotFather](https://t.me/BotFather);
- canal ou grupo com transmissão ao vivo;
- Docker Desktop com Docker Compose;
- qBittorrent com Web UI habilitada para fontes torrent;
- **API Read Access Token** do
  [TMDB](https://www.themoviedb.org/settings/api);
- um disco com espaço para o buffer dos filmes.

Python 3.13 e `uv` são usados uma vez no host para gerar a sessão e descobrir
os IDs. Para instalar o `uv` no Windows:

```powershell
winget install --id Python.Python.3.13 -e
python -m pip install uv
python --version
uv --version
```

O FFmpeg só é necessário no host para desenvolvimento; a imagem Docker já o
inclui.

## Início rápido

1. Clone o projeto:

```bash
git clone https://github.com/SanNw/telegram-video-bridge.git
cd telegram-video-bridge
```

2. Crie a configuração:

```bash
cp .env.example .env
```

No PowerShell:

```powershell
Copy-Item .env.example .env
```

3. Preencha pelo menos:

```dotenv
API_ID=
API_HASH=
SESSION_STRING=
CHAT_ID=
STREAM_CHAT_ID=
OWNER_USER_ID=
BOT_TOKEN=
TMDB_API_KEY=

QBITTORRENT_HOST=host.docker.internal
QBITTORRENT_PORT=8080
QBITTORRENT_USERNAME=admin
QBITTORRENT_PASSWORD=
QBITTORRENT_SAVE_PATH=E:/Backup/Filmes
QBITTORRENT_LOCAL_PATH=/app/media/torrents
TORRENT_BUFFER_MB=300
TORRENT_TIMEOUT_SECONDS=600
REMOVE_TORRENT_AFTER_PLAY=true
```

`QBITTORRENT_SAVE_PATH` é o caminho visto pelo Windows e pelo qBittorrent.
`QBITTORRENT_LOCAL_PATH` é o mesmo diretório visto de dentro do container.

4. Gere a sessão da conta comum:

```bash
uv sync
uv run python -u scripts/generate_session.py
```

Cole o valor gerado em `SESSION_STRING`. Nunca publique essa string: ela dá
acesso à conta autenticada.

Para descobrir `CHAT_ID`, `STREAM_CHAT_ID` e o ID da conta humana que operará o
bot, informe o `@username` dessa pessoa:

```bash
uv run python scripts/list_chats.py @username_do_operador
```

Use o valor `OWNER_USER_ID` exibido, não o `SESSION_ACCOUNT_ID` da conta
dedicada. O operador precisa ter iniciado uma conversa com a conta dedicada ou
compartilhar um grupo/canal acessível a ela.

5. Adicione a conta comum e o bot como administradores do canal. A conta comum
precisa poder gerenciar transmissões ao vivo.

6. Suba o serviço:

```bash
docker compose up -d --build
docker compose ps
```

7. Abra o bot no privado. Administradores recebem o manual de comandos no
primeiro contato. Usuários sem permissão recebem uma recusa e seus comandos não
são executados.

## Comandos principais

| Comando | Função |
|---|---|
| `/find <filme>` | Pesquisa filmes e abre o catálogo interativo |
| `/canal <filme>` | Pesquisa vídeos já publicados no canal |
| `/play <arquivo ou URL>` | Adiciona uma fonte direta à fila |
| `/pause` / `/resume` | Pausa ou retoma RTMP/PyTgCalls |
| `/stop` / `/skip` | Encerra o item atual ou avança a fila |
| `/restart` | Reinicia o filme atual |
| `/volume <0-200>` | Ajusta o volume |
| `/queue` / `/clear` | Consulta ou limpa itens pendentes |
| `/remove <posição>` | Remove um item pendente |
| `/loop <off\|item\|queue>` | Configura repetição |
| `/legenda on\|off` | Liga ou desliga a legenda disponível |
| `/subdelay <ms>` | Corrige sincronização da legenda |
| `/nowplaying` / `/status` | Exibe reprodução e saúde do serviço |

Filmes identificados como dublados, nacionais ou em português não recebem
legenda automática. Em fontes `Dual Áudio`, a legenda é mantida por segurança.

## Armazenamento

O desenho recomendado mantém vídeos e legendas no HD externo:

```text
E:/Backup/Filmes/
├── <downloads do qBittorrent>
├── channel/       # cópias progressivas vindas do canal
└── .subtitles/    # legendas temporárias
```

O FFmpeg começa após o buffer configurado e lê o mesmo arquivo que ainda está
sendo baixado. Ao terminar, pular ou parar a reprodução, o serviço libera a
fonte e remove os arquivos temporários conforme a configuração.

## Desenvolvimento

```bash
uv sync --all-groups
uv run ruff check app tests
uv run black --check app tests
uv run mypy
uv run pytest
```

A suíte possui mais de 500 testes. A configuração de CI exige cobertura mínima
de 90%; novos fluxos devem incluir testes de regressão.

## Documentação completa

Consulte [docs/MANUAL.md](docs/MANUAL.md) para:

- preparação das contas e permissões do Telegram;
- configuração variável por variável;
- instalação do qBittorrent e armazenamento no HD;
- arquitetura e ciclo completo de reprodução;
- RTMP, fallback, legendas, TMDB e addons;
- operação, atualização, backup, testes e troubleshooting;
- instruções para replicar o projeto em outra máquina ou canal.

## Segurança

- `.env`, `SESSION_STRING`, tokens e senhas nunca devem ser versionados;
- addons executam Python no processo principal e devem vir de fontes confiáveis;
- gerenciamento destrutivo de addons é restrito a `OWNER_USER_ID`;
- fontes locais são confinadas ao diretório de mídia permitido;
- o FFmpeg é iniciado sem shell e recebe argumentos já validados.
