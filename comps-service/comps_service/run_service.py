from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from threading import Event, Thread
from typing import NoReturn, Protocol
from uuid import UUID, uuid4

from talk_to_your_stock_shared import (
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    GenerateCompsToolRequest,
    GenerateCompsToolResponse,
    PaginationMeta,
    Run,
    RunStatus,
    RunTableResponse,
    TraceResponse,
)
from talk_to_your_stock_shared.time import utc_now

from .artifacts import SourceSnapshot
from .calculator import CompanyCompsInput, CompsCalculationError, CompsCalculator
from .provider_config import positive_seconds_setting


COMPS_RUN_LEASE_SECONDS_VAR = "COMPS_RUN_LEASE_SECONDS"
DEFAULT_COMPS_RUN_LEASE_SECONDS = 6.0


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


class RunCalculationInProgress(RuntimeError):
    def __init__(self, run: Run) -> None:
        super().__init__("Tool invocation calculation is already running.")
        self.run = run


@dataclass(frozen=True)
class RecoveredFailedRun:
    run: Run
    status_code: int
    error: ErrorResponse


class RecoveredFailedCompsRun(RuntimeError):
    def __init__(self, result: RecoveredFailedRun) -> None:
        super().__init__(result.error.error.message)
        self.result = result


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
        status_code: int,
        error: ErrorResponse,
    ) -> None:
        super().__init__(str(cause))
        self.run_id = run_id
        self.cause = cause
        self.status_code = status_code
        self.error = error


class CompanyDataSource(Protocol):
    def load(
        self,
        *,
        tickers: list[str],
        currency: str,
    ) -> LoadedCompanyData: ...


class CompsRunRepository(Protocol):
    def get_succeeded_result(
        self,
        *,
        invocation_id: UUID,
    ) -> GenerateCompsToolResponse | None: ...

    def get_failed_result(
        self,
        *,
        invocation_id: UUID,
    ) -> RecoveredFailedRun | None: ...

    def get_run_by_invocation(self, *, invocation_id: UUID) -> Run | None: ...

    def get_active_run_by_invocation(self, *, invocation_id: UUID) -> Run | None: ...

    def get_validation_evidence(
        self,
        *,
        invocation_id: UUID,
    ) -> dict[str, dict[str, object]] | None: ...

    def reserve_run(
        self,
        *,
        invocation_id: UUID,
        run: Run,
        validation_evidence: dict[str, dict[str, object]],
    ) -> Run: ...

    def claim_run(
        self,
        *,
        run_id: UUID,
        owner_id: UUID,
        lease_seconds: float,
    ) -> Run | None: ...

    def renew_run_lease(
        self,
        *,
        run_id: UUID,
        owner_id: UUID,
        lease_seconds: float,
    ) -> bool: ...

    def complete_succeeded_run(
        self,
        *,
        owner_id: UUID,
        run: Run,
        table: RunTableResponse,
        trace: TraceResponse,
        source_snapshot: SourceSnapshot,
    ) -> bool: ...

    def complete_failed_run(
        self,
        *,
        owner_id: UUID,
        run: Run,
        status_code: int,
        error: ErrorResponse,
        source_snapshot: SourceSnapshot,
    ) -> bool: ...

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

    def get_trace(self, run_id: UUID) -> TraceResponse | None: ...

    def get_source_snapshot(self, run_id: UUID) -> SourceSnapshot | None: ...


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
        self._lease_seconds = positive_seconds_setting(
            os.environ,
            name=COMPS_RUN_LEASE_SECONDS_VAR,
            default=DEFAULT_COMPS_RUN_LEASE_SECONDS,
        )

    def recover_succeeded(
        self,
        request: GenerateCompsToolRequest,
    ) -> GenerateCompsToolResponse | None:
        existing = self._repository.get_succeeded_result(
            invocation_id=request.invocation_id,
        )
        if existing is not None:
            self._require_matching_request(request=request, run=existing.run)
        return existing

    def recover_terminal(
        self,
        request: GenerateCompsToolRequest,
    ) -> GenerateCompsToolResponse | None:
        succeeded = self.recover_succeeded(request)
        if succeeded is not None:
            return succeeded
        failed = self._repository.get_failed_result(
            invocation_id=request.invocation_id,
        )
        if failed is not None:
            self._require_matching_request(request=request, run=failed.run)
            raise RecoveredFailedCompsRun(failed)
        return None

    def requires_external_validation(
        self,
        request: GenerateCompsToolRequest,
    ) -> bool:
        current = self._repository.get_run_by_invocation(
            invocation_id=request.invocation_id,
        )
        if current is None:
            return True
        self._require_matching_request(request=request, run=current)
        if current.status == RunStatus.RUNNING:
            active = self._repository.get_active_run_by_invocation(
                invocation_id=request.invocation_id,
            )
            if active is not None:
                raise RunCalculationInProgress(active)
        return False

    def generate(
        self,
        request: GenerateCompsToolRequest,
        *,
        validation_evidence: dict[str, dict[str, object]],
    ) -> GenerateCompsToolResponse:
        existing = self.recover_terminal(request)
        if existing is not None:
            return existing

        target_ticker = request.target_ticker.upper()
        peer_tickers = [ticker.upper() for ticker in request.peer_tickers]
        requested_tickers = [target_ticker, *peer_tickers]
        run_id = request.invocation_id
        created_at = utc_now()
        reservation = self._repository.reserve_run(
            invocation_id=request.invocation_id,
            validation_evidence=validation_evidence,
            run=Run(
                id=run_id,
                thread_id=request.thread_id,
                trigger_message_id=request.trigger_message_id,
                status=RunStatus.QUEUED,
                target_ticker=target_ticker,
                peer_tickers=peer_tickers,
                currency=request.currency.upper(),
                as_of=None,
                created_at=created_at,
            ),
        )
        authoritative_validation = self._repository.get_validation_evidence(
            invocation_id=request.invocation_id,
        )
        if authoritative_validation is not None:
            validation_evidence.clear()
            validation_evidence.update(authoritative_validation)
        self._require_matching_request(request=request, run=reservation)
        existing = self.recover_terminal(request)
        if existing is not None:
            return existing

        owner_id = uuid4()
        started_at = utc_now()
        claimed_run = self._repository.claim_run(
            run_id=run_id,
            owner_id=owner_id,
            lease_seconds=self._lease_seconds,
        )
        if claimed_run is None:
            existing = self.recover_terminal(request)
            if existing is not None:
                return existing
            current = self._repository.get_run_by_invocation(
                invocation_id=request.invocation_id,
            )
            raise RunCalculationInProgress(current or reservation)

        with self._renew_lease(run_id=run_id, owner_id=owner_id):
            return self._generate_claimed_run(
                request=request,
                reservation=reservation,
                claimed_run=claimed_run,
                owner_id=owner_id,
                started_at=started_at,
                requested_tickers=requested_tickers,
            )

    def _generate_claimed_run(
        self,
        *,
        request: GenerateCompsToolRequest,
        reservation: Run,
        claimed_run: Run,
        owner_id: UUID,
        started_at: datetime,
        requested_tickers: list[str],
    ) -> GenerateCompsToolResponse:
        run_id = request.invocation_id
        target_ticker = request.target_ticker.upper()
        peer_tickers = [ticker.upper() for ticker in request.peer_tickers]
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
                created_at=reservation.created_at,
                started_at=claimed_run.started_at or started_at,
                owner_id=owner_id,
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
                created_at=reservation.created_at,
                started_at=claimed_run.started_at or started_at,
                owner_id=owner_id,
                loaded=loaded,
                cause=exc,
            )

        completed_at = utc_now()
        run = Run(
            id=run_id,
            thread_id=request.thread_id,
            trigger_message_id=request.trigger_message_id,
            status=RunStatus.SUCCEEDED,
            target_ticker=target_ticker,
            peer_tickers=peer_tickers,
            currency=request.currency.upper(),
            as_of=table.as_of,
            warnings=warnings,
            created_at=reservation.created_at,
            started_at=claimed_run.started_at or started_at,
            completed_at=completed_at,
        )
        source_snapshot = SourceSnapshot(
            run_id=run_id,
            raw_provider_evidence=loaded.raw_provider_evidence,
            normalized_inputs=companies,
            created_at=completed_at,
        )
        completed = self._repository.complete_succeeded_run(
            owner_id=owner_id,
            run=run,
            table=table,
            trace=trace,
            source_snapshot=source_snapshot,
        )
        if not completed:
            existing = self._repository.get_succeeded_result(
                invocation_id=request.invocation_id,
            )
            if existing is not None:
                self._require_matching_request(request=request, run=existing.run)
                return existing
            failed = self._repository.get_failed_result(
                invocation_id=request.invocation_id,
            )
            if failed is not None:
                raise RecoveredFailedCompsRun(failed)
            current = self._repository.get_run_by_invocation(
                invocation_id=request.invocation_id,
            )
            raise RunCalculationInProgress(current or claimed_run)
        return GenerateCompsToolResponse(
            run=run,
            table=table,
            trace=trace,
            warnings=run.warnings,
        )

    @staticmethod
    def _require_matching_request(
        *,
        request: GenerateCompsToolRequest,
        run: Run,
    ) -> None:
        if (
            run.id != request.invocation_id
            or run.thread_id != request.thread_id
            or run.trigger_message_id != request.trigger_message_id
            or run.target_ticker != request.target_ticker.upper()
            or run.peer_tickers
            != [ticker.upper() for ticker in request.peer_tickers]
            or run.currency != request.currency.upper()
        ):
            raise DuplicateToolInvocation(
                "Tool invocation is already linked to a different Run request."
            )

    def _save_failed_run(
        self,
        *,
        request: GenerateCompsToolRequest,
        run_id: UUID,
        target_ticker: str,
        peer_tickers: list[str],
        created_at: datetime,
        started_at: datetime,
        owner_id: UUID,
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
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
        )
        source_snapshot = SourceSnapshot(
            run_id=run_id,
            raw_provider_evidence=loaded.raw_provider_evidence,
            normalized_inputs=loaded.companies,
            created_at=completed_at,
        )
        dependency_unavailable = isinstance(cause, CompanyDataUnavailable)
        status_code = 503 if dependency_unavailable else 502
        details = {
            **(getattr(cause, "details", None) or {}),
            "thread_id": str(request.thread_id),
            "trigger_message_id": str(request.trigger_message_id),
        }
        error = ErrorResponse(
            error=ErrorDetail(
                code=(
                    ErrorCode.INTERNAL_ERROR
                    if dependency_unavailable
                    else ErrorCode.UPSTREAM_ERROR
                ),
                message=str(cause),
                details=details,
                run_id=run_id,
            )
        )
        completed = self._repository.complete_failed_run(
            owner_id=owner_id,
            run=run,
            status_code=status_code,
            error=error,
            source_snapshot=source_snapshot,
        )
        if not completed:
            recovered = self._repository.get_failed_result(
                invocation_id=request.invocation_id,
            )
            if recovered is not None:
                raise RecoveredFailedCompsRun(recovered) from cause
            succeeded = self._repository.get_succeeded_result(
                invocation_id=request.invocation_id,
            )
            if succeeded is not None:
                raise DuplicateToolInvocation(
                    "Tool invocation already completed successfully."
                ) from cause
            current = self._repository.get_run_by_invocation(
                invocation_id=request.invocation_id,
            )
            raise RunCalculationInProgress(current or run) from cause
        raise FailedCompsRun(
            run_id=run_id,
            cause=cause,
            status_code=status_code,
            error=error,
        ) from cause

    @contextmanager
    def _renew_lease(self, *, run_id: UUID, owner_id: UUID):
        stopped = Event()

        def renew() -> None:
            while not stopped.wait(self._lease_seconds / 3):
                if not self._repository.renew_run_lease(
                    run_id=run_id,
                    owner_id=owner_id,
                    lease_seconds=self._lease_seconds,
                ):
                    return

        thread = Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=self._lease_seconds)

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
