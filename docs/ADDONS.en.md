# 🧩 Addon contract

[Português](ADDONS.md) · [Español](ADDONS.es.md)

Each addon contains `manifest.json` and `plugin.py`. The manifest requires `name`, `version` and `entrypoint`; `min_core_version` declares the minimum Telerion version. The class must extend `BaseAddon` and asynchronously implement `search`, `get_metadata` and `get_streams`.

Return empty lists when nothing is found. Do not access Telegram, FFmpeg, the queue or files outside addon configuration. Implement `health` for external dependencies and `close` for HTTP clients. Addons run in the main process: never log secrets or execute system commands.
