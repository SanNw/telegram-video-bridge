"""Testes de `app/utils/retry.py`."""

from __future__ import annotations

import pytest

from app.utils.retry import RetryExhaustedError, RetryPolicy, retry_with_backoff


def test_delay_for_attempt_grows_exponentially_and_caps() -> None:
    policy = RetryPolicy(
        base_delay_seconds=1.0, max_delay_seconds=5.0, max_attempts=10, jitter_seconds=0.0
    )
    assert policy.delay_for_attempt(1) == 1.0
    assert policy.delay_for_attempt(2) == 2.0
    assert policy.delay_for_attempt(3) == 4.0
    assert policy.delay_for_attempt(4) == 5.0  # capped
    assert policy.delay_for_attempt(10) == 5.0  # ainda capped


def test_delay_for_attempt_includes_jitter_within_bounds() -> None:
    policy = RetryPolicy(
        base_delay_seconds=1.0, max_delay_seconds=100.0, max_attempts=5, jitter_seconds=2.0
    )
    for _ in range(50):
        delay = policy.delay_for_attempt(1)
        assert 1.0 <= delay <= 3.0


async def test_retry_with_backoff_succeeds_first_try() -> None:
    policy = RetryPolicy(
        base_delay_seconds=0.001, max_delay_seconds=0.001, max_attempts=3, jitter_seconds=0.0
    )
    calls = 0

    async def _operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await retry_with_backoff(_operation, policy)
    assert result == "ok"
    assert calls == 1


async def test_retry_with_backoff_succeeds_after_failures() -> None:
    policy = RetryPolicy(
        base_delay_seconds=0.001, max_delay_seconds=0.001, max_attempts=5, jitter_seconds=0.0
    )
    calls = 0

    async def _operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("falha transitória")
        return "ok"

    result = await retry_with_backoff(_operation, policy)
    assert result == "ok"
    assert calls == 3


async def test_retry_with_backoff_exhausts_and_raises() -> None:
    policy = RetryPolicy(
        base_delay_seconds=0.001, max_delay_seconds=0.001, max_attempts=3, jitter_seconds=0.0
    )
    calls = 0

    async def _operation() -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("sempre falha")

    with pytest.raises(RetryExhaustedError) as exc_info:
        await retry_with_backoff(_operation, policy)

    assert calls == 3
    assert isinstance(exc_info.value.__cause__, RuntimeError)


async def test_retry_with_backoff_calls_on_retry_callback() -> None:
    policy = RetryPolicy(
        base_delay_seconds=0.001, max_delay_seconds=0.001, max_attempts=2, jitter_seconds=0.0
    )
    attempts_seen: list[int] = []

    async def _operation() -> None:
        raise RuntimeError("falha")

    def _on_retry(attempt: int, _exc: Exception) -> None:
        attempts_seen.append(attempt)

    with pytest.raises(RetryExhaustedError):
        await retry_with_backoff(_operation, policy, on_retry=_on_retry)

    assert attempts_seen == [1, 2]
