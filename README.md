# DateTime

Phase 1: a **real 4D component**, not a native 4D plug-in, wrapping a standalone
C++ CLI with statically linked ICU and embedded tailored data. Convert strict
offset-bearing RFC3339 instants to explicitly selected IANA zones, individually
or in bounded ordered batches. The host requires neither an installed plug-in
nor external ICU, data files, runtime downloads or network services.

Identity is case-sensitive: repository/package/project/catalog/release basename
`DateTime`, 4D namespace `DateTime`, public class `DateTime`, CLI `datetime`
(`datetime.exe` on Windows). Host API: `cs.DateTime.DateTime`.

Read [AGENTS.md](AGENTS.md) for the retained template's build/acceptance gates and
[the component skill](.github/skills/4d-cli-component/SKILL.md). The repository is
initialized for **C++ only**. Do not run initialization again with other inputs.

## Phase 1 API

[DateTime class contract](component/DateTime/Documentation/Classes/DateTime.md)
defines the complete input/output, range, precision and batch error semantics.
Generic infrastructure remains separate:
[_CLI](component/DateTime/Documentation/Classes/_CLI.md) resolves only bundled
resources; [_Process](component/DateTime/Documentation/Classes/_Process.md)
quotes literal arguments into SystemWorker command-line Text, closes stdin and
preserves stdout/stderr/exit status with a 30-second timeout.

```4d
var $tool : cs.DateTime.DateTime
var $target : Text
$target:="macOS-universal"
If (Is Windows)
  $target:="Windows-x64"
End if
$tool:=cs.DateTime.DateTime.new($target)
$result:=$tool.convert("2026-01-15T12:00:00.123Z"; "Asia/Kathmandu")
// $result.value.local = "2026-01-15T17:45:00.123+05:45"
$batch:=$tool.convertBatch(New collection(\
  New object("instant"; "2026-07-15T12:00:00Z"; "zone"; "Europe/Paris"); \
  New object("instant"; "bad"; "zone"; "America/New_York")))
// A valid batch returns ordered successes/errors; inspect each item's ok.
```

Strict proleptic Gregorian years 0001–9999, uppercase `T`/`Z`, explicit `Z` or
numeric offset, valid dates, no leap seconds or unknown `-00:00` offset.
Zero to three fractional digits are accepted and padded to milliseconds;
higher precision errors, never silently truncates. UTC and local output must
stay in range. Named-zone validation rejects unknown/custom zones instead of
ICU's GMT fallback. Historical second-resolution offsets remain exact.
No host-zone/locale guessing. Batch size 1–256, ordered per-item semantic errors.

## CLI protocol 1

UTF-8, exactly one JSON line plus LF. `--version` returns
`{"protocol":1,"version":"0.1.1"}`. `convert --instant TEXT --zone TEXT` returns:

```json
{"protocol":1,"instant":"2026-01-15T12:00:00.123Z","zone":"Asia/Kathmandu","local":"2026-01-15T17:45:00.123+05:45","offsetSeconds":20700}
```

`batch --item INSTANT ZONE [--item INSTANT ZONE ...]` returns
`{"protocol":1,"results":[{"ok":true,"value":{...}},{"ok":false,"error":{"code":"...","message":"..."}}]}`.
Nested successful values omit `protocol`. Valid batches exit zero even when
items fail. Malformed invocation and single semantic errors exit 2, empty
stdout and JSON diagnostic stderr. ICU initialization/system errors fail
explicitly. `inspect-args TEXT...` is a bounded diagnostic for real native
File/Folder/Unicode/quote round trips; it is not a date conversion API.

## Build and acceptance

Requirements: Python 3.10+, CMake, C++17 compiler and selected native ICU build
tools. macOS targets deploy to 11+, universal x86_64 + ARM64; Windows x64 uses
static CRT. Windows ARM64 and C++ cross mode are unsupported. ICU is built from
pinned, checksum-verified source/data with a supported source-data filter;
static archives alone do not reduce its data blob. No system ICU is linked.

```sh
python3 scripts/doctor.py --root .
python3 scripts/build_cli.py --root . --target macos-arm64
python3 -m unittest discover -s tests -p test_cli.py -v
TEST_CLI_LANGUAGE=none python3 -m unittest discover -s tests -v
python3 scripts/generate_icons.py --check
python3 scripts/ci_config.py download-tool4d --root .
```

Use the actual native target (`macos-arm64`, `macos-x64`, `windows-x64`).
Each native artifact has a manifest binding current CLI inputs, commit and
binary hash. Resource assembly requires all three same-input artifacts:

```sh
python3 scripts/ci_config.py assemble --root .
python3 scripts/test_4d.py --root . --tool4d .tools/tool4d.app/Contents/MacOS/tool4d
python3 scripts/compile_component.py --root . --tool4d .tools/tool4d.app/Contents/MacOS/tool4d
python3 scripts/package_component.py --root . --repository miyako/DateTime \
  --tag v0.1.0 --unpack-test
python3 scripts/test_4d.py --root . --tool4d .tools/tool4d.app/Contents/MacOS/tool4d \
  --component build/release-test/DateTime.4dbase
```

`--tag` validates metadata, **does not publish/create a tag**. Packaging requires
actual fresh compilation, native resources, source stripping, explicit ZIP
directory records and final real unpacked compiled-host loading. Missing
platform artifacts block a distributable ZIP; no synthetic substitute is used.
The free pinned tool4d 20.7 build 101905 is ARM64 macOS-only. Its fixed original
vendor URL may redirect only through exact HTTPS `resources-download.4d.com`
and `builds2.4d.com` origins, bounded to five redirects; the original SHA256 and
safe extraction checks are mandatory. Unsigned local acceptance needs no
activation/signing secrets.

Full/static versus tailored/static installed and compressed executable
measurements, reproducible batch/process timing and actual per-platform
acceptance evidence belong to the Phase 1 evidence, not speculative size or
speed promises. A per-executable ZIP is **not** a compiled component ZIP.
Cross-built or translated execution is not native platform acceptance.
Partial local acceptance is **not release ready**.
See [Phase 1 measurements](component/DateTime/Documentation/Phase1.md) for actual
source/data pins, installed bytes, executable ZIP bytes and timing methodology.
CI's production native jobs retain mandatory protocol, dependency and
private-symbol checks. Full/unfiltered comparisons run in separate optional
baseline jobs; a baseline failure is recorded as incomplete measurement evidence,
not a production success or a reason to bypass any package/host gate.

## CI and Publish

The two Actions workflows are **CI** and **Publish**, following
[miyako/4d-component-template#4](https://github.com/miyako/4d-component-template/pull/4).
These workflow changes must be merged to the default branch before the new
Publish button is available. CI can run on a selected development branch;
Publish runs on **main** only. Do not precreate a release tag.

Set the repository Actions **variable** `FOURD_ENABLED=true` to enable hosted
4D acceptance. With it enabled, **CI → Run workflow** builds native resources,
runs source and compiled-host acceptance, and retains **compiled-component**
with `DateTime.zip` and adjacent acceptance evidence for 30 days. This is
inspection without publication; it does not create a tag or GitHub release.
Diagnostics are retained separately, including when a host check fails.

For an authorized release, open **Publish → Run workflow**, select **main**,
and choose **mode**: **patch** (default), **minor**, or **major**. No tag entry
is needed. The workflow bumps from the highest stable version tag, or from the
configured version when none exists; for example, an initial configured
`0.1.0` becomes `0.1.1` with patch. It synchronizes the component/CLI version,
contracts and current examples, commits only registered version files to main,
then builds and accepts that exact commit. Branch protection must permit that
non-force bot commit; no personal token or protection bypass is used.

Only after final package/host and transferred-asset verification does Publish
create the new tag and release using GitHub's built-in token. It checks remote
tag/release collisions before the version commit and again before publication.
A failed build leaves its version commit, not a release tag. A same-mode retry
can reuse that candidate; an existing tag or release is never overwritten.

Signing is controlled by version policy, not by a Publish checkbox. Unsigned
publication is the default; list the next exact version in
`release-policy.json.apple_notarized_versions` to require signed/notarized macOS
assets. The signed path signs an isolated compiled component, notarizes
and staples a DMG, repacks `DateTime.zip` from the final mounted DMG, and reruns
the exact final ZIP's compiled-host checks. Missing credentials or failed
signing never fall back to unsigned. See [AGENTS.md](AGENTS.md#8-icons-licensing-and-optional-signing)
for the seven Apple secret requirements; the local six-secret provisioning
helper does not supply `CODESIGN_APPLE_ID`. Windows Authenticode is not included.
Adding secrets alone neither enables signing nor authorizes a release.

## Install and deployment

After an authorized future release, add GitHub dependency `miyako/DateTime`
in the 4D Dependency manager; the released asset must be exactly `DateTime.zip`.
Manual installation places unpacked `DateTime.4dbase` in host `Components/`.
Source development dependencies point at the component package root, not its
project anchor. Dependency-manager download/load is separate evidence from
manual local testing. This phase does not authorize publication, tags or signing.

## Deliberately deferred

Local-to-instant DST resolution, holidays/business calendars, civil/calendar
period/elapsed-duration abstractions, interval boundaries, recurrence,
multi-calendar/locale formatting and clock injection are future phases. This
phase adds no speculative API scaffolding for them.

The template's MIT license is retained. Bundled ICU/data/runtime notices must
ship in `component/DateTime/Resources/licenses/`; tool4d is never bundled.
