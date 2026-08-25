# 🧩 Contrato de addons

[Português](ADDONS.md) · [English](ADDONS.en.md)

Cada addon contiene `manifest.json` y `plugin.py`. El manifiesto requiere `name`, `version` y `entrypoint`; `min_core_version` indica la versión mínima de Telerion. La clase debe heredar de `BaseAddon` e implementar de forma asíncrona `search`, `get_metadata` y `get_streams`.

Devuelve listas vacías cuando no haya resultados. No accedas a Telegram, FFmpeg, la cola ni archivos fuera de la configuración del addon. Implementa `health` para dependencias externas y `close` para clientes HTTP. Los addons se ejecutan en el proceso principal: nunca registres secretos ni ejecutes comandos del sistema.
