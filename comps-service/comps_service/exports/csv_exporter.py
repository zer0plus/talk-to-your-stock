from __future__ import annotations

import csv
from io import StringIO

from talk_to_your_stock_shared import CompsTable

COLUMNS = [
    "ticker",
    "company_name",
    "is_target",
    "share_price",
    "market_cap",
    "enterprise_value",
    "revenue_ltm",
    "ebit_ltm",
    "ebitda_ltm",
    "net_income_ltm",
    "ev_to_revenue",
    "ev_to_ebit",
    "ev_to_ebitda",
    "pe",
]


def to_csv(table: CompsTable) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    for row in table.rows:
        payload = row.model_dump(mode="json")
        writer.writerow({column: payload.get(column) for column in COLUMNS})
    return buffer.getvalue()
