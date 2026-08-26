# Interactive Movie Rich Messages Design

## Objective

Turn the Telerion movie search into a user-specific interactive flow that updates one Telegram message instead of posting a sequence of messages. The flow must expose multiple relevant sources, prioritize usable torrent candidates, preserve the existing slash commands as a fallback, and improve the administrator onboarding message.

## Confirmed Constraints

- Use Telegram Bot API 10.3 directly through the already-installed `httpx` dependency for features not yet exposed by Kurigram 2.2.24.
- Keep Kurigram for receiving messages and callbacks, authorization, existing handlers, and group-call integration.
- Do not introduce another Telegram framework or replace the existing bot client.
- Prefer `1080p`; accept `720p` as fallback.
- Reject `1440p`, `2K`, `2160p`, `4K`, and higher resolutions.
- Match sources against the TMDB title, original title, and release year before presenting them.
- Rank matching sources by resolution and seed count.
- Preserve all current slash commands as operational and compatibility fallbacks.
- Every rendered button must have a registered, authorized, tested callback path.
- Work that requires Docker or a running Telegram bot must remain in a later verification phase.

## Current Behavior and Root Cause

`AddonService.resolve_top_candidates()` currently returns at most one candidate per addon. It selects the first acceptable stream and stores a short callback token. Consequently, the Telegram interface cannot let the user inspect or choose among all valid files even when Stremio or a torrent addon returns several streams.

The current interaction also sends separate catalog, detail, source, progress, and confirmation messages. This creates chat noise and makes navigation difficult. The administrator onboarding response is plain Markdown sent without an explicit compatible formatting path, which can expose literal `*` characters.

## Architecture

### Direct Bot API Adapter

Add a focused asynchronous adapter around Telegram Bot API methods required by the new interface:

- `sendRichMessage`
- `editMessageText` with `rich_message`
- `editEphemeralMessageText`
- `editEphemeralMessageReplyMarkup`
- Rich-message sends using `ephemeral_message_parameters`

The adapter receives the bot token through existing settings, owns a shared `httpx.AsyncClient`, validates Telegram responses, and raises a small project exception containing a sanitized Telegram error. It must never log the bot token or the complete request URL.

Kurigram remains responsible for receiving callback queries. Callback handlers call the direct adapter only when they need Bot API 10.3 output or edits.

### Interactive Flow

The primary flow is:

1. Main menu.
2. Search prompt.
3. Paginated TMDB movie results.
4. Selected movie details.
5. Source filters and candidate list.
6. Selected source confirmation.
7. Preparation progress.
8. Queue confirmation or recoverable error.

Each transition replaces the current user-specific ephemeral message. Navigation includes `Back`, `Next`, `Previous`, `Refresh`, and `Cancel` where applicable. A user must not be able to alter another user's view.

### Interaction State

Store only the minimum state needed to resolve callbacks:

- requesting user ID;
- chat ID;
- ephemeral or source message identifier;
- current view;
- selected TMDB movie ID;
- candidate tokens;
- creation or expiration time.

State is process-local and short-lived. Restarting the bot invalidates open panels; callbacks then return a clear instruction to start a new search. Persistent conversational state is deliberately excluded.

### Candidate Collection and Ranking

Replace the one-candidate-per-addon presentation path with collection of all eligible candidates for the selected movie.

Candidate identity matching uses normalized TMDB title, original title, and year. It must tolerate release-name punctuation and tags while rejecting different movies, sequels, and wrong-year remakes. Literal filename equality is not required because valid torrent names contain codec, source, audio, and release-group tags.

Filtering order:

1. Reject candidates that do not match the selected movie identity.
2. Reject `1440p`, `2K`, `2160p`, `4K`, `8K`, or any detected resolution above 1080p.
3. Accept explicit `1080p` and `720p` candidates.
4. Reject candidates without a recognized accepted resolution from the interactive list.
5. Apply the existing maximum-size safety limit.
6. Deduplicate equivalent candidates.

Ranking order:

1. `1080p` before `720p`.
2. Known seed counts before unknown seed counts.
3. More seeds before fewer seeds.
4. Preferred audio/language signal.
5. Smaller file size as a deterministic tie-breaker.

The interface shows resolution, detected language, addon, size, and seeds. Unknown metadata is displayed as unknown rather than zero. Results are paginated so Telegram button and payload limits are respected.

### Buttons and Slash Commands

The main administrator panel exposes:

- Search movie;
- Search configured channel;
- Now playing;
- Queue;
- Playback controls;
- Addons;
- Help;
- Owner-only administration.

Actions that need free text use a button-triggered prompt. For example, `Search movie` asks the user to type a title; the user does not need to enter `/find`. Commands remain registered for fallback, automation, older Telegram clients, and recovery from expired interaction state.

Owner-only addon mutations retain the existing owner authorization check even when reached through buttons.

### Administrator Onboarding

Replace the current static Markdown message with a personalized Rich Message that:

- greets the administrator by first name;
- names the configured channel when Telegram exposes it;
- briefly explains the bot's purpose;
- opens the main menu through working buttons;
- is still sent only once per user and authorization state;
- preserves `onboarding.json` behavior.

Unauthorized users receive a clean, non-technical denial without operational buttons or internal configuration details.

If the direct Rich Message send fails, onboarding falls back to correctly formatted plain text without Markdown markers.

## Error Handling and Fallbacks

- Bot API 10.3 rejection: log a sanitized warning and render the equivalent existing Kurigram message where possible.
- Expired interaction: tell the user to reopen the menu or search again.
- Candidate no longer available: refresh the candidate list instead of silently choosing another file.
- Torrent resolution failure: keep the selection screen usable and show the failure in the same ephemeral flow.
- Queue full or invalid source: show the existing domain error in the same interface.
- Missing seed metadata: rank after candidates with known seed counts.
- No accepted 1080p or 720p candidate: report that no compatible source was found.
- Old Telegram client: rely on Telegram's update placeholder and retain slash-command fallbacks.

## Testing Strategy

### Docker-Independent Automated Tests

- Bot API request serialization for rich blocks, embedded buttons, documents, and ephemeral parameters.
- Token and URL sanitization in failures and logs.
- Candidate title/year matching.
- Resolution rejection and 1080p/720p ordering.
- Seed-count ordering, unknown-seed behavior, deduplication, and pagination.
- Interaction state ownership, expiry, and concurrent users.
- Every rendered callback action maps to a registered handler.
- Back/next/previous/refresh/cancel transitions.
- Authorization for normal, administrator, and owner-only actions.
- Rich onboarding personalization and plain-text fallback.
- Existing slash-command regression tests.

### Integration Tests After Docker Is Available

- Start the application with the real runtime configuration.
- Send the onboarding message to a test administrator.
- Navigate every button in the main menu.
- Search a movie and traverse results, details, sources, and pagination.
- Confirm that 4K/2K sources never appear and 1080p precedes 720p.
- Run two simultaneous user flows and verify isolation.
- Select a torrent and verify progress, resolution, subtitles, and queue confirmation.
- Simulate an expired callback and a Bot API error.
- Confirm that the same ephemeral message is replaced instead of generating chat noise.

## Implementation Order

### Phase 1: No Docker Required

1. Add candidate normalization, filtering, ranking, and focused unit tests.
2. Add the direct Bot API 10.3 adapter and serialization tests using mocked HTTP responses.
3. Add minimal interaction-state storage and transition tests.
4. Add rich-message builders and callback coverage tests.
5. Personalize onboarding and retain its persistence tests.
6. Wire handlers while keeping existing command fallbacks.
7. Run formatting, type checking, and the complete unit-test suite locally.

### Phase 2: Docker or Real Telegram Required

1. Validate runtime configuration and container networking.
2. Run the real Bot API interactive smoke test.
3. Exercise torrent download, subtitle, playback, and queue integration.
4. Correct any Telegram rendering or Bot API schema differences found in the live client.

## Out of Scope

- Replacing Kurigram.
- Building a Telegram Mini App.
- Persisting navigation sessions across process restarts.
- Supporting resolutions above 1080p.
- Removing slash commands.
- Generating artificial documents solely to demonstrate inline-document support.
