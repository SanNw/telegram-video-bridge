# 🧩 Contrato de addons

[English](ADDONS.en.md) · [Español](ADDONS.es.md)

Cada addon contém `manifest.json` e `plugin.py`. O manifesto exige `name`, `version` e `entrypoint`; `min_core_version` informa a versão mínima do Telerion. A classe deve herdar `BaseAddon` e implementar `search`, `get_metadata` e `get_streams` como funções assíncronas.

Retorne listas vazias quando não houver resultado. Não acesse Telegram, FFmpeg, fila ou arquivos fora da configuração do addon. Implemente `health` para dependências externas e `close` para liberar clientes HTTP. Addons rodam no processo principal: não registre segredos e não execute comandos do sistema.

```bash
uv run python scripts/new_addon.py meu_addon
uv run pytest tests/test_addon_system_manifest.py tests/test_addon_system_loader.py
```
