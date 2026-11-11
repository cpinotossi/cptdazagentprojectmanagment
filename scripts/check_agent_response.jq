def tool_result:
  if (.result | type) == "string" and (.result | test("^\\s*\\{"))
  then .result | fromjson
  else .result
  end;

if length != 1 or (.[0] | type) != "object"
then error("Expected exactly one JSON response object")
else .[0]
end
| [
    (if .status != "completed" or .error != null or .incomplete_details != null
     then {status, error, incomplete_details}
     else empty end),
    (.calls[]? | . as $call
     | (if .status != "completed" or .error != null
        then {tool: .name, status, error}
        else empty end),
       (tool_result | select(type == "object")
        | if .error != null
             or .additionalProperties._meta.error_classification != null
             or .additionalProperties._meta.error_reason != null
          then {tool: $call.name, error, service: .additionalProperties._meta}
          else empty end))
  ]
| if length > 0 then error(tojson) else true end
