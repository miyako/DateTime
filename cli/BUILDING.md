# Pinned static ICU / native CLI

Build only on native macOS ARM64, native macOS x86_64, or native Windows x64.
Rosetta and foreign/cross builds are rejected. CMake and Python 3 are required.
The download manifests declare ICU **77.1**, Unicode data **16.0**, and worldwide
IANA timezone rules **2025a**. The SHA512 values come from the official release
`SHASUM512.txt`; both source archives are checked before safe extraction. The
release is a deliberate fixed snapshot, not the host's current tzdb.

```sh
python3 scripts/build_icu.py --target macos-arm64
cmake -S cli -B build/native-cli -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DICU_ROOT="$PWD/build/icu/macos-arm64/tailored/install"
cmake --build build/native-cli --config Release
ctest --test-dir build/native-cli --output-on-failure
python3 scripts/measure_cli.py --executable build/native-cli/datetime --compare
```

Use `macos-x64` and `x86_64` on Intel. `build_icu.build(root, target)` returns
the static installation directory for the repository build orchestrator;
the named `build_icu(root, target)` entry point is equivalent. CMake accepts
`ICU_ROOT` or the equivalent `DATETIME_ICU_ROOT` (conflicting values fail).
Its cache marker binds the script, filter, manifests, compiler, options, and
every installed library/header/license hash. Logs are under
`build/icu/<target>/<profile>/build.log`. Removing that profile rebuilds it.

## Native Windows runner setup (not locally executed)

Use a native Windows x64 runner with Visual Studio 2022 C++ build tools,
Windows SDK, Python 3 and CMake. Initialize its **x64 Native Tools** environment
(`VsDevCmd.bat -arch=x64 -host_arch=x64`); ensure `MSBuild.exe`, `cl.exe`,
`lib.exe` and `dumpbin.exe` are on PATH. Do not use MinGW, MSYS cross compilation,
prebuilt ICU or an ICU runtime installation.
The supported upstream Windows data renderer constructs unquoted build-time
shell commands: use a workspace path without whitespace or shell metacharacters,
such as GitHub Actions' `D:\a\DateTime\DateTime`. The builder fails explicitly
otherwise. This upstream build-time constraint does not affect the shipping
CLI's literal argument/executable-path quoting. The source builder explicitly
selects VS2022's `v143` toolset and its newest installed Windows SDK.

```powershell
python scripts/build_icu.py --target windows-x64
cmake -S cli -B build/native-cli -A x64 -DCMAKE_BUILD_TYPE=Release `
  "-DICU_ROOT=$PWD/build/icu/windows-x64/tailored/install"
cmake --build build/native-cli --config Release
ctest --test-dir build/native-cli -C Release --output-on-failure
python scripts/measure_cli.py --executable build/native-cli/Release/datetime.exe --compare
```

The Windows path first builds **source-derived build tools** in the build tree,
then runs ICU's supported `windows-exec` data builder with the same source
filter and packages static data. Finally it builds static common/i18n libraries
with `/MT`, `/O1`, `/Gy`, `/Gw`; the CLI also uses `/MT` and `/OPT:REF /OPT:ICF`.
Bootstrap DLLs are build-only and must never be copied into component resources.
Final native dependency inspection rejects ICU DLLs and non-system CRT DLLs.
This setup is implemented but remains **not_run** until an actual Windows runner
passes the source build, protocol tests and dependency inspection.
The native CI job initializes this VS2022 x64 environment itself, builds before
running Python contract tests, and supplies `DATETIME_TEST_EXECUTABLE` so those
tests reuse the same executable. Artifact upload excludes CMake intermediates
and the invalid-data test executable; it retains the real CLI and build manifest.

## Data and runtime constraints

The source-data ZIP completely replaces the release's data directory, including
its build rules; any unexpected prebuilt `.dat` input is rejected. The additive
`icu-filter.json` omits collation, transliteration, break iteration, legacy
conversion mappings and localized display/formatting trees. It retains global
`zoneinfo64`, canonical timezone types, meta-zone/Windows mappings, Gregorian
supplemental dependencies and minimal root/English Gregorian resources.
It does **not** slice worldwide timezone IDs/rules by locale or country.

Both profiles compile identical ICU source code and options. Only data filtering
differs; `full` is the default upstream source-data build, including all feature
data, not a prebuilt full `.dat` override. No whole-archive linking is used.
Explicit references pull in static data; ICU symbols are hidden and unused
sections are dead-stripped. At runtime the CLI calls `udata_setFileAccess` with
`UDATA_NO_FILES`, registers embedded common data and verifies its pinned
ICU/tzdb versions. Named-zone validation requires ICU's canonical **system ID**
flag; unknown IDs and custom GMT offsets cannot fall back to GMT.
The requested zone must also match an enumerated system-zone ID byte-for-byte,
so capitalization is strict even if an ICU canonicalization path is permissive.

The calendar is explicitly Gregorian with a proleptic cutover. Strict parsing
and civil arithmetic do not use libc time, locale or the host timezone.
Historical offset seconds are preserved in `offsetSeconds` and local `±HH:MM:SS`.
No stdin, shell, JSON-parser dependency, external data or runtime download is used.
The separate `datetime-invalid-data` executable injects an invalid embedded data
header for fail-closed native tests; it is never a shipping executable/resource.

## Protocol

All commands emit one UTF-8 JSON object plus LF. Success stderr is empty.

* `--version`: `{protocol:1,version:"0.1.0"}`.
* `--info`: `{protocol:1,version:"0.1.0",icuVersion:"77.1",dataVersion:"77.1",
  unicodeVersion:"16.0",tzdataVersion:"2025a",dataMode:"embedded-static"}`.
  `dataVersion` identifies the integrity-pinned source-data release; ICU, Unicode
  and tzdb versions are verified/read through ICU APIs. Conversion values do not
  contain these provenance fields.
* `convert --instant TEXT --zone TEXT`: fields in order
  `protocol,instant,zone,local,offsetSeconds`.
  `instant` is canonical UTC `.SSSZ`, `zone` preserves the requested system ID,
  and `local` always has `.SSS` and an explicit offset.
* `batch --item INSTANT ZONE ...`: 1–256 positional pairs; ordered
  `{protocol:1,results:[{ok:true,value:CONVERSION},{ok:false,error:{code,message}}]}`.
  Item conversion values omit `protocol`. Structurally valid mixed batches exit 0.
* `inspect-args TEXT ...`: 0–256 literal arguments; `{protocol:1,args:[...]}`.
  This bounded diagnostic supports real generic File/Folder/argument round trips.

Every incoming argument must be strict UTF-8; aggregate command argument bytes
must be at most 1048576. Instant inputs have a semantic bound of **35 ASCII
characters** and zone inputs **64 ASCII bytes**, checked before parsing/ICU
lookup. Oversized/non-ASCII instant or zone fields yield per-item
`invalid_instant` or `invalid_zone` in a structurally valid batch, not a batch
invocation error. `inspect-args` permits at most 256 Text arguments, each at most
4096 UTF-8 bytes and at most **8192 aggregate Text argument bytes**, excluding
the command name. Malformed UTF-8 or structural invocations exit 2,
with empty stdout and `{protocol:1,error:{code,message}}` on stderr.
Input timestamps require years 0001–9999, uppercase `T`/`Z`, exact Gregorian
dates, explicit `Z` or `±HH:MM` (up to 23:59), zero to three fractional digits,
no leap second, and no RFC3339 unknown `-00:00` offset. UTC and local output must
also stay in years 0001–9999. These fixed errors are shared by single/batch items:
Year `0000` or more than four year digits is `invalid_instant`; `out_of_range`
is reserved for an otherwise valid instant whose UTC/local conversion spills
outside years 0001–9999.

Windows CreateProcess has a roughly 32767-UTF-16-unit command-line ceiling.
The caller must reject oversized transport Text **before launch**, including
the executable path, separators and the fully quoted arguments. Quoting can
double backslashes/quotes in malformed fields even when each fits its semantic
bound: raw length × count alone is not a safe upper bound. A conservative
fully quoted command-line budget of 30000 UTF-16 units leaves headroom.
The 4D wrapper reports such prelaunch structural bounds as `invalid_argument`;
valid-size semantic errors remain native per-item batch results.

| Code | Message |
| --- | --- |
| invalid_arguments | invalid command arguments |
| invalid_instant | invalid RFC3339 instant |
| invalid_zone | unknown IANA time zone |
| out_of_range | date outside years 0001-9999 |
| icu_error | ICU conversion failed |

## Footprint / performance evidence

`measure_cli.py --compare` builds full/tailored binaries with identical code,
compiler, architecture and options and runs native tests against each binary.
It reports actual executable bytes and reproducible executable ZIP bytes
**separately**, with SHA256, source/data versions, architecture, dependency
inspection and raw latency samples. Neither ICU static archive size nor a
compressed `.dat` size is an installed-executable footprint.

Default samples: five measurements per 1/16/256 items, two warmup rounds,
one batch process versus N separate convert processes. Startup is included;
cache is warm, and sample order alternates batch then repeated processes.
Use `--samples`, `--warmups`, `--counts` to reproduce a bounded experiment.
Results are in `build/measure/<native-target>/measurement.json`; these are native
CLI evidence only, not 4D host/package acceptance or release readiness.

## Redistribution

The complete upstream ICU `LICENSE`, including Unicode-3.0 and applicable
third-party notices, is retained as `install/licenses/ICU-LICENSE.txt`.
Copy that complete file into the component's redistribution notices when
assembling resources; the source/CLI MIT license must also be retained.
Unicode timezone data derives from IANA's public-domain tz database; ICU's
compiled resource/data distribution is covered by its bundled notices.
macOS uses only system libc++/system libraries. Windows static CRT redistribution
is subject to Visual Studio's redistribution terms; the source build does not
grant a license to redistribute Microsoft tools or bootstrap runtimes.
