from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from comps_service.alpha_vantage import AlphaVantageClient
from comps_service.calculator import CompanyCompsInput


class AlphaVantageNormalizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class FxRate:
    rate: float
    date: date


class AlphaVantageCompsNormalizer:
    def __init__(self, client: AlphaVantageClient) -> None:
        self.client = client

    def normalize_company(self, symbol: str, *, output_currency: str) -> CompanyCompsInput:
        ticker = symbol.upper()
        quote = self.client.get_global_quote(ticker)
        overview = self.client.get_overview(ticker)
        income_statement = self.client.get_income_statement(ticker)
        balance_sheet = self.client.get_balance_sheet(ticker)

        quote_payload = self._required_mapping(quote, "Global Quote", "GLOBAL_QUOTE")
        latest_income_reports = self._required_reports(
            income_statement,
            "quarterlyReports",
            "INCOME_STATEMENT",
            min_count=4,
        )[:4]
        latest_balance = self._required_reports(
            balance_sheet,
            "quarterlyReports",
            "BALANCE_SHEET",
            min_count=1,
        )[0]

        source_currency = self._source_currency(overview, latest_income_reports, latest_balance)
        requested_currency = output_currency.upper()
        as_of = self._parse_provider_date(
            self._required_value(quote_payload, "07. latest trading day", "GLOBAL_QUOTE"),
            "GLOBAL_QUOTE.07. latest trading day",
        )
        fx_rate = self._fx_rate(source_currency, requested_currency, as_of.date())

        sources = {
            "share_price": self._with_fx_source(
                "alpha_vantage.GLOBAL_QUOTE.05. price",
                source_currency,
                requested_currency,
                fx_rate,
            ),
            "shares_outstanding": "alpha_vantage.OVERVIEW.SharesOutstanding",
            "cash": self._with_fx_source(
                "alpha_vantage.BALANCE_SHEET.quarterlyReports[0].cashAndCashEquivalentsAtCarryingValue",
                source_currency,
                requested_currency,
                fx_rate,
            ),
            "total_debt": self._with_fx_source(
                "alpha_vantage.BALANCE_SHEET.quarterlyReports[0].shortLongTermDebtTotal",
                source_currency,
                requested_currency,
                fx_rate,
            ),
            "revenue_ltm": self._with_fx_source(
                "alpha_vantage.INCOME_STATEMENT.quarterlyReports[0:4].totalRevenue",
                source_currency,
                requested_currency,
                fx_rate,
            ),
            "ebit_ltm": self._with_fx_source(
                "alpha_vantage.INCOME_STATEMENT.quarterlyReports[0:4].ebit",
                source_currency,
                requested_currency,
                fx_rate,
            ),
            "ebitda_ltm": self._with_fx_source(
                "alpha_vantage.INCOME_STATEMENT.quarterlyReports[0:4].ebitda",
                source_currency,
                requested_currency,
                fx_rate,
            ),
            "net_income_ltm": self._with_fx_source(
                "alpha_vantage.INCOME_STATEMENT.quarterlyReports[0:4].netIncome",
                source_currency,
                requested_currency,
                fx_rate,
            ),
        }

        return CompanyCompsInput(
            ticker=ticker,
            company_name=self._optional_string(overview.get("Name")),
            currency=requested_currency,
            share_price=self._money(quote_payload, "05. price", "GLOBAL_QUOTE") * fx_rate.rate,
            shares_outstanding=self._number(overview, "SharesOutstanding", "OVERVIEW"),
            cash=self._money(latest_balance, "cashAndCashEquivalentsAtCarryingValue", "BALANCE_SHEET") * fx_rate.rate,
            total_debt=self._money(latest_balance, "shortLongTermDebtTotal", "BALANCE_SHEET") * fx_rate.rate,
            revenue_ltm=self._ltm_money(latest_income_reports, "totalRevenue") * fx_rate.rate,
            ebit_ltm=self._ltm_money(latest_income_reports, "ebit") * fx_rate.rate,
            ebitda_ltm=self._ltm_money(latest_income_reports, "ebitda") * fx_rate.rate,
            net_income_ltm=self._ltm_money(latest_income_reports, "netIncome") * fx_rate.rate,
            as_of=as_of,
            sources=sources,
        )

    def _source_currency(
        self,
        overview: dict[str, Any],
        income_reports: list[dict[str, Any]],
        balance_report: dict[str, Any],
    ) -> str:
        currencies = [
            self._optional_string(overview.get("Currency")),
            self._optional_string(income_reports[0].get("reportedCurrency")),
            self._optional_string(balance_report.get("reportedCurrency")),
        ]
        present = {currency.upper() for currency in currencies if currency}
        if not present:
            raise AlphaVantageNormalizationError("Alpha Vantage payload is missing currency.")
        if len(present) > 1:
            raise AlphaVantageNormalizationError(
                f"Alpha Vantage payload has inconsistent currencies: {', '.join(sorted(present))}."
            )
        return present.pop()

    def _fx_rate(self, source_currency: str, requested_currency: str, as_of: date) -> FxRate:
        if source_currency == requested_currency:
            return FxRate(rate=1.0, date=as_of)

        payload = self.client.get_fx_daily(source_currency, requested_currency)
        series = self._required_mapping(payload, "Time Series FX (Daily)", "FX_DAILY")
        usable_dates = sorted(
            (self._parse_date_key(value) for value in series),
            reverse=True,
        )
        fx_date = next((value for value in usable_dates if value <= as_of), None)
        if fx_date is None:
            raise AlphaVantageNormalizationError(
                f"FX rate {source_currency}/{requested_currency} is unavailable for {as_of.isoformat()}."
            )

        daily_value = series[fx_date.isoformat()]
        close_value = self._money(daily_value, "4. close", "FX_DAILY")
        return FxRate(rate=close_value, date=fx_date)

    def _with_fx_source(
        self,
        source: str,
        source_currency: str,
        requested_currency: str,
        fx_rate: FxRate,
    ) -> str:
        if source_currency == requested_currency:
            return source
        return f"{source}; fx={source_currency}/{requested_currency}@{fx_rate.rate} date={fx_rate.date.isoformat()}"

    def _ltm_money(self, reports: list[dict[str, Any]], field_name: str) -> float:
        return sum(self._money(report, field_name, "INCOME_STATEMENT") for report in reports)

    def _money(self, payload: dict[str, Any], field_name: str, payload_name: str) -> float:
        return self._number(payload, field_name, payload_name)

    def _number(self, payload: dict[str, Any], field_name: str, payload_name: str) -> float:
        value = self._required_value(payload, field_name, payload_name)
        try:
            return float(Decimal(value.replace(",", "")))
        except (AttributeError, InvalidOperation, ValueError) as exc:
            raise AlphaVantageNormalizationError(
                f"Alpha Vantage {payload_name} field {field_name} is not numeric."
            ) from exc

    def _required_mapping(self, payload: dict[str, Any], field_name: str, payload_name: str) -> dict[str, Any]:
        value = payload.get(field_name)
        if not isinstance(value, dict):
            raise AlphaVantageNormalizationError(
                f"Alpha Vantage {payload_name} payload is missing {field_name}."
            )
        return value

    def _required_reports(
        self,
        payload: dict[str, Any],
        field_name: str,
        payload_name: str,
        *,
        min_count: int,
    ) -> list[dict[str, Any]]:
        value = payload.get(field_name)
        if not isinstance(value, list) or len(value) < min_count:
            raise AlphaVantageNormalizationError(
                f"Alpha Vantage {payload_name} payload needs at least {min_count} {field_name}."
            )
        if not all(isinstance(report, dict) for report in value):
            raise AlphaVantageNormalizationError(
                f"Alpha Vantage {payload_name} {field_name} must contain object reports."
            )
        return value

    def _required_value(self, payload: dict[str, Any], field_name: str, payload_name: str) -> str:
        value = payload.get(field_name)
        if value in (None, "", "None", "-"):
            raise AlphaVantageNormalizationError(
                f"Alpha Vantage {payload_name} payload is missing {field_name}."
            )
        return str(value)

    def _parse_provider_date(self, value: str, field_name: str) -> datetime:
        return datetime.combine(self._parse_date_value(value, field_name), time.min, tzinfo=UTC)

    def _parse_date_key(self, value: str) -> date:
        return self._parse_date_value(value, "FX_DAILY date")

    def _parse_date_value(self, value: str, field_name: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise AlphaVantageNormalizationError(
                f"Alpha Vantage {field_name} is not an ISO date."
            ) from exc

    def _optional_string(self, value: Any) -> str | None:
        if value in (None, "", "None", "-"):
            return None
        return str(value).strip()
