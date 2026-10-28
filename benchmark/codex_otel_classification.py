"""Versioned, conservative classifier for observed Codex HTTP/WS telemetry.

This is a diagnostic only: observed records do not establish complete agent
accounting, billed usage, or an enforceable live token cap.
"""
CLASSIFIER_VERSION = 'codex-otel-classification-v1'
TOKEN_FIELDS = {
    'input_tokens': 'input_token_count',
    'output_tokens': 'output_token_count',
    'cached_input_tokens': 'cached_token_count',
    'reasoning_output_tokens': 'reasoning_token_count',
}
# Values are used only transiently as internal correlation keys and never emitted.
CORRELATION_FIELDS = (
    'response_id', 'response.id', 'request_id', 'request.id',
)
OPTIONAL_TOKEN_FIELDS = ('cache_write_token_count', 'tool_token_count')


def _semantic_name(event):
    attrs = event.get('attributes')
    if isinstance(attrs, dict) and attrs.get('event.name') is not None:
        return attrs['event.name']
    return event.get('event_name')


def _token_integer(value):
    if isinstance(value, bool):
        raise ValueError('boolean token value')
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or value < 0:
        raise ValueError('invalid token value')
    return value


def _usage(attrs):
    present = [key for key in TOKEN_FIELDS.values() if key in attrs]
    if not present and not any(key in attrs for key in OPTIONAL_TOKEN_FIELDS):
        return None
    if len(present) != len(TOKEN_FIELDS):
        raise ValueError('partial token fields')
    usage = {out: _token_integer(attrs[source]) for out, source in TOKEN_FIELDS.items()}
    for field in OPTIONAL_TOKEN_FIELDS:
        if field in attrs:
            usage[field] = _token_integer(attrs[field])
    if usage['cached_input_tokens'] > usage['input_tokens']:
        raise ValueError('cached exceeds input')
    if usage['reasoning_output_tokens'] > usage['output_tokens']:
        raise ValueError('reasoning exceeds output')
    return usage


def classify(events):
    """Classify decoded OTLP events without exposing telemetry identities.

    Tokenless ``response.completed`` events are markers. Each valid usage-bearing
    completion is retained as a record, including zero-output records. A marker
    is never paired with a usage record by its position in the input.
    """
    result = {
        'classifier_version': CLASSIFIER_VERSION,
        'observed_http_requests': 0,
        'observed_websocket_requests': 0,
        'observed_response_completed_events': 0,
        'observed_response_completed_markers': 0,
        'observed_usage_records': 0,
        'usage_records': [],
        'usage_totals': {key: 0 for key in (*TOKEN_FIELDS, *OPTIONAL_TOKEN_FIELDS)},
        'optional_token_field_observations': {key: 0 for key in OPTIONAL_TOKEN_FIELDS},
        'usage_totals_scope': 'observed valid records only; missing optional fields are unknown',
        'invalid_usage_records': 0,
        'duplicate_identifiable_usage_records': 0,
        'correlation': {'available': False, 'key_names': [],
                        'identifiable_usage_records': 0,
                        'uncorrelated_usage_records': 0},
        'errors': [],
        'diagnostic_only': True,
        'complete_agent_accounting': False,
        'product_promotion': False,
        'live_token_cap': 'unverified',
    }
    errors = set()
    identifiable = {}
    seen_keys = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        name = _semantic_name(event)
        attrs = event.get('attributes')
        if not isinstance(attrs, dict):
            attrs = {}
        if name == 'codex.api_request':
            result['observed_http_requests'] += 1
        elif name == 'codex.websocket_request':
            result['observed_websocket_requests'] += 1
        if name != 'codex.sse_event' or attrs.get('event.kind') != 'response.completed':
            continue
        result['observed_response_completed_events'] += 1
        try:
            record = _usage(attrs)
        except ValueError:
            result['invalid_usage_records'] += 1
            errors.add('invalid_or_partial_usage_record')
            continue
        if record is None:
            result['observed_response_completed_markers'] += 1
            continue
        result['observed_usage_records'] += 1
        result['usage_records'].append(record)
        for field, value in record.items():
            result['usage_totals'][field] += value
            if field in OPTIONAL_TOKEN_FIELDS:
                result['optional_token_field_observations'][field] += 1
        keys = []
        for key in CORRELATION_FIELDS:
            value = attrs.get(key)
            if value is not None and isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
                keys.append((key, str(value)))
        if keys:
            result['correlation']['identifiable_usage_records'] += 1
            for key, value in keys:
                seen_keys.add(key)
            duplicates = any((key, value) in identifiable for key, value in keys)
            if duplicates:
                result['duplicate_identifiable_usage_records'] += 1
                errors.add('duplicate_identifiable_usage_record')
            for key, value in keys:
                identifiable[(key, value)] = True
        else:
            result['correlation']['uncorrelated_usage_records'] += 1
    result['correlation']['available'] = bool(seen_keys)
    result['correlation']['key_names'] = sorted(seen_keys)
    if result['correlation']['uncorrelated_usage_records']:
        errors.add('usage_records_uncorrelated_completeness_unproven')
    if not result['observed_usage_records']:
        errors.add('no_valid_usage_records')
    result['errors'] = sorted(errors)
    result['reconciliation_pass'] = False
    return result
