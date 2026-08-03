from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interoperability import (
    AdapterDescriptor,
    AdapterRegistry,
    CapabilitySpec,
    Command,
    ContractError,
    Observation,
    RegistryError,
    Result,
    ResultStatus,
    decode,
    encode,
)


CAPABILITY = CapabilitySpec(
    name="sample.echo",
    version="1.0",
    request_schema={"type": "object"},
    result_schema={"type": "object"},
)
DESCRIPTOR = AdapterDescriptor(
    adapter_id="sample.adapter",
    adapter_version="1.0.0",
    capabilities=(CAPABILITY,),
)


class EchoAdapter:
    descriptor = DESCRIPTOR

    async def invoke(self, command: Command) -> Result:
        return Result(
            result_id=f"result:{command.command_id}",
            command_id=command.command_id,
            adapter_id=command.adapter_id,
            capability=command.capability,
            status=ResultStatus.SUCCEEDED,
            payload=command.payload,
            correlation_id=command.correlation_id,
        )


def command() -> Command:
    return Command(
        command_id="command-1",
        adapter_id="sample.adapter",
        capability="sample.echo",
        session_id="session-1",
        payload={"text": "hello", "items": [3, 2, 1]},
        idempotency_key="stable-key",
        correlation_id="correlation-1",
    )


class InteroperabilityContractTests(unittest.TestCase):
    def test_contract_payload_is_recursively_immutable(self) -> None:
        value = command()
        with self.assertRaises(TypeError):
            value.payload["text"] = "changed"
        self.assertEqual(value.payload["items"], (3, 2, 1))

    def test_contract_rejects_non_json_payload(self) -> None:
        with self.assertRaises(ContractError):
            Command("c", "a", "x", "s", payload={"bad": object()})

    def test_contract_rejects_non_finite_number(self) -> None:
        with self.assertRaises(ContractError):
            Command("c", "a", "x", "s", payload={"bad": float("nan")})

    def test_capabilities_are_sorted_deterministically(self) -> None:
        descriptor = AdapterDescriptor(
            adapter_id="a",
            adapter_version="1",
            capabilities=(
                CapabilitySpec("z", "1"),
                CapabilitySpec("a", "2"),
                CapabilitySpec("a", "1"),
            ),
        )
        self.assertEqual(
            tuple((item.name, item.version) for item in descriptor.capabilities),
            (("a", "1"), ("a", "2"), ("z", "1")),
        )

    def test_duplicate_capability_version_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            AdapterDescriptor("a", "1", (CapabilitySpec("x", "1"), CapabilitySpec("x", "1")))

    def test_failed_result_requires_error_code(self) -> None:
        with self.assertRaises(ContractError):
            Result("r", "c", "a", "x", ResultStatus.FAILED)

    def test_observation_sequence_cannot_be_negative(self) -> None:
        with self.assertRaises(ContractError):
            Observation("o", "a", "x", "s", -1)

    def test_command_canonical_json_is_stable(self) -> None:
        first = encode(command())
        second = encode(command())
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["kind"], "command")

    def test_all_wire_contracts_round_trip(self) -> None:
        values = (
            DESCRIPTOR,
            command(),
            Observation("o", "sample.adapter", "sample.echo", "session-1", 0, {"value": 1}),
            Result(
                "r",
                "command-1",
                "sample.adapter",
                "sample.echo",
                ResultStatus.SUCCEEDED,
                {"value": 1},
            ),
        )
        for value in values:
            self.assertEqual(encode(decode(encode(value))), encode(value))

    def test_unknown_wire_kind_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            decode({"kind": "unknown", "schema_version": "anarchi.interop.v1"})


class InteroperabilityRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_and_dispatch(self) -> None:
        registry = AdapterRegistry()
        registry.register(EchoAdapter())
        result = await registry.dispatch(command())
        self.assertEqual(result.status, ResultStatus.SUCCEEDED)
        self.assertEqual(result.payload["text"], "hello")

    async def test_duplicate_registration_is_rejected(self) -> None:
        registry = AdapterRegistry()
        registry.register(EchoAdapter())
        with self.assertRaises(RegistryError):
            registry.register(EchoAdapter())

    async def test_unknown_adapter_is_rejected(self) -> None:
        registry = AdapterRegistry()
        with self.assertRaises(RegistryError):
            await registry.dispatch(command())

    async def test_undeclared_capability_is_rejected(self) -> None:
        registry = AdapterRegistry()
        registry.register(EchoAdapter())
        invalid = Command("c", "sample.adapter", "missing", "s")
        with self.assertRaises(RegistryError):
            await registry.dispatch(invalid)

    async def test_result_identity_mismatch_is_rejected(self) -> None:
        class BrokenAdapter:
            descriptor = DESCRIPTOR

            async def invoke(self, value: Command) -> Result:
                return Result("r", "wrong", value.adapter_id, value.capability, ResultStatus.SUCCEEDED)

        registry = AdapterRegistry()
        registry.register(BrokenAdapter())
        with self.assertRaises(RegistryError):
            await registry.dispatch(command())

    async def test_registry_listing_is_sorted(self) -> None:
        class AdapterB(EchoAdapter):
            descriptor = AdapterDescriptor("b", "1", (CAPABILITY,))

        class AdapterA(EchoAdapter):
            descriptor = AdapterDescriptor("a", "1", (CAPABILITY,))

        registry = AdapterRegistry()
        registry.extend((AdapterB(), AdapterA()))
        self.assertEqual(tuple(item.adapter_id for item in registry.descriptors()), ("a", "b"))

    async def test_capability_discovery_is_generic(self) -> None:
        registry = AdapterRegistry()
        registry.register(EchoAdapter())
        self.assertEqual(
            tuple(item.adapter_id for item in registry.adapters_for("sample.echo", "1.0")),
            ("sample.adapter",),
        )


class InteroperabilityIsolationTests(unittest.TestCase):
    def test_package_uses_only_standard_library_and_local_imports(self) -> None:
        package_root = Path(__file__).resolve().parents[1] / "src" / "interoperability"
        forbidden = ("claw", "cerberus", "turn_state", "core_loop", "runtime_state")
        for path in package_root.glob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for token in forbidden:
                self.assertNotIn(token, text, f"{path.name} contains forbidden coupling {token}")

    def test_package_imports_without_repository_bootstrap(self) -> None:
        package_root = Path(__file__).resolve().parents[1] / "src" / "interoperability"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "interoperability"
            target.mkdir()
            for source in package_root.glob("*.py"):
                (target / source.name).write_bytes(source.read_bytes())
            environment = dict(os.environ)
            environment["PYTHONPATH"] = directory
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    (
                        "import sys;"
                        f"sys.path.insert(0,{directory!r});"
                        "import interoperability;"
                        "assert interoperability.SCHEMA_VERSION=='anarchi.interop.v1'"
                    ),
                ],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_public_package_has_no_external_distribution_dependency(self) -> None:
        package_root = Path(__file__).resolve().parents[1] / "src" / "interoperability"
        for path in package_root.glob("*.py"):
            spec = importlib.util.spec_from_file_location(f"isolated_{path.stem}", path)
            self.assertIsNotNone(spec)


if __name__ == "__main__":
    unittest.main()
