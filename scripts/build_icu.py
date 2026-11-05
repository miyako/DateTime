#!/usr/bin/env python3
"""Build pinned ICU and filtered SOURCE data, on a matching native host only."""
import argparse
from collections import deque
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_ATTEMPTS = 4
WINDOWS_DATA_TOOLS = (
    "gencnval", "gencfu", "makeconv", "genbrk", "gensprep", "gendict", "icupkg", "genrb")


def retryable_download_error(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (408, 429) or 500 <= error.code <= 599
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, TimeoutError)
    return isinstance(error, TimeoutError)


def digest(path, algorithm="sha256"):
    h = hashlib.new(algorithm)
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def checked_directory(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("build path traverses a symlink")
    path.mkdir(parents=True, exist_ok=True)
    return path


def member_path(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
            or any(":" in part for part in path.parts)):
        raise ValueError("unsafe archive member: " + name)
    return path


def extract(archive, destination):
    """Reject all links/special entries; validate the entire archive before writing."""
    destination = checked_directory(destination)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            entries = source.infolist()
            for item in entries:
                member_path(item.filename)
                mode = item.external_attr >> 16
                if mode & 0o170000 not in (0, 0o040000, 0o100000):
                    raise ValueError("archive links/special entries are unsupported")
            for item in entries:
                output = destination.joinpath(*member_path(item.filename).parts)
                if item.is_dir():
                    checked_directory(output)
                else:
                    checked_directory(output.parent)
                    if output.exists():
                        raise ValueError("duplicate archive output: " + str(output))
                    with source.open(item) as inp, output.open("xb") as out:
                        shutil.copyfileobj(inp, out)
    else:
        with tarfile.open(archive) as source:
            entries = source.getmembers()
            for item in entries:
                member_path(item.name)
                if not (item.isfile() or item.isdir()):
                    raise ValueError("archive links/special entries are unsupported")
            for item in entries:
                output = destination.joinpath(*member_path(item.name).parts)
                if item.isdir():
                    checked_directory(output)
                else:
                    checked_directory(output.parent)
                    if output.exists():
                        raise ValueError("duplicate archive output: " + str(output))
                    with source.extractfile(item) as inp, output.open("xb") as out:
                        shutil.copyfileobj(inp, out)
                    output.chmod(item.mode & 0o777)


def download(spec, directory):
    directory = checked_directory(directory)
    if not spec["url"].startswith("https://"):
        raise ValueError("dependency URL must use HTTPS")
    output = directory / spec["url"].rsplit("/", 1)[1]
    if output.is_symlink():
        raise ValueError("dependency download must not be a symlink")
    if not output.exists():
        partial = output.with_suffix(output.suffix + ".part")
        if partial.is_symlink():
            raise ValueError("partial download is a symlink")
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            if partial.is_symlink():
                raise ValueError("partial download is a symlink")
            try:
                with urllib.request.urlopen(spec["url"], timeout=120) as inp, partial.open("wb") as out:
                    if not inp.url.startswith("https://"):
                        raise ValueError("dependency redirected away from HTTPS")
                    shutil.copyfileobj(inp, out)
                if digest(partial, "sha512") != spec["sha512"]:
                    raise ValueError("dependency SHA512 mismatch: " + output.name)
                partial.replace(output)
                break
            except (urllib.error.URLError, TimeoutError) as error:
                if not retryable_download_error(error):
                    raise
                reason = "HTTP " + str(error.code) if isinstance(error, urllib.error.HTTPError) else "socket timeout"
                if isinstance(error, urllib.error.HTTPError):
                    error.close()
                if attempt == DOWNLOAD_ATTEMPTS:
                    raise ValueError(f"ICU dependency download exhausted {attempt} attempts: {output.name}: {reason}") from error
                delay = 2 ** (attempt - 1)
                print(f"ICU dependency download retry {attempt}/{DOWNLOAD_ATTEMPTS - 1} "
                      f"for {output.name}: {reason}; waiting {delay}s", file=sys.stderr)
                time.sleep(delay)
            finally:
                partial.unlink(missing_ok=True)
    if digest(output, "sha512") != spec["sha512"]:
        raise ValueError("cached dependency SHA512 mismatch: " + output.name)
    return output


def native_target():
    system, machine = platform.system(), platform.machine().lower()
    if system == "Darwin" and machine in ("arm64", "x86_64"):
        translated = subprocess.run(["sysctl", "-in", "sysctl.proc_translated"],
                                    capture_output=True, text=True, check=False)
        if translated.stdout.strip() == "1":
            raise ValueError("Rosetta is not a native ICU build host")
        return "macos-arm64" if machine == "arm64" else "macos-x64"
    if system == "Windows" and machine in ("amd64", "x86_64"):
        if "arm" in os.environ.get("PROCESSOR_ARCHITEW6432", "").lower():
            raise ValueError("Windows ARM emulation is unsupported")
        return "windows-x64"
    raise ValueError("unsupported native ICU host: " + system + "/" + machine)


def run(command, cwd, env, log):
    log.write("$ " + subprocess.list2cmdline(list(map(str, command))) + "\n")
    log.flush()
    try:
        subprocess.run(list(map(str, command)), cwd=cwd, env=env, stdout=log,
                       stderr=subprocess.STDOUT, check=True, timeout=1800)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        log.flush()
        print("ICU build command failed; native log: " + str(log.name), file=sys.stderr)
        with Path(log.name).open(encoding="utf-8", errors="replace") as stream:
            tail = deque(stream, maxlen=80)
        print("".join(tail)[-20000:], file=sys.stderr, end="")
        raise


def windows_data_library(source, installation, generated, scratch, work, env, log):
    # ICU 77.1 pkgdata's static mode still emits a DLL export directive in its COFF object.
    run([source.parent / "bin64/pkgdata.exe", "-m", "common", "-p", "icudt77l",
         "-s", generated, "-d", scratch, "-T", scratch, scratch / "icudata.lst"],
        work, env, log)
    data = scratch / "icudt77l.dat"
    if not data.is_file() or data.is_symlink():
        raise ValueError("ICU source data packaging did not produce its common package")
    run([source.parent / "bin64/genccode.exe", "-o", "--skip-dll-export", "-e", "icudt77",
         "-c", "x64", "-f", "icudata", "-d", scratch, data], work, env, log)
    object_file = scratch / "icudata.obj"
    if not object_file.is_file() or object_file.is_symlink():
        raise ValueError("ICU genccode did not produce its non-exporting static data object")
    run(["lib.exe", "/NOLOGO", "/OUT:" + str(installation / "lib/icudt.lib"), object_file],
        work, env, log)


def windows_data_filter(scratch, filter_file):
    options = json.loads(filter_file.read_text(encoding="utf-8")) if filter_file else {}
    options["usePoolBundle"] = False
    path = scratch / "windows-data-filter.json"
    path.write_text(json.dumps(options, indent=2) + "\n", encoding="utf-8")
    return path


def windows_data_directory(work):
    # ICU looks for loose data inside its package-named directory, as makedata.mak does.
    return work / "generated" / "icudt77l"


def windows_data_command(source, generated, scratch, filter_file):
    command = [os.sys.executable, "-u", "-B", "-m", "icutools.databuilder",
               "--mode", "windows-exec", "--seqmode", "parallel", "--verbose",
               "--src_dir", source / "data", "--tool_dir", source / "tools",
               "--tool_cfg", "x64/Release", "--out_dir", generated, "--tmp_dir", scratch,
               "--filter_file", windows_data_filter(scratch, filter_file)]
    return command


def windows_build(source, installation, work, filter_file, env, log, jobs):
    """Native VS2022 bootstrap tools, source databuilder, then /MT static libraries."""
    # ICU's supported Windows data renderer forms unquoted shell command lines.
    if any(character.isspace() or character in '"&|<>^()%!' for character in str(source)):
        raise ValueError("ICU's Windows data builder requires a workspace path without spaces or shell metacharacters")
    for tool in ("MSBuild.exe", "cl.exe", "lib.exe"):
        if shutil.which(tool) is None:
            raise ValueError("run in a VS2022 x64 Native Tools environment: missing " + tool)
    # Bootstrap DLLs stay in the build tree; no bootstrap runtime is shipped.
    tools = ";".join(("common", "i18n", "genccode", "gencmn", "pkgdata", *WINDOWS_DATA_TOOLS))
    run(["MSBuild.exe", source / "allinone/allinone.sln", "/m:" + str(jobs),
         "/p:Configuration=Release", "/p:Platform=x64", "/p:DefaultPlatformToolset=v143",
         "/t:" + tools], source, env, log)
    for tool in WINDOWS_DATA_TOOLS:
        executable = source / "tools" / tool / "x64/Release" / (tool + ".exe")
        if not executable.is_file() or executable.is_symlink():
            raise ValueError("ICU native source-data tool is missing: " + str(executable))
    generated = checked_directory(windows_data_directory(work))
    scratch = checked_directory(work / "data-tmp")
    data_env = env.copy()
    data_env["PYTHONPATH"] = str(source / "python")
    data_env["PATH"] = str(source.parent / "bin64") + os.pathsep + env["PATH"]
    run(windows_data_command(source, generated, scratch, filter_file), source / "data", data_env, log)
    windows_data_library(source, installation, generated, scratch, work, data_env, log)
    # The global property overrides DLL project type; late import overrides /MD.
    props = work / "static.props"
    props.write_text(
        '<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">'
        '<ItemDefinitionGroup><ClCompile><RuntimeLibrary>MultiThreaded</RuntimeLibrary>'
        '<Optimization>MinSpace</Optimization><FunctionLevelLinking>true</FunctionLevelLinking>'
        '<PreprocessorDefinitions>U_STATIC_IMPLEMENTATION;%(PreprocessorDefinitions)</PreprocessorDefinitions>'
        '<AdditionalOptions>/Gw %(AdditionalOptions)</AdditionalOptions></ClCompile>'
        '<Lib><OutputFile>$(OutDir)$(TargetName).lib</OutputFile></Lib>'
        '</ItemDefinitionGroup></Project>', encoding="utf-8")
    for directory, name in (("common", "icuuc"), ("i18n", "icuin")):
        project = source / directory / (directory + ".vcxproj")
        namespace = "{http://schemas.microsoft.com/developer/msbuild/2003}"
        ET.register_namespace("", namespace[1:-1])
        tree = ET.parse(project)
        project_root = tree.getroot()
        targets = next(item for item in project_root
                       if item.tag == namespace + "Import"
                       and "Microsoft.Cpp.targets" in item.get("Project", ""))
        project_root.insert(list(project_root).index(targets),
                            ET.Element(namespace + "Import", {"Project": str(props)}))
        tree.write(project, encoding="utf-8", xml_declaration=True)
        run(["MSBuild.exe", project, "/t:Rebuild",
             "/m:" + str(jobs), "/p:Configuration=Release", "/p:Platform=x64",
             "/p:DefaultPlatformToolset=v143",
             "/p:ConfigurationType=StaticLibrary", "/p:BuildProjectReferences=false",
             "/p:TargetName=" + name, "/p:TargetExt=.lib",
             "/p:SolutionDir=" + str(source / "allinone") + "\\",
             "/p:OutDir=" + str(installation / "lib") + "\\",
             "/p:IntDir=" + str(work / ("obj-" + directory)) + "\\"], source, env, log)
    shutil.copytree(source / "common/unicode", installation / "include/unicode")
    shutil.copytree(source / "i18n/unicode", installation / "include/unicode", dirs_exist_ok=True)


@contextmanager
def build_lock(base):
    lock = base / "build.lock"
    if lock.is_symlink():
        raise ValueError("ICU build lock is a symlink")
    with lock.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def build(root=ROOT, target=None, profile="tailored", jobs=None):
    root = Path(root).resolve()
    target = target or native_target()
    if target != native_target() or profile not in ("tailored", "full"):
        raise ValueError("ICU builds require a matching native target and supported profile")
    base = checked_directory(root / "build/icu" / target / profile)
    with build_lock(base):
        return _build(root, target, profile, jobs)


def build_icu(root=ROOT, target=None, profile="tailored", jobs=None):
    """Named integration entry point; identical to build()."""
    return build(root, target, profile, jobs)


def _build(root, target, profile, jobs):
    root = Path(root).resolve()
    target = target or native_target()
    if target != native_target():
        raise ValueError("ICU cross mode is unsupported; use a matching native runner")
    if profile not in ("tailored", "full"):
        raise ValueError("unknown ICU profile")
    jobs = jobs or min(os.cpu_count() or 2, 8)
    pin = json.loads((root / "cli/icu-dependency.json").read_text(encoding="utf-8"))
    filter_file = root / "cli" / pin["filter"] if profile == "tailored" else None
    base = checked_directory(root / "build/icu" / target / profile)
    installation = base / "install"
    env = os.environ.copy()
    for variable in ("ICU_DATA", "ICU_DATA_FILTER_FILE", "ICU_DATA_BUILDTOOL_OPTS"):
        env.pop(variable, None)
    if target.startswith("macos-"):
        compiler = [env.get("CXX", "clang++"), "--version"]
    else:
        compiler = ["cl.exe"]
    tool = subprocess.run(compiler, capture_output=True, text=True, check=False)
    if target.startswith("macos-") and tool.returncode:
        raise ValueError("C++ compiler unavailable")
    compiler_version = (tool.stdout + tool.stderr).strip()
    flags = ("/MT /O1 /Gy /Gw" if target == "windows-x64" else
             "-Oz -DNDEBUG -ffunction-sections -fdata-sections -fvisibility=hidden")
    inputs = {str(p.relative_to(root)): digest(p) for p in
              (root / "cli/icu-dependency.json", root / "cli/icu-filter.json",
               root / "scripts/build_icu.py")}
    signature = {"schema": 1, "target": target, "profile": profile, "pins": pin,
                 "inputs": inputs, "compiler": compiler_version, "flags": flags,
                 "macosDeploymentTarget": "11.0",
                 "dataBuildOptions": {"seqmode": "parallel", "usePoolBundle": False}
                 if target == "windows-x64" else {"usePoolBundle": True}}
    marker = installation / "icu-build.json"
    if marker.is_file() and not marker.is_symlink():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if (previous.get("configuration") == signature and previous.get("files") and
                all((installation / p).is_file() and not (installation / p).is_symlink()
                    and digest(installation / p) == value for p, value in previous["files"].items())):
            return installation
    if base.is_symlink():
        raise ValueError("build directory must not be a symlink")
    for name in ("source", "data-source", "work", "install"):
        path = base / name
        if path.is_symlink():
            raise ValueError("build directory must not be a symlink")
        if path.exists():
            shutil.rmtree(path)
    checked_directory(installation / "lib")
    archives = checked_directory(root / "build/dependencies")
    source_archive = download(pin["source"], archives)
    data_archive = download(pin["data"], archives)
    extract(source_archive, base / "source")
    extract(data_archive, base / "data-source")
    source = base / "source/icu/source"
    candidates = list((base / "data-source").rglob("locales/root.txt"))
    if len(candidates) != 1:
        raise ValueError("source data archive has no unique locales/root.txt")
    data = candidates[0].parent.parent
    # The source-data release includes build rules; replace the entire old data tree.
    shutil.rmtree(source / "data")
    shutil.copytree(data, source / "data")
    if list((source / "data").rglob("*.dat")):
        raise ValueError("source-data archive unexpectedly contains a prebuilt .dat")
    zone_source = (source / "data/misc/zoneinfo64.txt").read_text(encoding="utf-8")
    if "tz version:  " + pin["tzdataVersion"] not in zone_source:
        raise ValueError("source timezone version differs from dependency pin")
    work = checked_directory(base / "work")
    with (base / "build.log").open("w", encoding="utf-8") as log:
        if target.startswith("macos-"):
            for variable in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
                env.pop(variable, None)
            env.update(CFLAGS=flags, CXXFLAGS=flags, MACOSX_DEPLOYMENT_TARGET="11.0",
                       ZERO_AR_DATE="1", SOURCE_DATE_EPOCH="0")
            if filter_file:
                env["ICU_DATA_FILTER_FILE"] = str(filter_file)
            configure = [source / "runConfigureICU", "MacOSX",
                         "--prefix=" + str(installation), "--disable-shared", "--enable-static",
                         "--with-data-packaging=static", "--disable-tests", "--disable-samples",
                         "--disable-extras", "--disable-icuio"]
            run(configure, work, env, log)
            run(["make", "-j" + str(jobs)], work, env, log)
            run(["make", "install"], work, env, log)
        else:
            windows_build(source, installation, work, filter_file, env, log, jobs)
    checked_directory(installation / "licenses")
    licenses = base / "source/icu/LICENSE"
    shutil.copyfile(licenses, installation / "licenses/ICU-LICENSE.txt")
    expected = ["icuin.lib", "icuuc.lib", "icudt.lib"] if target == "windows-x64" else [
        "libicui18n.a", "libicuuc.a", "libicudata.a"]
    for library in expected:
        if not (installation / "lib" / library).is_file():
            raise ValueError("ICU build omitted required static library: " + library)
    files = {str(p.relative_to(installation)): digest(p) for p in installation.rglob("*")
             if p.is_file() and not p.is_symlink()}
    marker.write_text(json.dumps({"configuration": signature, "files": files}, indent=2) + "\n",
                      encoding="utf-8")
    return installation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--target", choices=("macos-arm64", "macos-x64", "windows-x64"))
    parser.add_argument("--profile", choices=("tailored", "full"), default="tailored")
    parser.add_argument("--jobs", type=int)
    args = parser.parse_args()
    try:
        print(build(args.root, args.target, args.profile, args.jobs))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
