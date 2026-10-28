"""Validate broker-advertised discovery; not proof of client consumption."""
import json
from types import SimpleNamespace

from broker_mcp import handle


def discovery(path, arm, validate_schema=True):
    expected = (handle({'id': 1, 'method': 'tools/list'}, SimpleNamespace(arm=arm))['result']['tools']
                if validate_schema else None)
    captured, errors = [], []
    if not path.exists():
        return dict(valid=False, errors=['Missing discovery capture'], captured_tool_lists=[])
    for number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            row = json.loads(line)
            request = row['request']
            if not isinstance(request, dict):
                raise ValueError('request must be an object')
            if request.get('method') != 'tools/list':
                continue
            reply = row['reply']
            if ('id' not in request or not isinstance(reply, dict)
                    or reply.get('id') != request['id'] or 'error' in reply):
                raise ValueError('invalid tools/list response envelope')
            result = reply['result']
            tools = result['tools']
            if not isinstance(tools, list):
                raise ValueError('tools must be an array')
            if any(not isinstance(t, dict) or not isinstance(t.get('name'), str)
                   or not isinstance(t.get('inputSchema'), dict) for t in tools):
                raise ValueError('malformed tool definition')
            if not tools or len({t['name'] for t in tools}) != len(tools):
                raise ValueError('empty or duplicate tool names')
            captured.append(tools)
            if result.get('nextCursor') is not None:
                errors.append(f'Line {number}: unexpected paginated discovery')
            # Order is immaterial, but duplicates, descriptions and full schemas matter.
            canonical = lambda values: sorted(json.dumps(t, sort_keys=True) for t in values)
            if validate_schema and canonical(tools) != canonical(expected):
                errors.append(f'Line {number}: advertised tools differ from expected arm schema')
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f'Line {number}: malformed discovery capture ({exc})')
    if not captured:
        errors.append('No successful tools/list response captured')
    return dict(valid=not errors, errors=errors, captured_tool_lists=captured)
