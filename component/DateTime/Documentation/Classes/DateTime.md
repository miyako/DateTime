# DateTime

`cs.DateTime.DateTime.new(target : Text)` wraps the bundled C++ executable
`datetime` with embedded static ICU. No 4D plug-in or external ICU installation
is required. Internal class identity is `cs.DateTime`; host namespace is
`DateTime`. On macOS omit `target` or pass `"macOS-universal"`; Windows requires
explicit `"Windows-x64"`. Windows ARM64 is unsupported.

| Method | Contract |
| --- | --- |
| `convert(instant : Variant; zone : Variant) : Object` | Both inputs must be Text; convert an explicit instant to a named IANA zone |
| `convertBatch(items : Variant) : Object` | Collection of 1–256 Objects with exactly Text `instant` and `zone` fields |
| `version() : Object` | Component CLI version, protocol 1 |

## Instant and zone semantics

Accept exactly `YYYY-MM-DDTHH:MM:SS[.f[f[f]]](Z|+HH:MM|-HH:MM)`.
Year 0001–9999, proleptic Gregorian calendar, valid calendar dates, hours 00–23,
minutes and seconds 00–59, numeric offsets up to 23:59. Uppercase `T`/`Z`,
mandatory offset, no whitespace, no leap seconds, no `-00:00` unknown offset,
no locale/date guessing. More than three fractional digits is an error, never
truncated. Missing fractional digits are padded; all outputs use exactly three
millisecond digits. UTC and converted local years must also remain 0001–9999.

Zone IDs are exact, case-sensitive ICU system/IANA identifiers and accepted
aliases in the pinned dataset, not custom `GMT+...` offsets. Unknown zones fail
rather than falling back to GMT. The caller's/host's default zone is never used.
Historical offsets with second precision are preserved as `+HH:MM:SS`; modern
minute-aligned offsets use `+HH:MM`. Future rules reflect the pinned data, not a
promise that legislatures will retain them. Forward conversion from an instant
is unambiguous; civil-to-instant DST gap/fold resolution is deferred.

## Results and errors

Each call is synchronous, closes stdin immediately and has the generic finite
30-second process timeout. Calls have no shared mutable worker/PID state.

Success preserves `{ok:true,stdout:Text,stderr:Text,exitCode:0,value:Object}`.
A single conversion's `value` is:

```json
{"protocol":1,"instant":"2026-01-15T12:00:00.123Z","zone":"Asia/Kathmandu","local":"2026-01-15T17:45:00.123+05:45","offsetSeconds":20700}
```

A structurally valid batch exits zero even when some/all items are invalid.
Its `value` is `{protocol:1,results:[...]}` in input order. Each entry is either
`{ok:true,value:{instant,zone,local,offsetSeconds}}` (no nested protocol) or
`{ok:false,error:{code,message}}`. Inspect **every item's** `ok`; outer `ok`
means the process/protocol succeeded, not that every conversion did.

Transport accepts at most 35 characters per instant, 64 per zone, and 30000
quoted argument characters per batch (leaving executable-path headroom on
Windows). These bounds include malformed strings, not just successful inputs.
Wrong batch shape/types, empty/oversized batches, exceeded transport limits and unsupported argument NUL
are rejected before launch with `invalid_argument`, without invented exitCode.
Semantic timestamp/zone/range errors in a batch are per-item errors. Single
semantic failures preserve empty stdout, JSON stderr and exitCode 2; the generic
adapter reports `cli_error` with the original diagnostic. CLI diagnostic codes
include `invalid_arguments`, `invalid_instant`, `invalid_zone`, `out_of_range`
and `icu_error`. Generic resolver/process failures retain available streams,
status and details; nonempty stderr alone is not failure.

Decoding rejects non-Object JSON, unsupported/non-numeric protocol, wrong types,
missing/extra response fields, malformed conversion shapes and batch counts
different from the submitted input. `_cli`, `_decode` and other underscore
methods are internal by convention, not a 4D access-control boundary.

```4d
var $tool : cs.DateTime.DateTime
var $result : Object
var $target : Text
$target:="macOS-universal"
If (Is Windows)
  $target:="Windows-x64"
End if
$tool:=cs.DateTime.DateTime.new($target)
$result:=$tool.convert("2026-01-15T12:00:00.123Z"; "Asia/Kathmandu")
If ($result.ok)
  ALERT($result.value.local)
Else
  ALERT($result.error.code+": "+$result.error.message)
End if
```
