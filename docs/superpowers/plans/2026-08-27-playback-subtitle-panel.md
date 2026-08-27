# Playback Subtitle Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Manter o controle da reprodução em uma única Rich Message e permitir troca manual de legendas locais ou do OpenSubtitles durante o filme.

**Architecture:** `PlaybackService` continua sendo o único dono do estado de reprodução e ganha troca de faixa preservando posição. Um `SubtitleService` isolado coordena arquivos locais e a API oficial por meio de `OpenSubtitlesClient`; `menu.py` mantém apenas estado transitório de paginação/tokens e renderiza tudo na mesma Rich Message.

**Tech Stack:** Python 3.13, asyncio, httpx, Pydantic Settings, Pyrogram, Telegram Rich Messages, FFmpeg, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-playback-subtitle-panel-design.md`

## Global Constraints

- Não adicionar dependências: reutilizar `httpx`, Pydantic, pathlib e asyncio.
- Botões Rich sem emojis, sem quebras e com no máximo 16 caracteres.
- Somente arquivos `.srt` diretamente em `qbittorrent_local_path/.subtitles`.
- Downloads de legenda limitados a 2 MB.
- Segredos, JWTs e URLs temporárias nunca entram em logs ou callback data.
- Filas antigas sem os novos campos continuam carregando.
- Comandos `/legenda` e `/subdelay` permanecem como fallback.
- Toda alteração de comportamento segue TDD e termina com commit próprio.

---

### Task 1: Persistir contexto do filme na fila

**Files:**
- Modify: `app/player/models.py`
- Modify: `app/services/playback_service.py`
- Modify: `app/services/addon_service.py`
- Test: `tests/test_player_models.py`
- Test: `tests/test_services_playback_service.py`
- Test: `tests/test_services_addon_service.py`

**Interfaces:**
- Produces: `QueueItem.media_id: str | None`, `QueueItem.display_title: str | None`.
- Produces: `PlaybackService.play(source_raw, requested_by, subtitle_path=None, *, media_id=None, display_title=None) -> int`.

- [ ] **Step 1: Write failing persistence tests**

```python
def test_queue_item_from_dict_accepts_legacy_payload() -> None:
    item = QueueItem.from_dict({
        "source": {"raw": "/media/a.mp4", "type": "local_file"},
        "requested_by": 1,
    })
    assert item.media_id is None
    assert item.display_title is None


def test_queue_item_roundtrip_preserves_movie_context() -> None:
    item = _item("a.mp4")
    item.media_id = "tt0133093"
    item.display_title = "The Matrix"
    restored = QueueItem.from_dict(item.to_dict())
    assert (restored.media_id, restored.display_title) == ("tt0133093", "The Matrix")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_player_models.py --no-cov`

Expected: FAIL because `QueueItem` has no movie-context fields.

- [ ] **Step 3: Add optional fields and serialization**

```python
@dataclass(slots=True)
class QueueItem:
    source: MediaSource
    requested_by: int
    subtitle_path: str | None = None
    subtitle_delay_ms: int = 0
    subtitles_enabled: bool = True
    media_id: str | None = None
    display_title: str | None = None
```

Include both keys in `to_dict()` and read them with `data.get(...)` in `from_dict()`.

- [ ] **Step 4: Write failing propagation tests**

```python
async def test_play_persists_movie_context(make_service: Any) -> None:
    service, queue, *_ = make_service()
    await service.play(
        "/media/a.mp4",
        1,
        media_id="tt0133093",
        display_title="The Matrix",
    )
    current = queue.snapshot().current
    assert current is not None
    assert (current.media_id, current.display_title) == ("tt0133093", "The Matrix")
```

Add an `AddonService` assertion that `_play_candidate` passes `result.media_id.rsplit(":", 1)[-1]` and `result.title`.

- [ ] **Step 5: Implement minimal propagation**

Construct `QueueItem` with the optional keyword-only values in `PlaybackService.play`. In `AddonService._play_candidate`, pass the normalized final media-ID segment and title in both subtitle and no-subtitle branches.

- [ ] **Step 6: Verify GREEN**

Run: `pytest -q tests/test_player_models.py tests/test_services_playback_service.py tests/test_services_addon_service.py --no-cov`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/player/models.py app/services/playback_service.py app/services/addon_service.py tests/test_player_models.py tests/test_services_playback_service.py tests/test_services_addon_service.py
git commit -m "feat: persist movie context in playback queue"
```

### Task 2: Trocar a faixa de legenda preservando a posição

**Files:**
- Modify: `app/player/queue_manager.py`
- Modify: `app/services/playback_service.py`
- Test: `tests/test_player_queue_manager.py`
- Test: `tests/test_services_playback_service.py`

**Interfaces:**
- Produces: `QueueManager.set_subtitle_path(path: str | None) -> None`.
- Produces: `PlaybackService.set_subtitle_path(path: str | None) -> None`.

- [ ] **Step 1: Write failing queue test**

```python
async def test_set_subtitle_path_updates_current_item(manager: QueueManager) -> None:
    await manager.add(_make_item("a.mp4"))
    await manager.set_subtitle_path("/media/.subtitles/matrix.srt")
    current = manager.snapshot().current
    assert current is not None
    assert current.subtitle_path == "/media/.subtitles/matrix.srt"
    assert current.subtitles_enabled is True
```

- [ ] **Step 2: Verify RED and implement queue mutation**

Run: `pytest -q tests/test_player_queue_manager.py::test_set_subtitle_path_updates_current_item --no-cov`

Implementation:

```python
async def set_subtitle_path(self, path: str | None) -> None:
    async with self._lock:
        if self._current is not None:
            self._current.subtitle_path = path
            self._current.subtitles_enabled = path is not None
            await self._save()
```

- [ ] **Step 3: Write failing playback test**

```python
async def test_set_subtitle_path_restarts_at_elapsed_position(make_service: Any) -> None:
    service, queue, streamer, call = make_service()
    await _play_one(service)
    service._current_started_at = datetime.now(UTC) - timedelta(seconds=42)
    await service.set_subtitle_path("/media/.subtitles/matrix-alt.srt")
    args = streamer.change_source.await_args.args
    assert args[2] == "/media/.subtitles/matrix-alt.srt"
    assert 41 <= args[4] <= 43
```

- [ ] **Step 4: Implement service method under the playback lock**

Reuse the elapsed-time and `prepare_rtmp()` pattern from `set_subtitle_delay`. Validate that a non-`None` path is an existing file before mutating the queue. Call `change_source` with current source, output URL, selected path, current delay, elapsed time and current volume.

- [ ] **Step 5: Verify GREEN**

Run: `pytest -q tests/test_player_queue_manager.py tests/test_services_playback_service.py --no-cov`

- [ ] **Step 6: Commit**

```bash
git add app/player/queue_manager.py app/services/playback_service.py tests/test_player_queue_manager.py tests/test_services_playback_service.py
git commit -m "feat: switch active subtitle track"
```

### Task 3: Cliente oficial do OpenSubtitles

**Files:**
- Create: `app/services/opensubtitles_client.py`
- Modify: `app/config/settings.py`
- Modify: `.env.example`
- Test: `tests/test_services_opensubtitles_client.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `SubtitleOption(file_id: int, language: str, release: str, downloads: int)`.
- Produces: `OpenSubtitlesClient.search(imdb_id: str) -> list[SubtitleOption]`.
- Produces: `OpenSubtitlesClient.download(file_id: int) -> bytes`.
- Produces: `OpenSubtitlesClient.close() -> None`.

- [ ] **Step 1: Add failing settings tests**

```python
def test_opensubtitles_is_disabled_without_api_key(make_settings: Any) -> None:
    settings = make_settings(opensubtitles_api_key=None)
    assert settings.opensubtitles_enabled is False


def test_opensubtitles_is_enabled_with_all_credentials(make_settings: Any) -> None:
    settings = make_settings(
        opensubtitles_api_key="key",
        opensubtitles_username="user",
        opensubtitles_password="secret",
    )
    assert settings.opensubtitles_enabled is True
```

- [ ] **Step 2: Add settings fields**

Use `SecretStr | None` for API key and password, `str | None` for username, and `str = "Telerion v1"` for user-agent. The computed `opensubtitles_enabled` property requires all three credentials.

- [ ] **Step 3: Write HTTP contract tests with `httpx.MockTransport`**

Cover:

```python
async def test_search_logs_in_and_returns_portuguese_files() -> None:
    # Assert POST /login carries username/password.
    # Assert GET /subtitles carries imdb_id without "tt" and languages="pt-BR,pt".
    # Return two literal file fixtures and assert SubtitleOption values.


async def test_download_uses_file_id_and_rejects_oversized_body() -> None:
    # Assert POST /download receives {"file_id": 123}.
    # Return a temporary link, then a body larger than 2 * 1024 * 1024.
    # Assert OpenSubtitlesError("Legenda excede 2 MB.").
```

- [ ] **Step 4: Verify RED**

Run: `pytest -q tests/test_services_opensubtitles_client.py tests/test_config.py --no-cov`

- [ ] **Step 5: Implement the minimal client**

Use one `httpx.AsyncClient(base_url="https://api.opensubtitles.com/api/v1")`. Default headers contain only `Api-Key`, `User-Agent` and JSON content type. Store JWT in memory and add `Authorization: Bearer <token>` per authenticated request. Retry login once on HTTP 401. Map network, auth, rate-limit and malformed responses to `OpenSubtitlesError` without embedding response bodies or URLs.

- [ ] **Step 6: Verify GREEN**

Run: `pytest -q tests/test_services_opensubtitles_client.py tests/test_config.py --no-cov`

- [ ] **Step 7: Document environment variables and commit**

```dotenv
OPENSUBTITLES_API_KEY=
OPENSUBTITLES_USERNAME=
OPENSUBTITLES_PASSWORD=
OPENSUBTITLES_USER_AGENT=Telerion v1
```

```bash
git add app/services/opensubtitles_client.py app/config/settings.py .env.example tests/test_services_opensubtitles_client.py tests/test_config.py
git commit -m "feat: add OpenSubtitles API client"
```

### Task 4: Serviço de seleção local e online

**Files:**
- Create: `app/services/subtitle_service.py`
- Test: `tests/test_services_subtitle_service.py`

**Interfaces:**
- Consumes: `OpenSubtitlesClient.search`, `OpenSubtitlesClient.download`.
- Produces: `LocalSubtitle(token: str, name: str, path: Path)`.
- Produces: `SubtitleService.list_local() -> list[LocalSubtitle]`.
- Produces: `SubtitleService.search(imdb_id: str) -> list[SubtitleOption]`.
- Produces: `SubtitleService.download(option: SubtitleOption, imdb_id: str) -> Path`.

- [ ] **Step 1: Write failing local-file tests**

```python
async def test_list_local_returns_only_direct_srt_files(tmp_path: Path) -> None:
    root = tmp_path / ".subtitles"
    root.mkdir()
    (root / "a.srt").write_text("ok", encoding="utf-8")
    (root / "ignore.txt").write_text("no", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "b.srt").write_text("no", encoding="utf-8")
    service = SubtitleService(root, client=None)
    assert [item.name for item in service.list_local()] == ["a.srt"]
```

Add a test that a forged token/path outside `root.resolve()` is rejected.

- [ ] **Step 2: Implement safe local listing**

Use `Path.glob("*.srt")`, `is_file()`, `resolve()` and `relative_to(root.resolve())`. Tokens are decimal indexes stored in a private dictionary rebuilt on each list call; callback data never carries a path.

- [ ] **Step 3: Write failing online download test**

```python
async def test_download_writes_safe_srt_name(tmp_path: Path) -> None:
    client = AsyncMock()
    client.download.return_value = b"1\n00:00:00,000 --> 00:00:01,000\nOi\n"
    service = SubtitleService(tmp_path, client)
    path = await service.download(SubtitleOption(7, "pt-BR", "../../Matrix", 10), "tt0133093")
    assert path.parent == tmp_path.resolve()
    assert path.suffix == ".srt"
```

- [ ] **Step 4: Implement online delegation and safe write**

Normalize the release with the existing filename sanitization pattern or a local regex `[A-Za-z0-9._-]`, prefix with IMDb ID, cap filename to 120 characters, create the root directory, and write bytes with `asyncio.to_thread`.

- [ ] **Step 5: Verify and commit**

Run: `pytest -q tests/test_services_subtitle_service.py --no-cov`

```bash
git add app/services/subtitle_service.py tests/test_services_subtitle_service.py
git commit -m "feat: manage local and online subtitles"
```

### Task 5: Formatar painel e submenus mobile-safe

**Files:**
- Modify: `app/bot/formatting.py`
- Test: `tests/test_bot_formatting.py`

**Interfaces:**
- Produces: `format_playback_panel(status, state) -> dict[str, object]`.
- Produces: `format_volume_panel(status) -> dict[str, object]`.
- Produces: `format_subtitle_panel(current) -> dict[str, object]`.
- Produces: `format_subtitle_options(title, entries, page, prefix) -> dict[str, object]`.

- [ ] **Step 1: Write failing callback/action tests**

```python
def test_playback_panel_exposes_controls_without_mobile_unsafe_labels() -> None:
    message = format_playback_panel(_status(), _state_with_current())
    assert {
        "control:pause", "control:resume", "control:stop", "control:skip",
        "control:restart", "menu:volume", "subtitle:menu", "menu:queue",
    } <= rich_callback_actions(message)
    assert_mobile_safe_buttons(message)


def test_subtitle_panel_exposes_toggle_search_local_and_delay() -> None:
    message = format_subtitle_panel(_item("a.mp4"))
    assert {
        "subtitle:toggle", "subtitle:local:0", "subtitle:search:0",
        "subtitle:delay", "menu:controls",
    } <= rich_callback_actions(message)
    assert_mobile_safe_buttons(message)
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q tests/test_bot_formatting.py -k "playback_panel or subtitle_panel" --no-cov`

- [ ] **Step 3: Implement pure formatters**

Reuse `_button` and `_button_row`. Put long movie/subtitle names in paragraph blocks and use `Escolher N` buttons. The main panel receives the current `QueueItem` and `ServiceStatus`; do not perform service calls in formatting code.

- [ ] **Step 4: Verify GREEN and commit**

Run: `pytest -q tests/test_bot_formatting.py --no-cov`

```bash
git add app/bot/formatting.py tests/test_bot_formatting.py
git commit -m "feat: format playback and subtitle panels"
```

### Task 6: Implementar callbacks na mesma Rich Message

**Files:**
- Modify: `app/bot/handlers/menu.py`
- Modify: `app/bot/handlers/addons.py`
- Modify: `app/bot/client.py`
- Modify: `app/main.py`
- Test: `tests/test_bot_menu.py`
- Test: `tests/test_bot_movie_flow.py`
- Test: `tests/test_bot_client.py`

**Interfaces:**
- Consumes: `SubtitleService`, `PlaybackService.set_subtitle_path` and Task 5 formatters.
- Produces callbacks: `menu:volume`, `subtitle:menu`, `subtitle:toggle`, `subtitle:delay:<ms>`, `subtitle:local:<token>`, `subtitle:search:<page>`, `subtitle:pick:<token>`.

- [ ] **Step 1: Write failing post-play panel test**

```python
async def test_source_success_replaces_progress_with_playback_panel(make_settings: Any) -> None:
    # Dispatch movie:0, then source:0.
    # Assert final Rich Message has control:pause, control:stop and subtitle:menu.
```

- [ ] **Step 2: Pass `PlaybackService` into the search handler**

Change `addons.register_search(..., playback: PlaybackService | None = None)` and pass it from `build_bot`. After success, render `format_playback_panel(playback.status(), playback.queue_snapshot())`; retain the current textual confirmation only when `playback is None` in legacy tests/clients.

- [ ] **Step 3: Write failing subtitle callback tests**

Cover literal behavior:

```python
async def test_local_subtitle_selection_updates_same_message() -> None:
    # subtitle:local:0 -> list, subtitle:local-pick:0 -> playback.set_subtitle_path(path)
    # Assert edit_rich_message called and no send_message call.


async def test_online_result_is_bound_to_current_imdb_id() -> None:
    # Open search for tt0133093, replace current queue item with tt0234215,
    # then click subtitle:pick:0 and assert an expired alert.
```

- [ ] **Step 4: Implement handler state and actions**

Expand `_is_menu_callback` to accept `subtitle:`. Keep a per-user dictionary containing current media ID, local token map and online option map. Before applying any token, compare stored media ID with `playback.queue_snapshot().current.media_id`. Toggle calls `set_subtitles_enabled`; delay calls `set_subtitle_delay`; selection calls `set_subtitle_path`.

- [ ] **Step 5: Wire services and lifecycle**

In `main.py`, instantiate `OpenSubtitlesClient` only when enabled, then `SubtitleService(settings.qbittorrent_local_path / ".subtitles", client)`. Pass it through `build_bot` to `menu.register`. Close the client during shutdown. Tests inject `None` or fakes, so existing constructors remain source-compatible through optional parameters.

- [ ] **Step 6: Verify GREEN**

Run: `pytest -q tests/test_bot_menu.py tests/test_bot_movie_flow.py tests/test_bot_client.py --no-cov`

- [ ] **Step 7: Commit**

```bash
git add app/bot/handlers/menu.py app/bot/handlers/addons.py app/bot/client.py app/main.py tests/test_bot_menu.py tests/test_bot_movie_flow.py tests/test_bot_client.py
git commit -m "feat: control playback and subtitles in one message"
```

### Task 7: Configurar, validar e publicar

**Files:**
- Modify: `.env` (local, não commitado)
- Modify: `README.md`
- Test: full suite and live Docker environment

**Interfaces:**
- Consumes all previous tasks.
- Produces deployable `telerion-bridge` container with OpenSubtitles enabled.

- [ ] **Step 1: Add the user's OpenSubtitles credentials locally**

Populate the four `OPENSUBTITLES_*` variables in `.env`. Do not print their values. Verify only booleans such as `OPENSUBTITLES_ENABLED True`.

- [ ] **Step 2: Run static checks**

```bash
ruff check .
black --check .
mypy app
```

Expected: exit code 0 for each command.

- [ ] **Step 3: Run the full suite**

```bash
pytest -q
```

Expected: all tests pass and coverage remains at or above 88%.

- [ ] **Step 4: Update operational documentation**

Document the panel flow, required OpenSubtitles credentials, `.subtitles` location, download limit and command fallbacks in `README.md`.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md
git commit -m "docs: document subtitle control panel"
```

- [ ] **Step 6: Merge locally and rebuild Docker**

```bash
git merge --ff-only <implementation-branch>
docker compose up -d --build bridge
```

- [ ] **Step 7: Validate without exposing secrets**

Inside the running container, assert:

- health status is `healthy`;
- qBittorrent authentication succeeds;
- OpenSubtitles login/search succeeds and returns only counts/languages;
- a Telegram mobile test can start a movie, open controls, change volume, open subtitle options, select a local/online track, toggle it and adjust delay without creating another Rich Message.

- [ ] **Step 8: Final commit/status check**

Run: `git status --short`

Expected: no tracked changes. `.env` remains ignored.
