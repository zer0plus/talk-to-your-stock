from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from comps_service.calculator import (
    CompanyCompsInput,
    CompsCalculationError,
    CompsCalculator,
)
from talk_to_your_stock_shared import TraceOutputField


class CompsCalculatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calculator = CompsCalculator()
        self.as_of = datetime(2026, 6, 26, tzinfo=UTC)

    def test_generate_calculates_expected_comps_table(self) -> None:
        table, trace = self.calculator.generate(
            run_id=uuid4(),
            target_ticker="AAPL",
            companies=[self._company("AAPL")],
            currency="USD",
        )

        row = table.rows[0]
        self.assertTrue(row.is_target)
        self.assertEqual(row.market_cap, 1000)
        self.assertEqual(row.net_debt, 300)
        self.assertEqual(row.enterprise_value, 1300)
        self.assertEqual(row.ev_to_revenue, 5.2)
        self.assertEqual(row.ev_to_ebit, 13.0)
        self.assertEqual(row.ev_to_ebitda, 10.4)
        self.assertEqual(row.pe, 20.0)

        self.assertEqual(table.summary.stats.ev_to_revenue.min, 5.2)
        self.assertEqual(table.summary.stats.ev_to_revenue.median, 5.2)
        self.assertEqual(table.summary.stats.ev_to_revenue.max, 5.2)
        self.assertEqual(table.summary.stats.ev_to_ebit.min, 13.0)
        self.assertEqual(table.summary.stats.ev_to_ebitda.min, 10.4)
        self.assertEqual(table.summary.stats.pe.min, 20.0)

        self.assertEqual(trace.run_id, table.run_id)
        self.assertEqual(
            {formula.output_field for formula in trace.formulas},
            {
                TraceOutputField.EQUITY_VALUE,
                TraceOutputField.NET_DEBT,
                TraceOutputField.ENTERPRISE_VALUE,
                TraceOutputField.EV_TO_REVENUE,
                TraceOutputField.EV_TO_EBIT,
                TraceOutputField.EV_TO_EBITDA,
                TraceOutputField.PE,
            },
        )

    def test_zero_denominators_return_none_and_stats_ignore_none(self) -> None:
        table, _trace = self.calculator.generate(
            run_id=uuid4(),
            target_ticker="AAPL",
            companies=[
                self._company(
                    "AAPL",
                    revenue_ltm=0,
                    ebit_ltm=0,
                    ebitda_ltm=0,
                    net_income_ltm=0,
                ),
                self._company("MSFT"),
            ],
            currency="USD",
        )

        target_row = table.rows[0]
        peer_row = table.rows[1]
        self.assertIsNone(target_row.ev_to_revenue)
        self.assertIsNone(target_row.ev_to_ebit)
        self.assertIsNone(target_row.ev_to_ebitda)
        self.assertIsNone(target_row.pe)

        self.assertEqual(table.summary.stats.ev_to_revenue.min, peer_row.ev_to_revenue)
        self.assertEqual(table.summary.stats.ev_to_revenue.median, peer_row.ev_to_revenue)
        self.assertEqual(table.summary.stats.ev_to_revenue.max, peer_row.ev_to_revenue)
        self.assertEqual(table.summary.stats.pe.min, peer_row.pe)

    def test_missing_target_ticker_raises(self) -> None:
        with self.assertRaisesRegex(CompsCalculationError, "Target ticker AAPL is missing"):
            self.calculator.generate(
                run_id=uuid4(),
                target_ticker="AAPL",
                companies=[self._company("MSFT")],
                currency="USD",
            )

    def test_duplicate_ticker_raises(self) -> None:
        with self.assertRaisesRegex(CompsCalculationError, "Duplicate ticker input: AAPL"):
            self.calculator.generate(
                run_id=uuid4(),
                target_ticker="AAPL",
                companies=[self._company("AAPL"), self._company("aapl")],
                currency="USD",
            )

    def test_empty_company_list_raises(self) -> None:
        with self.assertRaisesRegex(CompsCalculationError, "At least one company"):
            self.calculator.generate(
                run_id=uuid4(),
                target_ticker="AAPL",
                companies=[],
                currency="USD",
            )

    def _company(self, ticker: str, **overrides: float) -> CompanyCompsInput:
        values = {
            "share_price": 10.0,
            "shares_outstanding": 100.0,
            "cash": 200.0,
            "total_debt": 500.0,
            "revenue_ltm": 250.0,
            "ebit_ltm": 100.0,
            "ebitda_ltm": 125.0,
            "net_income_ltm": 50.0,
        }
        values.update(overrides)
        return CompanyCompsInput(
            ticker=ticker,
            company_name=f"{ticker.upper()} Inc.",
            currency="USD",
            as_of=self.as_of,
            sources={field: f"fixture.{field}" for field in values},
            **values,
        )


if __name__ == "__main__":
    unittest.main()
