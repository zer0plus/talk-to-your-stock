from __future__ import annotations

import re
from collections.abc import Sequence
from statistics import median

from talk_to_your_stock_shared import (
    ComparisonConfidence,
    ComparisonTakeaway,
    CompsRow,
    RunTableResponse,
)


class InvalidComparisonTakeaway(ValueError):
    pass


_METRICS = (
    ("ev_to_revenue", "EV / Revenue"),
    ("ev_to_ebitda", "EV / EBITDA"),
    ("pe", "P / E"),
)
_VERDICT_PATTERN = re.compile(r"\b(?:buy|sell|hold)\b", re.IGNORECASE)


def build_comparison_takeaway(
    *,
    target_ticker: str,
    rows: Sequence[CompsRow],
) -> ComparisonTakeaway:
    target = next((row for row in rows if row.is_target), None)
    if target is None or target.ticker != target_ticker:
        raise InvalidComparisonTakeaway(
            "The Comparison Takeaway requires the Comps Table's Target Ticker row."
        )

    peers = [row for row in rows if not row.is_target]
    for field_name, label in _METRICS:
        target_value = getattr(target, field_name)
        peer_values = [
            value
            for peer in peers
            if (value := getattr(peer, field_name)) is not None
        ]
        if target_value is None or not peer_values:
            continue

        peer_median = median(peer_values)
        if target_value > peer_median * 1.05:
            relationship = "at a premium to"
            meaning = "higher"
        elif target_value < peer_median * 0.95:
            relationship = "at a discount to"
            meaning = "lower"
        else:
            relationship = "in line with"
            meaning = "similar to"

        confidence = _confidence(len(peer_values))
        return ComparisonTakeaway(
            headline=(
                f"{target_ticker} trades {relationship} its peers on {label}."
            ),
            interpretation=(
                f"The Comps Table shows {target_ticker}'s {label} is {meaning} "
                "the peer median. This is relative-valuation decision support; "
                "review the table, evidence, and warnings before drawing a conclusion."
            ),
            confidence=confidence,
        )

    return ComparisonTakeaway(
        headline=f"{target_ticker}'s relative valuation is inconclusive.",
        interpretation=(
            "The Comps Table does not contain enough comparable valuation evidence "
            "to establish a relative position. Review the missing values, evidence, "
            "and warnings before drawing a conclusion."
        ),
        confidence=ComparisonConfidence.LIMITED,
    )


def verify_comparison_takeaway(table: RunTableResponse) -> None:
    expected = build_comparison_takeaway(
        target_ticker=table.target_ticker,
        rows=table.rows,
    )
    if table.comparison_takeaway != expected:
        raise InvalidComparisonTakeaway(
            "The Comparison Takeaway is not supported by its enclosing Comps Table."
        )
    prose = (
        f"{table.comparison_takeaway.headline} "
        f"{table.comparison_takeaway.interpretation}"
    )
    if _VERDICT_PATTERN.search(prose):
        raise InvalidComparisonTakeaway(
            "The Comparison Takeaway must not contain a buy, sell, or hold verdict."
        )


def _confidence(peer_count: int) -> ComparisonConfidence:
    if peer_count >= 3:
        return ComparisonConfidence.STRONG
    if peer_count == 2:
        return ComparisonConfidence.MODERATE
    return ComparisonConfidence.LIMITED
