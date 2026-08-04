# stremio

Addon oficial que consome addons Stremio externos (protocolo HTTP/JSON),
configurados pelo operador — não baixa nem executa código de terceiros, só
fala HTTP com uma URL já configurada em `config/addons/stremio.json`.

## O que faz

- `search(query)` — para cada `upstream` configurado, consulta cada
  `catalog` declarado (`GET /catalog/<type>/<id>/search=<query>.json`) e
  agrega os `metas` retornados.
- `get_metadata(media_id)` — busca `GET /meta/<type>/<id>.json` no upstream
  de origem.
- `get_streams(media_id)` — busca `GET /stream/<type>/<id>.json`; descarta
  entradas sem `url` direta (ex.: fontes só com `infoHash` de torrent, que o
  pipeline FFmpeg atual não sabe reproduzir — só http/https/hls/rtmp/rtsp).
- `health()` — não saudável se nenhum upstream estiver configurado, ou se
  algum `GET /manifest.json` falhar.

`media_id` internamente carrega `<upstream>:<type>:<id_do_upstream>` (ex.:
`"cinemeta:movie:tt1254207"`) para que `get_metadata`/`get_streams` saibam a
qual upstream voltar — é um detalhe de implementação, nunca exposto ao
operador (que só vê o resultado numerado de `/find`).

## Limitações e avisos

- Sem upstreams configurados, o addon fica inerte: `/find` não retorna nada
  dele, e `/addon info stremio` mostra `health` não saudável.
- Só resolve streams com `url` HTTP(S)/HLS/RTMP/RTSP direta — magnet
  links/torrents (`infoHash`) não são suportados (mesma limitação de
  `/play`, ver `app/utils/sanitize.py`).
- Este addon não valida a licença ou legalidade do conteúdo servido pelo
  upstream — isso é responsabilidade de qual addon Stremio o operador
  escolhe configurar.

## Configuração

Obrigatória para o addon fazer algo útil — `config/addons/stremio.json` (ou
`.yaml`/`.yml`):

```json
{
  "upstreams": [
    {
      "name": "cinemeta",
      "base_url": "https://v3-cinemeta.strem.io",
      "catalogs": [
        { "type": "movie", "id": "top" },
        { "type": "series", "id": "top" }
      ]
    }
  ]
}
```

- `name` — identificador curto do upstream, usado internamente no
  `media_id` (não exibido ao operador).
- `base_url` — raiz do addon Stremio (sem `/manifest.json` no final).
- `catalogs` — lista de `{type, id}` a pesquisar em `/find`; os valores
  válidos dependem de cada upstream (consulte o `/manifest.json` dele).

## Estrutura

```
addons/stremio/
  manifest.json   metadados do addon (nome, versão, entrypoint)
  plugin.py       implementação de BaseAddon sobre StremioAddonClient
  README.md       este arquivo
```
