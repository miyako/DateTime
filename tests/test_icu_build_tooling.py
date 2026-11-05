"""Offline failure-injection tests; no native build or download is claimed here."""
import contextlib
import hashlib
import io
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_icu
import measure_cli


class Response(io.BytesIO):
    url = "https://example.invalid/source.tar.gz"


class DownloadTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "build/tooling-test-fixtures"
        base.mkdir(parents=True, exist_ok=True)
        scratch = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)
        self.body = b"integrity-checked test fixture"
        self.spec = {"url": Response.url, "sha512": hashlib.sha512(self.body).hexdigest()}

    def test_transient_http_statuses_retry_same_pin(self):
        for code in (408, 429, 500, 504, 599):
            with self.subTest(code=code):
                with patch.object(build_icu.urllib.request, "urlopen", side_effect=[
                    urllib.error.HTTPError(self.spec["url"], code, "transient", {}, None),
                    Response(self.body),
                ]) as opening, patch.object(build_icu.time, "sleep") as sleeping, \
                        contextlib.redirect_stderr(io.StringIO()) as diagnostics:
                    output = build_icu.download(self.spec, self.directory)
                self.assertEqual(output.read_bytes(), self.body)
                self.assertEqual(opening.call_count, 2)
                self.assertTrue(all(call.args == (self.spec["url"],)
                                    and call.kwargs == {"timeout": 120} for call in opening.call_args_list))
                sleeping.assert_called_once_with(1)
                self.assertIn("HTTP " + str(code), diagnostics.getvalue())
                self.assertFalse(output.with_suffix(".gz.part").exists())
                output.unlink()

    def test_socket_timeout_and_wrapped_timeout_retry(self):
        for error in (socket.timeout("timed out"), urllib.error.URLError(socket.timeout("timed out"))):
            with self.subTest(error=type(error).__name__):
                with patch.object(build_icu.urllib.request, "urlopen",
                                  side_effect=[error, Response(self.body)]) as opening, \
                        patch.object(build_icu.time, "sleep"), contextlib.redirect_stderr(io.StringIO()):
                    output = build_icu.download(self.spec, self.directory)
                self.assertEqual(opening.call_count, 2)
                self.assertEqual(output.read_bytes(), self.body)
                output.unlink()

    def test_read_timeout_removes_partial_before_retry(self):
        class PartialTimeout(Response):
            def read(self, size=-1):
                if self.tell() == 0:
                    return super().read(3)
                raise socket.timeout("read timed out")
        with patch.object(build_icu.urllib.request, "urlopen", side_effect=[
            PartialTimeout(b"unfinished"), Response(self.body),
        ]) as opening, patch.object(build_icu.time, "sleep"), contextlib.redirect_stderr(io.StringIO()):
            output = build_icu.download(self.spec, self.directory)
        self.assertEqual(opening.call_count, 2)
        self.assertEqual(output.read_bytes(), self.body)
        self.assertFalse(output.with_suffix(".gz.part").exists())

    def test_exhaustion_is_bounded_and_cleans_partial(self):
        with patch.object(build_icu.urllib.request, "urlopen", side_effect=socket.timeout("timeout")) as opening, \
                patch.object(build_icu.time, "sleep") as sleeping, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "exhausted 4 attempts"):
                build_icu.download(self.spec, self.directory)
        self.assertEqual(opening.call_count, 4)
        self.assertEqual([call.args[0] for call in sleeping.call_args_list], [1, 2, 4])
        self.assertFalse(list(self.directory.glob("*.part")))

    def test_nontransient_errors_are_not_retried(self):
        for error in (urllib.error.HTTPError(self.spec["url"], 404, "missing", {}, None),
                      urllib.error.HTTPError(self.spec["url"], 403, "forbidden", {}, None),
                      urllib.error.URLError("DNS failure")):
            with self.subTest(error=str(error)):
                with patch.object(build_icu.urllib.request, "urlopen", side_effect=error) as opening, \
                        patch.object(build_icu.time, "sleep") as sleeping:
                    with self.assertRaises(type(error)):
                        build_icu.download(self.spec, self.directory)
                self.assertEqual(opening.call_count, 1)
                sleeping.assert_not_called()

    def test_hash_mismatch_is_not_retried(self):
        with patch.object(build_icu.urllib.request, "urlopen", return_value=Response(b"wrong")) as opening, \
                patch.object(build_icu.time, "sleep") as sleeping:
            with self.assertRaisesRegex(ValueError, "SHA512 mismatch"):
                build_icu.download(self.spec, self.directory)
        self.assertEqual(opening.call_count, 1)
        sleeping.assert_not_called()
        self.assertFalse(list(self.directory.glob("*.part")))

    def test_cached_hash_mismatch_never_downloads(self):
        (self.directory / "source.tar.gz").write_bytes(b"wrong cached content")
        with patch.object(build_icu.urllib.request, "urlopen") as opening:
            with self.assertRaisesRegex(ValueError, "cached dependency SHA512 mismatch"):
                build_icu.download(self.spec, self.directory)
        opening.assert_not_called()

    def test_redirect_safety_failure_is_not_retried(self):
        response = Response(self.body)
        response.url = "http://example.invalid/unsafe"
        with patch.object(build_icu.urllib.request, "urlopen", return_value=response) as opening, \
                patch.object(build_icu.time, "sleep") as sleeping:
            with self.assertRaisesRegex(ValueError, "redirected away from HTTPS"):
                build_icu.download(self.spec, self.directory)
        self.assertEqual(opening.call_count, 1)
        sleeping.assert_not_called()
        self.assertFalse(list(self.directory.glob("*.part")))


class WindowsDataWiringTests(unittest.TestCase):
    def test_supported_nonexporting_object_generator_and_static_librarian(self):
        source, install, generated, scratch, work = map(Path, (
            "fixture/icu/source", "fixture/install", "fixture/generated", "fixture/scratch", "fixture/work"))
        with patch.object(build_icu, "run") as run, patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "is_symlink", return_value=False):
            build_icu.windows_data_library(source, install, generated, scratch, work, {}, io.StringIO())
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][1:3], ["-m", "common"])
        self.assertIn(scratch / "icudata.lst", commands[0])
        self.assertIn("--skip-dll-export", commands[1])
        self.assertIn(scratch / "icudt77l.dat", commands[1])
        self.assertIn("icudt77", commands[1])
        self.assertIn("x64", commands[1])
        self.assertEqual(commands[2], [
            "lib.exe", "/NOLOGO", "/OUT:" + str(install / "lib/icudt.lib"), scratch / "icudata.obj"])


class ExportInspectionTests(unittest.TestCase):
    def test_real_export_table_symbols_fail_with_names(self):
        names = ("icudt77_dat", "ucal_open_77", "u_getVersion_77",
                 "?createInstance@Calendar@icu_77@@SAPAXXZ")
        output = "ordinal hint RVA      name\n" + "\n".join(
            f" {i} {i:X} 00102030 {name}" for i, name in enumerate(names, 1))
        self.assertEqual(measure_cli.exported_icu_symbols(output, "windows-x64"), list(names))
        with patch.object(measure_cli.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["dumpbin.exe"], 0, output, "")):
            with self.assertRaisesRegex(ValueError, "icudt77_dat"):
                measure_cli.exports(Path("fixture.exe"), "windows-x64")

    def test_banner_or_filename_is_not_an_export_symbol(self):
        output = ("Dump of file D:\\icu\\icudt77_data\\datetime.exe\n"
                  "File Type: EXECUTABLE IMAGE\nSummary\n1000 .rdata\n")
        self.assertEqual(measure_cli.exported_icu_symbols(output, "windows-x64"), [])
        with patch.object(measure_cli.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["dumpbin.exe"], 0, output, "")):
            self.assertEqual(measure_cli.exports(Path("fixture.exe"), "windows-x64")["status"], "passed")

    def test_macos_c_and_cpp_exports_remain_rejected(self):
        names = ["_ucal_open_77", "_u_getVersion_77", "__ZN6icu_778CalendarC1Ev"]
        output = "offset symbol\n" + "\n".join(f"0x00001000 {name}" for name in names)
        self.assertEqual(measure_cli.exported_icu_symbols(output, "macos-arm64"), names)

    def test_missing_inspection_output_fails_closed(self):
        with patch.object(measure_cli.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["dumpbin.exe"], 0, "", "")):
            with self.assertRaisesRegex(ValueError, "no output"):
                measure_cli.exports(Path("fixture.exe"), "windows-x64")


if __name__ == "__main__":
    unittest.main()
