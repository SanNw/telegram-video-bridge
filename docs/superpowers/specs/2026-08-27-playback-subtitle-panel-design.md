# Painel de reprodução e seleção de legendas

## Objetivo

Transformar a mesma Rich Message usada para escolher e iniciar um filme em um painel persistente de reprodução. O painel deve controlar o player sem criar novas mensagens e oferecer seleção manual de legendas locais ou do OpenSubtitles.

## Escopo

- Exibir o painel imediatamente após a fonte entrar na fila.
- Reutilizar os callbacks existentes de pausa, retomada, parada, avanço, reinício, loop e volume.
- Adicionar submenu de volume e submenu de legendas na mesma mensagem.
- Listar legendas `.srt` existentes em `.subtitles`.
- Buscar, baixar e selecionar alternativas em português pela API oficial do OpenSubtitles.
- Ligar/desligar a legenda e ajustar atraso sem reiniciar o filme do zero.
- Manter os rótulos mobile-safe: sem emojis, sem quebras e com até 16 caracteres.

Não faz parte deste trabalho: suporte a formatos de legenda diferentes de SRT, edição do conteúdo da legenda, sincronização automática por áudio ou tradução automática.

## Experiência na Rich Message

### Painel principal

O painel mostra o filme atual, estado da reprodução, volume, legenda ativa e atraso. Os botões são organizados em linhas curtas:

- `Pausar`, `Retomar`
- `Parar`, `Pular`, `Reiniciar`
- `Volume`, `Legendas`, `Fila`
- `Início`

Após qualquer ação, o handler edita a mesma mensagem e renderiza novamente o painel com o estado atualizado. Erros aparecem na própria mensagem com `Tentar novamente` e `Voltar` quando a ação puder ser recuperada.

### Submenu de volume

Exibe `50%`, `100%`, `150%`, `200%` e `Voltar`. A seleção chama o callback `control:volume:<valor>` já existente e retorna ao painel principal.

### Submenu de legendas

Exibe:

- legenda ativa ou `Sem legenda`;
- `Ligar` ou `Desligar`, conforme o estado atual;
- `Arquivos locais`;
- `Buscar online`;
- `Adiantar`, `Zerar`, `Atrasar`;
- `Voltar`.

`Adiantar` e `Atrasar` abrem presets de `-1000`, `-500`, `+500` e `+1000 ms`. O comando `/subdelay` continua disponível para valores personalizados.

### Seleção de faixa

Resultados locais e online usam uma linha descritiva no corpo da mensagem e um botão curto `Escolher N`. No máximo cinco resultados aparecem por página. A faixa ativa é identificada no texto, não por emoji.

## Estado e persistência

`QueueItem` recebe dois campos opcionais e retrocompatíveis:

- `media_id`: IMDb ID usado na busca de legendas;
- `display_title`: título legível usado no painel e nos nomes de arquivo.

Filas antigas continuam carregando porque ambos têm valor padrão `None`.

O estado transitório dos resultados do OpenSubtitles fica no handler por usuário, com tokens curtos usados nos callbacks. URLs de download, credenciais e IDs internos não entram em `callback_data` nem em logs.

## Integração OpenSubtitles

Um cliente assíncrono pequeno usa `httpx`, já instalado, contra `https://api.opensubtitles.com/api/v1`.

Configuração no `.env`:

- `OPENSUBTITLES_API_KEY`: chave da aplicação;
- `OPENSUBTITLES_USERNAME`: usuário da conta;
- `OPENSUBTITLES_PASSWORD`: senha da conta;
- `OPENSUBTITLES_USER_AGENT`: identificação da aplicação, com padrão `Telerion v1`.

O cliente:

1. autentica em `/login` e mantém o token somente em memória;
2. busca em `/subtitles` pelo IMDb ID e idiomas portugueses;
3. prioriza `pt-BR`/português brasileiro, depois português;
4. solicita o link temporário em `/download` usando o `file_id` escolhido;
5. baixa no máximo 2 MB e grava em `.subtitles` com nome seguro;
6. renova a autenticação uma vez quando o token expirar.

Falha de rede, autenticação, limite da API ou resposta inválida produz mensagem amigável e preserva o painel. Credenciais, token e URL temporária nunca são registrados.

## Arquivos locais

O serviço lista somente arquivos `.srt` diretamente dentro de `qbittorrent_local_path/.subtitles`. Caminhos resolvidos devem permanecer dentro dessa pasta. Nomes são ordenados alfabeticamente e exibidos sem o caminho absoluto.

## Troca de legenda durante a reprodução

`QueueManager` ganha uma operação para atualizar `subtitle_path`. `PlaybackService` ganha `set_subtitle_path(path | None)`, seguindo a mesma estratégia já usada por `set_subtitle_delay` e `set_subtitles_enabled`:

1. calcula o tempo decorrido;
2. persiste a nova faixa no item atual;
3. chama `FFmpegStreamer.change_source` com a mesma mídia e a nova legenda;
4. retoma aproximadamente do tempo anterior;
5. mantém volume e atraso atuais.

Selecionar `Sem legenda` desativa a faixa sem apagar o arquivo. Selecionar outra faixa ativa legendas automaticamente.

## Integração entre handlers

- `menu.py` passa a aceitar callbacks `subtitle:` e renderiza painel/submenus.
- `addons.py`, após enfileirar a fonte, renderiza o mesmo painel principal em vez da confirmação textual isolada.
- `register_search` recebe a referência de `PlaybackService` necessária para montar o estado atual após a reprodução começar.
- Os comandos existentes continuam como fallback e usam os mesmos métodos do serviço.

## Erros e concorrência

- Se nada estiver tocando, controles e legendas mostram alerta sem alterar a mensagem.
- Se o filme mudar enquanto uma busca online está aberta, a seleção é recusada como expirada.
- Uma seleção de legenda por vez é aplicada sob o lock já usado pelo `PlaybackService` para mudanças de fonte.
- Resultados online são vinculados ao usuário e ao `media_id` atual.
- Falhas ao editar Rich Messages usam o fallback existente.

## Testes

- Formatação mobile-safe de todos os novos painéis.
- Painel exibido depois de uma fonte ser enfileirada.
- Cada callback existente continua chamando o método correto.
- Liga/desliga e presets de atraso atualizam a mesma mensagem.
- Listagem local aceita apenas `.srt` dentro da pasta permitida.
- Busca OpenSubtitles envia cabeçalhos corretos sem expor segredos.
- Download limita tamanho, sanitiza nome e grava na pasta correta.
- Seleção troca a faixa preservando posição, volume e atraso.
- Tokens expirados ou pertencentes a outro filme são rejeitados.
- Compatibilidade ao carregar filas antigas sem `media_id` e `display_title`.
- Suíte completa, Ruff, Black, Mypy e teste manual no Telegram mobile.

## Publicação

A implementação será feita em worktree isolada, integrada localmente após aprovação, validada novamente na `main` e publicada por rebuild do contêiner `bridge`.
