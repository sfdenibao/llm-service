import asyncio
from contextlib import suppress
import time
import anyio

from server.errors import ServiceError
from server.streaming import encode_event, parse_events


async def cancellable(coro, request=None, timeout=None):
    task = asyncio.create_task(coro)
    try:
        async with asyncio.timeout(timeout):
            while not task.done():
                if request is not None and await request.is_disconnected():
                    raise asyncio.CancelledError()
                await asyncio.wait({task}, timeout=.05)
            return await task
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


class Generation:
    def __init__(self, service, who, payload, route, idem=None, parent=None):
        self.s = service
        self.who, self.payload, self.route, self.idem, self.parent = who, payload, route, idem, parent
        self.rid = None
        self.slot = self.entered = self.closed = False
        self.started = time.monotonic()
        self.queue_ms = 0

    async def prepare(self, request=None):
        if self.payload['model'] != self.s.settings.model:
            raise ServiceError(404, 'model_not_found')
        self.s.store.check_idempotency(self.who, self.route, self.idem, self.payload)
        count = await cancellable(self.s.engine.count_input(self.payload), request, 6)
        if count + self.payload['max_tokens'] > self.s.settings.config['context_length']:
            raise ServiceError(400, 'context_length_exceeded')
        self.rid = self.s.store.reserve(self.who, self.route, self.payload, count, self.payload['max_tokens'], self.idem, self.parent)
        queue_start = time.monotonic()
        try:
            await cancellable(self.s.admission.acquire(), request)
            self.slot = True
            self.queue_ms = (time.monotonic() - queue_start) * 1000
        except BaseException as exc:
            code = getattr(exc, 'code', 'cancelled')
            status = 'cancelled' if isinstance(exc, asyncio.CancelledError) else ('timed_out' if code == 'queue_timeout' else 'rejected')
            await self.finish(status, code)
            raise

    def start_engine(self):
        self.s.store.engine_started(self.rid)
        self.entered = True

    async def finish(self, status, error=None):
        if self.closed:
            return
        self.closed = True
        # A shielded cleanup survives client disconnect and reaches abort + ledger + release.
        async def cleanup():
            try:
                if status != 'completed' and self.entered:
                    acknowledged = await self.s.engine.abort(self.rid)
                    self.s.metrics.counters['abort_acknowledged' if acknowledged else 'abort_failed'] += 1
                if self.rid:
                    self.s.store.finish(self.rid, status, error)
                    duration = (time.monotonic() - self.started) * 1000
                    self.s.store.timings(self.rid, self.queue_ms, duration)
                    self.s.metrics.recent.append({'request_id': self.rid, 'status': status,
                                                 'queue_ms': self.queue_ms, 'duration_ms': duration})
                if status != 'rejected':
                    self.s.metrics.counters[status] += 1
            finally:
                if self.slot:
                    self.s.admission.release()
                    self.slot = False
        # Starlette uses an AnyIO cancellation scope: shielding only an asyncio Task
        # does not shield the following await from repeated scope cancellation.
        with anyio.CancelScope(shield=True):
            await cleanup()

    async def complete(self, request=None):
        try:
            await self.prepare(request)
            self.start_engine()
            value = await cancellable(self.s.engine.complete(self.payload, self.rid), request,
                                      self.s.settings.config['execution_timeout_seconds'])
            self.s.store.journal(self.rid, value['usage'])
            await self.finish('completed')
            return value
        except asyncio.CancelledError:
            await self.finish('cancelled', 'client_disconnected')
            raise
        except TimeoutError:
            await self.finish('timed_out', 'execution_timeout')
            raise ServiceError(504, 'execution_timeout') from None
        except Exception as exc:
            await self.finish('failed', getattr(exc, 'code', 'internal_error'))
            raise

    async def stream(self, request=None):
        try:
            await self.prepare(request)
            self.start_engine()
            timeout = self.s.settings.config['execution_timeout_seconds']
            self.deadline = time.monotonic() + timeout
            self.response = await cancellable(self.s.engine.open_stream(self.payload, self.rid), request, timeout)
            return self.events()
        except asyncio.CancelledError:
            await self.finish('cancelled', 'client_disconnected')
            raise
        except TimeoutError:
            await self.finish('timed_out', 'execution_timeout')
            raise ServiceError(504, 'execution_timeout') from None
        except Exception as exc:
            await self.finish('failed', getattr(exc, 'code', 'internal_error'))
            raise

    async def events(self):
        finished = usage_seen = completed = False
        include_usage = self.payload.get('stream_options', {}).get('include_usage', False)
        try:
            async with asyncio.timeout(max(.001, self.deadline - time.monotonic())):
                async for event in parse_events(self.response):
                    if event == '[DONE]':
                        if not finished or not usage_seen:
                            raise ServiceError(502, 'incomplete_sse')
                        # Commit before DONE reaches the caller, so its next usage query sees this bill.
                        await self.finish('completed')
                        completed = True
                        yield encode_event('[DONE]')
                        break
                    if 'error' in event:
                        raise ServiceError(502, 'upstream_stream_error')
                    if isinstance(event.get('usage'), dict):
                        self.s.store.journal(self.rid, event['usage'])
                        usage_seen = True
                    for choice in event.get('choices', []):
                        content = choice.get('delta', {}).get('content')
                        if content is not None and not isinstance(content, str):
                            raise ServiceError(502, 'invalid_sse_content')
                        if choice.get('finish_reason') is not None:
                            finished = True
                    if not include_usage and 'usage' in event:
                        event = {k: v for k, v in event.items() if k != 'usage'}
                        if not event.get('choices'):
                            continue
                    yield encode_event(event)
                if not completed:
                    raise ServiceError(502, 'missing_sse_done')
        except asyncio.CancelledError:
            await self.finish('cancelled', 'client_disconnected')
            raise
        except TimeoutError:
            await self.finish('timed_out', 'execution_timeout')
            yield encode_event(ServiceError(504, 'execution_timeout').body())
        except Exception as exc:
            code = getattr(exc, 'code', 'upstream_stream_error')
            await self.finish('failed', code)
            yield encode_event(ServiceError(502, code).body())
        finally:
            # Starlette cancels the stream task on disconnect; shield closing the upstream as well.
            with anyio.CancelScope(shield=True):
                await self.response.aclose()
            if not self.closed:
                await self.finish('cancelled', 'stream_closed')


class ModelService:
    def __init__(self, settings, store, engine, admission, metrics):
        self.settings, self.store, self.engine, self.admission, self.metrics = settings, store, engine, admission, metrics

    def generation(self, who, payload, route='/v1/chat/completions', idem=None, parent=None):
        return Generation(self, who, payload, route, idem, parent)

    async def reconcile(self, restart=False):
        limit = asyncio.Semaphore(8)
        async def one(row):
            async with limit:
                if row['engine_started']:
                    ok = await self.engine.abort(row['request_id'])
                    self.metrics.counters['abort_acknowledged' if ok else 'abort_failed'] += 1
                self.store.recover(row['request_id'])
        await asyncio.gather(*(one(row) for row in self.store.unfinished(restart)))
