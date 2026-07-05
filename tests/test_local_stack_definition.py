from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class LocalStackDefinitionTest(unittest.TestCase):
    def test_compose_starts_postgres_and_backend_services(self) -> None:
        compose_path = REPO_ROOT / "dev" / "docker-compose.yml"
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
                    "${TALK_TO_YOUR_STOCK_ENV}",
                )

        self.assertIn("healthcheck", services["postgres"])

    def test_dockerignore_excludes_local_env_files_but_keeps_examples(self) -> None:
        dockerignore = (REPO_ROOT / ".dockerignore").read_text().splitlines()

        self.assertIn(".env", dockerignore)
        self.assertIn("**/.env", dockerignore)
        self.assertNotIn(".env.example", dockerignore)
        self.assertNotIn("**/.env.example", dockerignore)


if __name__ == "__main__":
    unittest.main()
