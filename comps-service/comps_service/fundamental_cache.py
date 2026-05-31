from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import redis
from psycopg.types.json import Jsonb

from comps_service.db import connect
from comps_service.settings import settings
from talk_to_your_stock_shared.time import utc_now


class CacheRefreshInProgress(RuntimeError):
    pass


@dataclass(frozen=True)
class FundamentalCacheEntry:
    symbol: str
    statement_type: str
    period_type: str
    latest_fiscal_date: date | None
    payload_jsonb: dict[str, Any]
    source_hash: str
    fetched_at: datetime
    next_expected_refresh_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "FundamentalCacheEntry":
        return cls(
            symbol=row["symbol"],
            statement_type=row["statement_type"],
            period_type=row["period_type"],
            latest_fiscal_date=row["latest_fiscal_date"],
            payload_jsonb=row["payload_jsonb"],
            source_hash=row["source_hash"],
            fetched_at=row["fetched_at"],
            next_expected_refresh_at=row["next_expected_refresh_at"],
        )

    @classmethod
    def from_json(cls, raw: str) -> "FundamentalCacheEntry":
        payload = json.loads(raw)
        latest_fiscal_date = payload.get("latest_fiscal_date")
        return cls(
            symbol=payload["symbol"],
            statement_type=payload["statement_type"],
            period_type=payload["period_type"],
            latest_fiscal_date=date.fromisoformat(latest_fiscal_date) if latest_fiscal_date else None,
            payload_jsonb=payload["payload_jsonb"],
            source_hash=payload["source_hash"],
            fetched_at=datetime.fromisoformat(payload["fetched_at"]),
            next_expected_refresh_at=datetime.fromisoformat(payload["next_expected_refresh_at"]),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "symbol": self.symbol,
                "statement_type": self.statement_type,
                "period_type": self.period_type,
                "latest_fiscal_date": self.latest_fiscal_date.isoformat() if self.latest_fiscal_date else None,
                "payload_jsonb": self.payload_jsonb,
                "source_hash": self.source_hash,
                "fetched_at": self.fetched_at.isoformat(),
                "next_expected_refresh_at": self.next_expected_refresh_at.isoformat(),
            },
            sort_keys=True,
        )


class FundamentalCache:
    def __init__(self) -> None:
        self.redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    def get_or_refresh(
        self,
        *,
        symbol: str,
        statement_type: str,
        period_type: str,
        fetch_payload: Callable[[], dict[str, Any]],
        latest_fiscal_date_fn: Callable[[dict[str, Any]], date | None],
        next_expected_refresh_fn: Callable[[dict[str, Any], date | None], datetime],
    ) -> dict[str, Any]:
        symbol = symbol.upper()
        redis_key = self._redis_key(symbol, statement_type, period_type)
        cached = self._get_redis(redis_key)
        if cached and self._is_before_refresh_window(cached):
            return cached.payload_jsonb

        persisted = self._get_postgres(symbol, statement_type, period_type)
        if persisted and self._is_before_refresh_window(persisted):
            self._set_redis(redis_key, persisted)
            return persisted.payload_jsonb

        lock_key = f"{redis_key}:refresh_lock"
        lock_value = utc_now().isoformat()
        lock_acquired = bool(self.redis.set(lock_key, lock_value, nx=True, ex=settings.cache_refresh_lock_seconds))
        if not lock_acquired:
            if persisted:
                self._set_redis(redis_key, persisted)
                return persisted.payload_jsonb
            raise CacheRefreshInProgress(f"Refresh already in progress for {symbol} {statement_type} {period_type}.")

        try:
            payload = fetch_payload()
            source_hash = self._hash_payload(payload)
            latest_fiscal_date = latest_fiscal_date_fn(payload)
            next_expected_refresh_at = next_expected_refresh_fn(payload, latest_fiscal_date)
            now = utc_now()
            if next_expected_refresh_at <= now:
                next_expected_refresh_at = now + timedelta(days=settings.cache_refresh_backoff_days)
            if persisted and persisted.source_hash == source_hash and not self._is_before_refresh_window(persisted):
                next_expected_refresh_at = now + timedelta(days=settings.cache_refresh_backoff_days)
            entry = FundamentalCacheEntry(
                symbol=symbol,
                statement_type=statement_type,
                period_type=period_type,
                latest_fiscal_date=latest_fiscal_date,
                payload_jsonb=payload,
                source_hash=source_hash,
                fetched_at=now,
                next_expected_refresh_at=next_expected_refresh_at,
            )
            self._upsert_postgres(entry)
            self._set_redis(redis_key, entry)
            return entry.payload_jsonb
        finally:
            self.redis.delete(lock_key)

    def ping(self) -> None:
        self.redis.ping()

    def _is_before_refresh_window(self, entry: FundamentalCacheEntry) -> bool:
        return utc_now() < self._ensure_aware(entry.next_expected_refresh_at)

    def _get_redis(self, key: str) -> FundamentalCacheEntry | None:
        raw = self.redis.get(key)
        if not raw:
            return None
        return FundamentalCacheEntry.from_json(raw)

    def _set_redis(self, key: str, entry: FundamentalCacheEntry) -> None:
        self.redis.set(key, entry.to_json())

    def _get_postgres(self, symbol: str, statement_type: str, period_type: str) -> FundamentalCacheEntry | None:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT symbol, statement_type, period_type, latest_fiscal_date, payload_jsonb,
                       source_hash, fetched_at, next_expected_refresh_at
                FROM fundamental_cache
                WHERE symbol = %s AND statement_type = %s AND period_type = %s
                """,
                (symbol, statement_type, period_type),
            ).fetchone()
        return FundamentalCacheEntry.from_row(row) if row else None

    def _upsert_postgres(self, entry: FundamentalCacheEntry) -> None:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO fundamental_cache (
                    symbol, statement_type, period_type, latest_fiscal_date, payload_jsonb,
                    source_hash, fetched_at, next_expected_refresh_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, statement_type, period_type) DO UPDATE SET
                    latest_fiscal_date = EXCLUDED.latest_fiscal_date,
                    payload_jsonb = EXCLUDED.payload_jsonb,
                    source_hash = EXCLUDED.source_hash,
                    fetched_at = EXCLUDED.fetched_at,
                    next_expected_refresh_at = EXCLUDED.next_expected_refresh_at
                """,
                (
                    entry.symbol,
                    entry.statement_type,
                    entry.period_type,
                    entry.latest_fiscal_date,
                    Jsonb(entry.payload_jsonb),
                    entry.source_hash,
                    entry.fetched_at,
                    entry.next_expected_refresh_at,
                ),
            )
            conn.commit()

    def _hash_payload(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _redis_key(self, symbol: str, statement_type: str, period_type: str) -> str:
        return f"fundamental_cache:{symbol}:{statement_type}:{period_type}"

    def _ensure_aware(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
