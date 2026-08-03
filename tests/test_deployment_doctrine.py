from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentDoctrineTests(unittest.TestCase):
    def test_repository_contract_names_wsl_as_canonical_target(self) -> None:
        contract = (ROOT / "anarchi.yaml").read_text(encoding="utf-8")

        self.assertIn("canonical_target: wsl-ubuntu-local", contract)
        self.assertIn("supervisor: systemd", contract)
        self.assertIn("production_service: cerberus.service", contract)
        self.assertIn("runtime_lifecycle_current: Pulse", contract)
        self.assertIn("render_com_status: legacy", contract)
        self.assertIn("railway_status: legacy", contract)

    def test_primary_readme_marks_cloud_routes_as_legacy(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("## Self-hosted WSL Ubuntu service", readme)
        self.assertIn("Render.com and Railway are not active deployment targets.", readme)
        self.assertNotIn("## Render Launch", readme)
        self.assertNotIn("Suggested Render environment values", readme)

    def test_local_linux_readme_documents_release_conveyor(self) -> None:
        readme = (
            ROOT / "deployment" / "local-linux" / "README.md"
        ).read_text(encoding="utf-8")

        self.assertIn("# Canonical self-hosted WSL Ubuntu runtime", readme)
        self.assertIn("build-release.sh <full-commit>", readme)
        self.assertIn("verify-release.sh <full-commit>", readme)
        self.assertIn("activate-staging.sh <full-commit>", readme)
        self.assertIn("promote-production.sh <full-commit>", readme)
        self.assertIn("rollback-production.sh", readme)


if __name__ == "__main__":
    unittest.main()
