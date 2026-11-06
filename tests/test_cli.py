"""Exercise the configured native executable; ICU is built once, never per test."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_cli import build, native_target, smoke_test


class CppCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("TEST_CLI_LANGUAGE") == "none":
            raise unittest.SkipTest("native CLI execution explicitly disabled")
        if os.environ.get("TEST_CLI_LANGUAGE") not in (None, "cpp"):
            raise RuntimeError("DateTime requires the selected C++ toolchain")
        configured = os.environ.get("DATETIME_TEST_EXECUTABLE")
        cls.executable = Path(configured).resolve() if configured else build(ROOT, native_target())
        if not cls.executable.is_file():
            raise RuntimeError("native test executable does not exist")

    def run_cli(self, args, status=0, env=None):
        result = subprocess.run([str(self.executable), *args], capture_output=True, timeout=10, env=env)
        self.assertEqual(result.returncode, status, result.stderr)
        if status == 0:
            self.assertEqual(result.stderr, b"")
            self.assertTrue(result.stdout.endswith(b"\n"))
            self.assertEqual(result.stdout.count(b"\n"), 1)
            return json.loads(result.stdout)
        self.assertEqual(result.stdout, b"")
        error = json.loads(result.stderr)
        self.assertEqual(error["protocol"], 1)
        self.assertTrue(error["error"]["message"])
        return error

    def convert(self, instant, zone, status=0, env=None):
        return self.run_cli(["convert", "--instant", instant, "--zone", zone], status, env)

    def test_native_smoke_contract(self):
        smoke_test(self.executable, "0.1.1", ROOT / "tests/cli-contract.json")

    def test_offsets_and_day_rollover(self):
        self.assertEqual(self.convert("2026-01-15T17:45:00.12+05:45", "Asia/Kathmandu"), {
            "protocol": 1, "instant": "2026-01-15T12:00:00.120Z", "zone": "Asia/Kathmandu",
            "local": "2026-01-15T17:45:00.120+05:45", "offsetSeconds": 20700})
        self.assertEqual(self.convert("2026-01-01T00:00:00Z", "America/New_York")["local"],
                         "2025-12-31T19:00:00.000-05:00")

    def test_dst_forward_conversion(self):
        vectors = (
            ("2026-03-08T06:59:59.999Z", "America/New_York", "2026-03-08T01:59:59.999-05:00"),
            ("2026-03-08T07:00:00Z", "America/New_York", "2026-03-08T03:00:00.000-04:00"),
            ("2026-11-01T05:30:00Z", "America/New_York", "2026-11-01T01:30:00.000-04:00"),
            ("2026-11-01T06:30:00Z", "America/New_York", "2026-11-01T01:30:00.000-05:00"),
        )
        for instant, zone, local in vectors:
            with self.subTest(instant=instant):
                self.assertEqual(self.convert(instant, zone)["local"], local)

    def test_strict_instants(self):
        invalid = (
            "", "2026-02-30T12:00:00Z", "2026-01-15T24:00:00Z", "2026-01-15T12:60:00Z",
            "2026-01-15T12:00:60Z", "2026-01-15T12:00:00.1234Z",
            "2026-01-15T12:00:00.Z", "2026-01-15T12:00:00", "2026-01-15t12:00:00z",
            "2026-01-15T12:00:00-00:00", "2026-01-15T12:00:00+24:00",
            "2026-01-15T12:00:00+00:60", "0000-01-01T00:00:00Z", "10000-01-01T00:00:00Z",
            " 2026-01-15T12:00:00Z", "2026-01-15T12:00:00Z ", "1900-02-29T00:00:00Z",
        )
        for instant in invalid:
            with self.subTest(instant=instant):
                self.assertEqual(self.convert(instant, "Etc/UTC", 2)["error"]["code"], "invalid_instant")
        self.assertEqual(self.convert("2000-02-29T00:00:00Z", "Etc/UTC")["instant"],
                         "2000-02-29T00:00:00.000Z")

    def test_range_boundaries_are_not_normalized_outside_contract(self):
        for instant in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"):
            self.assertEqual(self.convert(instant, "Etc/UTC", 2)["error"]["code"], "out_of_range")
        self.assertEqual(self.convert("9999-12-31T23:59:59Z", "Asia/Kathmandu", 2)["error"]["code"],
                         "out_of_range")

    def test_unknown_and_custom_zones_error(self):
        for zone in ("Not/A_Zone", "Europe/paris", "GMT+05:45", "", "Europe/Paris ", "日本語"):
            with self.subTest(zone=zone):
                self.assertEqual(self.convert("2026-01-15T12:00:00Z", zone, 2)["error"]["code"],
                                 "invalid_zone")

    def test_batch_order_errors_and_bound(self):
        args = ["batch", "--item", "2026-01-15T12:00:00Z", "Asia/Kathmandu",
                "--item", "bad", "Etc/UTC", "--item", "2026-01-15T12:00:00Z", "Not/A_Zone"]
        results = self.run_cli(args)["results"]
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["value"]["offsetSeconds"], 20700)
        self.assertEqual(results[1]["error"]["code"], "invalid_instant")
        self.assertEqual(results[2]["error"]["code"], "invalid_zone")
        pair = ["--item", "2026-01-15T12:00:00Z", "Etc/UTC"]
        self.assertEqual(len(self.run_cli(["batch", *pair * 256])["results"]), 256)
        self.assertEqual(self.run_cli(["batch", *pair * 257], 2)["error"]["code"], "invalid_arguments")

    def test_bundled_data_and_host_zone_independence(self):
        with tempfile.TemporaryDirectory(prefix="empty ICU data ") as directory:
            environment = dict(os.environ, ICU_DATA=directory, TZ="Pacific/Honolulu")
            first = self.convert("2026-01-15T12:00:00Z", "Europe/Paris", env=environment)
            environment["TZ"] = "Asia/Tokyo"
            self.assertEqual(first, self.convert("2026-01-15T12:00:00Z", "Europe/Paris", env=environment))

    def test_literal_argument_round_trips(self):
        args = ["日本語 café", "quote \" and \\ slash", "line\nbreak", "$() ; & % !", " trailing\\", ""]
        self.assertEqual(self.run_cli(["inspect-args", *args]), {"protocol": 1, "args": args})

    @unittest.skipUnless(os.name == "posix", "raw argv byte validation is POSIX only")
    def test_malformed_utf8(self):
        for malformed in (b"\x80", b"\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xff"):
            self.assertEqual(self.run_cli([b"inspect-args", malformed], 2)["error"]["code"],
                             "invalid_arguments")
