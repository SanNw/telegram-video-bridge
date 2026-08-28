# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and semantic versioning.

## [Unreleased]

### Added

- Botão `Sair` no painel de reprodução para encerrar a live e apagar o torrent atual com seus arquivos.
- Diagramas editoriais da arquitetura e do fluxo completo de reprodução.
- Trilingual community documentation and cinematic README system.
- Installation doctor, Docker healthcheck and portable host-media path.
- Community governance, Docker CI, Dependabot and GHCR release workflow.

### Fixed

- `Parar` agora descarta a live RTMP do Telegram em vez de deixá-la aberta como pausada.
- Torrents com caminhos maliciosos não conseguem acessar arquivos fora do diretório de download.
- URLs de mídia e legenda destinadas a redes internas são rejeitadas antes de qualquer conexão.
- Downloads de legenda não seguem redirecionamentos fornecidos por addons externos.

## [0.1.0] - 2026-08-24

### Added

- Initial Telegram cinema pipeline with TMDB, addons, queue, subtitles, qBittorrent, RTMP and PyTgCalls fallback.
