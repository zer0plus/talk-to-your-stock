from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

from talk_to_your_stock_shared import User
from talk_to_your_stock_shared.readiness import (
    ENVIRONMENT_VAR,
    LOCAL_ENVIRONMENTS,
    PRODUCTION_ENVIRONMENT,
)
from talk_to_your_stock_shared.time import utc_now


class AuthenticationError(RuntimeError):
    pass


def authenticate_user(
    authorization: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> User:
    env = os.environ if environ is None else environ
    environment = env.get(ENVIRONMENT_VAR, "").strip().lower()

    if environment in LOCAL_ENVIRONMENTS:
        return _dev_user(env)

    if environment == PRODUCTION_ENVIRONMENT:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("Bearer authentication is required.")
        raise AuthenticationError("Managed JWT verification is not implemented.")

    raise AuthenticationError(f"{ENVIRONMENT_VAR} is not configured.")


def _dev_user(environ: Mapping[str, str]) -> User:
    user_id = environ.get("DEV_AUTH_USER_ID", "").strip()
    email = environ.get("DEV_AUTH_EMAIL", "").strip()
    if not user_id or not email:
        raise AuthenticationError("DEV_AUTH_USER_ID and DEV_AUTH_EMAIL are required.")

    now = utc_now()
    return User(
        id=UUID(user_id),
        email=email,
        created_at=now,
        updated_at=now,
    )
