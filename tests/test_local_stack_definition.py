from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class LocalStackDefinitionTest(unittest.TestCase):
    def test_compose_migrates_database_before_starting_schema_owners(self) -> None:
        compose_path = REPO_ROOT / "dev" / "docker-compose.yml"
        services = yaml.safe_load(compose_path.read_text())["services"]

        migration = services["database-migrate"]
        self.assertEqual(migration["command"], "python -m alembic upgrade head")
        self.assertIn("DATABASE_URL", migration["environment"])
        self.assertEqual(
            migration["depends_on"]["postgres"]["condition"],
            "service_healthy",
        )
        for service_name in ("web-bff", "comps-service"):
            with self.subTest(service=service_name):
                self.assertEqual(
                    services[service_name]["depends_on"]["database-migrate"][
                        "condition"
                    ],
                    "service_completed_successfully",
                )

    def test_compose_starts_postgres_and_backend_services(self) -> None:
        compose_path = REPO_ROOT / "dev" / "docker-compose.yml"
        compose = yaml.safe_load(compose_path.read_text())

        services = compose["services"]
        self.assertEqual(
            set(services),
            {
                "postgres",
                "database-migrate",
                "web-bff",
                "agent-service",
                "comps-service",
            },
        )

        for service_name, port in (
            ("web-bff", "127.0.0.1:8000:8000"),
            ("agent-service", "127.0.0.1:8001:8001"),
            ("comps-service", "127.0.0.1:8002:8002"),
        ):
            with self.subTest(service=service_name):
                service = services[service_name]
                self.assertEqual(service["depends_on"]["postgres"]["condition"], "service_healthy")
                self.assertEqual(service["ports"], [port])
                self.assertIn("DATABASE_URL", service["environment"])
                self.assertEqual(
                    service["environment"]["TALK_TO_YOUR_STOCK_ENV"],
                    "${TALK_TO_YOUR_STOCK_ENV}",
                )

        self.assertEqual(
            services["postgres"]["ports"],
            ["127.0.0.1:5432:5432"],
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
