property _cli : cs._CLI

Class constructor($target : Text)
	This._cli:=cs._CLI.new("datetime"; $target)

Function convert($instant : Variant; $zone : Variant)->$result : Object
	If ((Value type($instant)#Is text) | (Value type($zone)#Is text))
		return This._invalid("Instant and zone must be Text")
	End if
	If ((Length($instant)>35) | (Length($zone)>64))
		return This._invalid("Instant and zone exceed the 35/64 character transport limits")
	End if
	$result:=This._decode(This._cli.execute(New collection("convert"; "--instant"; $instant; "--zone"; $zone)); "instant")
	If ($result.ok)
		If ($result.value.zone#$zone)
			$result:=This._protocolError($result; "Response zone differs from requested zone")
		End if
	End if

Function convertBatch($items : Variant)->$result : Object
	var $arguments : Collection
	var $item : Variant
	var $i : Integer
	var $argument : Text
	var $commandLength : Integer
	If (Value type($items)#Is collection)
		return This._invalid("Batch must be a Collection of 1 to 256 instant/zone Objects")
	End if
	If (($items.length<1) | ($items.length>256))
		return This._invalid("Batch must be a Collection of 1 to 256 instant/zone Objects")
	End if
	$arguments:=New collection("batch")
	For each ($item; $items)
		If (Value type($item)#Is object)
			return This._invalid("Each batch item must contain Text instant and zone")
		End if
		If ((Value type($item.instant)#Is text) | (Value type($item.zone)#Is text))
			return This._invalid("Each batch item must contain Text instant and zone")
		End if
		If (OB Keys($item).length#2)
			return This._invalid("Each batch item must contain only instant and zone")
		End if
		If ((Length($item.instant)>35) | (Length($item.zone)>64))
			return This._invalid("Instant and zone exceed the 35/64 character transport limits")
		End if
		$arguments.push("--item"; $item.instant; $item.zone)
	End for each
	$commandLength:=0
	For each ($argument; $arguments)
		$commandLength+=Length(cs._Process.new().quote($argument))+1
	End for each
	If ($commandLength>30000)
		return This._invalid("Quoted batch arguments exceed the 30000 character transport limit")
	End if
	$result:=This._decode(This._cli.execute($arguments); "results")
	If ($result.ok)
		If ($result.value.results.length#$items.length)
			$result:=This._protocolError($result; "Batch result count differs from input")
		Else
			For ($i; 0; $items.length-1)
				If ($result.value.results[$i].ok)
					If ($result.value.results[$i].value.zone#$items[$i].zone)
						return This._protocolError($result; "Batch response zone differs from requested zone")
					End if
				End if
			End for
		End if
	End if

Function version()->$result : Object
	$result:=This._decode(This._cli.execute(New collection("--version")); "version")

Function _invalid($message : Text)->$result : Object
	$result:=New object("ok"; False; "error"; New object("code"; "invalid_argument"; "message"; $message))

Function _protocolError($result : Object; $message : Text)->$decoded : Object
	$result.ok:=False
	$result.error:=New object("code"; "invalid_protocol"; "message"; $message)
	$decoded:=$result

Function _validConversion($value : Variant; $withProtocol : Boolean)->$valid : Boolean
	var $key : Text
	$valid:=False
	If (Value type($value)#Is object)
		return False
	End if
	For each ($key; New collection("instant"; "zone"; "local"))
		If (Value type($value[$key])#Is text)
			return False
		End if
	End for each
	If ((Value type($value.offsetSeconds)#Is real) & (Value type($value.offsetSeconds)#Is longint))
		return False
	End if
	If (($value.offsetSeconds#Int($value.offsetSeconds)) | (Abs($value.offsetSeconds)>=86400))
		return False
	End if
	If (Not(Match regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{3}Z$"; $value.instant)))
		return False
	End if
	If (Not(Match regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{3}[+-][0-9]{2}:[0-9]{2}(:[0-9]{2})?$"; $value.local)))
		return False
	End if
	If ($value.zone="")
		return False
	End if
	If ($withProtocol)
		return OB Keys($value).length=5
	End if
	$valid:=OB Keys($value).length=4

Function _decode($result : Object; $field : Text)->$decoded : Object
	var $payload; $item : Variant
	var $previous : Text
	var $parseErrors : Collection
	var CLI_LastErrors : Collection
	If (Not($result.ok))
		return $result
	End if
	$previous:=Method called on error(ek local)
	CLI_LastErrors:=Null
	ON ERR CALL("_CaptureError"; ek local)
	$payload:=JSON Parse($result.stdout)
	ON ERR CALL($previous; ek local)
	$parseErrors:=CLI_LastErrors
	CLI_LastErrors:=Null
	If (($parseErrors#Null) | (Value type($payload)#Is object))
		$result:=This._protocolError($result; "CLI response must be a JSON object")
		$result.error.details:=$parseErrors
		return $result
	End if
	If ((Value type($payload.protocol)#Is real) & (Value type($payload.protocol)#Is longint))
		return This._protocolError($result; "Unsupported CLI protocol version")
	End if
	If ($payload.protocol#1)
		return This._protocolError($result; "Unsupported CLI protocol version")
	End if
	Case of
		: ($field="version")
			If (Value type($payload.version)#Is text)
				return This._protocolError($result; "CLI version must be Text")
			End if
			If (($payload.version#"0.1.0") | (OB Keys($payload).length#2))
				return This._protocolError($result; "Unsupported CLI version")
			End if
		: ($field="instant")
			If (Not(This._validConversion($payload; True)))
				return This._protocolError($result; "Invalid conversion response")
			End if
		: ($field="results")
			If (Value type($payload.results)#Is collection)
				return This._protocolError($result; "Batch results must be a Collection")
			End if
			If ((OB Keys($payload).length#2) | ($payload.results.length<1) | ($payload.results.length>256))
				return This._protocolError($result; "Invalid batch response")
			End if
			For each ($item; $payload.results)
				If (Value type($item)#Is object)
					return This._protocolError($result; "Invalid batch item")
				End if
				If (Value type($item.ok)#Is boolean)
					return This._protocolError($result; "Invalid batch status")
				End if
				If (OB Keys($item).length#2)
					return This._protocolError($result; "Invalid batch item fields")
				End if
				If ($item.ok)
					If (Not(This._validConversion($item.value; False)))
						return This._protocolError($result; "Invalid batch conversion")
					End if
				Else
					If (Value type($item.error)#Is object)
						return This._protocolError($result; "Invalid batch error")
					End if
					If ((Value type($item.error.code)#Is text) | (Value type($item.error.message)#Is text))
						return This._protocolError($result; "Invalid batch diagnostic")
					End if
					If (($item.error.code="") | ($item.error.message="") | (OB Keys($item.error).length#2))
						return This._protocolError($result; "Invalid batch diagnostic")
					End if
				End if
			End for each
		Else
			return This._protocolError($result; "Unknown response contract")
	End case
	$result.value:=$payload
	$decoded:=$result
