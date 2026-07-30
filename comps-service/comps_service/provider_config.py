from __future__ import annotations

import math
from collections.abc import Mapping


ALPHA_VANTAGE_QUOTE_ENTITLEMENT_VAR = "ALPHA_VANTAGE_QUOTE_ENTITLEMENT"
ALPHA_VANTAGE_QUOTE_ENTITLEMENTS = frozenset({"realtime", "delayed"})


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
    if not math.isfinite(value):
        raise InvalidProviderConfiguration(
            name=name,
            message=f"{name} must be finite.",
        )
    if value < 0:
        raise InvalidProviderConfiguration(
            name=name,
            message=f"{name} must not be negative.",
        )
    return value


def quote_entitlement_setting(environ: Mapping[str, str]) -> str | None:
    value = environ.get(ALPHA_VANTAGE_QUOTE_ENTITLEMENT_VAR, "").strip()
    if not value:
        return None
    if value not in ALPHA_VANTAGE_QUOTE_ENTITLEMENTS:
        raise InvalidProviderConfiguration(
            name=ALPHA_VANTAGE_QUOTE_ENTITLEMENT_VAR,
            message=(
                f"{ALPHA_VANTAGE_QUOTE_ENTITLEMENT_VAR} must be "
                "'realtime' or 'delayed'."
            ),
        )
    return value
