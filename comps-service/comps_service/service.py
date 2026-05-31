from __future__ import annotations

from comps_service.calculator import CompsCalculator
from comps_service.fundamentals import FundamentalDataService
from comps_service.repository import CompsRepository
from talk_to_your_stock_shared import GenerateCompsToolRequest, GenerateCompsToolResponse, Run, RunStatus, new_id
from talk_to_your_stock_shared.time import utc_now


class CompsService:
    def __init__(
        self,
        repository: CompsRepository | None = None,
        fundamentals: FundamentalDataService | None = None,
        calculator: CompsCalculator | None = None,
    ) -> None:
        self.repository = repository or CompsRepository()
        self.fundamentals = fundamentals or FundamentalDataService()
        self.calculator = calculator or CompsCalculator()

    def generate_comps_table(self, request: GenerateCompsToolRequest) -> GenerateCompsToolResponse:
        now = utc_now()
        target_ticker = request.target_ticker.upper()
        peer_tickers = [ticker.upper() for ticker in request.peer_tickers]
        run = Run(
            id=new_id(),
            thread_id=request.thread_id,
            trigger_message_id=request.trigger_message_id,
            status=RunStatus.SUCCEEDED,
            target_ticker=target_ticker,
            peer_tickers=peer_tickers,
            currency=request.currency.upper(),
            as_of=now,
            created_at=now,
            started_at=now,
            completed_at=now,
        )
        companies = [self.fundamentals.get_company_fundamentals(ticker) for ticker in [target_ticker, *peer_tickers]]
        table, trace = self.calculator.generate(
            run_id=run.id,
            target_ticker=target_ticker,
            companies=companies,
            currency=run.currency,
        )
        run.as_of = table.as_of
        self.repository.save_run_result(run=run, table=table, trace=trace)
        return GenerateCompsToolResponse(run=run, table=table, trace=trace)
