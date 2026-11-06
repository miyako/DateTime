"""Exercise the release registry against the actual adopted C++ feature."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bump_release


class DateTimeVersionTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / (".datetime-version-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        paths = [
            "template.json", "package.json", "release-policy.json", "README.md",
            "cli/main.cpp", "cli/CMakeLists.txt", "cli/icu.json",
            "tests/test_cli.py", "tests/test_datetime_native.py",
            "tests/cli-contract.json", "tests/host/contract.json",
            "tests/host/Project/Sources/Methods/Smoke.4dm",
        ]
        component = "component/DateTime/"
        paths += [component + name for name in (
            "Project/DateTime.4DProject", "Project/Sources/settings.4DSettings",
            "Project/Sources/catalog.4DCatalog", "Project/Sources/Classes/DateTime.4dm",
            "Documentation/Classes/DateTime.md", "Documentation/Phase1.md",
        )]
        for relative in paths:
            source = ROOT / relative
            if source.is_file():
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        self.version = json.loads((self.root / "template.json").read_text())["version"]
        major, minor, patch_version = map(int, self.version.split("."))
        self.patch_version = f"{major}.{minor}.{patch_version + 1}"
        self.minor_version = f"{major}.{minor + 1}.0"
        self.major_version = f"{major + 1}.0.0"
        self.git("init", "-q")
        self.git("config", "user.name", "Release fixture")
        self.git("config", "user.email", "release-fixture@example.invalid")
        self.git("remote", "add", "origin", "https://github.com/miyako/DateTime.git")
        self.git("add", "--", ".")
        self.git("commit", "-q", "-m", "DateTime version fixture")
        environment = patch.dict(os.environ, {"GITHUB_REPOSITORY": "miyako/DateTime"})
        environment.start()
        self.addCleanup(environment.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}

    def test_real_cpp_feature_contracts_and_protocol_examples_move_together(self):
        before = self.snapshot()
        version = self.minor_version
        result = bump_release.apply(self.root, version, stage=True)
        after = self.snapshot()
        changed = sorted(name for name in after if after[name] != before[name])
        self.assertEqual(result["changed_paths"], changed)
        self.assertEqual(self.git("diff", "--cached", "--name-only").splitlines(), changed)
        self.assertIn("cli/main.cpp", changed)
        feature = "component/DateTime/Project/Sources/Classes/DateTime.4dm"
        self.assertIn(feature, changed)
        self.assertIn(f'$payload.version#"{version}"', after[feature].decode())
        self.assertIn(rf'\"version\":\"{version}\"', after["cli/main.cpp"].decode())
        self.assertNotIn(rf'\"version\":\"{self.version}\"', after["cli/main.cpp"].decode())
        self.assertIn(f'smoke_test(self.executable, "{version}"', after["tests/test_cli.py"].decode())
        native = after["tests/test_datetime_native.py"].decode()
        self.assertEqual(native.count(f'"version": "{version}"'), 2)
        self.assertIn('"icuVersion": "77.1"', native)
        self.assertIn('"tzdataVersion": "2025a"', native)
        self.assertIn(f'`{{"protocol":1,"version":"{version}"}}`', after["README.md"].decode())
        contract = json.loads(after["tests/host/contract.json"])
        assertions = contract["feature"]["assertions"] + contract["execution"]["assertions"]
        values = {a["path"]: a.get("equals") for a in assertions}
        self.assertEqual(values["/version/value"], {"protocol": 1, "version": version})
        self.assertEqual(json.loads(values["/renamedExecutable"]["stdout"])["version"], version)
        self.assertEqual(json.loads(after["tests/cli-contract.json"])["cases"][0]["stdout"]["version"],
                         "$VERSION")
        for name in ("cli/CMakeLists.txt", "cli/icu.json",
                     "component/DateTime/Documentation/Phase1.md", "release-policy.json"):
            if name in before:
                self.assertEqual(after[name], before[name], name)
        self.assertEqual(bump_release.apply(self.root, version)["changed_paths"], [])
        self.assertEqual(after, self.snapshot())

    def test_same_candidate_retry_does_not_increment_or_weaken_validation(self):
        self.git("tag", "v" + self.version)
        for mode, version in (("patch", self.patch_version), ("minor", self.minor_version),
                              ("major", self.major_version)):
            self.assertEqual(bump_release.next_version(self.root, mode)["version"], version)
        version = self.patch_version
        bump_release.apply(self.root, version)
        before = self.snapshot()
        self.assertEqual(bump_release.next_version(self.root, "patch"),
                         {"version": version, "tag": "v" + version})
        self.assertEqual(bump_release.apply(self.root, version, stage=True)["changed_paths"], [])
        self.assertEqual(before, self.snapshot())
        feature = self.root / "component/DateTime/Project/Sources/Classes/DateTime.4dm"
        feature.write_text(feature.read_text().replace(f'$payload.version#"{version}"',
                                                       '$payload.version#""'))
        invalid = self.snapshot()
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            bump_release.next_version(self.root, "patch")
        self.assertEqual(invalid, self.snapshot())

    def test_actual_surfaces_fail_before_writing_or_staging(self):
        targets = {
            "template.json": (f'"version": "{self.version}"', '"version": "not-semver"'),
            "package.json": (f'"version": "{self.version}"', f'"version": "{self.minor_version}"'),
            "cli/main.cpp": (rf'\"version\":\"{self.version}\"', rf'\"version\":\"{self.minor_version}\"'),
            "component/DateTime/Project/Sources/Classes/DateTime.4dm":
                (f'$payload.version#"{self.version}"', '$payload.version#""'),
            "tests/test_cli.py":
                (f'self.executable, "{self.version}"', f'self.executable, "{self.minor_version}"'),
            "tests/test_datetime_native.py":
                (f'"version": "{self.version}"', f'"version": "{self.minor_version}"'),
            "tests/host/Project/Sources/Methods/Smoke.4dm":
                (f'#"{self.version}"', f'#"{self.minor_version}"'),
            "tests/host/contract.json":
                (rf'\"version\":\"{self.version}\"', rf'\"version\":\"{self.minor_version}\"'),
            "README.md": (f'"version":"{self.version}"', f'"version":"{self.minor_version}"'),
        }
        for relative, (old, new) in targets.items():
            path = self.root / relative
            original = path.read_text()
            self.assertIn(old, original, relative)
            path.write_text(original.replace(old, new))
            before = self.snapshot()
            with self.subTest(path=relative), self.assertRaises(ValueError):
                bump_release.apply(self.root, self.major_version, stage=True)
            self.assertEqual(before, self.snapshot())
            self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
            path.write_text(original)

    def test_cpp_optional_cmake_versions_and_docs_are_bounded(self):
        cmake = self.root / "cli/CMakeLists.txt"
        cmake.write_text(cmake.read_text().replace("project(datetime LANGUAGES",
                                                  f"project(datetime VERSION {self.version} LANGUAGES")
                         + f'\nset(COMPONENT_VERSION "{self.version}")\nset(ICU_VERSION "77.1")\n')
        doc = self.root / "component/DateTime/Documentation/Classes/DateTime.md"
        doc.write_text(doc.read_text() + f'\n`{{"protocol":1,"version":"{self.version}"}}`\n')
        readme = self.root / "README.md"
        historical = '\nHistorical response {"protocol":1,"version":"0.1.0"}\n'
        readme.write_text(readme.read_text() + historical)
        bump_release.apply(self.root, self.major_version)
        self.assertIn(f"project(datetime VERSION {self.major_version} LANGUAGES", cmake.read_text())
        self.assertIn(f'set(COMPONENT_VERSION "{self.major_version}")', cmake.read_text())
        self.assertIn('set(ICU_VERSION "77.1")', cmake.read_text())
        self.assertIn(f'"version":"{self.major_version}"', doc.read_text())
        self.assertTrue(readme.read_text().endswith(historical))

    def test_metadata_duplicates_wrong_identity_and_invalid_versions_are_rejected(self):
        before = self.snapshot()
        for version in ("v1.0.0", "01.2.3", "1.2", "1.2.3\n", "1.2.3-beta", None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                bump_release.apply(self.root, version)
            self.assertEqual(before, self.snapshot())
        for repository in ("miyako/datetime", "other/DateTime"):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                bump_release.apply(self.root, self.major_version, repository)
        path = self.root / "package.json"
        path.write_text(path.read_text().replace(f'"version": "{self.version}"',
                                                f'"version": "{self.version}", "version": "{self.version}"'))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            bump_release.apply(self.root, self.major_version, stage=True)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
