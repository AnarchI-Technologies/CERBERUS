from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "architecture" / "cerberus-system.v1.json"
PRODUCT_MANIFEST_PATH = ROOT / "deployment" / "client-shell" / "product-manifest.json"


class SystemNamespaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.product_manifest = json.loads(PRODUCT_MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_cerberus_is_reserved_for_the_entire_system(self) -> None:
        system = self.contract["system"]

        self.assertEqual(system["displayName"], "CERBERUS")
        self.assertEqual(system["canonicalName"], "cerberus")
        self.assertEqual(system["scope"], "entire-system")
        self.assertTrue(system["componentUseForbidden"])
        self.assertNotIn("cerberus", self.contract["components"])

    def test_canonical_responsibility_names_are_stable(self) -> None:
        expected = {
            "helm": "Helm",
            "vigil": "Vigil",
            "spine": "Spine",
            "neural-network": "Neural Network",
            "pulse": "Pulse",
            "switch-board": "Switch Board",
            "conecktion": "Co'neck'tion",
        }

        self.assertEqual(
            {key: value["displayName"] for key, value in self.contract["components"].items()},
            expected,
        )
        self.assertEqual(self.contract["concepts"]["reach"]["kind"], "permission-scope")
        self.assertEqual(self.contract["concepts"]["neck"]["kind"], "lineage-boundary")

    def test_display_and_technical_names_follow_the_namespace_rules(self) -> None:
        rules = self.contract["namespaceRules"]
        identifier = re.compile(rules["canonicalIdentifierPattern"])
        forbidden_suffixes = tuple(rules["componentStyleDisplaySuffixesForbidden"])
        entries = [
            *self.contract["components"].values(),
            *self.contract["concepts"].values(),
        ]

        canonical_names = []
        display_names = []
        for entry in entries:
            display_name = entry["displayName"]
            canonical_name = entry["canonicalName"]
            display_names.append(display_name.casefold())
            canonical_names.append(canonical_name)
            self.assertLessEqual(len(display_name.split()), rules["displayNameWordLimit"])
            self.assertRegex(canonical_name, identifier)
            self.assertNotIn("'", canonical_name)
            self.assertFalse(display_name.endswith(forbidden_suffixes), display_name)

        self.assertEqual(len(canonical_names), len(set(canonical_names)))
        self.assertEqual(len(display_names), len(set(display_names)))

    def test_helm_and_vigil_artifacts_and_default_reach_match_product_manifest(self) -> None:
        product = self.product_manifest["product"]
        surfaces = self.product_manifest["surfaceContracts"]

        self.assertEqual(product["systemName"], "CERBERUS")
        self.assertEqual(product["systemScope"], "entire-system")
        self.assertEqual(product["namespaceContract"], "docs/architecture/cerberus-system.v1.json")
        self.assertEqual(surfaces["helm"]["artifacts"], ["helm.exe", "helm-setup.exe"])
        self.assertEqual(surfaces["helm"]["defaultReach"], "standard")
        self.assertTrue(surfaces["helm"]["supportsElevatedReach"])
        self.assertEqual(surfaces["vigil"]["artifacts"], ["vigil.apk", "vigil.aab"])
        self.assertEqual(surfaces["vigil"]["defaultReach"], "read-only")
        self.assertTrue(surfaces["vigil"]["supportsExplicitRemoteReach"])

    def test_repository_contract_uses_the_same_names(self) -> None:
        repository_contract = (ROOT / "anarchi.yaml").read_text(encoding="utf-8")

        for declaration in (
            "scope: entire-system",
            "windows_surface: Helm",
            "android_surface: Vigil",
            "account_orchestrator: Spine",
            "permission_scope: Reach",
            "interoperability: Co'neck'tion",
            "lineage_boundary: Neck",
        ):
            self.assertIn(declaration, repository_contract)

    def test_retired_component_labels_do_not_reappear_in_namespace_surfaces(self) -> None:
        paths = (
            ROOT / "README.md",
            ROOT / "anarchi.yaml",
            ROOT / "docs" / "architecture" / "cerberus-system-namespaces.md",
            PRODUCT_MANIFEST_PATH,
            ROOT / "deployment" / "client-shell" / "README.md",
        )
        retired = ("CERBERUS Desktop", "CERBERUS Service", "Cerberus Core.exe", "Cerberus Companion.apk")

        for path in paths:
            content = path.read_text(encoding="utf-8")
            for label in retired:
                self.assertNotIn(label, content, f"{label!r} returned in {path}")


if __name__ == "__main__":
    unittest.main()
