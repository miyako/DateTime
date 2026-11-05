import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_provenance import record, verify
from build_cli import inspect_cpp_dependencies


class DateTimeProvenanceTests(unittest.TestCase):
    def test_native_dependency_gate_rejects_external_icu_and_dynamic_crt(self):
        cases = (
            ("macos-arm64", "/usr/lib/libc++.1.dylib (compatibility version 1.0.0)", True),
            ("macos-arm64", "/usr/lib/libicucore.A.dylib (compatibility version 1.0.0)", False),
            ("macos-arm64", "/opt/homebrew/lib/libicuuc.dylib (compatibility version 1.0.0)", False),
            ("windows-x64", "    KERNEL32.dll\n", True),
            ("windows-x64", "    KERNEL32.dll\n    VCRUNTIME140.dll\n", False),
            ("windows-x64", "    icuuc77.dll\n", False),
            ("windows-x64", "no dependency evidence", False),
        )
        for target, output, accepted in cases:
            with self.subTest(target=target, output=output), patch(
                    "build_cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                if accepted:
                    self.assertTrue(inspect_cpp_dependencies(Path("datetime"), target))
                else:
                    with self.assertRaises(ValueError):
                        inspect_cpp_dependencies(Path("datetime"), target)

    def test_dependency_build_and_protocol_inputs_invalidate_manifest(self):
        with tempfile.TemporaryDirectory(prefix="datetime manifest ") as temporary:
            root = Path(temporary)
            for name in ("cli", "scripts", "tests", ".github/workflows", ".github/actions/cpp-environment"):
                (root / name).mkdir(parents=True)
            (root / "template.json").write_text(json.dumps({"language": "cpp"}))
            (root / "package.json").write_text("{}")
            (root / "cli/main.cpp").write_text("int main() {}")
            executable = root / "datetime"
            executable.write_bytes(b"synthetic test artifact, never executed")
            for relative in ("scripts/build_icu.py", "scripts/build_cli.py",
                             "scripts/build_provenance.py", "scripts/measure_cli.py",
                             "tests/cli-contract.json", ".github/workflows/ci.yml",
                             ".github/actions/cpp-environment/action.yml",
                             ".github/workflows/publish.yml", "scripts/bump_release.py",
                             "release-policy.json"):
                path = root / relative
                path.write_text("original build input")
                record(root, "macos-arm64", executable, False, "synthetic")
                verify(root, "macos-arm64", executable)
                path.write_text("modified build input")
                with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, "stale"):
                    verify(root, "macos-arm64", executable)
