from __future__ import annotations

import asyncio
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from alembic import command
from alembic.config import Config

from agent_service.comps_client import CompsToolError, HttpCompsToolClient
from talk_to_your_stock_shared import (
    AnalysisPeriod,
    GenerateCompsToolRequest,
    PeerSelectionMode,
)

from tests.live_service import (
    restartable_service_process,
    running_service_process,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL_VAR = "VERTICAL_ACCEPTANCE_DATABASE_URL"
INTERNAL_TOKEN = "vertical-acceptance-token"
LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"


@unittest.skipUnless(
    os.environ.get(DATABASE_URL_VAR),
    f"{DATABASE_URL_VAR} is required for the production-shaped acceptance test.",
)
class CanonicalBackendPathTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = os.environ[DATABASE_URL_VAR]
        migration_config = Config(str(REPO_ROOT / "alembic.ini"))
        with patch.dict(os.environ, {"DATABASE_URL": cls.database_url}):
            command.downgrade(migration_config, "base")
            command.upgrade(migration_config, "head")

        cls.stack = ExitStack()
        try:
            inherited = dict(os.environ)
            cls.external_url = cls.stack.enter_context(
                running_service_process(
                    "tests.external_substitutes:app",
                    environ=inherited,
                    health_path="/health",
                )
            )
            cls.comps_process = cls.stack.enter_context(
                restartable_service_process(
                    "comps_service.main:app",
                    environ={
                        **inherited,
                        "DATABASE_URL": cls.database_url,
                        "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOKEN,
                        "ALPHA_VANTAGE_API_KEY": "deterministic-provider-secret",
                        "ALPHA_VANTAGE_BASE_URL": (
                            f"{cls.external_url}/alpha-vantage"
                        ),
                        "ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS": "0",
                        "COMPS_RUN_LEASE_SECONDS": "0.6",
                    },
                )
            )
            cls.comps_url = cls.comps_process.url
            cls.agent_url = cls.stack.enter_context(
                running_service_process(
                    "agent_service.main:app",
                    environ={
                        **inherited,
                        "DATABASE_URL": cls.database_url,
                        "GOOGLE_API_KEY": "deterministic-gemini-key",
                        "GOOGLE_GEMINI_BASE_URL": cls.external_url,
                        "GEMINI_MODEL": "gemini-3.1-flash-lite",
                        "COMPS_SERVICE_URL": cls.comps_url,
                        "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOKEN,
                    },
                )
            )
            cls.second_agent_process = cls.stack.enter_context(
                restartable_service_process(
                    "agent_service.main:app",
                    environ={
                        **inherited,
                        "DATABASE_URL": cls.database_url,
                        "GOOGLE_API_KEY": "deterministic-gemini-key",
                        "GOOGLE_GEMINI_BASE_URL": cls.external_url,
                        "GEMINI_MODEL": "gemini-3.1-flash-lite",
                        "COMPS_SERVICE_URL": cls.comps_url,
                        "COMPS_SERVICE_INTERNAL_TOKEN": INTERNAL_TOKEN,
                    },
                )
            )
            cls.second_agent_url = cls.second_agent_process.url
            cls.web_process = cls.stack.enter_context(
                restartable_service_process(
                    "web_bff.main:app",
                    environ={
                        **inherited,
                        "TALK_TO_YOUR_STOCK_ENV": "test",
                        "DATABASE_URL": cls.database_url,
                        "DEV_AUTH_USER_ID": LOCAL_USER_ID,
                        "DEV_AUTH_EMAIL": "vertical-acceptance@example.com",
                        "AGENT_SERVICE_URL": cls.second_agent_url,
                        "COMPS_SERVICE_URL": cls.comps_url,
                    },
                )
            )
            cls.web_url = cls.web_process.url
            cls.second_web_url = cls.stack.enter_context(
                running_service_process(
                    "web_bff.main:app",
                    environ={
                        **inherited,
                        "TALK_TO_YOUR_STOCK_ENV": "test",
                        "DATABASE_URL": cls.database_url,
                        "DEV_AUTH_USER_ID": LOCAL_USER_ID,
                        "DEV_AUTH_EMAIL": "vertical-acceptance@example.com",
                        "AGENT_SERVICE_URL": cls.agent_url,
                        "COMPS_SERVICE_URL": cls.comps_url,
                    },
                )
            )
        except BaseException:
            cls.stack.close()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stack.close()

    def test_explicit_peer_message_completes_one_persisted_run(self) -> None:
        httpx.post(
            f"{self.external_url}/control/alpha-vantage/success"
        ).raise_for_status()
        with httpx.Client(base_url=self.web_url, timeout=30) as client:
            thread_response = client.post(
                "/v1/threads",
                json={"title": "AAPL comps"},
            )
            self.assertEqual(thread_response.status_code, 201, thread_response.text)
            thread = thread_response.json()["thread"]

            created = client.post(
                f"/v1/threads/{thread['id']}/messages",
                json={
                    "message_id": str(uuid4()),
                    "content": "Compare Apple with Microsoft and Nvidia",
                },
            )

            self.assertEqual(created.status_code, 201, created.text)
            body = created.json()
            run_id = body["run"]["id"]
            self.assertEqual(body["run"]["status"], "succeeded")
            self.assertEqual(body["assistant_message"]["run_id"], run_id)
            self.assertEqual(
                body["run"]["trigger_message_id"],
                body["user_message"]["id"],
            )

            messages = client.get(
                f"/v1/threads/{thread['id']}/messages"
            )
            linked_thread = client.get(f"/v1/threads/{thread['id']}")
            run = client.get(f"/v1/runs/{run_id}")
            table = client.get(f"/v1/runs/{run_id}/table")
            trace = client.get(f"/v1/runs/{run_id}/trace")
            source_snapshot = client.get(
                f"/v1/runs/{run_id}/source-snapshot"
            )

        self.assertEqual(messages.status_code, 200, messages.text)
        self.assertEqual(
            messages.json()["messages"],
            [body["user_message"], body["assistant_message"]],
        )
        self.assertEqual(linked_thread.status_code, 200, linked_thread.text)
        self.assertEqual(linked_thread.json()["thread"]["latest_run_id"], run_id)
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run"], body["run"])
        self.assertEqual(table.status_code, 200, table.text)
        self.assertEqual(table.json()["run_id"], run_id)
        self.assertEqual(trace.status_code, 200, trace.text)
        self.assertEqual(trace.json()["run_id"], run_id)
        self.assertEqual(source_snapshot.status_code, 200, source_snapshot.text)
        self.assertEqual(source_snapshot.json()["run_id"], run_id)
        self.assertEqual(
            {row["ticker"] for row in table.json()["rows"]},
            {"AAPL", "MSFT", "NVDA"},
        )
        self._assert_success_persisted_once(
            response=body,
            table=table.json(),
            trace=trace.json(),
            source_snapshot=source_snapshot.json(),
        )

    def test_lost_response_retry_replays_one_persisted_run(self) -> None:
        httpx.post(
            f"{self.external_url}/control/alpha-vantage/success"
        ).raise_for_status()
        message_id = str(uuid4())
        request_body = {
            "message_id": message_id,
            "content": "Compare Apple with Microsoft and Nvidia",
        }
        with httpx.Client(base_url=self.web_url, timeout=30) as client:
            thread_response = client.post(
                "/v1/threads",
                json={"title": "Lost response AAPL comps"},
            )
            self.assertEqual(thread_response.status_code, 201, thread_response.text)
            thread_id = thread_response.json()["thread"]["id"]

            with client.stream(
                "POST",
                f"/v1/threads/{thread_id}/messages",
                json=request_body,
            ) as lost_response:
                self.assertEqual(lost_response.status_code, 201)
            first_request_counts = httpx.get(
                f"{self.external_url}/control/alpha-vantage/requests"
            ).json()

            replayed = client.post(
                f"/v1/threads/{thread_id}/messages",
                json=request_body,
            )

        self.assertEqual(replayed.status_code, 201, replayed.text)
        body = replayed.json()
        self.assertEqual(body["user_message"]["id"], message_id)
        self.assertEqual(body["assistant_message"]["run_id"], body["run"]["id"])
        self.assertEqual(body["run"]["id"], message_id)
        request_counts = httpx.get(
            f"{self.external_url}/control/alpha-vantage/requests"
        ).json()
        self.assertGreater(first_request_counts["requests"], 0)
        self.assertEqual(
            request_counts["requests_by_function"],
            first_request_counts["requests_by_function"],
        )

    def test_message_identity_rejects_different_content(self) -> None:
        httpx.post(
            f"{self.external_url}/control/alpha-vantage/success"
        ).raise_for_status()
        message_id = str(uuid4())
        with httpx.Client(base_url=self.web_url, timeout=30) as client:
            thread = client.post(
                "/v1/threads",
                json={"title": "Conflicting Message identity"},
            ).json()["thread"]
            created = client.post(
                f"/v1/threads/{thread['id']}/messages",
                json={
                    "message_id": message_id,
                    "content": "Compare Apple with Microsoft and Nvidia",
                },
            )
            conflict = client.post(
                f"/v1/threads/{thread['id']}/messages",
                json={
                    "message_id": message_id,
                    "content": "Compare Tesla with Ford and GM",
                },
            )

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["error"]["code"], "CONFLICT")
        self._assert_one_run_for_message(message_id)

    def test_overlapping_same_message_converges_across_web_processes(self) -> None:
        httpx.post(
            f"{self.external_url}/control/alpha-vantage/success"
        ).raise_for_status()
        httpx.post(
            f"{self.external_url}/control/gemini/blocked"
        ).raise_for_status()
        message_id = str(uuid4())
        with httpx.Client(base_url=self.web_url, timeout=30) as client:
            thread_response = client.post(
                "/v1/threads",
                json={"title": "Overlapping AAPL comps"},
            )
        self.assertEqual(thread_response.status_code, 201, thread_response.text)
        thread_id = thread_response.json()["thread"]["id"]
        request_body = {
            "message_id": message_id,
            "content": "Compare Apple with Microsoft and Nvidia",
        }

        def create_message(web_url: str) -> httpx.Response:
            return httpx.post(
                f"{web_url}/v1/threads/{thread_id}/messages",
                json=request_body,
                timeout=30,
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(create_message, self.web_url)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                requests = httpx.get(
                    f"{self.external_url}/control/gemini/requests"
                ).json()["requests"]
                if requests >= 1:
                    break
                time.sleep(0.02)
            else:
                self.fail("The first request did not reach Gemini routing.")
            current = create_message(self.second_web_url)
            httpx.post(
                f"{self.external_url}/control/release-gemini"
            ).raise_for_status()
            completed = first.result()

        calls_after_completed = httpx.get(
            f"{self.external_url}/control/alpha-vantage/requests"
        ).json()["requests_by_function"]
        replayed = create_message(self.second_web_url)

        self.assertEqual(current.status_code, 409, current.text)
        self.assertIsNone(current.json()["error"]["run_id"])
        self.assertEqual(current.json()["error"]["details"]["status"], "routing")
        gemini_requests = httpx.get(
            f"{self.external_url}/control/gemini/requests"
        ).json()["requests"]
        self.assertEqual(gemini_requests, 1)
        self.assertEqual(completed.status_code, 201, completed.text)
        self.assertEqual(replayed.status_code, 201, replayed.text)
        self.assertEqual(completed.json(), replayed.json())
        self.assertEqual(completed.json()["run"]["id"], message_id)
        request_counts = httpx.get(
            f"{self.external_url}/control/alpha-vantage/requests"
        ).json()["requests_by_function"]
        self.assertEqual(request_counts, calls_after_completed)

    def test_comps_restart_reclaims_persisted_calculation_ownership(self) -> None:
        httpx.post(
            f"{self.external_url}/control/alpha-vantage/blocked"
        ).raise_for_status()
        message_id = str(uuid4())
        with httpx.Client(base_url=self.web_url, timeout=30) as client:
            thread_response = client.post(
                "/v1/threads",
                json={"title": "Restarted AAPL comps"},
            )
        self.assertEqual(thread_response.status_code, 201, thread_response.text)
        thread_id = thread_response.json()["thread"]["id"]
        request_body = {
            "message_id": message_id,
            "content": "Compare Apple with Microsoft and Nvidia",
        }

        with ThreadPoolExecutor(max_workers=1) as executor:
            interrupted = executor.submit(
                httpx.post,
                f"{self.web_url}/v1/threads/{thread_id}/messages",
                json=request_body,
                timeout=30,
            )
            self._wait_for_run_status(run_id=message_id, status="running")
            self._wait_for_provider_request(function="GLOBAL_QUOTE")
            calls_before_restart = httpx.get(
                f"{self.external_url}/control/alpha-vantage/requests"
            ).json()["requests_by_function"]
            comps_client = HttpCompsToolClient(
                base_url=self.comps_url,
                internal_token=INTERNAL_TOKEN,
            )
            with self.assertRaises(CompsToolError) as in_progress:
                asyncio.run(
                    comps_client.generate_comps_table(
                        GenerateCompsToolRequest(
                            invocation_id=UUID(message_id),
                            thread_id=UUID(thread_id),
                            trigger_message_id=UUID(message_id),
                            target_ticker="AAPL",
                            peer_tickers=["MSFT", "NVDA"],
                            peer_selection_mode=PeerSelectionMode.USER_SUPPLIED,
                            analysis_period=AnalysisPeriod.LATEST,
                        )
                    )
                )
            self.assertEqual(in_progress.exception.status_code, 409)
            self.assertEqual(
                in_progress.exception.error.error.run_id,
                UUID(message_id),
            )
            self.assertEqual(
                in_progress.exception.error.error.details["status"],
                "running",
            )
            self.comps_process.stop()
            httpx.post(
                f"{self.external_url}/control/release-alpha-vantage"
            ).raise_for_status()
            interrupted_response = interrupted.result()

        self.assertEqual(
            interrupted_response.status_code,
            502,
            interrupted_response.text,
        )
        time.sleep(0.7)
        self.comps_process.start()
        recovered = httpx.post(
            f"{self.web_url}/v1/threads/{thread_id}/messages",
            json=request_body,
            timeout=30,
        )

        self.assertEqual(recovered.status_code, 201, recovered.text)
        body = recovered.json()
        self.assertEqual(body["run"]["id"], message_id)
        self.assertEqual(body["run"]["status"], "succeeded")
        self.assertEqual(body["assistant_message"]["run_id"], message_id)
        calls_after_recovery = httpx.get(
            f"{self.external_url}/control/alpha-vantage/requests"
        ).json()["requests_by_function"]
        self.assertEqual(
            calls_after_recovery.get("SYMBOL_SEARCH"),
            calls_before_restart.get("SYMBOL_SEARCH"),
        )
        self._assert_one_run_for_message(message_id)

    def test_web_restart_does_not_duplicate_live_agent_routing(self) -> None:
        httpx.post(
            f"{self.external_url}/control/alpha-vantage/success"
        ).raise_for_status()
        httpx.post(
            f"{self.external_url}/control/gemini/blocked"
        ).raise_for_status()
        message_id = str(uuid4())
        with httpx.Client(base_url=self.web_url, timeout=30) as client:
            thread = client.post(
                "/v1/threads",
                json={"title": "Interrupted routing"},
            ).json()["thread"]

        def create_message() -> httpx.Response:
            return httpx.post(
                f"{self.web_url}/v1/threads/{thread['id']}/messages",
                json={
                    "message_id": message_id,
                    "content": "Compare Apple with Microsoft and Nvidia",
                },
                timeout=30,
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            interrupted_request = executor.submit(create_message)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                gemini_requests = httpx.get(
                    f"{self.external_url}/control/gemini/requests"
                ).json()["requests"]
                if gemini_requests == 1:
                    break
                time.sleep(0.02)
            else:
                self.fail("The interrupted request did not reach Gemini routing.")

            self.web_process.stop()
            with self.assertRaises(httpx.HTTPError):
                interrupted_request.result()

        self._assert_no_run_for_message(message_id)

        overlapping = httpx.post(
            f"{self.second_web_url}/v1/threads/{thread['id']}/messages",
            json={
                "message_id": message_id,
                "content": "Compare Apple with Microsoft and Nvidia",
            },
            timeout=30,
        )
        self.assertEqual(overlapping.status_code, 409, overlapping.text)
        self.assertEqual(overlapping.json()["error"]["details"]["status"], "routing")

        httpx.post(f"{self.external_url}/control/release-gemini").raise_for_status()
        self._wait_for_run_status(run_id=message_id, status="succeeded")
        self.web_process.start()
        recovered = create_message()

        self.assertEqual(recovered.status_code, 201, recovered.text)
        self.assertEqual(recovered.json()["run"]["id"], message_id)
        gemini_requests = httpx.get(
            f"{self.external_url}/control/gemini/requests"
        ).json()["requests"]
        self.assertEqual(gemini_requests, 1)
        provider_counts = httpx.get(
            f"{self.external_url}/control/alpha-vantage/requests"
        ).json()["requests_by_function"]
        self.assertEqual(provider_counts["SYMBOL_SEARCH"], 3)
        self.assertEqual(provider_counts["GLOBAL_QUOTE"], 3)
        self._assert_one_run_for_message(message_id)

    def _wait_for_run_status(self, *, run_id: str, status: str) -> None:
        import psycopg

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "select status from comps_runs where id = %s",
                        (UUID(run_id),),
                    )
                    row = cursor.fetchone()
            if row is not None and row[0] == status:
                return
            time.sleep(0.02)
        self.fail(f"Run {run_id} did not reach {status}.")

    def _wait_for_provider_request(self, *, function: str) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            counts = httpx.get(
                f"{self.external_url}/control/alpha-vantage/requests"
            ).json()["requests_by_function"]
            if counts.get(function, 0) > 0:
                return
            time.sleep(0.02)
        self.fail(f"Provider did not receive {function}.")

    def _assert_one_run_for_message(self, message_id: str) -> None:
        import psycopg

        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select count(*) from comps_runs where trigger_message_id = %s",
                    (UUID(message_id),),
                )
                count = cursor.fetchone()[0]
        self.assertEqual(count, 1)

    def _assert_no_run_for_message(self, message_id: str) -> None:
        import psycopg

        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select count(*) from comps_runs where trigger_message_id = %s",
                    (UUID(message_id),),
                )
                count = cursor.fetchone()[0]
        self.assertEqual(count, 0)

    def test_provider_failure_completes_one_linked_failed_run(self) -> None:
        httpx.post(
            f"{self.external_url}/control/alpha-vantage/failure"
        ).raise_for_status()
        message_id = str(uuid4())
        request_body = {
            "message_id": message_id,
            "content": "Compare Apple with Microsoft and Nvidia",
        }
        with httpx.Client(base_url=self.web_url, timeout=30) as client:
            thread_response = client.post(
                "/v1/threads",
                json={"title": "Failed AAPL comps"},
            )
            self.assertEqual(thread_response.status_code, 201, thread_response.text)
            thread = thread_response.json()["thread"]

            with client.stream(
                "POST",
                f"/v1/threads/{thread['id']}/messages",
                json=request_body,
            ) as lost_failure:
                self.assertEqual(lost_failure.status_code, 502)

            first_request_counts = httpx.get(
                f"{self.external_url}/control/alpha-vantage/requests"
            ).json()
            replayed = client.post(
                f"/v1/threads/{thread['id']}/messages",
                json=request_body,
            )

            self.assertEqual(replayed.status_code, 502, replayed.text)
            error = replayed.json()["error"]
            run_id = error["run_id"]
            self.assertEqual(error["code"], "UPSTREAM_ERROR")
            self.assertEqual(
                error["message"],
                "Alpha Vantage request limit was reached while loading AAPL.",
            )
            self.assertEqual(error["details"]["thread_id"], thread["id"])

            messages = client.get(
                f"/v1/threads/{thread['id']}/messages"
            )
            linked_thread = client.get(f"/v1/threads/{thread['id']}")
            run = client.get(f"/v1/runs/{run_id}")
            source_snapshot = client.get(
                f"/v1/runs/{run_id}/source-snapshot"
            )

        replay_request_counts = httpx.get(
            f"{self.external_url}/control/alpha-vantage/requests"
        ).json()
        self.assertEqual(
            replay_request_counts["requests_by_function"],
            first_request_counts["requests_by_function"],
        )
        self.assertEqual(messages.status_code, 200, messages.text)
        persisted_messages = messages.json()["messages"]
        self.assertEqual(
            [(message["role"], message["status"]) for message in persisted_messages],
            [("user", "complete"), ("assistant", "failed")],
        )
        self.assertEqual(persisted_messages[-1]["run_id"], run_id)
        self.assertEqual(
            error["details"]["trigger_message_id"],
            persisted_messages[0]["id"],
        )
        self.assertEqual(linked_thread.status_code, 200, linked_thread.text)
        self.assertEqual(linked_thread.json()["thread"]["latest_run_id"], run_id)
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run"]["status"], "failed")
        self.assertEqual(run.json()["run"]["error_message"], error["message"])
        self.assertEqual(source_snapshot.status_code, 200, source_snapshot.text)
        self.assertEqual(source_snapshot.json()["run_id"], run_id)
        self.assertNotIn("deterministic-provider-secret", source_snapshot.text)
        self.assertNotIn("deterministic-provider-secret", replayed.text)
        self.assertNotIn(INTERNAL_TOKEN, replayed.text)
        self._assert_failure_persisted_once(
            trigger_message_id=persisted_messages[0]["id"],
            run_id=run_id,
            error=error,
        )

    def _assert_success_persisted_once(
        self,
        *,
        response: dict[str, object],
        table: dict[str, object],
        trace: dict[str, object],
        source_snapshot: dict[str, object],
    ) -> None:
        import psycopg
        from psycopg.rows import dict_row

        run = response["run"]
        assert isinstance(run, dict)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select r.id as run_id, r.status, t.run_id as table_run_id,
                           tr.run_id as trace_run_id, s.run_id as snapshot_run_id,
                           s.raw_provider_evidence, s.normalized_inputs,
                           s.created_at as snapshot_created_at
                    from comps_runs r
                    join comps_tables t on t.run_id = r.id
                    join comps_traces tr on tr.run_id = r.id
                    join comps_source_snapshots s on s.run_id = r.id
                    where r.trigger_message_id = %s
                    """,
                    (UUID(run["trigger_message_id"]),),
                )
                persisted = cursor.fetchall()

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["status"], "succeeded")
        self.assertEqual(str(persisted[0]["run_id"]), run["id"])
        self.assertEqual(str(persisted[0]["table_run_id"]), table["run_id"])
        self.assertEqual(str(persisted[0]["trace_run_id"]), trace["run_id"])
        self.assertEqual(str(persisted[0]["snapshot_run_id"]), run["id"])
        self.assertEqual(
            persisted[0]["raw_provider_evidence"],
            source_snapshot["raw_provider_evidence"],
        )
        self.assertEqual(
            persisted[0]["normalized_inputs"],
            source_snapshot["normalized_inputs"],
        )
        self.assertEqual(
            persisted[0]["snapshot_created_at"],
            datetime.fromisoformat(source_snapshot["created_at"]),
        )

    def _assert_failure_persisted_once(
        self,
        *,
        trigger_message_id: str,
        run_id: str,
        error: dict[str, object],
    ) -> None:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select r.id as run_id, r.status, r.failure_http_status,
                           r.failure_code, r.failure_details,
                           t.run_id as table_run_id,
                           tr.run_id as trace_run_id,
                           s.run_id as snapshot_run_id,
                           s.raw_provider_evidence::text as raw_evidence
                    from comps_runs r
                    left join comps_tables t on t.run_id = r.id
                    left join comps_traces tr on tr.run_id = r.id
                    join comps_source_snapshots s on s.run_id = r.id
                    where r.trigger_message_id = %s
                    """,
                    (UUID(trigger_message_id),),
                )
                persisted = cursor.fetchall()

        self.assertEqual(len(persisted), 1)
        self.assertEqual(str(persisted[0]["run_id"]), run_id)
        self.assertEqual(persisted[0]["status"], "failed")
        self.assertEqual(persisted[0]["failure_http_status"], 502)
        self.assertEqual(persisted[0]["failure_code"], error["code"])
        self.assertEqual(persisted[0]["failure_details"], error["details"])
        self.assertIsNone(persisted[0]["table_run_id"])
        self.assertIsNone(persisted[0]["trace_run_id"])
        self.assertEqual(str(persisted[0]["snapshot_run_id"]), run_id)
        self.assertNotIn(
            "deterministic-provider-secret",
            persisted[0]["raw_evidence"],
        )


if __name__ == "__main__":
    unittest.main()
