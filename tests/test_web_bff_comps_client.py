from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI

from talk_to_your_stock_shared import (
    MinMedianMax,
    Run,
    RunResponse,
    RunStatus,
    RunTableResponse,
    TraceResponse,
)
from talk_to_your_stock_shared.schemas import RunTableSummary, RunTableSummaryStats
from tests.live_service import running_service
from web_bff.comps_client import HttpCompsClient


class WebBffCompsClientTest(unittest.TestCase):
    def test_reads_run_table_and_trace_through_configured_comps_service(self) -> None:
        now = datetime.now(UTC)
        run_id = uuid4()
        run = Run(
            id=run_id,
            thread_id=uuid4(),
            trigger_message_id=uuid4(),
            status=RunStatus.SUCCEEDED,
            target_ticker="AAPL",
            peer_tickers=["MSFT"],
            currency="USD",
            as_of=now,
            created_at=now,
            started_at=now,
            completed_at=now,
        )
        empty_stats = MinMedianMax(min=None, median=None, max=None)
        table = RunTableResponse(
            run_id=run_id,
            target_ticker="AAPL",
            currency="USD",
            as_of=now,
            rows=[],
            summary=RunTableSummary(
                stats=RunTableSummaryStats(
                    ev_to_revenue=empty_stats,
                    ev_to_ebit=empty_stats,
                    ev_to_ebitda=empty_stats,
                    pe=empty_stats,
                )
            ),
        )
        trace = TraceResponse(run_id=run_id, formulas=[])
        comps_app = FastAPI()

        @comps_app.get("/v1/runs/{requested_run_id}")
        def get_run(requested_run_id: str) -> RunResponse:
            self.assertEqual(requested_run_id, str(run_id))
            return RunResponse(run=run)

        @comps_app.get("/v1/runs/{requested_run_id}/table")
        def get_table(requested_run_id: str) -> RunTableResponse:
            self.assertEqual(requested_run_id, str(run_id))
            return table

        @comps_app.get("/v1/runs/{requested_run_id}/trace")
        def get_trace(requested_run_id: str) -> TraceResponse:
            self.assertEqual(requested_run_id, str(run_id))
            return trace

        with running_service(comps_app) as base_url:
            client = HttpCompsClient(base_url=base_url)

            self.assertEqual(client.get_run(run_id), run)
            self.assertEqual(client.get_table(run_id), table)
            self.assertEqual(client.get_trace(run_id), trace)


if __name__ == "__main__":
    unittest.main()
