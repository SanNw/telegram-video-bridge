# Manual técnico e operacional do Telerion

[English](MANUAL.en.md) · [Español](MANUAL.es.md)

Este manual explica como instalar, replicar, operar e manter o Telerion. Ele
descreve o comportamento atual do código; o README funciona como porta de
entrada.

## 1. Visão geral

Telerion transforma um canal ou grupo do Telegram em uma sala de cinema. Os
administradores conversam com um bot no privado, escolhem o filme e controlam a
sessão. Os inscritos assistem pela live do canal, sem acesso aos comandos.

O sistema aceita três origens:

1. `/find`: consulta TMDB e addons, apresenta opções e resolve uma fonte;
2. `/canal`: pesquisa vídeos e documentos já publicados no canal;
3. `/play`: recebe um arquivo local ou URL direta validada.

Todas terminam no mesmo `PlaybackService`, entram na mesma fila e usam o mesmo
FFmpeg.

## 2. Componentes externos

### Telegram

São necessárias duas identidades:

- **conta comum dedicada**: usa MTProto, gerencia RTMP, acessa o histórico e
  participa da chamada no fallback;
- **bot do BotFather**: recebe comandos privados e envia botões inline.

Bots comuns não participam sozinhos de group calls. Por isso a
`SESSION_STRING` de uma conta comum é obrigatória.

### TMDB

O TMDB fornece título canônico, ano, sinopse, pôster, imagens, elenco e IMDb.
O IMDb também ajuda a localizar a legenda correta. O valor configurado deve ser
o **API Read Access Token**, enviado como Bearer; não use a API Key v3 curta.

No fluxo interativo com `BOT_TOKEN`, o TMDB é obrigatório: sem ele `/find` não
monta o catálogo e não consulta as fontes. Isso é deliberado para garantir que
o TMDB sempre apareça na seleção.

### qBittorrent

O qBittorrent recebe magnets retornados pelos addons. O Telerion usa a Web API
para adicionar o torrent, ativar download sequencial, escolher o maior vídeo,
aguardar o buffer e liberar a fonte depois da reprodução.

O bridge não cria uma segunda cópia: qBittorrent e FFmpeg trabalham sobre o
mesmo arquivo.

### OpenSubtitles

A integração usa o endpoint Stremio do OpenSubtitles, sem conta ou chave
adicional. A preferência é português brasileiro (`pob`, `pt-BR`) e depois
português geral (`por`, `pt`). A legenda é limitada a 2 MB e gravada na imagem
pelo FFmpeg/libass.

## 3. Preparação do Telegram

### Criar a aplicação MTProto

1. Entre em [my.telegram.org](https://my.telegram.org) com a conta dedicada.
2. Abra **API development tools**.
3. Crie uma aplicação.
4. Guarde `API_ID` e `API_HASH`.

### Preparar Python e uv

No PowerShell:

```powershell
winget install --id Python.Python.3.13 -e
python -m pip install uv
python --version
uv --version
```

Reabra o terminal se `python` ainda não estiver disponível após a instalação.

### Gerar a sessão da conta

Com Python 3.13 e `uv`:

```bash
uv sync
uv run python -u scripts/generate_session.py
```

O Telegram solicita telefone, código de login e senha de duas etapas, quando
habilitada. Coloque o resultado em:

```dotenv
SESSION_STRING=<valor completo>
```

Essa string dá acesso à conta. Nunca a envie por chat, issue, log ou commit.
Se for exposta, encerre as sessões da conta e gere outra.

### Criar o bot de controle

No [@BotFather](https://t.me/BotFather):

1. use `/newbot`;
2. escolha nome e username;
3. copie o token para `BOT_TOKEN`;
4. opcionalmente cadastre os comandos da seção 10 com `/setcommands`.

### Permissões no canal

Adicione a conta dedicada e o bot como administradores.

A conta dedicada precisa iniciar e gerenciar lives, ler o histórico usado por
`/canal` e participar da chamada quando houver fallback. O bot precisa estar no
canal para verificar em tempo real quem é administrador.

No privado, somente `OWNER_USER_ID` e administradores atuais de
`STREAM_CHAT_ID` controlam o serviço. Uma whitelist antiga não transforma um
membro comum em administrador do canal.

A conta dedicada também registra handlers para compatibilidade e pode receber
comandos nos chats em que participa. Nesse cliente, `AUTHORIZED_USER_IDS` e
`CHAT_ID` ainda se aplicam. Para uma instalação centrada no bot privado, deixe
`AUTHORIZED_USER_IDS` vazio e controle tudo pelo BotFather; owner e
administradores do canal continuam autorizados.

### IDs do Telegram

IDs de canais e supergrupos normalmente começam com `-100`:

```dotenv
CHAT_ID=-100...
STREAM_CHAT_ID=-100...
```

- `STREAM_CHAT_ID`: canal da live, origem dos administradores e acervo de
  `/canal`;
- `CHAT_ID`: grupo base e compatibilidade com `AUTHORIZED_USER_IDS=all` fora
  do bot privado.

Depois de preencher `API_ID`, `API_HASH` e `SESSION_STRING`, liste os chats e
resolva o ID da conta humana que operará o bot sem usar bots de terceiros:

```bash
uv run python scripts/list_chats.py @username_do_operador
```

O script exibe `SESSION_ACCOUNT_ID` para identificar a conta dedicada,
`OWNER_USER_ID` para configurar o operador humano e depois os diálogos
acessíveis. Não use o ID da conta dedicada como owner, a menos que seja ela que
enviará os comandos. O operador precisa ter iniciado uma conversa com a conta
dedicada ou compartilhar um chat acessível a ela. Canais e supergrupos aparecem
normalmente com ID iniciado por `-100`.

## 4. Instalação recomendada no Windows

### Estrutura no HD

```text
E:/Backup/
├── Telerion/    # repositório
└── Filmes/      # torrents, arquivos do canal e legendas
```

### Clonar

```powershell
Set-Location E:\Backup
git clone https://github.com/SanNw/telegram-video-bridge.git Telerion
Set-Location E:\Backup\Telerion
Copy-Item .env.example .env
```

### Docker Desktop

Instale Docker Desktop, habilite WSL 2 e confirme:

```powershell
docker version
docker compose version
```

Autorize acesso ao drive `E:` se sua instalação exigir compartilhamento
explícito de unidades.

### qBittorrent

Na Web UI:

1. habilite a interface web;
2. defina usuário e senha fortes;
3. limite acesso à máquina/rede confiável;
4. configure `E:\Backup\Filmes` como diretório padrão;
5. não exponha a Web UI diretamente à internet.

Quando qBittorrent roda no Windows e Telerion no Docker:

```dotenv
QBITTORRENT_HOST=host.docker.internal
QBITTORRENT_PORT=8080
QBITTORRENT_SAVE_PATH=E:/Backup/Filmes
QBITTORRENT_LOCAL_PATH=/app/media/torrents
MEDIA_HOST_PATH=E:/Backup/Filmes
```

| Processo | Caminho do mesmo diretório |
|---|---|
| qBittorrent no Windows | `E:/Backup/Filmes` |
| origem do bind mount | `E:/Backup/Filmes` |
| Telerion no container | `/app/media/torrents` |

Não use o caminho Windows em `QBITTORRENT_LOCAL_PATH` dentro do container.

## 5. Configuração do `.env`

### Telegram e autorização

| Variável | Uso |
|---|---|
| `API_ID` | ID numérico da aplicação MTProto |
| `API_HASH` | segredo da aplicação |
| `SESSION_STRING` | sessão da conta comum |
| `CHAT_ID` | grupo base/compatibilidade |
| `STREAM_CHAT_ID` | canal da live e dos administradores |
| `AUTHORIZED_USER_IDS` | whitelist fora do privado; aceita IDs ou `all` |
| `OWNER_USER_ID` | proprietário e gestor de addons |
| `BOT_TOKEN` | bot privado e botões interativos |

### TMDB

| Variável | Recomendação |
|---|---|
| `TMDB_API_KEY` | API Read Access Token; preencher sempre |
| `TMDB_LANGUAGE` | `pt-BR` |
| `TMDB_REQUEST_TIMEOUT_SECONDS` | `10.0` |

### Torrent e armazenamento

| Variável | Exemplo recomendado |
|---|---|
| `QBITTORRENT_HOST` | `host.docker.internal` |
| `QBITTORRENT_PORT` | `8080` |
| `QBITTORRENT_USERNAME` | usuário da Web UI |
| `QBITTORRENT_PASSWORD` | senha da Web UI |
| `QBITTORRENT_CATEGORY` | categoria opcional do Telerion |
| `QBITTORRENT_SAVE_PATH` | `E:/Backup/Filmes` |
| `QBITTORRENT_LOCAL_PATH` | `/app/media/torrents` |
| `TORRENT_BUFFER_MB` | `300` para maior estabilidade |
| `TORRENT_TIMEOUT_SECONDS` | `600` para torrents lentos |
| `REMOVE_TORRENT_AFTER_PLAY` | `true` para evitar acúmulo |

O buffer indica quanto deve estar disponível antes do FFmpeg começar. O
restante continua baixando durante a sessão.

### Fila, retry e logs

| Variável | Padrão | Observação |
|---|---:|---|
| `QUEUE_MAX_ITEMS` | `50` | itens pendentes |
| `QUEUE_DATA_PATH` | `data/queue.json` | volume persistente |
| `RETRY_BASE_DELAY_SECONDS` | `2` | início do backoff |
| `RETRY_MAX_DELAY_SECONDS` | `60` | teto do backoff |
| `RETRY_MAX_ATTEMPTS` | `8` | falhas antes de degradar |
| `LOG_LEVEL` | `INFO` | use `DEBUG` só para diagnóstico |
| `LOG_ROTATION` | `10 MB` | rotação por arquivo |
| `LOG_RETENTION` | `7 days` | retenção |

### Exemplo operacional

```dotenv
API_ID=123456
API_HASH=<segredo>
SESSION_STRING=<segredo>
CHAT_ID=-1000000000000
STREAM_CHAT_ID=-1000000000000
AUTHORIZED_USER_IDS=
OWNER_USER_ID=123456789
BOT_TOKEN=<segredo>

TMDB_API_KEY=<segredo>
TMDB_LANGUAGE=pt-BR

QBITTORRENT_HOST=host.docker.internal
QBITTORRENT_PORT=8080
QBITTORRENT_USERNAME=telerion
QBITTORRENT_PASSWORD=<segredo>
QBITTORRENT_SAVE_PATH=E:/Backup/Filmes
QBITTORRENT_LOCAL_PATH=/app/media/torrents
TORRENT_BUFFER_MB=300
TORRENT_TIMEOUT_SECONDS=600
REMOVE_TORRENT_AFTER_PLAY=true
```

## 6. Inicialização e atualização

Primeira inicialização:

```bash
uv run python -m app.doctor
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 bridge
```

Estado esperado:

- serviço `bridge` em `Up` e saudável;
- cliente Telegram e `PlaybackService` iniciados;
- addons carregados;
- ausência de traceback atual em `errors.log`.

Atualização:

```bash
git pull --ff-only
docker compose up -d --build --force-recreate
docker compose ps
```

Encerramento sem apagar volumes:

```bash
docker compose stop bridge
docker compose down
```

Não use `docker compose down -v` sem intenção explícita: `-v` remove dados e
logs persistentes.

## 7. Arquitetura interna

```text
app/main.py
  -> Settings
  -> PlaybackService
  -> TorrentService + ChannelMediaService (liberação de fontes)
  -> AddonService + TMDBService
  -> bot do BotFather
  -> handlers e loop principal
```

| Diretório | Responsabilidade |
|---|---|
| `app/bot/` | autorização, comandos, callbacks e respostas |
| `app/services/` | casos de uso e orquestração |
| `app/player/` | fila, modelos e persistência |
| `app/streaming/` | comando, processo e supervisão do FFmpeg |
| `app/telegram/` | RTMP, PyTgCalls e cliente MTProto |
| `app/addon_system/` | descoberta e ciclo dos addons |
| `app/utils/` | validação, logging, idioma e retry |
| `addons/` | implementações de fontes externas |

Handlers chamam serviços. Eles não iniciam FFmpeg, manipulam torrents ou entram
em chamadas diretamente. Isso mantém rollback e limpeza no caminho comum.

### Ciclo de reprodução

```text
comando/callback
  -> autorização
  -> resolução da fonte
  -> preparação da legenda
  -> QueueManager.add/advance
  -> tenta RTMP
       -> sucesso: FFmpeg envia H.264/AAC
       -> falha: FFmpeg escreve pipes e PyTgCalls entra na chamada
  -> conclusão/skip/stop
  -> libera torrent ou arquivo do canal
  -> avança fila ou encerra
```

Se o primeiro item falhar, o serviço interrompe qualquer FFmpeg parcial,
descarta o item atual e libera a fonte.

### RTMP e fallback

RTMP usa H.264/AAC em FLV, 720p e bitrate de vídeo próximo de 3 Mbps. Quando
falha, o FFmpeg escreve vídeo raw e áudio PCM em FIFOs POSIX consumidos pelo
PyTgCalls.

O transporte anterior é encerrado antes da troca. Isso evita que um fallback
antigo tente se reconectar durante uma live RTMP.

### Controles durante RTMP

- `/pause`: guarda a posição e encerra FFmpeg sem liberar a fonte;
- `/resume`: prepara o endpoint e reinicia no ponto aproximado;
- `/volume`: aplica filtro de áudio e retoma da posição aproximada;
- legenda e `subdelay`: reiniciam o pipeline no mesmo ponto aproximado.

Pode existir pequena descontinuidade porque o seek depende dos keyframes.

### Persistência

A fila é gravada em JSON de forma atômica. Estado corrompido não derruba o
processo. Arquivos locais restaurados que não existem mais são ignorados.

## 8. Busca e seleção

`/find` consulta addons em paralelo, cada um com timeout e isolamento de
exceção. O TMDB fornece metadados e normaliza a busca. Um exemplo válido:

```text
/find Night of the Living Dead (1968)
```

O catálogo usa tokens curtos nos callbacks, não URLs completas. Se o TMDB
falhar, a comparação fuzzy preserva resultados para não esconder todo o
catálogo.

## 9. Filmes publicados no canal

`/canal <busca>` pesquisa vídeos e documentos no histórico de
`STREAM_CHAT_ID`.

Ao escolher:

1. a conta dedicada lê a mensagem;
2. `stream_media` grava progressivamente em `channel/`;
3. o item é liberado quando atinge o buffer;
4. o download continua durante a sessão;
5. TMDB/IMDb ajudam a localizar a legenda;
6. tarefa e arquivo são removidos ao liberar a fonte.

Cada pedido recebe um nome temporário exclusivo. Falhas no download, legenda,
fila ou início acionam rollback e limpeza.

## 10. Comandos

### Busca e reprodução

| Comando | Descrição |
|---|---|
| `/find <filme>` | catálogo externo com TMDB |
| `/canal <filme>` | acervo publicado no canal |
| `/play <fonte>` | arquivo local ou URL |
| `/pick <número>` | fallback textual da última busca |

### Controles e fila

| Comando | Descrição |
|---|---|
| `/pause` / `/resume` | pausa ou retoma |
| `/stop` / `/skip` | encerra ou avança |
| `/restart` | reinicia o item |
| `/volume <0-200>` | ajusta volume |
| `/queue` / `/clear` | consulta ou limpa pendentes |
| `/remove <posição>` | remove um pendente |
| `/loop off\|item\|queue` | repetição |

### Legendas e estado

| Comando | Descrição |
|---|---|
| `/legenda on\|off` | liga ou desliga legenda |
| `/subdelay 500` | atrasa 500 ms |
| `/subdelay -500` | adianta 500 ms |
| `/subdelay 0` | remove o ajuste |
| `/nowplaying` | item atual |
| `/status` | saúde do serviço |
| `/uptime` | tempo do processo |

### Addons

| Comando | Autorização |
|---|---|
| `/addons` | administrador |
| `/addon info <nome>` | administrador |
| `/addon enable\|disable\|reload\|uninstall <nome>` | somente owner |

Lista sugerida para `/setcommands`:

```text
find - Pesquisar um filme
canal - Pesquisar filmes publicados no canal
pause - Pausar a reprodução
resume - Retomar a reprodução
stop - Encerrar a reprodução
skip - Pular para o próximo filme
queue - Mostrar a fila
clear - Limpar a fila pendente
nowplaying - Mostrar o filme atual
status - Mostrar o estado do serviço
volume - Ajustar o volume
legenda - Ativar ou desativar legenda
subdelay - Ajustar sincronização da legenda
help - Mostrar todos os comandos
```

## 11. Legendas e idioma

Marcadores como `Dublado`, `Nacional` e bandeira brasileira impedem legenda
automática. `Dual Áudio` mantém a legenda porque a primeira faixa pode não ser
português.

`subdelay` corrige deslocamento constante. Se o erro cresce ao longo do filme,
a legenda pertence a outro corte ou frame rate e deve ser substituída.

## 12. Addons

Cada addon possui `manifest.json`, `plugin.py` e `README.md`. Ele implementa
`BaseAddon` e devolve resultados e fontes; não controla fila ou Telegram.

O addon Stremio separa catálogo e stream em `config/addons/stremio.json`. O
Internet Archive usa a API pública e prioriza H.264.

Addons executam Python no processo que contém a sessão Telegram. Instale apenas
código revisado. A instalação é manual; ações destrutivas são do owner.

## 13. Logs e diagnóstico

| Arquivo | Conteúdo |
|---|---|
| `stream.log` | inicialização, fila e transportes |
| `ffmpeg.log` | saída do FFmpeg |
| `bot.log` | handlers e eventos do bot |
| `errors.log` | exceções e falhas permanentes |

```bash
docker compose ps
docker compose logs --tail 100 bridge
docker compose exec bridge sh -lc "tail -n 100 /app/logs/stream.log"
docker compose exec bridge sh -lc "tail -n 100 /app/logs/errors.log"
```

Logs antigos continuam no arquivo. Sempre compare timestamps.

## 14. Solução de problemas

### Sem permissão

- use o privado do bot correto;
- confirme que o usuário é administrador atual de `STREAM_CHAT_ID`;
- confirme que o bot está no canal e consulta membros;
- confira `OWNER_USER_ID`.

### Bot não responde

- valide `BOT_TOKEN`;
- veja se o container está `Up`;
- procure erro atual de inicialização;
- confirme que não existe outra instância com o mesmo token.

### TMDB não encontra um filme

- use título e ano sem formatação incomum;
- confirme chave e conectividade;
- teste o título original;
- confira timeout/status HTTP nos logs.

### Filme baixa, mas a live não começa

- confirme permissão da conta para gerenciar lives;
- valide `STREAM_CHAT_ID`;
- procure falha em `prepare_rtmp`;
- confirme permissão para participar da chamada no fallback;
- verifique `ffmpeg.log` e o buffer.

### Qualidade ruim ou travamentos

- aumente o buffer;
- escolha fonte com mais seeds;
- evite 4K em hardware limitado;
- verifique CPU, disco e upload;
- evite suspensão do HD externo.

### qBittorrent conecta, mas o arquivo não existe

É quase sempre divergência entre o caminho Windows informado pela Web API e o
caminho Linux visto pelo container. Confira o bind mount e o par
`QBITTORRENT_SAVE_PATH`/`QBITTORRENT_LOCAL_PATH`.

### Legenda fora de sincronia

- use `/subdelay` para offset constante;
- desligue se o áudio estiver em português;
- confirme o filme/ano do TMDB;
- para drift crescente, procure legenda da mesma edição.

### Live parece aberta após `/stop`

O FFmpeg encerra o ingest imediatamente, mas a interface do Telegram pode levar
alguns segundos para refletir o fim. Confira se existe outro encoder ou outra
instância usando a mesma live.

## 15. Testes e qualidade

```bash
uv sync --all-groups
uv run ruff check app tests
uv run black --check app tests
uv run mypy
uv run pytest
```

Testes de FIFOs/processos POSIX são ignorados no Windows e executados no CI
Linux. Alterações de reprodução devem cobrir sucesso, falha, rollback,
conclusão, troca de transporte e persistência.

## 16. Backup e replicação

Na máquina antiga, registre a revisão e pare a instância para evitar duas
conexões usando o mesmo bot e a mesma sessão:

```bash
git rev-parse HEAD
docker compose stop bridge
```

Na máquina nova:

```bash
git clone https://github.com/SanNw/telegram-video-bridge.git
cd telegram-video-bridge
git checkout <hash obtido na máquina antiga>
```

Depois:

1. copie `.env` por canal seguro, fora do Git;
2. copie `config/addons/` se personalizado;
3. instale/configure qBittorrent e confirme o novo caminho de filmes;
4. confirme permissões da conta e do bot no canal;
5. suba o Compose e valide os logs;
6. só então remova a instalação antiga.

Os volumes são opcionais. `data` preserva fila e estado dos addons; `logs`
preserva histórico. Descubra os nomes reais com:

```bash
docker volume ls
```

Exemplo de backup no diretório atual, substituindo o nome do volume:

```powershell
docker run --rm -v telerion_data:/source -v "${PWD}:/backup" alpine `
  tar czf /backup/telerion-data.tgz -C /source .
```

Para restaurar, suba o projeto uma vez para criar o volume, pare o serviço e
extraia o arquivo no volume correspondente, substituindo o nome do volume:

```powershell
docker compose up -d
docker compose stop bridge
docker run --rm -v telerion_data:/target -v "${PWD}:/backup" alpine `
  sh -c "rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null; tar xzf /backup/telerion-data.tgz -C /target"
docker compose up -d
```

Esse procedimento substitui o conteúdo do volume de destino; confirme o nome
antes de executá-lo. Não restaure uma fila antiga se os arquivos temporários
não foram copiados: uma fila vazia é mais previsível.

Downloads temporários não precisam ser copiados.

Para trocar de canal, atualize `CHAT_ID` e `STREAM_CHAT_ID`, adicione as duas
identidades e recrie o container. Para trocar a conta, gere nova sessão antes
de remover a antiga.

Se um segredo vazar:

- revogue `BOT_TOKEN` no BotFather;
- encerre sessões e gere nova `SESSION_STRING`;
- altere a senha do qBittorrent;
- remova segredos do histórico Git, não só do último commit.

## 17. Limitações conhecidas

- uma reprodução por instância;
- seek aproximado em pausa, volume e legenda durante RTMP;
- primeira faixa de áudio selecionada por padrão;
- `subdelay` corrige offset, não drift;
- qualidade depende de CPU, upload, disco, seeds e fonte;
- addons externos e Telegram podem mudar;
- conteúdo sem TMDB/IMDb pode não receber legenda.

## 18. Checklist de produção

- [ ] conta dedicada com 2FA e sem uso pessoal;
- [ ] bot e conta administradores do canal;
- [ ] `STREAM_CHAT_ID` confirmado;
- [ ] TMDB testado;
- [ ] qBittorrent restrito à rede confiável;
- [ ] downloads no HD correto;
- [ ] `REMOVE_TORRENT_AFTER_PLAY=true`;
- [ ] buffer compatível com a conexão;
- [ ] nenhum segredo versionado;
- [ ] comandos críticos testados;
- [ ] espaço em disco e logs monitorados;
- [ ] backup seguro do `.env` fora do repositório.
