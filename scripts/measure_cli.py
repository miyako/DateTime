#!/usr/bin/env python3
"""Measure actual executable/ZIP bytes and reproducible native subprocess latency."""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time
import zipfile

from build_icu import ROOT, build, checked_directory, digest, native_target


def dependencies(executable, target):
    command = ["dumpbin.exe", "/dependents", str(executable)] if target == "windows-x64" else [
        "otool", "-L", str(executable)]
    output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    if target == "windows-x64":
        names = [line.strip() for line in output.splitlines() if line.strip().lower().endswith(".dll")]
        allowed = {"kernel32.dll", "advapi32.dll", "user32.dll", "shell32.dll",
                   "ole32.dll", "bcrypt.dll", "crypt32.dll", "ws2_32.dll", "ntdll.dll"}
        unexpected = [name for name in names if name.lower() not in allowed]
    else:
        names = [line.strip().split(" (", 1)[0] for line in output.splitlines()[1:]]
        unexpected = [name for name in names if not name.startswith(("/usr/lib/", "/System/Library/"))]
    if unexpected:
        raise ValueError("unexpected non-system runtime dependencies: " + repr(unexpected))
    return {"status": "passed", "command": command, "libraries": names, "raw": output}


def exports(executable, target):
    command = (["dumpbin.exe", "/exports", str(executable)] if target == "windows-x64"
               else ["dyld_info", "-exports", str(executable)])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    # The standalone program has no reason to export ICU's API or data symbols.
    import re
    if re.search(r"\b_?(?:ucal_|udata_|u_str|icudt77|_?ZN.*icu)", result.stdout):
        raise ValueError("ICU symbols are exported by the executable")
    return {"status": "passed", "command": command, "raw": result.stdout}


def checked_run(executable, args):
    completed = subprocess.run([str(executable), *args], capture_output=True, timeout=30, check=True)
    if completed.stderr:
        raise ValueError("unexpected stderr during measurement")
    value = json.loads(completed.stdout)
    if value.get("protocol") != 1:
        raise ValueError("invalid measured CLI protocol")
    if args[0] == "batch":
        if len(value.get("results", [])) != (len(args) - 1) // 3 or not all(
                item.get("ok") is True for item in value["results"]):
            raise ValueError("measurement batch failed")
    return value


def latency(executable, counts, samples, warmups):
    instant, zone = "2026-07-15T12:00:00.123Z", "Asia/Kathmandu"
    results = []
    single = ["convert", "--instant", instant, "--zone", zone]
    for count in counts:
        batch = ["batch", *(["--item", instant, zone] * count)]
        for _ in range(warmups):
            checked_run(executable, batch)
            checked_run(executable, single)
        timings = {"batch": [], "repeated_processes": []}
        for _ in range(samples):
            start = time.perf_counter_ns()
            checked_run(executable, batch)
            timings["batch"].append((time.perf_counter_ns() - start) / 1e6)
            start = time.perf_counter_ns()
            for _ in range(count):
                checked_run(executable, single)
            timings["repeated_processes"].append((time.perf_counter_ns() - start) / 1e6)
        row = {"items": count, "samples": samples, "warmups": warmups}
        for mode, values in timings.items():
            row[mode] = {"milliseconds": values, "median_ms": statistics.median(values),
                         "p95_ms": sorted(values)[math.ceil(len(values) * .95) - 1],
                         "processes_per_sample": 1 if mode == "batch" else count}
        results.append(row)
    return results


def measure(executable, target, output, counts, samples, warmups):
    executable = Path(executable).resolve(strict=True)
    archive = output / (executable.stem + "-executable.zip")
    item = zipfile.ZipInfo(executable.name, date_time=(1980, 1, 1, 0, 0, 0))
    item.create_system = 3
    item.external_attr = (0o100755 if target.startswith("macos-") else 0o100644) << 16
    item.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as stream:
        stream.writestr(item, executable.read_bytes(), compresslevel=9)
    architecture = subprocess.run(
        ["dumpbin.exe", "/headers", str(executable)] if target == "windows-x64" else [
            "lipo", "-archs", str(executable)], capture_output=True, text=True, check=True).stdout.strip()
    expected_arch = {"macos-arm64": "arm64", "macos-x64": "x86_64"}.get(target)
    if expected_arch and architecture != expected_arch:
        raise ValueError("measurement executable does not match native architecture")
    if target == "windows-x64" and "8664 machine (x64)" not in architecture:
        raise ValueError("measurement executable is not Windows x64")
    checked_run(executable, ["--version"])
    return {"status": "passed", "path": str(executable), "sha256": digest(executable),
            "installed_executable_bytes": executable.stat().st_size,
            "executable_zip_path": str(archive), "executable_zip_bytes": archive.stat().st_size,
            "executable_zip_sha256": digest(archive), "architecture": architecture,
            "runtime_dependencies": dependencies(executable, target),
            "icu_symbol_visibility": exports(executable, target),
            "latency": latency(executable, counts, samples, warmups)}


def comparison_binary(root, target, profile):
    installation = build(root, target, profile)
    directory = checked_directory(root / "build/measure" / target / profile / "cmake")
    command = ["cmake", "-S", str(root / "cli"), "-B", str(directory),
               "-DCMAKE_BUILD_TYPE=Release", "-DICU_ROOT=" + str(installation)]
    command += ["-A", "x64"] if target == "windows-x64" else [
        "-DCMAKE_OSX_ARCHITECTURES=" + ("arm64" if target == "macos-arm64" else "x86_64")]
    subprocess.run(command, check=True, timeout=120)
    subprocess.run(["cmake", "--build", str(directory), "--config", "Release"], check=True, timeout=300)
    subprocess.run(["ctest", "--test-dir", str(directory), "-C", "Release", "--output-on-failure"],
                   check=True, timeout=180)
    return directory / ("Release/datetime.exe" if target == "windows-x64" else "datetime")


def compiler_configuration(directory):
    keys = {"CMAKE_CXX_COMPILER", "CMAKE_CXX_FLAGS", "CMAKE_CXX_FLAGS_RELEASE",
            "CMAKE_OSX_ARCHITECTURES", "CMAKE_OSX_DEPLOYMENT_TARGET",
            "CMAKE_MSVC_RUNTIME_LIBRARY"}
    values = {}
    for line in (directory / "CMakeCache.txt").read_text(encoding="utf-8").splitlines():
        if "=" in line and ":" in line.split("=", 1)[0]:
            key = line.split(":", 1)[0]
            if key in keys:
                values[key] = line.split("=", 1)[1]
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--executable", type=Path, help="Actual installed executable, not an ICU archive")
    parser.add_argument("--compare", action="store_true", help="Build full/tailored with identical code/options")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--counts", type=int, nargs="+", default=[1, 16, 256])
    args = parser.parse_args()
    if not 1 <= args.samples <= 100 or not 0 <= args.warmups <= 100 or any(
            not 1 <= count <= 256 for count in args.counts):
        parser.error("bounded samples 1..100, warmups 0..100 and counts 1..256 required")
    if not args.compare and args.executable is None:
        parser.error("provide --executable and/or --compare")
    target = native_target()
    root = args.root.resolve()
    directory = checked_directory(root / "build/measure" / target)
    report = {"schema": 1, "target": target, "icuVersion": "77.1", "unicodeVersion": "16.0",
              "tzdataVersion": "2025a", "clock": "perf_counter_ns",
              "note": "Executable ZIP only, not component ZIP. Latency includes process startup. "
                      "Warm filesystem cache, alternating batch then repeated-process samples.",
              "code_inputs": {str(p.relative_to(root)): digest(p) for p in [
                  root / "cli/main.cpp", root / "cli/CMakeLists.txt"]}, "measurements": {},
              "other_native_platforms": {"status": "not_run", "reason": "matching native runtime unavailable"}}
    try:
        if args.executable:
            output = checked_directory(directory / "installed")
            report["measurements"]["installed"] = measure(
                args.executable, target, output, args.counts, args.samples, args.warmups)
        if args.compare:
            configurations = {}
            cli_configurations = {}
            for profile in ("tailored", "full"):
                executable = comparison_binary(root, target, profile)
                output = checked_directory(directory / profile)
                report["measurements"][profile] = measure(
                    executable, target, output, args.counts, args.samples, args.warmups)
                manifest = root / "build/icu" / target / profile / "install/icu-build.json"
                configurations[profile] = json.loads(manifest.read_text())["configuration"]
                cli_configurations[profile] = compiler_configuration(
                    root / "build/measure" / target / profile / "cmake")
            same = ("target", "pins", "inputs", "compiler", "flags", "macosDeploymentTarget")
            if any(configurations["tailored"][key] != configurations["full"][key] for key in same):
                raise ValueError("comparison ICU compiler/options/source pins differ")
            if cli_configurations["tailored"] != cli_configurations["full"]:
                raise ValueError("comparison CLI compiler/options/architecture differ")
            if any(digest(root / path) != value for path, value in report["code_inputs"].items()):
                raise ValueError("CLI source changed during comparison")
            report["comparison"] = {"status": "passed", "same_compiler_options_code_architecture": True,
                                    "configurations": configurations,
                                    "cli_configurations": cli_configurations}
        report["status"] = "passed"
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        path = directory / "measurement.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
