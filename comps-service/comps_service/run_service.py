from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from talk_to_your_stock_shared import (
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
    Run,
    RunStatus,
    RunTableResponse,
)
from talk_to_your_stock_shared.time import utc_now

from .calculator import CompanyCompsInput, CompsCalculator


class CompanyDataUnavailable(RuntimeError):
    pass


class CompsRunExecutionError(RuntimeError):
    pass


class DuplicateToolInvocation(RuntimeError):
    def __init__(self, *, invocation_id: UUID, existing_run_id: UUID) -> None:
        super().__init__("Tool invocation has already produced a Run.")
        self.invocation_id = invocation_id
        self.existing_run_id = existing_run_id


class CompanyDataSource(Protocol):
    def load_companies(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> list[CompanyCompsInput]: ...


class CompsRunRepository(Protocol):
    def save_succeeded_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableResponse,
    ) -> None: ...

    def get_run(self, run_id: UUID) -> Run | None: ...

    def get_table(self, run_id: UUID) -> RunTableResponse | None: ...

    def get_run_id_by_invocation_id(self, invocation_id: UUID) -> UUID | None: ...


class UnavailableCompanyDataSource:
    def load_companies(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> list[CompanyCompsInput]:
        del tickers, currency
        raise CompanyDataUnavailable(
            "Real provider and FX company inputs are not implemented yet."
        )


class CompsRunService:
    def __init__(
        self,
        *,
        repository: CompsRunRepository,
        company_data_source: CompanyDataSource,
        calculator: CompsCalculator | None = None,
    ) -> None:
        self._repository = repository
        self._company_data_source = company_data_source
        self._calculator = calculator or CompsCalculator()

    def generate(self, request: GenerateCompsToolRequest) -> GenerateCompsToolResponse:
        existing_run_id = self._repository.get_run_id_by_invocation_id(
            request.invocation_id
        )
        if existing_run_id is not None:
            raise DuplicateToolInvocation(
                invocation_id=request.invocation_id,
                existing_run_id=existing_run_id,
            )

        target_ticker = request.target_ticker.upper()
        peer_tickers = [ticker.upper() for ticker in request.peer_tickers]
        requested_tickers = [target_ticker, *peer_tickers]
        companies = self._order_requested_companies(
            requested_tickers=requested_tickers,
            companies=self._company_data_source.load_companies(
                tickers=requested_tickers,
                currency=request.currency.upper(),
            ),
        )

        run_id = uuid4()
        table, trace = self._calculator.generate(
            run_id=run_id,
            target_ticker=target_ticker,
            companies=companies,
            currency=request.currency.upper(),
        )
        now = utc_now()
        run = Run(
            id=run_id,
            thread_id=request.thread_id,
            trigger_message_id=request.trigger_message_id,
            status=RunStatus.SUCCEEDED,
            target_ticker=target_ticker,
            peer_tickers=peer_tickers,
            currency=request.currency.upper(),
            as_of=table.as_of,
            created_at=now,
            started_at=now,
            completed_at=now,
        )
        self._repository.save_succeeded_run(
            invocation_id=request.invocation_id,
            run=run,
            table=table,
        )
        return GenerateCompsToolResponse(
            run=run,
            table=table,
            trace=trace,
            warnings=run.warnings,
        )

    def _order_requested_companies(
        self,
        *,
        requested_tickers: list[str],
        companies: list[CompanyCompsInput],
    ) -> list[CompanyCompsInput]:
        companies_by_ticker = {
            company.ticker.upper(): company for company in companies
        }
        if (
            len(companies_by_ticker) != len(companies)
            or set(companies_by_ticker) != set(requested_tickers)
        ):
            raise CompsRunExecutionError(
                "Company inputs must contain the target and every requested peer "
                "exactly once."
            )
        return [companies_by_ticker[ticker] for ticker in requested_tickers]
