from __future__ import annotations

from collections.abc import Mapping


class InvalidProviderConfiguration(ValueError):
    def __init__(self, *, name: str, message: str) -> None:
        self.name = name
        super().__init__(message)


def seconds_setting(
    environ: Mapping[str, str],
    *,
    name: str,
    default: float,
) -> float:
    raw_value = environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise InvalidProviderConfiguration(
            name=name,
            message=f"{name} must be a number of seconds.",
        ) from exc
    if value < 0:
        raise InvalidProviderConfiguration(
            name=name,
            message=f"{name} must not be negative.",
        )
    return value
