# Interactive Movie Rich Messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the noisy movie-selection sequence with a user-specific Rich Message flow that exposes correctly matched 1080p/720p sources, updates one ephemeral message, provides button-based controls, and sends a personalized administrator welcome.

**Architecture:** Keep Kurigram as the update receiver and group-call client. Add one small `httpx` Bot API adapter for Bot API 10.3 sends/edits, reuse `AddonService` as the source of catalog and candidate state, and add one menu handler for callback navigation. Interaction state remains an in-memory dictionary scoped by chat and user; slash commands remain unchanged as fallback.

**Tech Stack:** Python 3.13, Kurigram/Pyrogram namespace, Telegram Bot API 10.3, `httpx`, Pydantic Settings, RapidFuzz, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-26-interactive-movie-rich-messages-design.md`

## Global Constraints

- Use the already-installed `httpx`; add no Telegram framework or new dependency.
- Keep Kurigram for updates, authorization, callbacks, commands, and group calls.
- Prefer `1080p`; accept `720p`; reject `1440p`, `2K`, `2160p`, `4K`, and higher.
- Require normalized TMDB title/original-title and release-year identity before presenting a source.
- Rank by resolution, known seed count, descending seeds, language signal, then smaller size.
- Keep all slash commands operational.
- Every rendered callback must have a registered and authorized handler.
- Never log `BOT_TOKEN` or a URL containing it.
- Use inline documents only when a real useful file exists.
- Preserve existing behavior outside the interactive bot-client path.

---

### Task 1: Filter and Rank All Compatible Sources

**Files:**
- Modify: `app/utils/title_matching.py`
- Modify: `app/services/addon_service.py`
- Test: `tests/test_utils_title_matching.py`
- Test: `tests/test_services_addon_service.py`

**Interfaces:**
- Consumes: `SearchResult`, `StreamCandidate`, `TMDBMetadata`, existing `_MAX_STREAM_SIZE_BYTES`.
- Produces: `matches_movie_release(candidate: str, titles: Sequence[str | None], year: int | None) -> bool`, `stream_resolution(text: str) -> int | None`, and `AddonService.resolve_candidates() -> list[tuple[str, SearchResult, StreamCandidate]]`.

- [ ] **Step 1: Write failing title and resolution tests**

```python
def test_matches_movie_release_accepts_release_tags() -> None:
    assert matches_movie_release(
        "The.Matrix.1999.1080p.BluRay.x264.DTS",
        ["The Matrix", None],
        1999,
    )


def test_matches_movie_release_rejects_wrong_sequel_and_year() -> None:
    assert not matches_movie_release("The Matrix Reloaded 2003 1080p", ["The Matrix"], 1999)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1080p BluRay", 1080), ("720p WEB-DL", 720), ("2160p 4K", 2160), ("no quality", None)],
)
def test_stream_resolution(text: str, expected: int | None) -> None:
    assert stream_resolution(text) == expected
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `pytest tests/test_utils_title_matching.py -q`

Expected: FAIL because `matches_movie_release` and `stream_resolution` do not exist.

- [ ] **Step 3: Implement normalized release matching with stdlib plus RapidFuzz**

```python
import re
import unicodedata

from rapidfuzz import fuzz

_RESOLUTION_RE = re.compile(r"(?<!\d)(720|1080|1440|2160|4320)p?\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_RELEASE_NOISE_RE = re.compile(
    r"\b(?:bluray|blu-ray|web(?:-?dl)?|webrip|hdrip|x26[45]|h26[45]|hevc|av1|dts|aac|ddp?\d(?:\.\d)?|remux)\b",
    re.IGNORECASE,
)


def _normalized_release(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    without_noise = _RELEASE_NOISE_RE.sub(" ", ascii_text)
    return " ".join(re.findall(r"[a-z0-9]+", without_noise.lower()))


def stream_resolution(text: str) -> int | None:
    match = _RESOLUTION_RE.search(text)
    if match:
        return int(match.group(1))
    lowered = text.lower()
    return 2160 if "4k" in lowered else 1440 if "2k" in lowered else None


def matches_movie_release(
    candidate: str, titles: Sequence[str | None], year: int | None
) -> bool:
    normalized = _normalized_release(candidate)
    if year is not None and str(year) not in normalized.split():
        return False
    candidate_title = _YEAR_RE.sub(" ", _RESOLUTION_RE.sub(" ", normalized))
    return any(
        fuzz.ratio(candidate_title.strip(), _normalized_release(title)) >= 90
        for title in titles
        if title
    )
```

- [ ] **Step 4: Write failing candidate collection and ranking tests**

```python
async def test_resolve_candidates_filters_resolution_title_and_orders_seeds(
    service: AddonService, fake_manager: _FakeManager
) -> None:
    metadata = TMDBMetadata(
        title="The Matrix",
        original_title="The Matrix",
        overview=None,
        poster_url=None,
        vote_average=8.2,
        genres=["Action"],
        release_date="1999-03-31",
        cast=[],
        backdrop_urls=[],
    )
    service._last_metadata = metadata
    service._last_results = [
        SearchResult(media_id="tt0133093", title="The Matrix", year=1999, addon_name="stremio")
    ]
    fake_manager.stream_results_by_addon = {
        "stremio": [
            StreamCandidate(title="The Matrix 1999 4K", quality="2160p", seeds=500),
            StreamCandidate(title="The Matrix Reloaded 2003", quality="1080p", seeds=900),
            StreamCandidate(title="The Matrix 1999", quality="720p", seeds=300),
            StreamCandidate(title="The Matrix 1999", quality="1080p", seeds=20),
            StreamCandidate(title="The Matrix 1999", quality="1080p", seeds=200),
        ]
    }

    candidates = await service.resolve_candidates()

    assert [(candidate.quality, candidate.seeds) for _, _, candidate in candidates] == [
        ("1080p", 200),
        ("1080p", 20),
        ("720p", 300),
    ]
```

- [ ] **Step 5: Run the service test and confirm failure**

Run: `pytest tests/test_services_addon_service.py::test_resolve_candidates_filters_resolution_title_and_orders_seeds -q`

Expected: FAIL because `resolve_candidates` does not exist.

- [ ] **Step 6: Replace the one-per-addon collector with the minimal all-candidate collector**

```python
async def resolve_candidates(self) -> list[tuple[str, SearchResult, StreamCandidate]]:
    self._candidates = {}
    collected: list[tuple[SearchResult, StreamCandidate]] = []
    metadata = self._last_metadata
    year = (
        int(metadata.release_date[:4])
        if metadata and metadata.release_date and metadata.release_date[:4].isdigit()
        else None
    )
    titles = [metadata.title, metadata.original_title] if metadata else []

    for result in self._last_results:
        try:
            streams = await self._manager.get_streams(result.addon_name, result.media_id)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Addon {addon} falhou ao listar streams: {err}", addon=result.addon_name, err=exc)
            continue
        for candidate in streams:
            label = f"{candidate.title} {candidate.quality or ''}"
            resolution = stream_resolution(label)
            if resolution not in (1080, 720):
                continue
            if titles and not matches_movie_release(label, titles, year):
                continue
            if candidate.size_bytes is not None and candidate.size_bytes > _MAX_STREAM_SIZE_BYTES:
                continue
            collected.append((result, candidate))

    collected.sort(key=_candidate_sort_key)
    output: list[tuple[str, SearchResult, StreamCandidate]] = []
    for result, candidate in collected:
        token = str(len(output))
        self._candidates[token] = (result, candidate)
        output.append((token, result, candidate))
    return output
```

Use this exact key beside the method:

```python
def _candidate_sort_key(item: tuple[SearchResult, StreamCandidate]) -> tuple[object, ...]:
    _, candidate = item
    label = f"{candidate.title} {candidate.quality or ''}"
    resolution = stream_resolution(label) or 0
    return (
        -resolution,
        candidate.seeds is None,
        -(candidate.seeds or 0),
        detect_language_flag(label) is None,
        candidate.size_bytes or float("inf"),
    )
```

- [ ] **Step 7: Update existing callers and tests from `resolve_top_candidates` to `resolve_candidates`**

Run: `rg -n "resolve_top_candidates" app tests`

Expected edits: `app/bot/handlers/addons.py`, `tests/test_services_addon_service.py`, and fake services in `tests/test_bot_handlers.py`; no remaining production reference.

- [ ] **Step 8: Run candidate and matching tests**

Run: `pytest tests/test_utils_title_matching.py tests/test_services_addon_service.py -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/utils/title_matching.py app/services/addon_service.py tests/test_utils_title_matching.py tests/test_services_addon_service.py tests/test_bot_handlers.py app/bot/handlers/addons.py
git commit -m "feat: rank compatible movie sources"
```

---

### Task 2: Add the Direct Telegram Bot API 10.3 Client

**Files:**
- Create: `app/telegram/bot_api.py`
- Create: `tests/test_telegram_bot_api.py`

**Interfaces:**
- Consumes: bot token string and optional injected `httpx.AsyncClient`.
- Produces: `BotAPIError`, `BotAPIClient.send_rich_message(...) -> dict[str, object]`, `BotAPIClient.edit_ephemeral_message(...) -> None`, and `BotAPIClient.close() -> None`.

- [ ] **Step 1: Write failing request, edit, error-sanitization, and close tests**

```python
async def test_send_rich_message_serializes_ephemeral_replacement() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BotAPIClient("123:secret", http=http)

    result = await client.send_rich_message(
        -1001,
        {"blocks": [{"type": "paragraph", "text": "Escolha"}]},
        receiver_user_id=111,
        callback_query_id="callback-1",
        replace_callback_query_message=True,
    )

    assert result["message_id"] == 7
    payload = json.loads(requests[0].content)
    assert payload["ephemeral_message_parameters"] == {
        "receiver_user_id": 111,
        "callback_query_id": "callback-1",
        "replace_callback_query_message": True,
    }


async def test_error_never_exposes_token() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(400, json={"ok": False, "description": "bad payload"})
        )
    )
    client = BotAPIClient("123:secret", http=http)
    with pytest.raises(BotAPIError) as exc:
        await client.send_rich_message(1, {"html": "x"})
    assert "123:secret" not in str(exc.value)


async def test_send_rich_message_preserves_real_document_media() -> None:
    payload = {
        "blocks": [
            {"type": "paragraph", "text": "Legenda"},
            {
                "type": "document",
                "document": {
                    "type": "document",
                    "media": "https://example.test/movie-pt.srt",
                },
            },
        ],
    }
    await client.send_rich_message(1, payload)
    assert json.loads(requests[0].content)["rich_message"] == payload
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_telegram_bot_api.py -q`

Expected: FAIL because `app.telegram.bot_api` does not exist.

- [ ] **Step 3: Implement one generic private request method and two public methods**

```python
class BotAPIError(RuntimeError):
    pass


class BotAPIClient:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._http = http or httpx.AsyncClient(timeout=20.0)
        self._owns_http = http is None

    async def _post(self, method: str, payload: dict[str, object]) -> object:
        try:
            response = await self._http.post(f"{self._base_url}/{method}", json=payload)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BotAPIError(f"Telegram Bot API request failed: {type(exc).__name__}") from exc
        if not response.is_success or not data.get("ok"):
            raise BotAPIError(str(data.get("description", "Telegram Bot API rejected the request")))
        return data["result"]

    async def send_rich_message(
        self,
        chat_id: int,
        rich_message: dict[str, object],
        *,
        receiver_user_id: int | None = None,
        callback_query_id: str | None = None,
        replace_callback_query_message: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"chat_id": chat_id, "rich_message": rich_message}
        if receiver_user_id is not None:
            payload["ephemeral_message_parameters"] = {
                "receiver_user_id": receiver_user_id,
                **({"callback_query_id": callback_query_id} if callback_query_id else {}),
                "replace_callback_query_message": replace_callback_query_message,
            }
        result = await self._post("sendRichMessage", payload)
        assert isinstance(result, dict)
        return result

    async def edit_ephemeral_message(
        self,
        chat_id: int,
        receiver_user_id: int,
        ephemeral_message_id: int,
        rich_message: dict[str, object],
    ) -> None:
        await self._post(
            "editEphemeralMessageText",
            {
                "chat_id": chat_id,
                "receiver_user_id": receiver_user_id,
                "ephemeral_message_id": ephemeral_message_id,
                "rich_message": rich_message,
            },
        )

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
```

- [ ] **Step 4: Run adapter tests**

Run: `pytest tests/test_telegram_bot_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/telegram/bot_api.py tests/test_telegram_bot_api.py
git commit -m "feat: add Telegram Bot API rich message client"
```

---

### Task 3: Build Rich Message Screens and Verify Every Button Action

**Files:**
- Modify: `app/bot/formatting.py`
- Test: `tests/test_bot_formatting.py`

**Interfaces:**
- Consumes: `TMDBMovie`, `TMDBMetadata`, ranked candidate tuples, `PlaybackState`, `ServiceStatus`, addon metadata, and current page.
- Produces: `format_main_menu(first_name: str, channel_title: str | None) -> dict[str, object]`, `format_movie_results(...) -> dict[str, object]`, `format_movie_details(...) -> dict[str, object]`, `format_candidate_page(...) -> dict[str, object]`, `format_now_playing_screen(...) -> dict[str, object]`, `format_queue_screen(...) -> dict[str, object]`, `format_controls_screen(...) -> dict[str, object]`, `format_addons_screen(...) -> dict[str, object]`, and `rich_callback_actions(message: dict[str, object]) -> set[str]` for tests only.

- [ ] **Step 1: Write failing payload-shape and action-coverage tests**

```python
def _callback_data(message: dict[str, object]) -> set[str]:
    return {
        button["callback_data"]
        for block in message["blocks"]
        if block["type"] == "buttons"
        for button in block["buttons"]
        if "callback_data" in button
    }


def test_main_menu_has_expected_actions() -> None:
    message = format_main_menu("Rafael", "Cinema")
    assert _callback_data(message) == {
        "menu:find",
        "menu:channel",
        "menu:now",
        "menu:queue",
        "menu:controls",
        "menu:addons",
        "menu:help",
        "menu:admin",
    }


def test_candidate_page_shows_ranked_metadata_and_navigation() -> None:
    message = format_candidate_page(_ranked_candidates(), page=0, page_size=5)
    text = json.dumps(message, ensure_ascii=False)
    assert "1080p" in text
    assert "200 seeders" in text
    assert "source:0" in _callback_data(message)
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_bot_formatting.py -q`

Expected: FAIL because the new formatting functions do not exist.

- [ ] **Step 3: Add minimal dictionary builders using Bot API block names**

```python
def _button(text: str, callback_data: str, style: str = "primary") -> dict[str, object]:
    return {"text": text, "style": style, "callback_data": callback_data}


def _button_row(*buttons: dict[str, object]) -> dict[str, object]:
    return {"type": "buttons", "buttons": list(buttons), "align": "center"}


def format_main_menu(first_name: str, channel_title: str | None) -> dict[str, object]:
    destination = f" no canal {channel_title}" if channel_title else ""
    return {
        "blocks": [
            {"type": "heading", "text": f"Olá, {first_name} 👋", "size": 2},
            {"type": "paragraph", "text": f"Controle o Telerion{destination} pelo painel abaixo."},
            _button_row(_button("🎬 Buscar filme", "menu:find"), _button("📺 Buscar no canal", "menu:channel")),
            _button_row(_button("▶️ Tocando agora", "menu:now"), _button("📋 Ver fila", "menu:queue")),
            _button_row(_button("⏯ Controles", "menu:controls"), _button("🧩 Addons", "menu:addons")),
            _button_row(_button("❓ Ajuda", "menu:help", "link"), _button("⚙️ Administração", "menu:admin")),
        ]
    }
```

Use the same `_button` and `_button_row` helpers for result, detail, candidate, progress, success, and error screens. Keep callback data below Telegram's 64-byte limit by using numeric tokens already stored by `AddonService`.

Add the dashboard screen functions as thin Rich Message equivalents of existing `format_now_playing`, `format_queue`, `format_status`, and `format_addons_list`; they must read the same service data and add only `menu:home` plus the relevant control buttons.

- [ ] **Step 4: Add candidate pagination and disabled state**

Use at most five source buttons per page. When a download is being prepared, render a `RichMessageButton` with:

```python
{"text": "Preparando reprodução…", "disabled": {}, "style": "primary"}
```

Do not add a custom paginator class; calculate slices with `start = page * page_size` as existing catalog formatting already does.

- [ ] **Step 5: Run formatting tests**

Run: `pytest tests/test_bot_formatting.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/bot/formatting.py tests/test_bot_formatting.py
git commit -m "feat: format interactive movie screens"
```

---

### Task 4: Implement the User-Specific Movie Navigation Flow

**Files:**
- Modify: `app/bot/handlers/addons.py`
- Test: `tests/test_bot_handlers.py`

**Interfaces:**
- Consumes: `BotAPIClient`, `AddonService.resolve_candidates`, Rich Message formatting functions, Kurigram callback queries.
- Produces: callbacks `movie:*`, `catalog:*`, `sources:*`, `source:*`, `flow:back`, `flow:refresh`, and `flow:cancel`; normal `/find` remains supported.

- [ ] **Step 1: Extend test doubles to record Bot API sends and edits**

```python
class _FakeBotAPI:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.edited: list[dict[str, object]] = []

    async def send_rich_message(self, chat_id: int, rich_message: dict[str, object], **kwargs: object) -> dict[str, object]:
        self.sent.append({"chat_id": chat_id, "rich_message": rich_message, **kwargs})
        return {"ephemeral_message_id": 77}

    async def edit_ephemeral_message(self, chat_id: int, receiver_user_id: int, ephemeral_message_id: int, rich_message: dict[str, object]) -> None:
        self.edited.append(locals())
```

- [ ] **Step 2: Write failing navigation, isolation, pagination, expiry, and playback tests**

```python
async def test_movie_callback_replaces_message_with_user_specific_details(...) -> None:
    callback = FakeCallbackQuery(data="movie:0", user_id=111)
    callback.id = "cb-1"
    await dispatch_callback(client, callback)
    assert bot_api.sent[-1]["receiver_user_id"] == 111
    assert bot_api.sent[-1]["replace_callback_query_message"] is True


async def test_two_users_keep_independent_ephemeral_state(...) -> None:
    await dispatch_callback(client, FakeCallbackQuery(data="movie:0", user_id=111))
    await dispatch_callback(client, FakeCallbackQuery(data="movie:1", user_id=222))
    assert flow_state[(-1001, 111)].movie_index == 0
    assert flow_state[(-1001, 222)].movie_index == 1


async def test_source_callback_edits_progress_then_success(...) -> None:
    callback = FakeCallbackQuery(data="source:0", user_id=111)
    await dispatch_callback(client, callback)
    assert len(bot_api.edited) == 2
    assert "Preparando" in json.dumps(bot_api.edited[0], ensure_ascii=False)
    assert "fila" in json.dumps(bot_api.edited[1], ensure_ascii=False)
```

- [ ] **Step 3: Run and confirm failure**

Run: `pytest tests/test_bot_handlers.py -q`

Expected: FAIL because `register_search` does not accept a Bot API client and does not maintain ephemeral state.

- [ ] **Step 4: Add the smallest sufficient flow state inside `register_search`**

```python
@dataclass(slots=True)
class _FlowState:
    movie_index: int | None = None
    ephemeral_message_id: int | None = None
    page: int = 0
    updated_at: float = field(default_factory=time.monotonic)


flows: dict[tuple[int, int], _FlowState] = {}


def _flow(chat_id: int, user_id: int) -> _FlowState:
    key = (chat_id, user_id)
    state = flows.get(key)
    if state is None or time.monotonic() - state.updated_at > 900:
        state = flows[key] = _FlowState()
    state.updated_at = time.monotonic()
    return state
```

This is deliberately process-local. Do not add persistence, a repository class, or a background cleanup task. Opportunistically remove expired entries when a flow is opened.

- [ ] **Step 5: Route catalog and source callbacks through Bot API edits**

For a callback from a normal message, call `send_rich_message` with:

```python
receiver_user_id=user_id,
callback_query_id=callback_query.id,
replace_callback_query_message=True,
```

Store the returned `ephemeral_message_id`. For later callbacks from the ephemeral message, call `edit_ephemeral_message` with the stored ID.

- [ ] **Step 6: Keep `/find` fallback and old-client fallback**

If `bot_api` is `None` or a `BotAPIError` is raised, execute the existing `reply_photo`/`reply_text` and `InlineKeyboardMarkup` path. Do not remove current callback handling until the live Bot API smoke test passes.

- [ ] **Step 7: Make source selection explicit**

The `source:<token>` callback must call only `service.pick_candidate(token, user_id)`. If the candidate disappears or torrent resolution fails, keep the source page available and show a retry/back action; never silently choose a different candidate.

- [ ] **Step 8: Run handler tests**

Run: `pytest tests/test_bot_handlers.py -q`

Expected: PASS, including existing slash-command tests.

- [ ] **Step 9: Commit**

```bash
git add app/bot/handlers/addons.py tests/test_bot_handlers.py
git commit -m "feat: add ephemeral movie navigation"
```

---

### Task 5: Add the Button Dashboard Without Removing Commands

**Files:**
- Create: `app/bot/handlers/menu.py`
- Modify: `app/bot/handlers/__init__.py`
- Modify: `app/bot/client.py`
- Test: `tests/test_bot_menu.py`
- Test: `tests/test_bot_client.py`

**Interfaces:**
- Consumes: `PlaybackService`, `AddonService`, optional `ChannelMediaService`, authorization filters, `BotAPIClient`.
- Produces: `menu.register(...)`, callback actions for main menu, queue/status/controls/addons/help/admin, and a `menu:home` callback used by other screens.

- [ ] **Step 1: Write failing dashboard authorization and action tests**

```python
@pytest.mark.parametrize(
    "action",
    ["menu:home", "menu:find", "menu:channel", "menu:now", "menu:queue", "menu:controls", "menu:addons", "menu:help"],
)
async def test_every_menu_action_is_registered_and_answers(action: str, wired_menu: MenuHarness) -> None:
    callback = FakeCallbackQuery(data=action, user_id=111)
    assert await wired_menu.dispatch(callback)
    assert callback.answers or wired_menu.bot_api.sent or wired_menu.bot_api.edited


async def test_admin_menu_rejects_non_owner(wired_menu: MenuHarness) -> None:
    callback = FakeCallbackQuery(data="menu:admin", user_id=222)
    await wired_menu.dispatch(callback)
    assert callback.answers[-1][1] is True
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_bot_menu.py -q`

Expected: FAIL because `app.bot.handlers.menu` does not exist.

- [ ] **Step 3: Implement one callback filter and direct service calls**

Use one `menu:` callback filter and one handler. Branch on the short action string and reuse existing formatters/service methods:

```python
if action == "now":
    current = playback_service.now_playing()
    screen = format_now_playing_screen(current)
elif action == "queue":
    screen = format_queue_screen(playback_service.queue_snapshot())
elif action == "controls":
    screen = format_controls_screen(playback_service.status())
elif action == "addons":
    screen = format_addons_screen(addon_service.list_addons())
```

Do not synthesize slash-command messages or redispatch commands internally.

- [ ] **Step 4: Add playback-control callbacks to the same handler**

Support `control:pause`, `control:resume`, `control:stop`, `control:skip`, `control:restart`, `control:loop:<mode>`, and a small fixed volume set such as `50`, `100`, `150`, `200`. Call the same `PlaybackService` methods used by command handlers and render the updated controls screen.

- [ ] **Step 5: Add prompt buttons only for actions requiring text**

`menu:find` and `menu:channel` send a force-reply prompt and mark the requesting `(chat_id, user_id)` as awaiting one text response. Do not build a Mini App or custom input component.

- [ ] **Step 6: Wire menu registration only on the bot client**

Add `bot_api: BotAPIClient | None = None` to `build_bot`. When both `bot_client` and `bot_api` exist, call `menu.register(...)`. Session-client behavior remains unchanged.

- [ ] **Step 7: Update handler-count assertions and run tests**

Run: `pytest tests/test_bot_menu.py tests/test_bot_client.py -q`

Expected: PASS with explicit counts updated to include the menu callback/text-prompt handlers.

- [ ] **Step 8: Commit**

```bash
git add app/bot/handlers/menu.py app/bot/handlers/__init__.py app/bot/client.py tests/test_bot_menu.py tests/test_bot_client.py
git commit -m "feat: add button control dashboard"
```

---

### Task 6: Personalize Onboarding and Wire Client Lifecycle

**Files:**
- Modify: `app/bot/handlers/onboarding.py`
- Modify: `app/main.py`
- Test: `tests/test_bot_onboarding.py`

**Interfaces:**
- Consumes: `BotAPIClient`, `format_main_menu`, administrator first name, configured stream chat title.
- Produces: personalized one-time Rich Message onboarding with plain-text fallback; clean shutdown of `BotAPIClient`.

- [ ] **Step 1: Write failing personalized onboarding and fallback tests**

```python
async def test_admin_receives_personalized_rich_menu_once(...) -> None:
    message = _Message(user_id=123, first_name="Rafael")
    await client.handler(client, message)
    sent = bot_api.sent[0]
    assert "Rafael" in json.dumps(sent["rich_message"], ensure_ascii=False)
    assert "menu:find" in json.dumps(sent["rich_message"])
    assert json.loads(path.read_text())["admins"] == [123]


async def test_onboarding_fallback_has_no_literal_markdown_markers(...) -> None:
    bot_api.error = BotAPIError("rejected")
    message = await _send(client, 123)
    assert "*" not in message.replies[0]
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_bot_onboarding.py -q`

Expected: FAIL because onboarding sends static Markdown and does not receive `BotAPIClient`.

- [ ] **Step 3: Replace `_ADMIN_TEXT` with personalized Rich Message send plus plain fallback**

Use `user.first_name or "administrador"`. Resolve the channel title once with `client.get_chat(settings.stream_chat_id or settings.chat_id)` and tolerate Telegram errors by omitting the title.

Only record the user in `onboarding.json` after either the Rich Message or fallback text succeeds.

- [ ] **Step 4: Instantiate and close `BotAPIClient` in `app/main.py`**

```python
bot_api = (
    BotAPIClient(settings.bot_token.get_secret_value())
    if settings.bot_token is not None
    else None
)
```

Pass it to `build_bot(...)`. In `finally`, after stopping `bot_client`, call `await bot_api.close()` when non-`None`.

- [ ] **Step 5: Run onboarding and wiring tests**

Run: `pytest tests/test_bot_onboarding.py tests/test_bot_client.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/bot/handlers/onboarding.py app/main.py tests/test_bot_onboarding.py tests/test_bot_client.py
git commit -m "feat: personalize administrator onboarding"
```

---

### Task 7: Full Local and Docker Verification

**Files:**
- Modify only if verification reveals a defect in files changed by Tasks 1-6.

**Interfaces:**
- Consumes: completed implementation, existing `.env`, Docker Compose services, real Telegram bot configuration.
- Produces: passing quality gates and a verified live interaction path.

- [ ] **Step 1: Run formatting and lint**

Run: `ruff check app tests`

Expected: PASS.

Run: `black --check app tests`

Expected: PASS.

- [ ] **Step 2: Run strict typing**

Run: `mypy app`

Expected: PASS.

- [ ] **Step 3: Run the complete test suite**

Run: `pytest -q`

Expected: PASS with coverage at or above 88%.

- [ ] **Step 4: Build containers**

Run: `docker compose build`

Expected: both application images build successfully without adding dependencies.

- [ ] **Step 5: Start the stack and inspect service health**

Run: `docker compose up -d`

Expected: configured services remain running.

Run: `docker compose ps`

Expected: no required service is restarting or exited.

- [ ] **Step 6: Run the live button checklist**

In Telegram, verify in order:

1. A new administrator receives the personalized menu without literal `*`.
2. Every main-menu button responds.
3. `Buscar filme` accepts a plain-text title.
4. Catalog pagination updates one user-specific message.
5. Selecting a movie displays multiple matching sources.
6. No 4K/2K/2160p/1440p source appears.
7. 1080p sources precede 720p; higher seed counts appear first within a resolution.
8. Back, next, previous, refresh, cancel, and home work.
9. Selecting a source shows progress and then queue confirmation in the same message.
10. Two administrators can navigate different movies simultaneously.
11. Slash commands still work.

- [ ] **Step 7: Inspect sanitized logs**

Run: `docker compose logs --tail 200`

Expected: no bot token, no Bot API URL containing the token, and no unhandled callback exception.

- [ ] **Step 8: Commit only verified fixes, if any**

```bash
git add <only-files-fixed-during-verification>
git commit -m "fix: address interactive bot verification"
```

Skip this commit when verification requires no changes.
