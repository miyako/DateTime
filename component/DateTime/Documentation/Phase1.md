# Phase 1 measurements and acceptance

## Dependency and implementation boundary

C++17, ICU **77.1**, Unicode **16.0**, timezone data **2025a**. Source and source
data archives are independently pinned by SHA512 in `cli/icu-dependency.json`,
verified against upstream `SHASUM512.txt`. The build discards the prebuilt data
directory and rebuilds from verified source data using ICU's supported filter.
The bundled `ICU-LICENSE.txt` retains the upstream Unicode-3.0 license and
third-party notices. Tool4d is not bundled.

Tailored data keeps the worldwide zone rules and supporting resources:
`res_index`, `en`, `en_US`, `en_US_POSIX`, `keyTypeData`, `metaZones`, `pool`,
`root`, `supplementalData`, `timezoneTypes`, `windowsZones`, `zoneinfo64`.
It removes irrelevant collation, transliteration, legacy conversion and other
data. Code and data are statically linked without whole-archive retention.
ICU symbols are hidden; macOS executable export inspection found no exports.
Explicit common-data registration and `UDATA_NO_FILES` prevent external ICU
data lookup. Empty `ICU_DATA` directories and different host `TZ` values were
tested, as was injected invalid embedded data (an explicit failure, not fallback).

Apple system libc++ and libSystem remain dynamic system dependencies, not
bundled ICU. Windows uses native VS2022 x64 and static CRT; native DLL inspection
is an independent mandatory build gate. No native Windows ARM64 support.

## Actual ARM64 full/static versus tailored/static comparison

Local macOS ARM64, Apple clang **21.0.0 (clang-2100.1.1.101)**, deployment target
11.0. Both profiles use identical CLI source, compiler, architecture and
optimization settings; only the ICU data filter differs. ICU options include
`-Oz -DNDEBUG -ffunction-sections -fdata-sections -fvisibility=hidden`; the CLI
uses CMake Release settings and its common size/dead-strip options.
Both profiles pass the same feature/error tests.

| ARM64 static profile | Installed executable bytes | Executable ZIP bytes |
| --- | ---: | ---: |
| Full/unfiltered | 32,943,496 | 12,881,653 |
| Tailored | 1,306,504 | 485,303 |

These are actual linked executable sizes, **not static-archive sizes**.
Executable ZIP sizes are reproducible DEFLATE-level-9 ZIPs containing only the
executable with fixed ZIP metadata. They are **not** `DateTime.zip` compiled
component sizes. Universal and Windows sizes must be measured from their actual
artifacts, not extrapolated by doubling ARM64.

Measured code inputs:

```text
cli/main.cpp SHA256:
48000df6384ab4f71fb35d20390679c5c837fe99e3f49d03bbba65d78fa6e56e
cli/CMakeLists.txt SHA256:
90fc7c64baf6940e1d74622ec0d9faabebaf9161c5f56eb1040a8f330d7dc437
tailored executable SHA256:
d6ad92e6b8fccfbb8429ea1e6f8c7c4f3695452370910705b4324d191cb303cb
full executable SHA256:
33854861873bc3d16a56e2a8c72c5549fa06132fa36f52170324387e4c549dd1
```

`otool -L` reports only `/usr/lib/libc++.1.dylib` and
`/usr/lib/libSystem.B.dylib` for both profiles. No external or system ICU link.
`dyld_info -exports` reports no exported ICU symbols.

## Process overhead, not an in-process library benchmark

Reproduce on a native supported host:

```sh
python3 scripts/measure_cli.py --root . \
  --executable build/macos-arm64/datetime --compare \
  --samples 5 --warmups 2 --counts 1 16 256
```

Raw report: `build/measure/macos-arm64/measurement.json`. Each size/timing result
binds the executable hash and inspected dependencies. Measurements use Python
`perf_counter_ns`, warmed filesystem caches, two warmups and five measured
samples for each item count/profile. A batch sample starts one process; a
repeated-call sample starts N processes sequentially. Samples alternate batch
then repeated calls and validate every response. Timing includes launch,
conversion, JSON and capture overhead; it is not isolated ICU algorithm latency.
The reported empirical p95 with five samples is the sample maximum, not a
population estimate. Cold-launch and 4D SystemWorker timings are not measured.

| Tailored ARM64 items | Batch median ms | Repeated-process median ms | Processes per sample |
| --- | ---: | ---: | --- |
| 1 | 14.933 | 10.525 | 1 versus 1 |
| 16 | 11.441 | 186.528 | 1 versus 16 |
| 256 | 26.644 | 2,762.486 | 1 versus 256 |

These are observations on this machine and sample, not throughput/latency
guarantees or a promised size threshold. Data legislation updates require a
deliberate repin and full retest. Phase 1 does not implement civil-to-instant DST
resolution, business calendars, recurrence or the broader future library.

## Evidence gates

Native CTest covers strict protocol/precision/calendar/range validation,
zone case sensitivity, unknown/custom zones, Kathmandu, Paris/New York
winter/summer and DST transitions, historical second offsets, ordered mixed
batches, max-size batches, malformed UTF-8 and embedded-data failure.
Independent Python CLI vectors and the actual namespaced 4D host cover these
alongside File/Folder/renamed executable/Unicode/quote round trips, unsupported
scalar arguments, leading/middle/trailing NUL and malformed response rejection.

Native platform jobs, source host, real compilation, packaging and the exact
unpacked compiled host are separate checks. Successful CLI CI is not Windows
4D acceptance. Signing, dependency-manager release download and native foreign
4D hosts must remain `not_run` without execution evidence. Local unsigned
packaging alone is not full cross-platform release readiness.
