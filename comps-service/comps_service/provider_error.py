from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderError:
    message: str
    details: dict[str, object]


def sanitize_provider_evidence(value: Any, *, secret: str) -> Any:
    if not secret:
        return value
    if isinstance(value, dict):
        return {
            (
                key.replace(secret, "[REDACTED]")
                if isinstance(key, str)
                else key
            ): sanitize_provider_evidence(item, secret=secret)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_provider_evidence(item, secret=secret) for item in value]
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    return value


def alpha_vantage_error(
    payload: object,
    *,
    operation: str,
    subject: str,
    action: str,
) -> ProviderError | None:
    if not isinstance(payload, dict):
        return None
    for key in ("Error Message", "Note", "Information"):
        value = payload.get(key)
        if not value:
            continue
        normalized = str(value).lower()
        if any(term in normalized for term in ("quota", "rate limit", "call frequency")):
            message = (
                "Alpha Vantage request limit was reached while "
                f"{action} {subject}."
            )
        else:
            message = f"Alpha Vantage {operation} rejected the request for {subject}."
        return ProviderError(
            message=message,
            details={
                "provider": "alpha_vantage",
                "operation": operation,
                "subject": subject,
            },
        )
    return None
