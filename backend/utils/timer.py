from __future__ import annotations

from time import perf_counter
from typing import Callable, TypeVar


T = TypeVar("T")


def timed(call: Callable[[], T]) -> tuple[T, float]:
    started = perf_counter()
    value = call()
    return value, round((perf_counter() - started) * 1000, 2)
