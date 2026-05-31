from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from comps_service.settings import settings
from talk_to_your_stock_shared.db_schema import APP_SCHEMA_SQL


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def ensure_schema() -> None:
    with connect() as conn:
        conn.execute(APP_SCHEMA_SQL)
        conn.commit()
