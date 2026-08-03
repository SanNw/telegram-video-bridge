"""Retry exponencial com jitter, compartilhado pelas camadas `streaming/` e `telegram/`.

Política: delay = min(base * 2^(tentativa-1), max) + jitter_aleatorio(0, jitter).
Após `max_attempts` tentativas sem sucesso, `RetryExhaustedError` é levantado para que
o chamador decida como degradar (pausar reprodução, alertar o operador via log ERROR).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from app.utils.logging import get_logger

T = TypeVar("T")

_logger = get_logger("services")


class RetryExhaustedError(Exception):
    """Levantado quando todas as tentativas de retry de uma operação se esgotam."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Parâmetros de backoff exponencial com jitter, vindos de `Settings`."""

    base_delay_seconds: float
    max_delay_seconds: float
    max_attempts: int
    jitter_seconds: float

    def delay_for_attempt(self, attempt: int) -> float:
        """Delay em segundos antes da tentativa `attempt` (1-indexada), incluindo jitter."""
        exponential: float = self.base_delay_seconds * (2 ** (attempt - 1))
        capped = min(exponential, self.max_delay_seconds)
        jitter = random.uniform(0, self.jitter_seconds)
        return capped + jitter


async def retry_with_backoff[T](
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T:
    """Executa `operation`, retentando com backoff exponencial + jitter em caso de exceção.

    Levanta `RetryExhaustedError` (com a última exceção como `__cause__`) após
    `policy.max_attempts` tentativas malsucedidas.
    """
    last_error: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001 - qualquer falha da operação é retentável aqui
            last_error = exc
            if on_retry is not None:
                on_retry(attempt, exc)
            if attempt == policy.max_attempts:
                break
            delay = policy.delay_for_attempt(attempt)
            _logger.warning(
                "Tentativa {attempt}/{max_attempts} falhou ({error}); nova tentativa em {delay:.1f}s",
                attempt=attempt,
                max_attempts=policy.max_attempts,
                error=str(exc),
                delay=delay,
            )
            await asyncio.sleep(delay)

    raise RetryExhaustedError(
        f"Todas as {policy.max_attempts} tentativas falharam."
    ) from last_error
