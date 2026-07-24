from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

import httpx

from .calculator import CompanyCompsInput
from .provider_config import InvalidProviderConfiguration, seconds_setting
from .run_service import (
    CompanyDataUnavailable,
    CompsRunExecutionError,
    LoadedCompanyData,
)
from .tool_validation import (
    ALPHA_VANTAGE_API_KEY_VAR,
    ALPHA_VANTAGE_BASE_URL_VAR,
    ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS_VAR,
    ALPHA_VANTAGE_TIMEOUT_SECONDS_VAR,
    DEFAULT_ALPHA_VANTAGE_BASE_URL,
    DEFAULT_ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS,
    DEFAULT_ALPHA_VANTAGE_TIMEOUT_SECONDS,
    ALPHA_VANTAGE_REQUEST_LIMITER,
    AlphaVantageRequestLimiter,
)

ALPHA_VANTAGE_QUOTE_ENTITLEMENT_VAR = "ALPHA_VANTAGE_QUOTE_ENTITLEMENT"


@dataclass(frozen=True)
class _SelectedQuarterlyReport:
    raw_index: int
    fiscal_date: date
    report: dict[str, Any]


class AlphaVantageCompanyDataSource:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        request_limiter: AlphaVantageRequestLimiter | None = None,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._transport = transport
        self._request_limiter = request_limiter or ALPHA_VANTAGE_REQUEST_LIMITER

    def load(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> LoadedCompanyData:
        companies: list[CompanyCompsInput] = []
        evidence: dict[str, object] = {}
        fx_cache: dict[
            tuple[str, str],
            tuple[float, dict[str, Any], str],
        ] = {}
        for ticker_candidate in tickers:
            ticker = ticker_candidate.upper()
            company, raw_evidence = self._load_company(
                ticker=ticker,
                requested_currency=currency.upper(),
                fx_cache=fx_cache,
            )
            companies.append(company)
            evidence[ticker] = raw_evidence
        return LoadedCompanyData(
            companies=companies,
            raw_provider_evidence=evidence,
        )

    def _load_company(
        self,
        *,
        ticker: str,
        requested_currency: str,
        fx_cache: dict[
            tuple[str, str],
            tuple[float, dict[str, Any], str],
        ],
    ) -> tuple[CompanyCompsInput, dict[str, object]]:
        quote_payload = self._fetch_json(function="GLOBAL_QUOTE", symbol=ticker)
        overview = self._fetch_json(function="OVERVIEW", symbol=ticker)
        income = self._fetch_json(function="INCOME_STATEMENT", symbol=ticker)
        balance_sheet = self._fetch_json(function="BALANCE_SHEET", symbol=ticker)

        quote = self._required_object(
            quote_payload,
            "Global Quote",
            provider_function="GLOBAL_QUOTE",
            ticker=ticker,
        )
        self._require_symbol(
            actual=quote.get("01. symbol"),
            expected=ticker,
            provider_function="GLOBAL_QUOTE",
        )
        self._require_symbol(
            actual=overview.get("Symbol"),
            expected=ticker,
            provider_function="OVERVIEW",
        )
        self._require_symbol(
            actual=income.get("symbol"),
            expected=ticker,
            provider_function="INCOME_STATEMENT",
        )
        self._require_symbol(
            actual=balance_sheet.get("symbol"),
            expected=ticker,
            provider_function="BALANCE_SHEET",
        )

        source_currency = self._required_text(
            overview.get("Currency"),
            field="OVERVIEW.Currency",
            ticker=ticker,
        ).upper()
        income_reports = self._latest_reports(
            income,
            ticker=ticker,
            provider_function="INCOME_STATEMENT",
            required_count=4,
        )
        balance_report_selection = self._latest_reports(
            balance_sheet,
            ticker=ticker,
            provider_function="BALANCE_SHEET",
            required_count=1,
        )[0]
        balance_report = balance_report_selection.report
        balance_source_prefix = (
            f"alpha_vantage.balance_sheet.{ticker}."
            f"quarterlyReports[{balance_report_selection.raw_index}]."
        )
        self._require_report_currency(
            reports=[
                *(selection.report for selection in income_reports),
                balance_report,
            ],
            expected=source_currency,
            ticker=ticker,
        )

        share_price = self._required_number(
            quote.get("05. price"),
            field="GLOBAL_QUOTE.05. price",
            ticker=ticker,
        )
        shares_outstanding, shares_source = self._first_number(
            [
                (
                    f"alpha_vantage.overview.{ticker}.SharesOutstanding",
                    overview.get("SharesOutstanding"),
                ),
                (
                    f"{balance_source_prefix}commonStockSharesOutstanding",
                    balance_report.get("commonStockSharesOutstanding"),
                ),
            ],
            metric="shares outstanding",
            ticker=ticker,
        )
        cash, cash_source = self._first_number(
            [
                (
                    f"{balance_source_prefix}"
                    "cashAndCashEquivalentsAtCarryingValue",
                    balance_report.get("cashAndCashEquivalentsAtCarryingValue"),
                ),
                (
                    f"{balance_source_prefix}cashAndShortTermInvestments",
                    balance_report.get("cashAndShortTermInvestments"),
                ),
            ],
            metric="cash",
            ticker=ticker,
        )
        total_debt, debt_source = self._total_debt(
            balance_report,
            ticker=ticker,
            raw_index=balance_report_selection.raw_index,
        )
        revenue_ltm = self._sum_reports(
            income_reports,
            field="totalRevenue",
            ticker=ticker,
        )
        ebit_ltm = self._sum_reports(
            income_reports,
            field="ebit",
            ticker=ticker,
        )
        ebitda_ltm = self._sum_reports(
            income_reports,
            field="ebitda",
            ticker=ticker,
        )
        net_income_ltm = self._sum_reports(
            income_reports,
            field="netIncome",
            ticker=ticker,
        )
        quote_as_of = self._required_quote_date(quote, ticker=ticker)
        balance_as_of = self._report_date(
            balance_report,
            provider_function="BALANCE_SHEET",
            ticker=ticker,
        )
        income_as_of = self._report_date(
            income_reports[0].report,
            provider_function="INCOME_STATEMENT",
            ticker=ticker,
        )
        shares_as_of = (
            balance_as_of
            if ".balance_sheet." in shares_source
            else self._overview_date(overview, ticker=ticker)
        )
        sources = {
            "share_price": f"alpha_vantage.global_quote.{ticker}.05. price",
            "shares_outstanding": shares_source,
            "cash": cash_source,
            "total_debt": debt_source,
            "revenue_ltm": self._income_statement_source(
                reports=income_reports,
                ticker=ticker,
                field="totalRevenue",
            ),
            "ebit_ltm": self._income_statement_source(
                reports=income_reports,
                ticker=ticker,
                field="ebit",
            ),
            "ebitda_ltm": self._income_statement_source(
                reports=income_reports,
                ticker=ticker,
                field="ebitda",
            ),
            "net_income_ltm": self._income_statement_source(
                reports=income_reports,
                ticker=ticker,
                field="netIncome",
            ),
        }
        currency_source = (
            f"alpha_vantage.overview.{ticker}.Currency={source_currency}"
        )
        for field in (
            "share_price",
            "cash",
            "total_debt",
            "revenue_ltm",
            "ebit_ltm",
            "ebitda_ltm",
            "net_income_ltm",
        ):
            sources[field] = f"{sources[field]}; {currency_source}"
        source_as_of = {
            "share_price": quote_as_of,
            "shares_outstanding": shares_as_of,
            "cash": balance_as_of,
            "total_debt": balance_as_of,
            "revenue_ltm": income_as_of,
            "ebit_ltm": income_as_of,
            "ebitda_ltm": income_as_of,
            "net_income_ltm": income_as_of,
        }
        raw_evidence: dict[str, object] = {
            "global_quote": quote_payload,
            "overview": overview,
            "income_statement": income,
            "balance_sheet": balance_sheet,
        }
        if source_currency != requested_currency:
            rate, fx_payload, fx_source = self._exchange_rate(
                from_currency=source_currency,
                to_currency=requested_currency,
                cache=fx_cache,
            )
            share_price *= rate
            cash *= rate
            total_debt *= rate
            revenue_ltm *= rate
            ebit_ltm *= rate
            ebitda_ltm *= rate
            net_income_ltm *= rate
            for field in (
                "share_price",
                "cash",
                "total_debt",
                "revenue_ltm",
                "ebit_ltm",
                "ebitda_ltm",
                "net_income_ltm",
            ):
                sources[field] = f"{sources[field]} * {fx_source}"
            raw_evidence["currency_exchange_rate"] = fx_payload

        company = CompanyCompsInput(
            ticker=ticker,
            company_name=self._optional_text(overview.get("Name")),
            currency=requested_currency,
            share_price=share_price,
            shares_outstanding=shares_outstanding,
            cash=cash,
            total_debt=total_debt,
            revenue_ltm=revenue_ltm,
            ebit_ltm=ebit_ltm,
            ebitda_ltm=ebitda_ltm,
            net_income_ltm=net_income_ltm,
            as_of=quote_as_of,
            sources=sources,
            source_as_of=source_as_of,
        )
        return company, raw_evidence

    def _exchange_rate(
        self,
        *,
        from_currency: str,
        to_currency: str,
        cache: dict[tuple[str, str], tuple[float, dict[str, Any], str]],
    ) -> tuple[float, dict[str, Any], str]:
        pair = (from_currency, to_currency)
        if pair in cache:
            return cache[pair]

        payload = self._fetch_json(
            function="CURRENCY_EXCHANGE_RATE",
            from_currency=from_currency,
            to_currency=to_currency,
        )
        evidence = self._required_object(
            payload,
            "Realtime Currency Exchange Rate",
            provider_function="CURRENCY_EXCHANGE_RATE",
            ticker=f"{from_currency}/{to_currency}",
        )
        actual_from = self._required_text(
            evidence.get("1. From_Currency Code"),
            field="CURRENCY_EXCHANGE_RATE.1. From_Currency Code",
            ticker=from_currency,
        ).upper()
        actual_to = self._required_text(
            evidence.get("3. To_Currency Code"),
            field="CURRENCY_EXCHANGE_RATE.3. To_Currency Code",
            ticker=to_currency,
        ).upper()
        if (actual_from, actual_to) != pair:
            raise CompsRunExecutionError(
                "Alpha Vantage CURRENCY_EXCHANGE_RATE returned evidence for "
                f"{actual_from}/{actual_to}, expected {from_currency}/{to_currency}."
            )
        rate = self._required_number(
            evidence.get("5. Exchange Rate"),
            field="CURRENCY_EXCHANGE_RATE.5. Exchange Rate",
            ticker=f"{from_currency}/{to_currency}",
        )
        if rate <= 0:
            raise CompsRunExecutionError(
                f"Alpha Vantage returned a non-positive FX rate for "
                f"{from_currency}/{to_currency}."
            )
        refreshed_at = self._required_text(
            evidence.get("6. Last Refreshed"),
            field="CURRENCY_EXCHANGE_RATE.6. Last Refreshed",
            ticker=f"{from_currency}/{to_currency}",
        )
        timezone = self._required_text(
            evidence.get("7. Time Zone"),
            field="CURRENCY_EXCHANGE_RATE.7. Time Zone",
            ticker=f"{from_currency}/{to_currency}",
        )
        source = (
            "alpha_vantage.currency_exchange_rate."
            f"{from_currency}_{to_currency}.5. Exchange Rate"
            f"@{refreshed_at} {timezone}"
        )
        cache[pair] = (rate, payload, source)
        return cache[pair]

    def _fetch_json(
        self,
        *,
        function: str,
        symbol: str | None = None,
        from_currency: str | None = None,
        to_currency: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "function": function,
            "apikey": self._api_key(),
        }
        subject: str
        if symbol is not None:
            params["symbol"] = symbol
            subject = symbol
        elif from_currency is not None and to_currency is not None:
            params["from_currency"] = from_currency
            params["to_currency"] = to_currency
            subject = f"{from_currency}/{to_currency}"
        else:
            raise AssertionError("Alpha Vantage request parameters are incomplete.")
        if function == "GLOBAL_QUOTE":
            entitlement = self._environ.get(
                ALPHA_VANTAGE_QUOTE_ENTITLEMENT_VAR,
                "",
            ).strip()
            if entitlement:
                params["entitlement"] = entitlement

        self._request_limiter.wait_for_slot(
            self._float_setting(
                ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS_VAR,
                DEFAULT_ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS,
            )
        )
        client_options: dict[str, object] = {
            "timeout": self._float_setting(
                ALPHA_VANTAGE_TIMEOUT_SECONDS_VAR,
                DEFAULT_ALPHA_VANTAGE_TIMEOUT_SECONDS,
            )
        }
        if self._transport is not None:
            client_options["transport"] = self._transport
        try:
            with httpx.Client(**client_options) as client:
                response = client.get(
                    self._environ.get(
                        ALPHA_VANTAGE_BASE_URL_VAR,
                        DEFAULT_ALPHA_VANTAGE_BASE_URL,
                    ),
                    params=params,
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CompsRunExecutionError(
                f"Alpha Vantage {function} request failed for {subject}."
            ) from exc
        if not isinstance(payload, dict):
            raise CompsRunExecutionError(
                f"Alpha Vantage {function} returned a non-object payload for "
                f"{subject}."
            )
        for key in ("Error Message", "Note", "Information"):
            if payload.get(key):
                raise CompsRunExecutionError(
                    f"Alpha Vantage {function} failed for {subject}: "
                    f"{payload[key]}"
                )
        return payload

    def _api_key(self) -> str:
        api_key = self._environ.get(ALPHA_VANTAGE_API_KEY_VAR, "").strip()
        if not api_key:
            raise CompanyDataUnavailable(
                f"Missing required configuration: {ALPHA_VANTAGE_API_KEY_VAR}."
            )
        return api_key

    def _float_setting(self, name: str, default: float) -> float:
        try:
            return seconds_setting(
                self._environ,
                name=name,
                default=default,
            )
        except InvalidProviderConfiguration as exc:
            raise CompanyDataUnavailable(str(exc)) from exc

    def _latest_reports(
        self,
        payload: dict[str, Any],
        *,
        ticker: str,
        provider_function: str,
        required_count: int,
    ) -> list[_SelectedQuarterlyReport]:
        reports = payload.get("quarterlyReports")
        if not isinstance(reports, list):
            raise CompsRunExecutionError(
                f"Alpha Vantage {provider_function} is missing quarterlyReports "
                f"for {ticker}."
            )
        dated_reports: list[_SelectedQuarterlyReport] = []
        for raw_index, report in enumerate(reports):
            if not isinstance(report, dict):
                continue
            fiscal_date = self._parse_date(
                report.get("fiscalDateEnding"),
                field=f"{provider_function}.fiscalDateEnding",
                ticker=ticker,
            )
            dated_reports.append(
                _SelectedQuarterlyReport(
                    raw_index=raw_index,
                    fiscal_date=fiscal_date,
                    report=report,
                )
            )
        dated_reports.sort(
            key=lambda selection: selection.fiscal_date,
            reverse=True,
        )
        if len(dated_reports) < required_count:
            raise CompsRunExecutionError(
                f"Alpha Vantage {provider_function} requires at least "
                f"{required_count} quarterly reports for {ticker}."
            )
        return dated_reports[:required_count]

    def _sum_reports(
        self,
        reports: list[_SelectedQuarterlyReport],
        *,
        field: str,
        ticker: str,
    ) -> float:
        return sum(
            self._required_number(
                selection.report.get(field),
                field=f"INCOME_STATEMENT.quarterlyReports.{field}",
                ticker=ticker,
            )
            for selection in reports
        )

    def _income_statement_source(
        self,
        *,
        reports: list[_SelectedQuarterlyReport],
        ticker: str,
        field: str,
    ) -> str:
        return " + ".join(
            f"alpha_vantage.income_statement.{ticker}."
            f"quarterlyReports[{selection.raw_index}].{field}"
            for selection in reports
        )

    def _total_debt(
        self,
        report: dict[str, Any],
        *,
        ticker: str,
        raw_index: int,
    ) -> tuple[float, str]:
        source_prefix = (
            f"alpha_vantage.balance_sheet.{ticker}."
            f"quarterlyReports[{raw_index}]."
        )
        direct_debt, direct_source = self._first_number(
            [
                (
                    f"{source_prefix}shortLongTermDebtTotal",
                    report.get("shortLongTermDebtTotal"),
                ),
                (
                    f"{source_prefix}debtLongtermAndShorttermCombinedAmount",
                    report.get("debtLongtermAndShorttermCombinedAmount"),
                ),
            ],
            metric="total debt",
            ticker=ticker,
            required=False,
        )
        if direct_debt is not None and direct_source is not None:
            return direct_debt, direct_source

        current_debt, current_source = self._first_number(
            [
                (f"{source_prefix}currentDebt", report.get("currentDebt")),
                (f"{source_prefix}shortTermDebt", report.get("shortTermDebt")),
                (
                    f"{source_prefix}currentLongTermDebt",
                    report.get("currentLongTermDebt"),
                ),
            ],
            metric="current debt",
            ticker=ticker,
            required=False,
        )
        long_term_debt, long_term_source = self._first_number(
            [
                (
                    f"{source_prefix}longTermDebtNoncurrent",
                    report.get("longTermDebtNoncurrent"),
                ),
                (f"{source_prefix}longTermDebt", report.get("longTermDebt")),
            ],
            metric="long-term debt",
            ticker=ticker,
            required=False,
        )
        if (
            current_debt is None
            or current_source is None
            or long_term_debt is None
            or long_term_source is None
        ):
            raise CompsRunExecutionError(
                f"Missing Alpha Vantage evidence for {ticker} total debt."
            )
        return current_debt + long_term_debt, (
            f"{current_source} + {long_term_source}"
        )

    def _first_number(
        self,
        candidates: list[tuple[str, object]],
        *,
        metric: str,
        ticker: str,
        required: bool = True,
    ) -> tuple[float | None, str | None]:
        for source, value in candidates:
            if self._is_missing_number(value):
                continue
            return (
                self._required_number(value, field=source, ticker=ticker),
                source,
            )
        if required:
            raise CompsRunExecutionError(
                f"Missing Alpha Vantage evidence for {ticker} {metric}."
            )
        return None, None

    def _require_report_currency(
        self,
        *,
        reports: list[dict[str, Any]],
        expected: str,
        ticker: str,
    ) -> None:
        currencies = {
            self._required_text(
                report.get("reportedCurrency"),
                field="quarterlyReports.reportedCurrency",
                ticker=ticker,
            ).upper()
            for report in reports
        }
        if currencies != {expected}:
            raise CompsRunExecutionError(
                f"Alpha Vantage currency evidence is inconsistent for {ticker}: "
                f"expected {expected}, received {', '.join(sorted(currencies))}."
            )

    def _required_object(
        self,
        payload: dict[str, Any],
        key: str,
        *,
        provider_function: str,
        ticker: str,
    ) -> dict[str, Any]:
        value = payload.get(key)
        if not isinstance(value, dict) or not value:
            raise CompsRunExecutionError(
                f"Alpha Vantage {provider_function} returned no evidence for "
                f"{ticker}."
            )
        return value

    def _require_symbol(
        self,
        *,
        actual: object,
        expected: str,
        provider_function: str,
    ) -> None:
        if str(actual or "").upper() != expected:
            raise CompsRunExecutionError(
                f"Alpha Vantage {provider_function} returned evidence for a "
                f"different Ticker than {expected}."
            )

    def _required_number(
        self,
        value: object,
        *,
        field: str,
        ticker: str,
    ) -> float:
        if self._is_missing_number(value):
            raise CompsRunExecutionError(
                f"Missing Alpha Vantage evidence for {ticker} at {field}."
            )
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise CompsRunExecutionError(
                f"Invalid Alpha Vantage numeric evidence for {ticker} at {field}."
            ) from exc
        if not math.isfinite(parsed):
            raise CompsRunExecutionError(
                f"Invalid Alpha Vantage numeric evidence for {ticker} at {field}."
            )
        return parsed

    def _is_missing_number(self, value: object) -> bool:
        return value is None or str(value).strip().lower() in {
            "",
            "none",
            "null",
            "nan",
            "-",
        }

    def _required_text(self, value: object, *, field: str, ticker: str) -> str:
        text_value = str(value or "").strip()
        if not text_value:
            raise CompsRunExecutionError(
                f"Missing Alpha Vantage evidence for {ticker} at {field}."
            )
        return text_value

    def _optional_text(self, value: object) -> str | None:
        text_value = str(value or "").strip()
        return text_value or None

    def _required_quote_date(
        self,
        quote: dict[str, Any],
        *,
        ticker: str,
    ) -> datetime:
        value = self._parse_date(
            quote.get("07. latest trading day"),
            field="GLOBAL_QUOTE.07. latest trading day",
            ticker=ticker,
        )
        return datetime.combine(value, time.min, tzinfo=UTC)

    def _overview_date(
        self,
        overview: dict[str, Any],
        *,
        ticker: str,
    ) -> datetime:
        value = self._parse_date(
            overview.get("LatestQuarter"),
            field="OVERVIEW.LatestQuarter",
            ticker=ticker,
        )
        return datetime.combine(value, time.min, tzinfo=UTC)

    def _report_date(
        self,
        report: dict[str, Any],
        *,
        provider_function: str,
        ticker: str,
    ) -> datetime:
        value = self._parse_date(
            report.get("fiscalDateEnding"),
            field=f"{provider_function}.fiscalDateEnding",
            ticker=ticker,
        )
        return datetime.combine(value, time.min, tzinfo=UTC)

    def _parse_date(self, value: object, *, field: str, ticker: str) -> date:
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CompsRunExecutionError(
                f"Missing or invalid Alpha Vantage date evidence for {ticker} "
                f"at {field}."
            ) from exc
