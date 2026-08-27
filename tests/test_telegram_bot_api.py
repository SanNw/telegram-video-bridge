"""Contrato HTTP mínimo para Rich Messages da Telegram Bot API 10.3."""

from __future__ import annotations

import json

import httpx
import pytest

from app.telegram.bot_api import BotAPIClient, BotAPIError


async def test_send_rich_message_serializes_ephemeral_replacement() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"ephemeral_message_id": 7}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BotAPIClient("123:secret", http=http)

    result = await client.send_rich_message(
        -1001,
        {"blocks": [{"type": "paragraph", "text": "Escolha"}]},
        receiver_user_id=111,
        callback_query_id="callback-1",
        replace_callback_query_message=True,
    )

    assert result["ephemeral_message_id"] == 7
    assert requests[0].url.path.endswith("/sendRichMessage")
    payload = json.loads(requests[0].content)
    assert payload["ephemeral_message_parameters"] == {
        "receiver_user_id": 111,
        "callback_query_id": "callback-1",
        "replace_callback_query_message": True,
    }
    await http.aclose()


async def test_send_rich_message_omits_ephemeral_parameters_in_private_chat() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BotAPIClient("123:secret", http=http)

    await client.send_rich_message(
        111,
        {
            "blocks": [
                {
                    "type": "buttons",
                    "buttons": [
                        {
                            "text": "Buscar filme",
                            "style": "primary",
                            "callback_data": "menu:find",
                        }
                    ],
                }
            ]
        },
        receiver_user_id=111,
    )

    payload = json.loads(requests[0].content)
    assert "ephemeral_message_parameters" not in payload
    assert payload["rich_message"]["blocks"][0]["type"] == "buttons"
    await http.aclose()


async def test_edit_ephemeral_message_serializes_identity() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BotAPIClient("123:secret", http=http)

    await client.edit_ephemeral_message(
        -1001,
        receiver_user_id=111,
        ephemeral_message_id=77,
        rich_message={"blocks": [{"type": "paragraph", "text": "Atualizado"}]},
    )

    assert requests[0].url.path.endswith("/editEphemeralMessageText")
    assert json.loads(requests[0].content)["ephemeral_message_id"] == 77
    await http.aclose()


async def test_edit_rich_message_serializes_persistent_message_identity() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BotAPIClient("123:secret", http=http)
    rich_message = {"blocks": [{"type": "paragraph", "text": "Atualizado"}]}

    await client.edit_rich_message(111, 9, rich_message)

    assert requests[0].url.path.endswith("/editMessageText")
    assert json.loads(requests[0].content) == {
        "chat_id": 111,
        "message_id": 9,
        "rich_message": rich_message,
    }
    await http.aclose()


async def test_error_never_exposes_token() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(400, json={"ok": False, "description": "bad payload"})
        )
    )
    client = BotAPIClient("123:secret", http=http)

    with pytest.raises(BotAPIError) as exc:
        await client.send_rich_message(1, {"blocks": []})

    assert "123:secret" not in str(exc.value)
    assert "bad payload" in str(exc.value)
    await http.aclose()


async def test_send_rich_message_preserves_real_document_media() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BotAPIClient("123:secret", http=http)
    rich_message = {
        "blocks": [
            {"type": "paragraph", "text": "Legenda"},
            {
                "type": "document",
                "document": {
                    "type": "document",
                    "media": "https://example.test/movie-pt.srt",
                },
            },
        ]
    }

    await client.send_rich_message(1, rich_message)

    assert json.loads(requests[0].content)["rich_message"] == rich_message
    await http.aclose()


async def test_close_does_not_close_injected_http_client() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200)))
    client = BotAPIClient("123:secret", http=http)

    await client.close()

    assert not http.is_closed
    await http.aclose()


async def test_transport_and_invalid_result_errors_are_sanitized() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(broken))
    with pytest.raises(BotAPIError, match="ConnectError"):
        await BotAPIClient("123:secret", http=http).send_rich_message(1, {"blocks": []})
    await http.aclose()

    invalid = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"ok": True, "result": True})
        )
    )
    with pytest.raises(BotAPIError, match="invalid result"):
        await BotAPIClient("123:secret", http=invalid).send_rich_message(1, {"blocks": []})
    await invalid.aclose()


async def test_close_closes_owned_http_client() -> None:
    client = BotAPIClient("123:secret")

    await client.close()

    assert client._http.is_closed
