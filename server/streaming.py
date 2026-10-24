import json
from server.errors import ServiceError


def encode_event(value):
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return ('data: ' + raw + '\n\n').encode('utf-8')


async def parse_events(response):
    """Incremental UTF-8/SSE decoding, independent of HTTP chunk boundaries."""
    lines, size = [], 0
    async for line in response.aiter_lines():
        size += len(line.encode('utf-8'))
        if size > 256000:
            raise ServiceError(502, 'sse_event_too_large')
        if not line:
            if lines:
                raw = '\n'.join(lines)
                lines, size = [], 0
                if raw == '[DONE]':
                    yield '[DONE]'
                else:
                    try:
                        event = json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise ServiceError(502, 'invalid_sse_json') from None
                    if not isinstance(event, dict):
                        raise ServiceError(502, 'invalid_sse_event')
                    yield event
            else:
                size = 0
        elif line.startswith('data:'):
            lines.append(line[5:].removeprefix(' '))
        elif line.startswith(':'):
            continue
    if lines:
        raise ServiceError(502, 'unterminated_sse_event')
