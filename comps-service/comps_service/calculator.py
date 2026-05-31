from __future__ import annotations

from statistics import median
from uuid import UUID

from comps_service.fundamentals import CompanyFundamentals
from talk_to_your_stock_shared import CompsRow, CompsTable, MinMedianMax, TraceFormula, TraceInput, TraceResponse
from talk_to_your_stock_shared.schemas import CompsSummary
from talk_to_your_stock_shared.time import utc_now


class CompsCalculationError(RuntimeError):
    pass


class CompsCalculator:
    def generate(
        self,
        *,
        run_id: UUID,
        target_ticker: str,
        companies: list[CompanyFundamentals],
        currency: str,
    ) -> tuple[CompsTable, TraceResponse]:
        if not companies:
            raise CompsCalculationError("At least one company is required to generate comps.")
        as_of = max((company.as_of for company in companies), default=utc_now())
        target = target_ticker.upper()
        rows: list[CompsRow] = []
        formulas: list[TraceFormula] = []

        for company in companies:
            market_cap = round(company.share_price * company.shares_outstanding, 2)
            net_debt = round(company.total_debt - company.cash, 2)
            enterprise_value = round(market_cap + net_debt, 2)
            ev_to_revenue = self._safe_ratio(enterprise_value, company.revenue_ltm)
            ev_to_ebit = self._safe_ratio(enterprise_value, company.ebit_ltm)
            ev_to_ebitda = self._safe_ratio(enterprise_value, company.ebitda_ltm)
            pe = self._safe_ratio(market_cap, company.net_income_ltm)

            rows.append(
                CompsRow(
                    ticker=company.symbol,
                    company_name=company.company_name,
                    is_target=company.symbol == target,
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
            formulas.extend(self._trace_formulas(company, market_cap, net_debt, enterprise_value, ev_to_revenue, ev_to_ebit, ev_to_ebitda, pe))

        table = CompsTable(
            run_id=run_id,
            target_ticker=target,
            currency=currency,
            as_of=as_of,
            rows=rows,
            summary=CompsSummary(
                stats={
                    "ev_to_revenue": self._stats([row.ev_to_revenue for row in rows]),
                    "ev_to_ebit": self._stats([row.ev_to_ebit for row in rows]),
                    "ev_to_ebitda": self._stats([row.ev_to_ebitda for row in rows]),
                    "pe": self._stats([row.pe for row in rows]),
                }
            ),
        )
        return table, TraceResponse(run_id=run_id, formulas=formulas)

    def _trace_formulas(
        self,
        company: CompanyFundamentals,
        market_cap: float,
        net_debt: float,
        enterprise_value: float,
        ev_to_revenue: float | None,
        ev_to_ebit: float | None,
        ev_to_ebitda: float | None,
        pe: float | None,
    ) -> list[TraceFormula]:
        as_of = company.as_of
        return [
            TraceFormula(
                ticker=company.symbol,
                output_field="equity_value",
                expression="share_price x shares_outstanding",
                output_value=market_cap,
                inputs=[
                    TraceInput(field="share_price", value=company.share_price, source=company.sources["share_price"], as_of=as_of),
                    TraceInput(field="shares_outstanding", value=company.shares_outstanding, source=company.sources["shares_outstanding"], as_of=as_of),
                ],
            ),
            TraceFormula(
                ticker=company.symbol,
                output_field="net_debt",
                expression="total_debt - cash",
                output_value=net_debt,
                inputs=[
                    TraceInput(field="total_debt", value=company.total_debt, source=company.sources["total_debt"], as_of=as_of),
                    TraceInput(field="cash", value=company.cash, source=company.sources["cash"], as_of=as_of),
                ],
            ),
            TraceFormula(
                ticker=company.symbol,
                output_field="enterprise_value",
                expression="equity_value + total_debt - cash",
                output_value=enterprise_value,
                inputs=[
                    TraceInput(field="equity_value", value=market_cap, source="calculated.equity_value", as_of=as_of),
                    TraceInput(field="total_debt", value=company.total_debt, source=company.sources["total_debt"], as_of=as_of),
                    TraceInput(field="cash", value=company.cash, source=company.sources["cash"], as_of=as_of),
                ],
            ),
            TraceFormula(
                ticker=company.symbol,
                output_field="ev_to_revenue",
                expression="enterprise_value / revenue_ltm",
                output_value=ev_to_revenue,
                inputs=[
                    TraceInput(field="enterprise_value", value=enterprise_value, source="calculated.enterprise_value", as_of=as_of),
                    TraceInput(field="revenue_ltm", value=company.revenue_ltm, source=company.sources["revenue_ltm"], as_of=as_of),
                ],
            ),
            TraceFormula(
                ticker=company.symbol,
                output_field="ev_to_ebit",
                expression="enterprise_value / ebit_ltm",
                output_value=ev_to_ebit,
                inputs=[
                    TraceInput(field="enterprise_value", value=enterprise_value, source="calculated.enterprise_value", as_of=as_of),
                    TraceInput(field="ebit_ltm", value=company.ebit_ltm, source=company.sources["ebit_ltm"], as_of=as_of),
                ],
            ),
            TraceFormula(
                ticker=company.symbol,
                output_field="ev_to_ebitda",
                expression="enterprise_value / ebitda_ltm",
                output_value=ev_to_ebitda,
                inputs=[
                    TraceInput(field="enterprise_value", value=enterprise_value, source="calculated.enterprise_value", as_of=as_of),
                    TraceInput(field="ebitda_ltm", value=company.ebitda_ltm, source=company.sources["ebitda_ltm"], as_of=as_of),
                ],
            ),
            TraceFormula(
                ticker=company.symbol,
                output_field="pe",
                expression="equity_value / net_income_ltm",
                output_value=pe,
                inputs=[
                    TraceInput(field="equity_value", value=market_cap, source="calculated.equity_value", as_of=as_of),
                    TraceInput(field="net_income_ltm", value=company.net_income_ltm, source=company.sources["net_income_ltm"], as_of=as_of),
                ],
            ),
        ]

    def _safe_ratio(self, numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator in (None, 0):
            return None
        return round(numerator / denominator, 2)

    def _stats(self, values: list[float | None]) -> MinMedianMax:
        present = sorted(value for value in values if value is not None)
        if not present:
            return MinMedianMax(min=None, median=None, max=None)
        return MinMedianMax(min=present[0], median=round(float(median(present)), 2), max=present[-1])
