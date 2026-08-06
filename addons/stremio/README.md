# stremio

Addon oficial que consome addons Stremio externos (protocolo HTTP/JSON),
configurados pelo operador — não baixa nem executa código de terceiros, só
fala HTTP com uma URL já configurada em `config/addons/stremio.json`.

## Provedores separados

O ecossistema Stremio separa quem **lista** mídias (catálogo) de quem **serve**
fontes reproduzíveis (stream). O Cinemeta fornece catálogo; o Torrentio
fornece apenas streams, sem catálogo. Este addon reflete essa divisão:

- **Provedores de catálogo** (`catalogs`) — alimentam `search` e
  `get_metadata`.
- **Provedores de stream** (`streams`) — alimentam `get_streams`. Um provedor
  de catálogo pode também aparecer como provedor de stream (ex.: addon em que
  o mesmo host faz tudo). A ligação entre os dois papéis é por `media_id`,
  não por compartilhar a `base_url`.

## O que faz

- `search(query)` — para cada provedor de catálogo configurado, consulta cada
  `catalog` declarado (`GET /catalog/<type>/<id>/search=<query>.json`) e
  agrega os `metas` retornados.
- `get_metadata(media_id)` — busca `GET /meta/<type>/<id>.json` no provedor de
  catálogo que originou o `media_id`.
- `get_streams(media_id)` — consulta `GET /stream/<type>/<id>.json` em **todos
  os provedores de stream configurados** em paralelo, agrega os resultados,
  remove duplicatas por `url` (ou por `infoHash`, quando não há `url` direta —
  mesma fonte torrent servida por dois provedores) e mantém a primeira
  ocorrência (que reflete a ordem dos provedores). Streams com `url` HTTP(S)
  direta viram candidatos reproduzíveis normalmente; streams só com
  `infoHash` (torrent sem serviço de debrid, caso comum do Torrentio) viram
  candidatos resolvidos via qBittorrent — ver
  [suporte a torrents](../../README.md#suporte-a-torrents-via-qbittorrent) no
  README principal. Também extrai seeds/tamanho do texto do stream (convenção
  comum entre addons torrent tipo Torrentio, ex.: `👤 156 💾 21.87 GB`),
  descarta candidatos com mais de 4GB confirmados e ordena o restante por
  seeds decrescente. Candidatos sem seeds/tamanho reconhecíveis no texto não
  são descartados (não dá pra confirmar violação do limite de 4GB), mas ficam
  ordenados por último dentre os empatados.
- `health()` — valida o `GET /manifest.json` de **todos** os provedores
  (catálogo e stream); não saudável se nenhum estiver configurado ou se algum
  falhar, listando os nomes inacessíveis. Saudável: informa a contagem por
  papel (catálogo/stream).

`media_id` internamente carrega `<provedor_de_catalogo>:<type>:<id>` (ex.:
`"cinemeta:movie:tt1254207"`) para que `get_metadata` saiba qual provedor de
catálogo consultar. Em `get_streams` o nome do provedor é ignorado — todos os
provedores de stream são consultados — é um detalhe de implementação, nunca
exposto ao operador (que só vê o resultado numerado de `/find`).

## Limitações e avisos

- Sem provedores configurados, o addon fica inerte: `/find` não retorna nada
  dele, e `/addon info stremio` mostra `health` não saudável.
- `base_url` deve ser **HTTP/HTTPS** (ex.: `https://torrentio.strem.fun/...`),
  não o esquema `stremio://` da UI do Stremio — este addon só fala HTTP.
- Streams só com `infoHash` (torrent sem debrid) são resolvidos via
  qBittorrent, exigindo uma instância configurada e acessível pelo bridge —
  ver [suporte a torrents](../../README.md#suporte-a-torrents-via-qbittorrent)
  no README principal. Sem qBittorrent configurado, esses candidatos falham
  ao serem escolhidos (`/pick` tenta o próximo; o botão de `/find` mostra um
  alerta pedindo para buscar de novo).
- Este addon não valida a licença ou legalidade do conteúdo servido pelo
  provedor — isso é responsabilidade de qual addon Stremio o operador
  escolhe configurar.

## Configuração

Obrigatória para o addon fazer algo útil — `config/addons/stremio.json` (ou
`.yaml`/`.yml`):

```json
{
  "catalogs": [
    {
      "name": "cinemeta",
      "base_url": "https://v3-cinemeta.strem.io",
      "catalogs": [
        { "type": "movie", "id": "top" },
        { "type": "series", "id": "top" }
      ]
    }
  ],
  "streams": [
    {
      "name": "torrentio",
      "base_url": "https://torrentio.strem.fun/qualityfilter=4k,cam|sizefilter=4gb"
    }
  ]
}
```

- `catalogs` — provedores de catálogo. Cada entrada tem:
  - `name` — identificador curto, usado no `media_id` (não exibido ao
    operador). Deve ser único entre catálogo e stream se quiser compartilhar
    o mesmo `httpx` client; nomes distintos criam clientes independentes.
  - `base_url` — raiz do addon Stremio (sem `/manifest.json` no final; HTTP/HTTPS).
  - `catalogs` — lista de `{type, id}` a pesquisar em `/find`; os valores
    válidos dependem de cada provedor (consulte o `/manifest.json` dele).
- `streams` — provedores de stream. Cada entrada tem `name` e `base_url`
  (HTTP/HTTPS). Um mesmo `name` já registrado como catálogo reutiliza o mesmo
  cliente HTTP (não duplica `httpx.AsyncClient`).

### Compatibilidade (formato legado `upstreams`)

A config antiga continua funcionando sem alteração: cada entrada de
`upstreams` é tratada simultaneamente como provedor de catálogo e de stream,
reproduzindo o comportamento anterior (onde o mesmo upstream fazia tudo). O
formato novo (`catalogs`/`streams`) prevalece se ambos coexistirem no arquivo.

```json
{
  "upstreams": [
    {
      "name": "cinemeta",
      "base_url": "https://v3-cinemeta.strem.io",
      "catalogs": [{ "type": "movie", "id": "top" }]
    }
  ]
}
```

## Estrutura

```
addons/stremio/
  manifest.json   metadados do addon (nome, versão, entrypoint)
  plugin.py       implementação de BaseAddon sobre StremioAddonClient
  README.md       este arquivo
```
