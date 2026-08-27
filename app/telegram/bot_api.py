"""Cliente HTTP mínimo para recursos novos da Telegram Bot API."""

from __future__ import annotations

from typing import Any

import httpx


class BotAPIError(RuntimeError):
    """Erro sanitizado devolvido pelo Telegram ou pela camada HTTP."""


class BotAPIClient:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._http = http or httpx.AsyncClient(timeout=20.0)
        self._owns_http = http is None

    async def _post(self, method: str, payload: dict[str, object]) -> object:
        try:
            response = await self._http.post(f"{self._base_url}/{method}", json=payload)
            data: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BotAPIError(f"Telegram Bot API request failed: {type(exc).__name__}") from exc
        if not isinstance(data, dict) or not response.is_success or not data.get("ok"):
            description = data.get("description") if isinstance(data, dict) else None
            raise BotAPIError(str(description or "Telegram Bot API rejected the request"))
        return data.get("result")

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
        if receiver_user_id is not None and chat_id < 0:
            payload["ephemeral_message_parameters"] = {
                "receiver_user_id": receiver_user_id,
                **({"callback_query_id": callback_query_id} if callback_query_id else {}),
                "replace_callback_query_message": replace_callback_query_message,
            }
        result = await self._post("sendRichMessage", payload)
        if not isinstance(result, dict):
            raise BotAPIError("Telegram Bot API returned an invalid result")
        return result

    async def edit_rich_message(
        self,
        chat_id: int,
        message_id: int,
        rich_message: dict[str, object],
    ) -> None:
        await self._post(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "rich_message": rich_message},
        )

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
