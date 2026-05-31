from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from comps_service.alpha_vantage import AlphaVantageClient
from comps_service.fundamental_cache import FundamentalCache
from comps_service.settings import settings
from talk_to_your_stock_shared.time import utc_now


class FundamentalDataUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CompanyFundamentals:
    symbol: str
    company_name: str | None
    currency: str
    share_price: float
    shares_outstanding: float
    cash: float
    total_debt: float
    revenue_ltm: float
    ebit_ltm: float
    ebitda_ltm: float
    net_income_ltm: float
    as_of: datetime
    sources: dict[str, str]


class FundamentalDataService:
    def __init__(self, provider: AlphaVantageClient | None = None, cache: FundamentalCache | None = None) -> None:
        self.provider = provider or AlphaVantageClient()
        self.cache = cache or FundamentalCache()

    def get_company_fundamentals(self, symbol: str) -> CompanyFundamentals:
        symbol = symbol.upper()
        quote = self.provider.get_global_quote(symbol)
        overview = self._get_overview(symbol)
        income = self._get_income_statement(symbol)
        balance_sheet = self._get_balance_sheet(symbol)

        income_reports = self._latest_reports(income, report_key="quarterlyReports", required_count=4)
        latest_balance = self._latest_reports(balance_sheet, report_key="quarterlyReports", required_count=1)[0]
        quote_time = self._quote_time(quote)
        currency = str(overview.get("Currency") or latest_balance.get("reportedCurrency") or "USD")

        shares, shares_source = self._first_number(
            [
                ("overview.SharesOutstanding", overview.get("SharesOutstanding")),
                ("balance_sheet.commonStockSharesOutstanding", latest_balance.get("commonStockSharesOutstanding")),
            ],
            metric="shares outstanding",
            symbol=symbol,
        )
        cash, cash_source = self._first_number(
            [
                ("balance_sheet.cashAndCashEquivalentsAtCarryingValue", latest_balance.get("cashAndCashEquivalentsAtCarryingValue")),
                ("balance_sheet.cashAndShortTermInvestments", latest_balance.get("cashAndShortTermInvestments")),
            ],
            metric="cash",
            symbol=symbol,
        )
        debt, debt_source = self._total_debt(latest_balance, symbol=symbol)

        return CompanyFundamentals(
            symbol=symbol,
            company_name=str(overview.get("Name") or symbol),
            currency=currency,
            share_price=self._required_number("global_quote.05. price", quote.get("05. price"), "share price", symbol),
            shares_outstanding=shares,
            cash=cash,
            total_debt=debt,
            revenue_ltm=self._sum_ltm(income_reports, "totalRevenue", "revenue", symbol),
            ebit_ltm=self._sum_ltm(income_reports, "ebit", "EBIT", symbol),
            ebitda_ltm=self._sum_ltm(income_reports, "ebitda", "EBITDA", symbol),
            net_income_ltm=self._sum_ltm(income_reports, "netIncome", "net income", symbol),
            as_of=quote_time,
            sources={
                "share_price": "global_quote.05. price",
                "shares_outstanding": shares_source,
                "cash": cash_source,
                "total_debt": debt_source,
                "revenue_ltm": "income_statement.quarterlyReports[0:4].totalRevenue",
                "ebit_ltm": "income_statement.quarterlyReports[0:4].ebit",
                "ebitda_ltm": "income_statement.quarterlyReports[0:4].ebitda",
                "net_income_ltm": "income_statement.quarterlyReports[0:4].netIncome",
            },
        )

    def ping_cache(self) -> None:
        self.cache.ping()

    def _get_overview(self, symbol: str) -> dict[str, Any]:
        return self.cache.get_or_refresh(
            symbol=symbol,
            statement_type="overview",
            period_type="latest",
            fetch_payload=lambda: self.provider.get_overview(symbol),
            latest_fiscal_date_fn=lambda payload: self._parse_optional_date(payload.get("LatestQuarter")),
            next_expected_refresh_fn=self._next_expected_refresh_at,
        )

    def _get_income_statement(self, symbol: str) -> dict[str, Any]:
        return self.cache.get_or_refresh(
            symbol=symbol,
            statement_type="income_statement",
            period_type="quarterly",
            fetch_payload=lambda: self.provider.get_income_statement(symbol),
            latest_fiscal_date_fn=lambda payload: self._latest_report_date(payload, "quarterlyReports"),
            next_expected_refresh_fn=self._next_expected_refresh_at,
        )

    def _get_balance_sheet(self, symbol: str) -> dict[str, Any]:
        return self.cache.get_or_refresh(
            symbol=symbol,
            statement_type="balance_sheet",
            period_type="quarterly",
            fetch_payload=lambda: self.provider.get_balance_sheet(symbol),
            latest_fiscal_date_fn=lambda payload: self._latest_report_date(payload, "quarterlyReports"),
            next_expected_refresh_fn=self._next_expected_refresh_at,
        )

    def _get_earnings_calendar(self, symbol: str) -> dict[str, Any]:
        return self.cache.get_or_refresh(
            symbol=symbol,
            statement_type="earnings_calendar",
            period_type="latest",
            fetch_payload=lambda: {"Symbol": symbol.upper(), "rows": self.provider.get_earnings_calendar(symbol)},
            latest_fiscal_date_fn=lambda payload: None,
            next_expected_refresh_fn=lambda _payload, _latest: utc_now() + timedelta(days=settings.cache_refresh_backoff_days),
        )

    def _next_expected_refresh_at(self, payload: dict[str, Any], latest_fiscal_date: date | None) -> datetime:
        symbol = str(payload.get("symbol") or payload.get("Symbol") or "")
        next_report_date = self._next_report_date_from_calendar(symbol) if symbol else None
        if next_report_date:
            return self._start_of_day_utc(next_report_date - timedelta(days=settings.cache_refresh_lead_days))
        if latest_fiscal_date is None:
            return utc_now() + timedelta(days=settings.cache_refresh_backoff_days)
        estimated_report_date = latest_fiscal_date + timedelta(days=settings.estimated_quarterly_report_lag_days)
        return self._start_of_day_utc(estimated_report_date - timedelta(days=settings.cache_refresh_lead_days))

    def _next_report_date_from_calendar(self, symbol: str) -> date | None:
        if not symbol:
            return None
        rows = self._get_earnings_calendar(symbol).get("rows", [])
        today = utc_now().date()
        dates = []
        for row in rows:
            if row.get("symbol", "").upper() != symbol.upper():
                continue
            report_date = self._parse_optional_date(row.get("reportDate"))
            if report_date and report_date >= today:
                dates.append(report_date)
        return min(dates) if dates else None

    def _latest_reports(self, payload: dict[str, Any], *, report_key: str, required_count: int) -> list[dict[str, Any]]:
        reports = payload.get(report_key)
        if not isinstance(reports, list):
            raise FundamentalDataUnavailable(f"Alpha Vantage payload is missing {report_key}.")
        sorted_reports = sorted(
            (report for report in reports if isinstance(report, dict)),
            key=lambda report: self._parse_optional_date(report.get("fiscalDateEnding")) or date.min,
            reverse=True,
        )
        if len(sorted_reports) < required_count:
            raise FundamentalDataUnavailable(f"Expected at least {required_count} reports in {report_key}.")
        return sorted_reports[:required_count]

    def _latest_report_date(self, payload: dict[str, Any], report_key: str) -> date | None:
        reports = self._latest_reports(payload, report_key=report_key, required_count=1)
        return self._parse_optional_date(reports[0].get("fiscalDateEnding"))

    def _sum_ltm(self, reports: list[dict[str, Any]], field: str, metric: str, symbol: str) -> float:
        return sum(self._required_number(f"income_statement.{field}", report.get(field), metric, symbol) for report in reports)

    def _total_debt(self, report: dict[str, Any], *, symbol: str) -> tuple[float, str]:
        direct, source = self._first_number(
            [
                ("balance_sheet.shortLongTermDebtTotal", report.get("shortLongTermDebtTotal")),
                ("balance_sheet.debtLongtermAndShorttermCombinedAmount", report.get("debtLongtermAndShorttermCombinedAmount")),
            ],
            metric="total debt",
            symbol=symbol,
            raise_if_missing=False,
        )
        if direct is not None and source is not None:
            return direct, source

        current_debt, current_source = self._first_number(
            [
                ("balance_sheet.currentDebt", report.get("currentDebt")),
                ("balance_sheet.shortTermDebt", report.get("shortTermDebt")),
                ("balance_sheet.currentLongTermDebt", report.get("currentLongTermDebt")),
            ],
            metric="current debt",
            symbol=symbol,
            raise_if_missing=False,
        )
        long_term_debt, long_term_source = self._first_number(
            [
                ("balance_sheet.longTermDebtNoncurrent", report.get("longTermDebtNoncurrent")),
                ("balance_sheet.longTermDebt", report.get("longTermDebt")),
            ],
            metric="long-term debt",
            symbol=symbol,
            raise_if_missing=False,
        )
        if current_debt is None and long_term_debt is None:
            raise FundamentalDataUnavailable(f"Missing total debt for {symbol}.")
        sources = [source for source in (current_source, long_term_source) if source]
        return float(current_debt or 0) + float(long_term_debt or 0), " + ".join(sources)

    def _first_number(
        self,
        candidates: list[tuple[str, Any]],
        *,
        metric: str,
        symbol: str,
        raise_if_missing: bool = True,
    ) -> tuple[float | None, str | None]:
        for source, value in candidates:
            parsed = self._optional_number(value)
            if parsed is not None:
                return parsed, source
        if raise_if_missing:
            raise FundamentalDataUnavailable(f"Missing {metric} for {symbol}.")
        return None, None

    def _required_number(self, source: str, value: Any, metric: str, symbol: str) -> float:
        parsed = self._optional_number(value)
        if parsed is None:
            raise FundamentalDataUnavailable(f"Missing {metric} for {symbol} at {source}.")
        return parsed

    def _optional_number(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {"", "none", "null", "nan"}:
            return None
        return float(value)

    def _quote_time(self, quote: dict[str, Any]) -> datetime:
        latest_day = self._parse_optional_date(quote.get("07. latest trading day"))
        if latest_day is None:
            return utc_now()
        return datetime.combine(latest_day, time.min, tzinfo=timezone.utc)

    def _parse_optional_date(self, value: Any) -> date | None:
        if not value or str(value).lower() in {"none", "null"}:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError as exc:
            raise FundamentalDataUnavailable(f"Invalid provider date value: {value}") from exc

    def _start_of_day_utc(self, value: date) -> datetime:
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
