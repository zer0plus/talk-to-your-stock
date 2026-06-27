from __future__ import annotations

import unittest

from comps_service.alpha_vantage import (
    AlphaVantageClient,
    AlphaVantageConfigError,
    AlphaVantageProviderError,
)
from comps_service.provider_normalizer import (
    AlphaVantageCompsNormalizer,
    AlphaVantageNormalizationError,
)


class AlphaVantageClientTest(unittest.TestCase):
    def test_missing_api_key_raises_config_error(self) -> None:
        client = AlphaVantageClient(api_key="", request_json=lambda _params: {})

        with self.assertRaisesRegex(AlphaVantageConfigError, "ALPHA_VANTAGE_API_KEY"):
            client.get_global_quote("AAPL")

    def test_provider_error_payload_raises_provider_error(self) -> None:
        client = AlphaVantageClient(
            api_key="test",
            request_json=lambda _params: {"Note": "rate limit reached"},
        )

        with self.assertRaisesRegex(AlphaVantageProviderError, "rate limit"):
            client.get_overview("AAPL")


class AlphaVantageCompsNormalizerTest(unittest.TestCase):
    def test_usd_payload_normalizes_without_fx(self) -> None:
        normalizer = AlphaVantageCompsNormalizer(_fixture_client(currency="USD"))

        company = normalizer.normalize_company("AAPL", output_currency="USD")

        self.assertEqual(company.ticker, "AAPL")
        self.assertEqual(company.company_name, "Apple Inc.")
        self.assertEqual(company.currency, "USD")
        self.assertEqual(company.share_price, 10.0)
        self.assertEqual(company.shares_outstanding, 100.0)
        self.assertEqual(company.cash, 200.0)
        self.assertEqual(company.total_debt, 500.0)
        self.assertEqual(company.revenue_ltm, 250.0)
        self.assertEqual(company.ebit_ltm, 100.0)
        self.assertEqual(company.ebitda_ltm, 125.0)
        self.assertEqual(company.net_income_ltm, 50.0)
        self.assertEqual(company.as_of.isoformat(), "2026-06-26T00:00:00+00:00")
        self.assertEqual(company.sources["cash"], "alpha_vantage.BALANCE_SHEET.quarterlyReports[0].cashAndCashEquivalentsAtCarryingValue")

    def test_non_usd_payload_converts_money_with_fx(self) -> None:
        normalizer = AlphaVantageCompsNormalizer(_fixture_client(currency="EUR", fx_rate="1.2"))

        company = normalizer.normalize_company("SAP", output_currency="USD")

        self.assertEqual(company.currency, "USD")
        self.assertEqual(company.share_price, 12.0)
        self.assertEqual(company.cash, 240.0)
        self.assertEqual(company.total_debt, 600.0)
        self.assertEqual(company.revenue_ltm, 300.0)
        self.assertEqual(company.ebit_ltm, 120.0)
        self.assertEqual(company.ebitda_ltm, 150.0)
        self.assertEqual(company.net_income_ltm, 60.0)
        self.assertIn("fx=EUR/USD@1.2 date=2026-06-26", company.sources["share_price"])
        self.assertIn("fx=EUR/USD@1.2 date=2026-06-26", company.sources["revenue_ltm"])

    def test_missing_required_financial_field_raises_normalization_error(self) -> None:
        client = _fixture_client(currency="USD")
        client.income_statement["quarterlyReports"][0].pop("totalRevenue")
        normalizer = AlphaVantageCompsNormalizer(client)

        with self.assertRaisesRegex(AlphaVantageNormalizationError, "totalRevenue"):
            normalizer.normalize_company("AAPL", output_currency="USD")

    def test_invalid_numeric_field_raises_normalization_error(self) -> None:
        client = _fixture_client(currency="USD")
        client.overview["SharesOutstanding"] = "not-a-number"
        normalizer = AlphaVantageCompsNormalizer(client)

        with self.assertRaisesRegex(AlphaVantageNormalizationError, "SharesOutstanding"):
            normalizer.normalize_company("AAPL", output_currency="USD")

    def test_missing_fx_data_raises_normalization_error(self) -> None:
        client = _fixture_client(currency="EUR", fx_rate=None)
        normalizer = AlphaVantageCompsNormalizer(client)

        with self.assertRaisesRegex(AlphaVantageNormalizationError, "FX rate EUR/USD"):
            normalizer.normalize_company("SAP", output_currency="USD")


class _FixtureAlphaVantageClient:
    def __init__(self, *, currency: str, fx_rate: str | None = "1.0") -> None:
        self.quote = {
            "Global Quote": {
                "01. symbol": "AAPL",
                "05. price": "10",
                "07. latest trading day": "2026-06-26",
            }
        }
        self.overview = {
            "Symbol": "AAPL",
            "Name": "Apple Inc.",
            "Currency": currency,
            "SharesOutstanding": "100",
        }
        self.income_statement = {
            "symbol": "AAPL",
            "quarterlyReports": [
                _income_report(currency, "2026-03-31", revenue="100", ebit="40", ebitda="50", net_income="20"),
                _income_report(currency, "2025-12-31", revenue="60", ebit="25", ebitda="30", net_income="15"),
                _income_report(currency, "2025-09-30", revenue="50", ebit="20", ebitda="25", net_income="10"),
                _income_report(currency, "2025-06-30", revenue="40", ebit="15", ebitda="20", net_income="5"),
            ],
        }
        self.balance_sheet = {
            "symbol": "AAPL",
            "quarterlyReports": [
                {
                    "fiscalDateEnding": "2026-03-31",
                    "reportedCurrency": currency,
                    "cashAndCashEquivalentsAtCarryingValue": "200",
                    "shortLongTermDebtTotal": "500",
                }
            ],
        }
        self.fx_daily = (
            {"Time Series FX (Daily)": {"2026-06-26": {"4. close": fx_rate}}}
            if fx_rate is not None
            else {"Time Series FX (Daily)": {}}
        )

    def get_global_quote(self, symbol: str) -> dict[str, object]:
        return self.quote

    def get_overview(self, symbol: str) -> dict[str, object]:
        return self.overview

    def get_income_statement(self, symbol: str) -> dict[str, object]:
        return self.income_statement

    def get_balance_sheet(self, symbol: str) -> dict[str, object]:
        return self.balance_sheet

    def get_fx_daily(self, from_currency: str, to_currency: str) -> dict[str, object]:
        return self.fx_daily


def _fixture_client(currency: str, fx_rate: str | None = "1.0") -> _FixtureAlphaVantageClient:
    return _FixtureAlphaVantageClient(currency=currency, fx_rate=fx_rate)


def _income_report(
    currency: str,
    fiscal_date: str,
    *,
    revenue: str,
    ebit: str,
    ebitda: str,
    net_income: str,
) -> dict[str, str]:
    return {
        "fiscalDateEnding": fiscal_date,
        "reportedCurrency": currency,
        "totalRevenue": revenue,
        "ebit": ebit,
        "ebitda": ebitda,
        "netIncome": net_income,
    }


if __name__ == "__main__":
    unittest.main()
