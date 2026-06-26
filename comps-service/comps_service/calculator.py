from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from uuid import UUID

from talk_to_your_stock_shared import (
    CompsRow,
    MinMedianMax,
    RunTableResponse,
    TraceFormula,
    TraceInput,
    TraceOutputField,
    TraceResponse,
)


class CompsCalculationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompanyCompsInput:
    ticker: str
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
    sources: dict[str, str] = field(default_factory=dict)


class CompsCalculator:
    def generate(
        self,
        *,
        run_id: UUID,
        target_ticker: str,
        companies: list[CompanyCompsInput],
        currency: str,
    ) -> tuple[RunTableResponse, TraceResponse]:
        self._validate_inputs(target_ticker=target_ticker, companies=companies)

        target = target_ticker.upper()
        rows: list[CompsRow] = []
        formulas: list[TraceFormula] = []

        for company in companies:
            market_cap = self._round(company.share_price * company.shares_outstanding)
            net_debt = self._round(company.total_debt - company.cash)
            enterprise_value = self._round(market_cap + net_debt)
            ev_to_revenue = self._safe_ratio(enterprise_value, company.revenue_ltm)
            ev_to_ebit = self._safe_ratio(enterprise_value, company.ebit_ltm)
            ev_to_ebitda = self._safe_ratio(enterprise_value, company.ebitda_ltm)
            pe = self._safe_ratio(market_cap, company.net_income_ltm)

            rows.append(
                CompsRow(
                    ticker=company.ticker.upper(),
                    company_name=company.company_name,
                    is_target=company.ticker.upper() == target,
                    currency=currency or company.currency,
                    share_price=company.share_price,
                    shares_outstanding=company.shares_outstanding,
                    market_cap=market_cap,
                    cash=company.cash,
                    total_debt=company.total_debt,
                    net_debt=net_debt,
                    enterprise_value=enterprise_value,
                    revenue_ltm=company.revenue_ltm,
                    ebit_ltm=company.ebit_ltm,
                    ebitda_ltm=company.ebitda_ltm,
                    net_income_ltm=company.net_income_ltm,
                    ev_to_revenue=ev_to_revenue,
                    ev_to_ebit=ev_to_ebit,
                    ev_to_ebitda=ev_to_ebitda,
                    pe=pe,
                    as_of=company.as_of,
                )
            )
            formulas.extend(
                self._trace_formulas(
                    company=company,
                    market_cap=market_cap,
                    net_debt=net_debt,
                    enterprise_value=enterprise_value,
                    ev_to_revenue=ev_to_revenue,
                    ev_to_ebit=ev_to_ebit,
                    ev_to_ebitda=ev_to_ebitda,
                    pe=pe,
                )
            )

        table = RunTableResponse(
            run_id=run_id,
            target_ticker=target,
            currency=currency,
            as_of=max(company.as_of for company in companies),
            rows=rows,
            summary={
                "stats": {
                    "ev_to_revenue": self._stats([row.ev_to_revenue for row in rows]),
                    "ev_to_ebit": self._stats([row.ev_to_ebit for row in rows]),
                    "ev_to_ebitda": self._stats([row.ev_to_ebitda for row in rows]),
                    "pe": self._stats([row.pe for row in rows]),
                }
            },
        )
        return table, TraceResponse(run_id=run_id, formulas=formulas)

    def _validate_inputs(
        self,
        *,
        target_ticker: str,
        companies: list[CompanyCompsInput],
    ) -> None:
        if not companies:
            raise CompsCalculationError("At least one company is required.")

        seen: set[str] = set()
        for company in companies:
            ticker = company.ticker.upper()
            if ticker in seen:
                raise CompsCalculationError(f"Duplicate ticker input: {ticker}.")
            seen.add(ticker)

        if target_ticker.upper() not in seen:
            raise CompsCalculationError(
                f"Target ticker {target_ticker.upper()} is missing from company inputs."
            )

    def _trace_formulas(
        self,
        *,
        company: CompanyCompsInput,
        market_cap: float,
        net_debt: float,
        enterprise_value: float,
        ev_to_revenue: float | None,
        ev_to_ebit: float | None,
        ev_to_ebitda: float | None,
        pe: float | None,
    ) -> list[TraceFormula]:
        return [
            TraceFormula(
                ticker=company.ticker.upper(),
                output_field=TraceOutputField.EQUITY_VALUE,
                expression="share_price * shares_outstanding",
                output_value=market_cap,
                inputs=[
                    self._trace_input(company, "share_price"),
                    self._trace_input(company, "shares_outstanding"),
                ],
            ),
            TraceFormula(
                ticker=company.ticker.upper(),
                output_field=TraceOutputField.NET_DEBT,
                expression="total_debt - cash",
                output_value=net_debt,
                inputs=[
                    self._trace_input(company, "total_debt"),
                    self._trace_input(company, "cash"),
                ],
            ),
            TraceFormula(
                ticker=company.ticker.upper(),
                output_field=TraceOutputField.ENTERPRISE_VALUE,
                expression="equity_value + net_debt",
                output_value=enterprise_value,
                inputs=[
                    TraceInput(
                        field="equity_value",
                        value=market_cap,
                        source="calculated.equity_value",
                        as_of=company.as_of,
                    ),
                    TraceInput(
                        field="net_debt",
                        value=net_debt,
                        source="calculated.net_debt",
                        as_of=company.as_of,
                    ),
                ],
            ),
            self._multiple_trace(
                company=company,
                field=TraceOutputField.EV_TO_REVENUE,
                denominator_field="revenue_ltm",
                output_value=ev_to_revenue,
                enterprise_value=enterprise_value,
            ),
            self._multiple_trace(
                company=company,
                field=TraceOutputField.EV_TO_EBIT,
                denominator_field="ebit_ltm",
                output_value=ev_to_ebit,
                enterprise_value=enterprise_value,
            ),
            self._multiple_trace(
                company=company,
                field=TraceOutputField.EV_TO_EBITDA,
                denominator_field="ebitda_ltm",
                output_value=ev_to_ebitda,
                enterprise_value=enterprise_value,
            ),
            TraceFormula(
                ticker=company.ticker.upper(),
                output_field=TraceOutputField.PE,
                expression="equity_value / net_income_ltm",
                output_value=pe,
                inputs=[
                    TraceInput(
                        field="equity_value",
                        value=market_cap,
                        source="calculated.equity_value",
                        as_of=company.as_of,
                    ),
                    self._trace_input(company, "net_income_ltm"),
                ],
            ),
        ]

    def _multiple_trace(
        self,
        *,
        company: CompanyCompsInput,
        field: TraceOutputField,
        denominator_field: str,
        output_value: float | None,
        enterprise_value: float,
    ) -> TraceFormula:
        return TraceFormula(
            ticker=company.ticker.upper(),
            output_field=field,
            expression=f"enterprise_value / {denominator_field}",
            output_value=output_value,
            inputs=[
                TraceInput(
                    field="enterprise_value",
                    value=enterprise_value,
                    source="calculated.enterprise_value",
                    as_of=company.as_of,
                ),
                self._trace_input(company, denominator_field),
            ],
        )

    def _trace_input(self, company: CompanyCompsInput, field_name: str) -> TraceInput:
        return TraceInput(
            field=field_name,
            value=getattr(company, field_name),
            source=company.sources.get(field_name, f"input.{field_name}"),
            as_of=company.as_of,
        )

    def _safe_ratio(self, numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator in (None, 0):
            return None
        return self._round(numerator / denominator)

    def _stats(self, values: list[float | None]) -> MinMedianMax:
        present = sorted(value for value in values if value is not None)
        if not present:
            return MinMedianMax(min=None, median=None, max=None)
        return MinMedianMax(
            min=present[0],
            median=self._round(float(median(present))),
            max=present[-1],
        )

    def _round(self, value: float) -> float:
        return round(value, 2)
