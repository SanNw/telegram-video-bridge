# Manual operativo de Telerion

[Português](MANUAL.md) · [English](MANUAL.en.md)

## 1. Arquitectura necesaria

Telerion usa dos identidades de Telegram: una cuenta de usuario dedicada autenticada mediante `SESSION_STRING` para MTProto y acceso al directo, y un bot opcional de BotFather para comandos privados y botones. La cuenta y el bot deben ser administradores del canal con directos habilitados.

Los componentes externos son TMDB para metadatos, qBittorrent para descargas progresivas y FFmpeg para codificación. qBittorrent y Telerion deben acceder al mismo directorio, aunque utilicen rutas diferentes.

## 2. Instalación

```bash
git clone https://github.com/SanNw/telegram-video-bridge.git
cd telegram-video-bridge
cp .env.example .env
uv sync
uv run python -u scripts/generate_session.py
uv run python scripts/list_chats.py @usuario_del_operador
uv run python -m app.doctor
docker compose up -d --build
```

Configura `MEDIA_HOST_PATH` con el directorio del host montado en `/app/media/torrents`. Usa en `QBITTORRENT_SAVE_PATH` la ruta informada por qBittorrent y `QBITTORRENT_LOCAL_PATH=/app/media/torrents` dentro de Docker.

## 3. Operación

Comprueba el servicio con `docker compose ps` y `docker compose logs --tail 100 bridge`. Usa `/status` para consultar la cola, FFmpeg y el transporte Telegram. Detén el servicio con `docker compose stop bridge`; no uses `docker compose down -v` salvo que quieras borrar datos persistentes.

## 4. Seguridad

Nunca publiques `.env`, sesiones, tokens ni contraseñas. Usa una cuenta dedicada con 2FA. Mantén qBittorrent en una red confiable. Los addons se ejecutan dentro del proceso que contiene las credenciales y deben revisarse antes de instalarlos.

## 5. Copias y actualizaciones

Registra el commit o tag actual, detén la instancia anterior, guarda `.env` fuera de Git y conserva el volumen `data` si necesitas la cola y el estado de addons. Actualiza con `git pull --ff-only` y `docker compose up -d --build --force-recreate`. No ejecutes dos instancias con el mismo token y sesión.

## 6. Solución de problemas

- Bot sin respuesta: comprueba `BOT_TOKEN`, membresía del canal y que no exista otra instancia.
- El directo no comienza: revisa permisos, `STREAM_CHAT_ID`, logs de FFmpeg y ancho de banda.
- Archivo torrent inexistente: corrige el mapeo entre `MEDIA_HOST_PATH`, `QBITTORRENT_SAVE_PATH` y `QBITTORRENT_LOCAL_PATH`.
- Subtítulo desfasado: usa `/subdelay`; un drift creciente requiere otra edición del subtítulo.

El [manual en portugués](MANUAL.md) continúa siendo la referencia exhaustiva de todas las variables.
