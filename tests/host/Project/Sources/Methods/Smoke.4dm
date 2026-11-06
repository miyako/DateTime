//%attributes = {"invisible":true}
var $ignored : Integer
var $parameter; $name; $target : Text
var $folderPath : Text
var $request; $result; $report : Object
var $tool : cs.DateTime.DateTime
var $requestFile; $statusFile : 4D.File
var $renamedExecutable : 4D.File
var $cases : Collection
var $case : Object
var $items : Collection
var $i : Integer
var $invalid : Variant
var HOST_StatusFile : 4D.File
var HOST_Phase : Text
var HOST_PreviousHandler : Text
$ignored:=Get database parameter(User param value; $parameter)
If (Is Windows)
	$requestFile:=File($parameter; fk platform path)
Else
	$requestFile:=File($parameter; fk posix path)
End if
$request:=JSON Parse($requestFile.getText())
If (Is Windows)
	$statusFile:=File($request.status; fk platform path)
Else
	$statusFile:=File($request.status; fk posix path)
End if
$statusFile.setText("{\"success\":false,\"phase\":\"starting\"}")
HOST_StatusFile:=$statusFile
HOST_Phase:="constructor"
HOST_PreviousHandler:=Method called on error(ek local)
ON ERR CALL("Host_error"; ek local)
$target:="macOS-universal"
If (Is Windows)
	$target:="Windows-x64"
End if
$tool:=cs.DateTime.DateTime.new($target)
$cases:=New collection
$cases.push(New object("instant"; "2026-01-15T12:00:00Z"; "zone"; "Europe/Paris"; "local"; "2026-01-15T13:00:00.000+01:00"))
$cases.push(New object("instant"; "2026-07-15T12:00:00Z"; "zone"; "Europe/Paris"; "local"; "2026-07-15T14:00:00.000+02:00"))
$cases.push(New object("instant"; "2026-01-15T12:00:00Z"; "zone"; "America/New_York"; "local"; "2026-01-15T07:00:00.000-05:00"))
$cases.push(New object("instant"; "2026-07-15T12:00:00Z"; "zone"; "America/New_York"; "local"; "2026-07-15T08:00:00.000-04:00"))
$cases.push(New object("instant"; "2026-01-15T12:00:00.123Z"; "zone"; "Asia/Kathmandu"; "local"; "2026-01-15T17:45:00.123+05:45"))
$cases.push(New object("instant"; "2026-01-15T17:45:00.1+05:45"; "zone"; "Etc/UTC"; "local"; "2026-01-15T12:00:00.100+00:00"))
$report:=New object("success"; True; "finished"; False; "cases"; New collection)
$report.compiled:=$tool._cli.compiled
HOST_Phase:="renamed executable"
If (Is Windows)
	$renamedExecutable:=File($request.executable; fk platform path)
Else
	$renamedExecutable:=File($request.executable; fk posix path)
End if
$report.renamedExecutable:=cs.DateTime._Process.new().execute($renamedExecutable; New collection("--version"))
If (Not($report.renamedExecutable.ok))
	$report.success:=False
End if
HOST_Phase:="feature cases"
If ($request.expectCompiled#$report.compiled)
	$report.success:=False
End if
For each ($case; $cases)
	$result:=$tool.convert($case.instant; $case.zone)
	$report.cases.push($result)
	If (Not($result.ok))
		$report.success:=False
	Else
		If ($result.value.local#$case.local)
			$report.success:=False
		End if
	End if
End for each
$result:=$tool.convert("not-an-instant"; "Europe/Paris")
$report.invalid:=$result
If ($result.ok | ($result.exitCode#2))
	$report.success:=False
End if
$report.invalidZones:=New collection
For each ($name; New collection("Not/A_Zone"; "GMT+05:45"; "Europe/paris"; ""; "日本語"; "Europe/Paris "))
	$result:=$tool.convert("2026-01-15T12:00:00Z"; $name)
	$report.invalidZones.push($result)
	If ($result.ok | ($result.exitCode#2))
		$report.success:=False
	End if
End for each
$report.invalidInstants:=New collection
For each ($name; New collection("2026-02-30T12:00:00Z"; "2026-01-15T12:00:00.1234Z"; "2026-01-15T12:00:60Z"; "2026-01-15T12:00:00"; "0000-01-01T00:00:00Z"; "2026-01-15T12:00:00-00:00"))
	$result:=$tool.convert($name; "Etc/UTC")
	$report.invalidInstants.push($result)
	If ($result.ok | ($result.exitCode#2))
		$report.success:=False
	End if
End for each
$report.batch:=$tool.convertBatch(New collection(New object("instant"; "2026-01-15T12:00:00.123Z"; "zone"; "Asia/Kathmandu"); New object("instant"; "bad"; "zone"; "Europe/Paris"); New object("instant"; "2026-01-15T12:00:00Z"; "zone"; "Not/A_Zone")))
If (Not($report.batch.ok))
	$report.success:=False
End if
$report.invalidBatch:=$tool.convertBatch(New collection)
$report.nullInstant:=$tool.convert(Null; "Etc/UTC")
$report.nullZone:=$tool.convert("2026-01-15T12:00:00Z"; Null)
$report.rejectedFeatureTypes:=New collection
For each ($invalid; New collection(123; True; Null; Current date; Current time; New collection; New object))
	$report.rejectedFeatureTypes.push($tool.convert($invalid; "Etc/UTC"))
	$report.rejectedFeatureTypes.push($tool.convert("2026-01-15T12:00:00Z"; $invalid))
End for each
$report.rejectedBatchShapes:=New collection($tool.convertBatch(Null); $tool.convertBatch(New object); $tool.convertBatch(New collection(New object("instant"; 123; "zone"; "Etc/UTC"))); $tool.convertBatch(New collection(New object("instant"; "2026-01-15T12:00:00Z"; "zone"; "Etc/UTC"; "extra"; True))))
$items:=New collection
For ($i; 1; 256)
	$items.push(New object("instant"; "2026-01-15T12:00:00Z"; "zone"; "Etc/UTC"))
End for
$report.maxBatch:=$tool.convertBatch($items)
$items.push(New object("instant"; "2026-01-15T12:00:00Z"; "zone"; "Etc/UTC"))
$report.oversizedBatch:=$tool.convertBatch($items)
$report.oversizedInstant:=$tool.convert("2026-01-15T12:00:00.123456789123456789Z"; "Etc/UTC")
$result:=$tool.version()
$report.version:=$result
If (Not($result.ok))
	$report.success:=False
Else
	If ($result.value.version#"0.1.1")
		$report.success:=False
	End if
End if
HOST_Phase:="missing executable"
$result:=cs.DateTime._CLI.new("missing-cli"; $target).execute(New collection("--version"))
$report.missing:=$result
If ($result.error.code#"missing_executable")
	$report.success:=False
End if
$result:=cs.DateTime.DateTime.new("unsupported-arm-target").version()
$report.unsupported:=$result
If ($result.error.code#"unsupported_architecture")
	$report.success:=False
End if
HOST_Phase:="launch failure"
$result:=cs.DateTime._Process.new().execute($requestFile; New collection)
$report.launch:=$result
If ($result.error.code#"launch_failed")
	$report.success:=False
End if
HOST_Phase:="malformed protocol"
$result:=$tool._decode(New object("ok"; True; "stdout"; "not json"; "stderr"; ""; "exitCode"; 0); "instant")
$report.protocol:=$result
If ($result.error.code#"invalid_protocol")
	$report.success:=False
End if
$report.protocolTypes:=New collection
For each ($name; New collection("{\"protocol\":true,\"instant\":\"invalid\"}"; "{\"protocol\":false,\"instant\":\"invalid\"}"))
	$result:=$tool._decode(New object("ok"; True; "stdout"; $name; "stderr"; ""; "exitCode"; 0); "instant")
	$report.protocolTypes.push($result)
	If ($result.ok | ($result.error.code#"invalid_protocol"))
		$report.success:=False
	End if
End for each
$result:=$tool._decode(New object("ok"; True; "stdout"; "{\"protocol\":null,\"instant\":\"invalid\"}"; "stderr"; ""; "exitCode"; 0); "instant")
$report.protocolNull:=$result
If ($result.ok | ($result.error.code#"invalid_protocol"))
	$report.success:=False
End if
$report.protocolScalars:=New collection
For each ($name; New collection("true"; "false"; "null"; "1"; "[]"))
	$result:=$tool._decode(New object("ok"; True; "stdout"; $name; "stderr"; ""; "exitCode"; 0); "instant")
	$report.protocolScalars.push($result)
	If ($result.ok | ($result.error.code#"invalid_protocol"))
		$report.success:=False
	End if
End for each
$report.protocolConversions:=New collection
For each ($name; New collection("{\"protocol\":1}"; "{\"protocol\":1,\"instant\":true,\"zone\":\"Etc/UTC\",\"local\":\"invalid\",\"offsetSeconds\":0}"; "{\"protocol\":1,\"instant\":\"2026-01-15T12:00:00.000Z\",\"zone\":\"Etc/UTC\",\"local\":\"2026-01-15T12:00:00.000+00:00\",\"offsetSeconds\":true}"))
	$report.protocolConversions.push($tool._decode(New object("ok"; True; "stdout"; $name; "stderr"; ""; "exitCode"; 0); "instant"))
End for each
$report.protocolBatches:=New collection
For each ($name; New collection("{\"protocol\":1,\"results\":[]}"; "{\"protocol\":1,\"results\":[{\"ok\":true,\"value\":null}]}"; "{\"protocol\":1,\"results\":[{\"ok\":false,\"error\":null}]}"; "{\"protocol\":1,\"results\":[{\"ok\":false,\"error\":{\"code\":\"\",\"message\":\"\"}}]}"; "{\"protocol\":1,\"results\":[true]}"))
	$report.protocolBatches.push($tool._decode(New object("ok"; True; "stdout"; $name; "stderr"; ""; "exitCode"; 0); "results"))
End for each
HOST_Phase:="File argument"
$result:=cs.DateTime._CLI.new("datetime"; $target).execute(New collection("inspect-args"; $requestFile))
$report.fileArgument:=$result
If (Not($result.ok))
	$report.success:=False
Else
	If (JSON Parse($result.stdout).args[0]#$parameter)
		$report.success:=False
	End if
End if
$report.rejectedArguments:=New collection
HOST_Phase:="scalar arguments"
For each ($invalid; New collection(123; 1.25; True; False; Null; Current date; Current time; New collection; New object))
	$result:=cs.DateTime._Process.new().execute($requestFile; New collection($invalid))
	$report.rejectedArguments.push(New object("type"; Value type($invalid); "result"; $result))
	If ($result.ok | ($result.error.code#"invalid_argument"))
		$report.success:=False
	End if
End for each
$report.rejectedNul:=New collection
HOST_Phase:="NUL arguments"
For each ($name; New collection(Char(0)+"prefix"; "middle"+Char(0)+"suffix"; "trailing"+Char(0)))
	$result:=cs.DateTime._Process.new().execute($requestFile; New collection($name))
	$report.rejectedNul.push($result)
	If ($result.ok | ($result.error.code#"invalid_argument"))
		$report.success:=False
	End if
End for each
$report.missingFile:=cs.DateTime._Process.new().execute(Null; New collection)
$report.missingArguments:=cs.DateTime._Process.new().execute($requestFile; Null)
If ($report.missingFile.ok | ($report.missingFile.error.code#"invalid_argument"))
	$report.success:=False
End if
If ($report.missingArguments.ok | ($report.missingArguments.error.code#"invalid_argument"))
	$report.success:=False
End if
HOST_Phase:="Folder argument"
$result:=cs.DateTime._CLI.new("datetime"; $target).execute(New collection("inspect-args"; $requestFile.parent))
$report.folderArgument:=$result
$folderPath:=$requestFile.parent.path
If (Is Windows)
	$folderPath:=$requestFile.parent.platformPath
End if
If (Not($result.ok))
	$report.success:=False
Else
	If (JSON Parse($result.stdout).args[0]#$folderPath)
		$report.success:=False
	End if
End if
$report.quotedArguments:=New collection
HOST_Phase:="literal arguments"
For each ($name; New collection("path with spaces"; "quote \" and \\ slash"; "line\nbreak"; "日本語"; "$() ; & % !"))
	$result:=cs.DateTime._CLI.new("datetime"; $target).execute(New collection("inspect-args"; $name))
	$report.quotedArguments.push($result)
	If (Not($result.ok))
		$report.success:=False
	Else
		If (JSON Parse($result.stdout).args[0]#$name)
			$report.success:=False
		End if
	End if
End for each
$report.finished:=True
ON ERR CALL(HOST_PreviousHandler; ek local)
$statusFile.setText(JSON Stringify($report))
HOST_StatusFile:=Null
HOST_Phase:=""
HOST_PreviousHandler:=""
QUIT 4D
