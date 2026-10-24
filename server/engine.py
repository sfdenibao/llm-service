import asyncio
import json
import httpx
from server.errors import ServiceError


class Engine:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.engine_url, timeout=None, trust_env=False,
                                        limits=httpx.Limits(max_connections=128, max_keepalive_connections=64))
        self.tokenizer = None
        if not settings.contract_test:
            from transformers import AutoTokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(settings.model, revision=settings.revision,
                                                           local_files_only=True, trust_remote_code=False)

    async def ready(self):
        try:
            response = await self.client.get('/health', timeout=2)
            if response.status_code != 200:
                return False
            models = await self.client.get('/v1/models', timeout=2)
            return models.status_code == 200 and any(m.get('id') == self.settings.model for m in models.json().get('data', []))
        except (httpx.HTTPError, ValueError):
            return False

    async def count_input(self, payload):
        if self.tokenizer is not None:
            ids = await asyncio.to_thread(self.tokenizer.apply_chat_template, payload['messages'],
                                          tokenize=True, add_generation_prompt=True)
            return len(ids)
        # Contract-test changes the engine only. The returned token count is trusted fixture evidence.
        try:
            r = await self.client.post('/tokenize', json={'model': payload['model'], 'messages': payload['messages'],
                                      'text': json.dumps(payload['messages'], ensure_ascii=False)}, timeout=5)
            r.raise_for_status()
            value = r.json()
            ids = value.get('input_ids', value.get('token_ids'))
            count = value.get('count')
            if type(count) is not int or count < 0 or not isinstance(ids, list) or count != len(ids):
                raise ValueError()
            return count
        except (httpx.HTTPError, ValueError, TypeError):
            raise ServiceError(503, 'tokenization_unavailable') from None

    def payload(self, payload, rid):
        value = {**payload, 'rid': rid}
        if value.get('stream'):
            value['stream_options'] = {'include_usage': True}
        else:
            value.pop('stream_options', None)
        if not self.settings.contract_test:
            value.update(top_p=1.0, top_k=-1, repetition_penalty=1.0)
        return value

    async def complete(self, payload, rid):
        try:
            r = await self.client.post('/v1/chat/completions', json=self.payload(payload, rid))
            if r.status_code != 200:
                raise ServiceError(503 if r.status_code >= 500 else 502, 'engine_error')
            value = r.json()
            choices = value['choices']
            if not choices or choices[0].get('finish_reason') is None or not isinstance(choices[0]['message'].get('content'), str):
                raise ValueError()
            if not isinstance(value.get('usage'), dict):
                raise ServiceError(502, 'missing_engine_usage')
            return value
        except httpx.HTTPError:
            raise ServiceError(503, 'engine_unavailable') from None
        except (ValueError, KeyError, TypeError, IndexError):
            raise ServiceError(502, 'invalid_engine_response') from None

    async def open_stream(self, payload, rid):
        request = self.client.build_request('POST', '/v1/chat/completions', json=self.payload(payload, rid))
        try:
            response = await self.client.send(request, stream=True)
        except httpx.HTTPError:
            raise ServiceError(503, 'engine_unavailable') from None
        if response.status_code != 200 or 'text/event-stream' not in response.headers.get('content-type', ''):
            await response.aclose()
            raise ServiceError(502, 'engine_stream_unavailable')
        return response

    async def abort(self, rid):
        try:
            r = await self.client.post('/abort_request', json={'rid': rid}, timeout=3)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self):
        await self.client.aclose()
