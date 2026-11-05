"""Protocol/semantic checks against ONE already-built native executable."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

EXECUTABLE = os.environ.get("DATETIME_EXECUTABLE")
INVALID_DATA_EXECUTABLE = os.environ.get("DATETIME_INVALID_DATA_EXECUTABLE")


class DateTimeNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not EXECUTABLE:
            raise unittest.SkipTest("set DATETIME_EXECUTABLE to a built binary")
        cls.executable = str(Path(EXECUTABLE).resolve())

    def invoke(self, *args, code=0, env=None):
        result = subprocess.run([self.executable, *args], capture_output=True, timeout=30, env=env)
        self.assertEqual(result.returncode, code, result)
        self.assertEqual(result.stderr if code == 0 else result.stdout, b"")
        stream = result.stdout if code == 0 else result.stderr
        self.assertTrue(stream.endswith(b"\n"))
        self.assertEqual(stream.count(b"\n"), 1)
        return json.loads(stream.decode("utf-8"))

    def convert(self, instant, zone):
        return self.invoke("convert", "--instant", instant, "--zone", zone)

    def test_version(self):
        self.assertEqual(self.invoke("--version"), {"protocol": 1, "version": "0.1.0"})

    def test_info(self):
        self.assertEqual(self.invoke("--info"), {
            "protocol": 1, "version": "0.1.0", "icuVersion": "77.1",
            "dataVersion": "77.1", "unicodeVersion": "16.0",
            "tzdataVersion": "2025a", "dataMode": "embedded-static"})

    def test_invalid_embedded_data_fails_closed(self):
        if not INVALID_DATA_EXECUTABLE:
            self.skipTest("negative-test executable not supplied")
        expected = {"code": "icu_error", "message": "ICU conversion failed"}
        command = [str(Path(INVALID_DATA_EXECUTABLE).resolve())]
        single = subprocess.run([*command, "convert", "--instant", "2026-01-01T00:00:00Z",
                                 "--zone", "Etc/UTC"], capture_output=True, timeout=30)
        self.assertEqual(single.returncode, 2)
        self.assertEqual(single.stdout, b"")
        self.assertEqual(json.loads(single.stderr), {"protocol": 1, "error": expected})
        batch = subprocess.run([*command, "batch", "--item", "2026-01-01T00:00:00Z", "Etc/UTC"],
                               capture_output=True, timeout=30)
        self.assertEqual(batch.returncode, 0)
        self.assertEqual(batch.stderr, b"")
        self.assertEqual(json.loads(batch.stdout), {
            "protocol": 1, "results": [{"ok": False, "error": expected}]})

    def test_zones_and_historical_seconds(self):
        for instant, zone, local, offset in [
            ("2026-01-15T12:00:00Z", "Europe/Paris", "2026-01-15T13:00:00.000+01:00", 3600),
            ("2026-07-15T12:00:00Z", "Europe/Paris", "2026-07-15T14:00:00.000+02:00", 7200),
            ("2026-01-15T12:00:00Z", "America/New_York", "2026-01-15T07:00:00.000-05:00", -18000),
            ("2026-07-15T12:00:00Z", "America/New_York", "2026-07-15T08:00:00.000-04:00", -14400),
            ("2026-01-15T12:00:00Z", "Asia/Kathmandu", "2026-01-15T17:45:00.000+05:45", 20700),
            ("1900-01-01T00:00:00Z", "Europe/Paris", "1900-01-01T00:09:21.000+00:09:21", 561),
            ("2026-01-15T12:00:00Z", "Pacific/Chatham", "2026-01-16T01:45:00.000+13:45", 49500),
        ]:
            with self.subTest(zone=zone, instant=instant):
                value = self.convert(instant, zone)
                self.assertEqual(value, {"protocol": 1, "instant": instant.replace("Z", ".000Z"),
                    "zone": zone, "local": local, "offsetSeconds": offset})

    def test_canonical_precision_and_offsets(self):
        for text, canonical in [
            ("2024-02-29T00:00:00.1Z", "2024-02-29T00:00:00.100Z"),
            ("2024-02-29T00:00:00.12Z", "2024-02-29T00:00:00.120Z"),
            ("2024-02-29T00:00:00.123+05:45", "2024-02-28T18:15:00.123Z"),
            ("2024-02-29T00:00:00-23:59", "2024-02-29T23:59:00.000Z"),
            ("0001-01-01T00:00:00Z", "0001-01-01T00:00:00.000Z"),
            ("9999-12-31T23:59:59.999Z", "9999-12-31T23:59:59.999Z"),
            ("1500-03-01T00:00:00Z", "1500-03-01T00:00:00.000Z"),
        ]:
            self.assertEqual(self.convert(text, "Etc/UTC")["instant"], canonical)

    def test_invalid_instants(self):
        for text in ["", "2026-01-01", "0000-01-01T00:00:00Z", "10000-01-01T00:00:00Z",
                     "2023-02-29T00:00:00Z", "1500-02-29T00:00:00Z",
                     "2026-04-31T00:00:00Z", "2026-01-01t00:00:00Z",
                     "2026-01-01T00:00:00z", "2026-01-01T24:00:00Z",
                     "2026-01-01T00:00:60Z", "2026-01-01T00:00:00.1234Z",
                     "2026-01-01T00:00:00.Z", "2026-01-01T00:00:00",
                     "2026-01-01T00:00:00-00:00", "2026-01-01T00:00:00+24:00",
                     "2026-01-01T00:00:00+00:60", "2026-01-01T00:00:00Z "]:
            with self.subTest(text=text):
                error = self.invoke("convert", "--instant", text, "--zone", "Etc/UTC", code=2)
                self.assertEqual(error["error"]["code"], "invalid_instant")

    def test_range(self):
        for text, zone in [("0001-01-01T00:00:00+00:01", "Etc/UTC"),
                           ("9999-12-31T23:59:59-00:01", "Etc/UTC"),
                           ("9999-12-31T23:59:59Z", "Asia/Tokyo")]:
            self.assertEqual(self.invoke("convert", "--instant", text, "--zone", zone, code=2)
                             ["error"]["code"], "out_of_range")

    def test_unknown_zone_no_fallback(self):
        for zone in ["", "Mars/Olympus", "america/new_york", "Europe/paris",
                     "GMT+05:00", "Etc/Unknown",
                     "../Europe/Paris", "é", "Europe/Paris "]:
            self.assertEqual(self.invoke("convert", "--instant", "2026-01-01T00:00:00Z",
                                        "--zone", zone, code=2)["error"]["code"], "invalid_zone")

    def test_worldwide_rules_and_alias(self):
        zones = {
            "Africa/Johannesburg": 7200, "America/St_Johns": -12600,
            "Asia/Tokyo": 32400, "Australia/Eucla": 31500,
            "Antarctica/Troll": 0, "Pacific/Kiritimati": 50400,
            "Indian/Mauritius": 14400, "Atlantic/Azores": -3600, "UTC": 0,
        }
        args = ["batch"]
        for zone in zones:
            args.extend(["--item", "2026-01-15T12:00:00Z", zone])
        result = self.invoke(*args)["results"]
        for item, (zone, offset) in zip(result, zones.items()):
            self.assertTrue(item["ok"])
            self.assertEqual(item["value"]["zone"], zone)
            self.assertEqual(item["value"]["offsetSeconds"], offset)

    def test_batch(self):
        result = self.invoke("batch", "--item", "2026-01-01T00:00:00Z", "Etc/UTC",
                             "--item", "bad", "Etc/UTC",
                             "--item", "2026-01-01T00:00:00Z", "Mars/Olympus")
        self.assertEqual(result["protocol"], 1)
        self.assertEqual(len(result["results"]), 3)
        self.assertTrue(result["results"][0]["ok"])
        self.assertNotIn("protocol", result["results"][0]["value"])
        self.assertEqual(result["results"][1], {"ok": False, "error": {
            "code": "invalid_instant", "message": "invalid RFC3339 instant"}})
        self.assertEqual(result["results"][2]["error"]["code"], "invalid_zone")
        item = ["--item", "2026-01-01T00:00:00Z", "Etc/UTC"]
        self.assertEqual(len(self.invoke("batch", *(item * 256))["results"]), 256)
        self.invoke("batch", *(item * 257), code=2)

    def test_semantic_ascii_and_length_limits_are_item_errors(self):
        instant = "2026-01-01T00:00:00Z"
        args = ["batch"]
        pairs = [
            ("a" * 35, "Etc/UTC", "invalid_instant"),
            ("a" * 36, "Etc/UTC", "invalid_instant"),
            ("a" * 5000, "Etc/UTC", "invalid_instant"),
            (instant, "a" * 64, "invalid_zone"),
            (instant, "a" * 65, "invalid_zone"),
            (instant, "a" * 5000, "invalid_zone"),
            ("２０２６-01-01T00:00:00Z", "Etc/UTC", "invalid_instant"),
            (instant, "Éurope/Paris", "invalid_zone"),
        ]
        for text, zone, _ in pairs:
            args.extend(["--item", text, zone])
        result = self.invoke(*args)["results"]
        self.assertEqual([item["error"]["code"] for item in result],
                         [code for _, _, code in pairs])
        self.assertTrue(all(item["ok"] is False for item in result))

    def test_invocation_and_literal_arguments(self):
        for args in [[], ["greet", "--name", "4D"], ["batch"], ["batch", "--item", "bad"],
                     ["convert", "--instant", "bad"], ["--version", "extra"],
                     ["convert", "--zone", "Etc/UTC", "--zone", "Etc/UTC"]]:
            self.assertEqual(self.invoke(*args, code=2)["error"], {
                "code": "invalid_arguments", "message": "invalid command arguments"})
        args = ["", "日本語 space", 'both\'"quotes\\', "\n\t", "$(echo injected)", "a" * 4096]
        self.assertEqual(self.invoke("inspect-args", *args), {"protocol": 1, "args": args})
        self.invoke("inspect-args", "a" * 4097, code=2)
        self.assertEqual(self.invoke("inspect-args", "a" * 4096, "b" * 4096)["args"],
                         ["a" * 4096, "b" * 4096])
        self.invoke("inspect-args", "a" * 4096, "b" * 4096, "c", code=2)

    @unittest.skipIf(os.name == "nt", "POSIX byte argv only")
    def test_malformed_utf8(self):
        for arg in [b"\xff", b"\xc0\x80", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"]:
            result = subprocess.run([os.fsencode(self.executable), b"inspect-args", arg], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_arguments")

    def test_environment_independence(self):
        expected = self.convert("2026-07-15T12:00:00Z", "Europe/Paris")
        env = os.environ.copy()
        env.update(TZ="Pacific/Honolulu", ICU_DATA="/nonexistent/no-data")
        actual = self.invoke("convert", "--instant", "2026-07-15T12:00:00Z",
                             "--zone", "Europe/Paris", env=env)
        self.assertEqual(actual, expected)

    def test_utc_civil_round_trip_samples(self):
        for y in (1, 400, 1500, 1582, 1600, 1900, 1970, 2000, 2024, 9999):
            for m in (1, 2, 3, 12):
                text = f"{y:04d}-{m:02d}-01T12:34:56.789Z"
                self.assertEqual(self.convert(text, "Etc/UTC")["instant"], text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True)
    parser.add_argument("--invalid-data-executable")
    options, rest = parser.parse_known_args()
    EXECUTABLE = options.executable
    INVALID_DATA_EXECUTABLE = options.invalid_data_executable
    unittest.main(argv=[sys.argv[0], *rest])
