from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

import httpx

from comps_service.provider import AlphaVantageCompanyDataSource
from comps_service.run_service import CompanyDataUnavailable, CompsRunExecutionError
from comps_service.tool_validation import TickerDirectory


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "alpha_vantage"


class AlphaVantageCompanyDataSourceTest(unittest.TestCase):
    def test_explicit_provider_field_variants_are_normalized(self) -> None:
        fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )
        fixture["OVERVIEW"]["SharesOutstanding"] = "None"
        balance = fixture["BALANCE_SHEET"]["quarterlyReports"][0]
        balance["cashAndCashEquivalentsAtCarryingValue"] = "None"
        balance["cashAndShortTermInvestments"] = "125"
        balance["shortLongTermDebtTotal"] = "None"
        balance["currentDebt"] = "75"
        balance["longTermDebtNoncurrent"] = "250"

        def respond(request):
            return httpx.Response(
                200,
                json=deepcopy(fixture[request.url.params["function"]]),
            )

        loaded = self._source(respond).load(tickers=["AAPL"], currency="USD")
        company = loaded.companies[0]

        self.assertEqual(company.shares_outstanding, 1000.0)
        self.assertEqual(company.cash, 125.0)
        self.assertEqual(company.total_debt, 325.0)
        self.assertIn(
            "commonStockSharesOutstanding",
            company.sources["shares_outstanding"],
        )
        self.assertIn("cashAndShortTermInvestments", company.sources["cash"])
        self.assertIn("currentDebt", company.sources["total_debt"])
        self.assertIn("longTermDebtNoncurrent", company.sources["total_debt"])

    def test_quote_entitlement_is_sent_only_with_quote_requests(self) -> None:
        fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )
        request_params: dict[str, dict[str, str]] = {}

        def respond(request):
            function = request.url.params["function"]
            request_params[function] = dict(request.url.params)
            return httpx.Response(200, json=deepcopy(fixture[function]))

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_QUOTE_ENTITLEMENT": "realtime",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
            ticker_directory=self._ticker_directory(),
        )

        source.load(tickers=["AAPL"], currency="USD")

        self.assertEqual(request_params["GLOBAL_QUOTE"]["entitlement"], "realtime")
        self.assertTrue(
            all(
                "entitlement" not in params
                for function, params in request_params.items()
                if function != "GLOBAL_QUOTE"
            )
        )

    def test_blank_quote_entitlement_is_not_sent(self) -> None:
        fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )
        quote_params: dict[str, str] = {}

        def respond(request):
            function = request.url.params["function"]
            if function == "GLOBAL_QUOTE":
                quote_params.update(request.url.params)
            return httpx.Response(200, json=deepcopy(fixture[function]))

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_QUOTE_ENTITLEMENT": "  ",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
            ticker_directory=self._ticker_directory(),
        )

        source.load(tickers=["AAPL"], currency="USD")

        self.assertNotIn("entitlement", quote_params)

    def test_invalid_quote_entitlement_fails_before_provider_request(self) -> None:
        def respond(_request):
            self.fail("Invalid configuration must not reach Alpha Vantage.")

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_QUOTE_ENTITLEMENT": "realtme",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
            ticker_directory=self._ticker_directory(),
        )

        with self.assertRaisesRegex(
            CompanyDataUnavailable,
            (
                "ALPHA_VANTAGE_QUOTE_ENTITLEMENT must be "
                "'realtime' or 'delayed'"
            ),
        ):
            source.load(tickers=["AAPL"], currency="USD")

    def test_missing_fundamental_evidence_fails_instead_of_building_input(
        self,
    ) -> None:
        fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )
        fixture["INCOME_STATEMENT"]["quarterlyReports"][2]["ebitda"] = "None"

        def respond(request):
            return httpx.Response(
                200,
                json=deepcopy(fixture[request.url.params["function"]]),
            )

        source = self._source(respond)

        with self.assertRaisesRegex(
            CompsRunExecutionError,
            "Missing Alpha Vantage evidence.*ebitda",
        ):
            source.load(tickers=["AAPL"], currency="USD")

    def test_non_positive_quote_price_fails_instead_of_building_input(
        self,
    ) -> None:
        fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )

        for quote_price in ("0", "-1"):
            with self.subTest(quote_price=quote_price):
                fixture["GLOBAL_QUOTE"]["Global Quote"]["05. price"] = quote_price

                def respond(request):
                    return httpx.Response(
                        200,
                        json=deepcopy(fixture[request.url.params["function"]]),
                    )

                with self.assertRaisesRegex(
                    CompsRunExecutionError,
                    "non-positive quote price",
                ):
                    self._source(respond).load(
                        tickers=["AAPL"],
                        currency="USD",
                    )

    def test_missing_fx_evidence_fails_instead_of_mislabeling_input(self) -> None:
        fixture = json.loads(
            (FIXTURE_ROOT / "usd_company_latest.json").read_text()
        )
        fixture["OVERVIEW"]["Currency"] = "CAD"
        for function in ("INCOME_STATEMENT", "BALANCE_SHEET"):
            for report in fixture[function]["quarterlyReports"]:
                report["reportedCurrency"] = "CAD"

        def respond(request):
            function = request.url.params["function"]
            if function == "CURRENCY_EXCHANGE_RATE":
                return httpx.Response(200, json={})
            return httpx.Response(200, json=deepcopy(fixture[function]))

        source = self._source(respond)

        with self.assertRaisesRegex(
            CompsRunExecutionError,
            "CURRENCY_EXCHANGE_RATE returned no evidence for CAD/USD",
        ):
            source.load(tickers=["AAPL"], currency="USD")

    def test_missing_quote_currency_evidence_fails_clearly(self) -> None:
        def respond(_request):
            self.fail("Missing quote currency must fail before provider requests.")

        source = AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
            ticker_directory=self._ticker_directory(currency=None),
        )

        with self.assertRaisesRegex(
            CompsRunExecutionError,
            "Missing Alpha Vantage evidence.*SYMBOL_SEARCH.8. currency",
        ):
            source.load(tickers=["AAPL"], currency="USD")

    def _source(self, respond) -> AlphaVantageCompanyDataSource:
        return AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
            ticker_directory=self._ticker_directory(),
        )

    def _ticker_directory(self, *, currency: str | None = "USD") -> TickerDirectory:
        directory = TickerDirectory()
        provider_match = {
            "1. symbol": "AAPL",
            "3. type": "Equity",
        }
        if currency is not None:
            provider_match["8. currency"] = currency
        directory.remember(
            "AAPL",
            is_supported=True,
            provider_match=provider_match,
        )
        return directory


if __name__ == "__main__":
    unittest.main()
