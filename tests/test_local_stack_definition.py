from __future__ import annotations

import unittest
from pathlib import Path

import yaml


class LocalStackDefinitionTest(unittest.TestCase):
    def test_root_compose_starts_postgres_and_backend_services(self) -> None:
        compose_path = Path(__file__).resolve().parents[1] / "compose.yml"
        compose = yaml.safe_load(compose_path.read_text())

        services = compose["services"]
        self.assertEqual(
            set(services),
            {"postgres", "web-bff", "agent-service", "comps-service"},
        )

        for service_name, port in (
            ("web-bff", "8000:8000"),
            ("agent-service", "8001:8001"),
            ("comps-service", "8002:8002"),
        ):
            with self.subTest(service=service_name):
                service = services[service_name]
                self.assertEqual(service["depends_on"]["postgres"]["condition"], "service_healthy")
                self.assertIn(port, service["ports"])
                self.assertIn("DATABASE_URL", service["environment"])
                self.assertEqual(
                    service["environment"]["TALK_TO_YOUR_STOCK_ENV"],
                    "${TALK_TO_YOUR_STOCK_ENV:-local}",
                )

        self.assertIn("healthcheck", services["postgres"])

    def test_dev_compose_file_remains_available_for_explicit_invocation(self) -> None:
        compose_path = Path(__file__).resolve().parents[1] / "dev" / "docker-compose.yml"
        compose = yaml.safe_load(compose_path.read_text())

        self.assertEqual(
            set(compose["services"]),
            {"postgres", "web-bff", "agent-service", "comps-service"},
        )


if __name__ == "__main__":
    unittest.main()
