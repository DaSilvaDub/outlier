import pytest

from outlier_scrapers import provider_executor


def _no_sleep(_seconds):
    pass


def test_succeeds_on_first_attempt_without_sleeping():
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        return "ok"

    result = provider_executor.execute_with_retry(
        call, max_attempts=5, sleep=_no_sleep, rng=lambda: 1.0
    )
    assert result == "ok"
    assert calls["n"] == 1


def test_retries_retryable_failure_then_succeeds():
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        if calls["n"] < 3:
            raise provider_executor.ProviderCallError("transient", retryable=True)
        return "ok"

    result = provider_executor.execute_with_retry(
        call, max_attempts=5, sleep=_no_sleep, rng=lambda: 1.0
    )
    assert result == "ok"
    assert calls["n"] == 3


def test_non_retryable_failure_propagates_immediately():
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        raise provider_executor.ProviderCallError("fatal", retryable=False)

    with pytest.raises(provider_executor.ProviderCallError):
        provider_executor.execute_with_retry(call, max_attempts=5, sleep=_no_sleep)
    assert calls["n"] == 1


def test_gives_up_after_max_attempts():
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        raise provider_executor.ProviderCallError("still failing", retryable=True)

    with pytest.raises(provider_executor.ProviderCallError):
        provider_executor.execute_with_retry(call, max_attempts=3, sleep=_no_sleep, rng=lambda: 1.0)
    assert calls["n"] == 3


def test_is_retryable_predicate_overrides_default():
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ValueError("flaky")
        return "ok"

    result = provider_executor.execute_with_retry(
        call,
        max_attempts=5,
        sleep=_no_sleep,
        rng=lambda: 1.0,
        is_retryable=lambda exc: isinstance(exc, ValueError),
    )
    assert result == "ok"
    assert calls["n"] == 2


def test_backoff_is_bounded_exponential_with_jitter():
    delays = []

    def sleep(seconds):
        delays.append(seconds)

    calls = {"n": 0}

    def call():
        calls["n"] += 1
        raise provider_executor.ProviderCallError("boom", retryable=True)

    with pytest.raises(provider_executor.ProviderCallError):
        provider_executor.execute_with_retry(
            call,
            max_attempts=4,
            base_delay_seconds=1.0,
            max_delay_seconds=10.0,
            sleep=sleep,
            rng=lambda: 1.0,  # pin jitter at the top of its range for a deterministic check
        )

    # base=1: 1, 2, 4 (uncapped) at full jitter (rng()=1.0 -> 100% of computed delay)
    assert delays == [1.0, 2.0, 4.0]


def test_backoff_is_capped_at_max_delay():
    delays = []

    def sleep(seconds):
        delays.append(seconds)

    calls = {"n": 0}

    def call():
        calls["n"] += 1
        raise provider_executor.ProviderCallError("boom", retryable=True)

    with pytest.raises(provider_executor.ProviderCallError):
        provider_executor.execute_with_retry(
            call,
            max_attempts=5,
            base_delay_seconds=10.0,
            max_delay_seconds=15.0,
            sleep=sleep,
            rng=lambda: 1.0,
        )

    assert all(d <= 15.0 for d in delays)


def test_rejects_non_positive_max_attempts():
    with pytest.raises(ValueError):
        provider_executor.execute_with_retry(lambda: "ok", max_attempts=0)
