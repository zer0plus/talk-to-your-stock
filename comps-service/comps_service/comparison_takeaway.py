from __future__ import annotations

import re

from talk_to_your_stock_shared import ComparisonTakeaway, RunTableDraftResponse


class InvalidComparisonTakeaway(ValueError):
    pass


_METRIC_PATTERNS = {
    r"EV\s*/\s*Revenue": "ev_to_revenue",
    r"EV\s*/\s*EBITDA": "ev_to_ebitda",
    r"EV\s*/\s*EBIT(?!DA)": "ev_to_ebit",
    r"P\s*/\s*E": "pe",
}
_VERDICT_PATTERN = re.compile(r"\b(?:buy|sell|hold)\b", re.IGNORECASE)


def verify_comparison_takeaway(
    *,
    table: RunTableDraftResponse,
    takeaway: ComparisonTakeaway,
) -> None:
    target = next((row for row in table.rows if row.is_target), None)
    if target is None or target.ticker != table.target_ticker:
        raise InvalidComparisonTakeaway(
            "The Comparison Takeaway requires the Comps Table's Target Ticker row."
        )

    prose = f"{takeaway.headline} {takeaway.interpretation}"
    if not re.search(rf"\b{re.escape(table.target_ticker)}\b", prose, re.IGNORECASE):
        raise InvalidComparisonTakeaway(
            "The Comparison Takeaway must identify its Target Ticker."
        )

    prose_without_ticker = re.sub(
        rf"\b{re.escape(table.target_ticker)}\b",
        "",
        prose,
    )
    if _VERDICT_PATTERN.search(prose_without_ticker):
        raise InvalidComparisonTakeaway(
            "The Comparison Takeaway must not contain a buy, sell, or hold verdict."
        )

    mentioned_metrics = [
        field_name
        for pattern, field_name in _METRIC_PATTERNS.items()
        if re.search(pattern, prose, re.IGNORECASE)
    ]
    if not mentioned_metrics:
        if _has_comparable_metric(table):
            raise InvalidComparisonTakeaway(
                "The Comparison Takeaway must identify a supported Comps Table Metric."
            )
        return

    peers = [row for row in table.rows if not row.is_target]
    for field_name in mentioned_metrics:
        if getattr(target, field_name) is None or not any(
            getattr(peer, field_name) is not None for peer in peers
        ):
            raise InvalidComparisonTakeaway(
                "The Comparison Takeaway references unavailable Comps Table evidence."
            )


def _has_comparable_metric(table: RunTableDraftResponse) -> bool:
    target = next(row for row in table.rows if row.is_target)
    peers = [row for row in table.rows if not row.is_target]
    return any(
        getattr(target, field_name) is not None
        and any(getattr(peer, field_name) is not None for peer in peers)
        for field_name in _METRIC_PATTERNS.values()
    )
