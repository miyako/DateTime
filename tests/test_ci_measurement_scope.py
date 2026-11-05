from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MeasurementScopeTests(unittest.TestCase):
    def setUp(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        jobs = workflow.split("jobs:\n", 1)[1]
        starts = list(re.finditer(r"(?m)^  ([a-z]+):\n", jobs))
        self.jobs = {match[1]: jobs[match.end():starts[index + 1].start() if index + 1 < len(starts) else len(jobs)]
                     for index, match in enumerate(starts)}

    def test_production_keeps_dependency_protocol_and_private_symbol_gates(self):
        native = self.jobs["native"]
        self.assertIn("scripts/build_cli.py", native)
        self.assertIn("test_cli.py", native)
        self.assertIn("scripts/measure_cli.py", native)
        self.assertNotIn("--compare", native)
        self.assertNotIn("continue-on-error", native)
        self.assertIn("private ICU symbols", native)

    def test_full_baseline_is_explicitly_optional_and_records_failures(self):
        baseline = self.jobs["baseline"]
        self.assertIn("continue-on-error: true", baseline)
        self.assertIn('"--compare"', baseline)
        self.assertIn("if: ${{ !cancelled() }}", baseline)
        self.assertIn("icu-baseline-diagnostics", baseline)
        self.assertIn("!inputs.release_call", baseline)
        self.assertIn("github.ref_type != 'tag'", baseline)
        self.assertIn("ref: ${{ inputs.source_sha || github.sha }}", baseline)

    def test_assembly_does_not_depend_on_optional_baseline(self):
        assembly = self.jobs["assembly"]
        self.assertIn("needs: [configuration, native]", assembly)
        self.assertNotIn("needs: [configuration, baseline]", assembly)
        for gate in ("scripts/ci_config.py assemble", "scripts/accept_hosted.py"):
            self.assertIn(gate, assembly)

    def test_branch_inspection_retains_compiled_package_and_failure_evidence(self):
        assembly = self.jobs["assembly"]
        self.assertIn("name: compiled-component", assembly)
        self.assertIn("dist/*.acceptance.json", assembly)
        self.assertIn("name: component-acceptance-diagnostics", assembly)
        self.assertIn("if: always() && vars.FOURD_ENABLED == 'true'", assembly)
        self.assertIn("!inputs.release_call", assembly)
        self.assertNotIn("gh release", assembly)

    def test_ci_does_not_run_on_release_tag_pushes(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("push:\n    branches: ['**']", workflow)
        self.assertNotIn("tags:", workflow)

    def test_shared_windows_environment_does_not_log_or_copy_github_auth(self):
        action = (ROOT / ".github/actions/cpp-environment/action.yml").read_text()
        self.assertIn("-arch=x64 -host_arch=x64", action)
        self.assertIn("^(GITHUB_|RUNNER_)", action)
        self.assertIn("NODE_OPTIONS", action)
        self.assertNotIn("Write-Output $variables", action)
        self.assertIn("using: composite", action)
