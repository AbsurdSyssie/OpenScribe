"""Content-free execution bounds for synchronous provider calls.

No helper thread survives a timeout. Unix main-thread workers are required.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator

import httpx

from app.errors import AppError

TransportEvent = dict[str, object]
TransportObserver = Callable[[TransportEvent], bool]


@dataclass(frozen=True)
class ExecutionBudget:
    deadline: float

    @classmethod
    def seconds(cls, seconds: float) -> ExecutionBudget:
        return cls(time.monotonic() + max(0.0, seconds))

    @classmethod
    def until(cls, deadline: datetime, *, maximum_seconds: float) -> ExecutionBudget:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        return cls.seconds(min(maximum_seconds, remaining))

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AppError(504, "llm_provider_timeout", "The LLM provider timed out")
        return remaining

    def timeout(self) -> httpx.Timeout:
        remaining = self.remaining()
        return httpx.Timeout(remaining, connect=min(10.0, remaining))


_budget: ContextVar[ExecutionBudget | None] = ContextVar("llm_execution_budget", default=None)
_observer: ContextVar[TransportObserver | None] = ContextVar("llm_transport_observer", default=None)


@contextmanager
def execution_policy(*, budget: ExecutionBudget, observer: TransportObserver | None = None) -> Iterator[None]:
    budget_token = _budget.set(budget)
    observer_token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(observer_token)
        _budget.reset(budget_token)


class _WallDeadline(BaseException):
    pass


def require_supported_execution_context() -> None:
    def unsupported() -> AppError:
        return AppError(503, "llm_execution_context_unsupported", "LLM execution requires a supported worker")

    if threading.current_thread() is not threading.main_thread():
        raise unsupported()
    if not all(hasattr(signal, name) for name in ("setitimer", "getitimer", "SIGALRM", "ITIMER_REAL")):
        raise unsupported()

    # Signal timers cannot safely interrupt greenlet/eventlet workers, even
    # when monkey-patching makes them look like the process main thread.
    greenlet_module = sys.modules.get("greenlet")
    if greenlet_module is not None:
        getcurrent = getattr(greenlet_module, "getcurrent", None)
        if callable(getcurrent):
            try:
                if getattr(getcurrent(), "parent", None) is not None:
                    raise unsupported()
            except AppError:
                raise
            except Exception:
                raise unsupported() from None

    for module_name, probe_name, patched_names in (
        ("eventlet.patcher", "is_monkey_patched", ("thread", "threading", "signal")),
        ("gevent.monkey", "is_module_patched", ("thread", "threading", "signal")),
    ):
        patcher = sys.modules.get(module_name)
        probe = getattr(patcher, probe_name, None) if patcher is not None else None
        if callable(probe):
            try:
                if any(probe(name) for name in patched_names):
                    raise unsupported()
            except AppError:
                raise
            except Exception:
                raise unsupported() from None

    try:
        if signal.getitimer(signal.ITIMER_REAL)[0] > 0:
            raise AppError(503, "llm_execution_context_unsupported", "LLM execution requires an available worker timer")
    except AppError:
        raise
    except Exception:
        raise unsupported() from None


@contextmanager
def wall_deadline(budget: ExecutionBudget) -> Iterator[None]:
    require_supported_execution_context()
    remaining = budget.remaining()
    prior_handler = signal.getsignal(signal.SIGALRM)
    prior_delay, prior_interval = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    # Do not commandeer an active host timer. A nested invocation uses the same
    # policy outside this helper rather than installing another signal scope.

    def expire(signum: int, frame: object) -> None:
        raise _WallDeadline()

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    except _WallDeadline:
        raise AppError(504, "llm_provider_timeout", "The LLM provider timed out") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prior_handler)
        signal.setitimer(signal.ITIMER_REAL, max(0.0, prior_delay - (time.monotonic() - started)), prior_interval)


def current_budget() -> ExecutionBudget:
    return _budget.get() or ExecutionBudget.seconds(90)


def _emit(event: TransportEvent) -> bool:
    observer = _observer.get()
    return observer is None or observer(event)


def bounded_request(call: Callable[[ExecutionBudget], object]) -> object:
    """Retry only explicit, temporary rejection of the frozen request."""
    # Unsupported worker setup is not an HTTP request observation. The caller
    # may already have submitted its durable attempt; settlement stays there.
    require_supported_execution_context()
    budget = current_budget()
    waited = 0.0
    with execution_policy(budget=budget, observer=_observer.get()):
        for ordinal in range(1, 5):
            budget.remaining()
            observer = _observer.get()
            active = getattr(observer, "is_active", None)
            if callable(active) and not active():
                raise AppError(409, "llm_attempt_inactive", "The generation attempt is no longer active")
            started = time.monotonic()
            status: int | None = None
            wait = 0.0
            code = "success"
            error: AppError | None = None
            result = None
            try:
                # Restore signals before database telemetry or settlement.
                with wall_deadline(budget):
                    result = call(budget)
            except AppError as exc:
                error = exc
                details = exc.details or {}
                raw_status = details.get("provider_http_status")
                status = raw_status if type(raw_status) is int and 100 <= raw_status <= 599 else None
                code = "timeout" if exc.code == "llm_provider_timeout" else "connection_error" if exc.code == "llm_provider_unreachable" else "rate_limited" if status == 429 else "provider_error"
                if status == 429 and details.get("temporary_capacity") is True and ordinal < 4:
                    wait = random.uniform(2 ** (ordinal - 1), 2 ** ordinal)
                    retry_after = details.get("retry_after_seconds")
                    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool) and math.isfinite(retry_after) and retry_after >= 0:
                        wait = max(wait, float(retry_after))
                    remaining = max(0.0, budget.deadline - time.monotonic())
                    if waited + wait > 30 or wait >= remaining:
                        wait = 0.0
                # The exception came from the bounded provider call itself.
                # Preserve only a categorical decision after local retry
                # controls are stripped below.
                if status == 429:
                    details = dict(details)
                    details["backup_failure_class"] = (
                        "temporary_capacity_exhausted"
                        if (exc.details or {}).get("temporary_capacity") is True
                        else "provider_quota_blocked"
                    )
                elif "backup_failure_class" not in details:
                    details = dict(details)
                    details["backup_failure_class"] = "provider_failure"
                exc.details = details
            if error is not None and error.details is not None:
                details = {
                    key: value
                    for key, value in error.details.items()
                    if key not in {"temporary_capacity", "retry_after_seconds"}
                }
                # Keep only a controlled terminal category for the optional
                # backup decision. Raw retry controls are transient transport
                # details and must not escape this bounded request boundary.
                error.details = details
            active = _emit({"occurred_at": datetime.now(timezone.utc).isoformat(), "request_ordinal": ordinal,
                   "duration_ms": min(150000, max(0, round((time.monotonic() - started) * 1000))),
                   "code": code, "http_status": status, "retry_wait_ms": round(wait * 1000)})
            if not active:
                if error is not None:
                    raise error
                raise AppError(409, "llm_attempt_inactive", "The generation attempt is no longer active")
            if error is None:
                return result
            if wait <= 0:
                raise error
            # Telemetry commits before sleeping. Check remaining again in case
            # persistence was slow; a timeout never triggers another HTTP call.
            if wait >= budget.remaining():
                raise error
            time.sleep(wait)
            waited += wait
    raise AssertionError("bounded provider loop exhausted")


def retry_after_seconds(value: object) -> float | None:
    """Read an HTTP delay/date transiently, returning only a finite number."""
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None
