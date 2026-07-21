from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

import httpx

from comps_service.provider import AlphaVantageCompanyDataSource
from comps_service.run_service import CompsRunExecutionError


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

    def _source(self, respond) -> AlphaVantageCompanyDataSource:
        return AlphaVantageCompanyDataSource(
            environ={
                "ALPHA_VANTAGE_API_KEY": "fixture-key",
                "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
            },
            transport=httpx.MockTransport(respond),
        )


if __name__ == "__main__":
    unittest.main()
