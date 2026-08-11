from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn, Protocol
from uuid import UUID, uuid4

from talk_to_your_stock_shared import (
    ComparisonTakeaway,
    GenerateCompsDraftResponse,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
    PaginationMeta,
    Run,
    RunStatus,
    RunTableDraftResponse,
    RunTableResponse,
    TraceResponse,
)
from talk_to_your_stock_shared.time import utc_now

from .artifacts import SourceSnapshot
from .calculator import CompanyCompsInput, CompsCalculationError, CompsCalculator


class CompanyDataUnavailable(RuntimeError):
    pass


class CompsRunExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details


class DuplicateToolInvocation(RuntimeError):
    pass


class CalculatedRunNotFound(RuntimeError):
    pass


class RecoveredFailedCompsRun(RuntimeError):
    def __init__(self, run: Run) -> None:
        super().__init__(run.error_message or "Comps Run failed.")
        self.run = run


@dataclass(frozen=True)
class LoadedCompanyData:
    companies: list[CompanyCompsInput]
    raw_provider_evidence: dict[str, object]


class CompanyDataLoadFailure(CompsRunExecutionError):
    def __init__(
        self,
        cause: CompanyDataUnavailable | CompsRunExecutionError,
        *,
        partial_data: LoadedCompanyData,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.partial_data = partial_data


class FailedCompsRun(RuntimeError):
    def __init__(
        self,
        *,
        run_id: UUID,
        cause: (
            CompanyDataUnavailable
            | CompsRunExecutionError
            | CompsCalculationError
        ),
    ) -> None:
        super().__init__(str(cause))
        self.run_id = run_id
        self.cause = cause


class CompanyDataSource(Protocol):
    def load(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> LoadedCompanyData: ...


class CompsRunRepository(Protocol):
    def save_calculated_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        table: RunTableDraftResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> None: ...

    def save_failed_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        source_snapshot: SourceSnapshot,
    ) -> None: ...

    def get_run(self, run_id: UUID) -> Run | None: ...

    def list_runs(
        self,
        *,
        thread_id: UUID,
        status: RunStatus | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Run], PaginationMeta]: ...

    def get_table(self, run_id: UUID) -> RunTableResponse | None: ...

    def get_draft_table(self, run_id: UUID) -> RunTableDraftResponse | None: ...

    def get_trace(self, run_id: UUID) -> TraceResponse | None: ...

    def get_source_snapshot(self, run_id: UUID) -> SourceSnapshot | None: ...

    def finalize_succeeded_run(
        self,
        *,
        run: Run,
        table: RunTableResponse,
    ) -> None: ...

    def finalize_failed_run(self, *, run: Run) -> None: ...

    def get_calculated_run_by_invocation(
        self,
        invocation_id: UUID,
    ) -> GenerateCompsDraftResponse | Run | None: ...


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

    def generate(self, request: GenerateCompsToolRequest) -> GenerateCompsDraftResponse:
        existing = self.resume(request)
        if existing is not None:
            return existing

        target_ticker = request.target_ticker.upper()
        peer_tickers = [ticker.upper() for ticker in request.peer_tickers]
        requested_tickers = [target_ticker, *peer_tickers]
        run_id = uuid4()
        started_at = utc_now()
        loaded = LoadedCompanyData(companies=[], raw_provider_evidence={})
        try:
            loaded = self._company_data_source.load(
                tickers=requested_tickers,
                currency=request.currency.upper(),
            )
            companies = self._order_requested_companies(
                requested_tickers=requested_tickers,
                companies=loaded.companies,
            )
            table, trace, warnings = self._calculator.generate(
                run_id=run_id,
                target_ticker=target_ticker,
                companies=companies,
                currency=request.currency.upper(),
            )
        except CompanyDataLoadFailure as exc:
            self._save_failed_run(
                request=request,
                run_id=run_id,
                target_ticker=target_ticker,
                peer_tickers=peer_tickers,
                started_at=started_at,
                loaded=exc.partial_data,
                cause=exc.cause,
            )
        except (
            CompanyDataUnavailable,
            CompsRunExecutionError,
            CompsCalculationError,
        ) as exc:
            self._save_failed_run(
                request=request,
                run_id=run_id,
                target_ticker=target_ticker,
                peer_tickers=peer_tickers,
                started_at=started_at,
                loaded=loaded,
                cause=exc,
            )

        run = Run(
            id=run_id,
            thread_id=request.thread_id,
            trigger_message_id=request.trigger_message_id,
            status=RunStatus.RUNNING,
            target_ticker=target_ticker,
            peer_tickers=peer_tickers,
            currency=request.currency.upper(),
            as_of=table.as_of,
            warnings=warnings,
            created_at=started_at,
            started_at=started_at,
        )
        source_snapshot = SourceSnapshot(
            run_id=run_id,
            raw_provider_evidence=loaded.raw_provider_evidence,
            normalized_inputs=companies,
            created_at=utc_now(),
        )
        try:
            self._repository.save_calculated_run(
                invocation_id=request.invocation_id,
                run=run,
                table=table,
                trace=trace,
                source_snapshot=source_snapshot,
            )
        except DuplicateToolInvocation:
            existing = self.resume(request)
            if existing is not None:
                return existing
            raise
        return GenerateCompsDraftResponse(
            run=run,
            table=table,
            trace=trace,
            warnings=run.warnings,
        )

    def resume(
        self,
        request: GenerateCompsToolRequest,
    ) -> GenerateCompsDraftResponse | None:
        existing = self._repository.get_calculated_run_by_invocation(
            request.invocation_id
        )
        if existing is None:
            return None
        existing_run = (
            existing.run
            if isinstance(existing, GenerateCompsDraftResponse)
            else existing
        )
        if (
            existing_run.thread_id != request.thread_id
            or existing_run.trigger_message_id != request.trigger_message_id
            or existing_run.target_ticker != request.target_ticker.upper()
            or existing_run.peer_tickers
            != [ticker.upper() for ticker in request.peer_tickers]
            or existing_run.currency != request.currency.upper()
        ):
            raise DuplicateToolInvocation(
                "Tool invocation has already produced a different Run."
            )
        if isinstance(existing, Run):
            if existing.status == RunStatus.FAILED:
                raise RecoveredFailedCompsRun(existing)
            raise DuplicateToolInvocation(
                "Tool invocation has already produced a terminal Run."
            )
        return existing

    def finalize(
        self,
        *,
        run_id: UUID,
        comparison_takeaway: ComparisonTakeaway,
    ) -> GenerateCompsToolResponse:
        run = self._repository.get_run(run_id)
        trace = self._repository.get_trace(run_id)
        if run is not None and run.status == RunStatus.SUCCEEDED:
            succeeded_table = self._repository.get_table(run_id)
            if (
                succeeded_table is not None
                and trace is not None
                and succeeded_table.comparison_takeaway == comparison_takeaway
            ):
                return GenerateCompsToolResponse(
                    run=run,
                    table=succeeded_table,
                    trace=trace,
                    warnings=run.warnings,
                )

        table = self._repository.get_draft_table(run_id)
        if (
            run is None
            or table is None
            or trace is None
            or run.status != RunStatus.RUNNING
        ):
            raise CalculatedRunNotFound("Calculated Comps Run not found.")

        completed_at = utc_now()
        succeeded_run = run.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "completed_at": completed_at,
            }
        )
        succeeded_table = RunTableResponse(
            **table.model_dump(),
            comparison_takeaway=comparison_takeaway,
        )
        self._repository.finalize_succeeded_run(
            run=succeeded_run,
            table=succeeded_table,
        )
        return GenerateCompsToolResponse(
            run=succeeded_run,
            table=succeeded_table,
            trace=trace,
            warnings=succeeded_run.warnings,
        )

    def fail(self, *, run_id: UUID, error_message: str) -> Run:
        run = self._repository.get_run(run_id)
        if run is None:
            raise CalculatedRunNotFound("Calculated Comps Run not found.")
        if run.status != RunStatus.RUNNING:
            return run

        failed_run = run.model_copy(
            update={
                "status": RunStatus.FAILED,
                "error_message": error_message,
                "completed_at": utc_now(),
            }
        )
        self._repository.finalize_failed_run(run=failed_run)
        return failed_run

    def _save_failed_run(
        self,
        *,
        request: GenerateCompsToolRequest,
        run_id: UUID,
        target_ticker: str,
        peer_tickers: list[str],
        started_at: datetime,
        loaded: LoadedCompanyData,
        cause: (
            CompanyDataUnavailable
            | CompsRunExecutionError
            | CompsCalculationError
        ),
    ) -> NoReturn:
        completed_at = utc_now()
        run = Run(
            id=run_id,
            thread_id=request.thread_id,
            trigger_message_id=request.trigger_message_id,
            status=RunStatus.FAILED,
            target_ticker=target_ticker,
            peer_tickers=peer_tickers,
            currency=request.currency.upper(),
            as_of=None,
            error_message=str(cause),
            created_at=started_at,
            started_at=started_at,
            completed_at=completed_at,
        )
        source_snapshot = SourceSnapshot(
            run_id=run_id,
            raw_provider_evidence=loaded.raw_provider_evidence,
            normalized_inputs=loaded.companies,
            created_at=completed_at,
        )
        self._repository.save_failed_run(
            invocation_id=request.invocation_id,
            run=run,
            source_snapshot=source_snapshot,
        )
        raise FailedCompsRun(run_id=run_id, cause=cause) from cause

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
