# archive_org

Addon oficial que busca filmes de domínio público / licença aberta no
[Internet Archive](https://archive.org).

## O que faz

- `search(query)` — consulta `archive.org/advancedsearch.php` filtrando por
  `mediatype:(movies)` e `licenseurl:(*publicdomain* OR *creativecommons*)`.
  Só devolve itens com licença aberta declarada nos metadados do próprio
  archive.org.
- `get_metadata(media_id)` — título, descrição e ano via
  `archive.org/metadata/<identifier>`.
- `get_streams(media_id)` — varre `files[]` do item, filtra por formato de
  vídeo conhecido (`MPEG4`, `h.264`, `512Kb MPEG4`, `MPEG2`, `Matroska`) e
  extensão `.mp4`/`.m4v`, monta a URL de download
  (`archive.org/download/<identifier>/<filename>`) e prioriza `h.264` por
  ser o formato mais compatível/menor.
- `health()` — checagem HTTP real contra `archive.org`.

## Limitações e avisos

- O filtro de licença depende dos metadados que o uploader original
  preencheu no archive.org — **não é uma garantia legal absoluta**. Itens
  mal categorizados podem passar pelo filtro ou faltar nele.
- Não há garantia de que todo resultado tenha um arquivo de vídeo em formato
  utilizável; `get_streams` pode devolver lista vazia mesmo para um item
  encontrado no `search`.
- Sem scraping, sem chave de API — usa só os endpoints públicos e
  documentados do archive.org.

## Configuração

Nenhuma obrigatória. Opcionalmente, `config/addons/archive_org.json` (ou
`.yaml`/`.yml`) aceita:

```json
{
  "timeout_seconds": 10.0
}
```

## Estrutura

```
addons/archive_org/
  manifest.json   metadados do addon (nome, versão, entrypoint)
  plugin.py       implementação de BaseAddon
  README.md       este arquivo
```
