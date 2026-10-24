from contextlib import asynccontextmanager, suppress
import asyncio
from datetime import datetime, timezone
import importlib.metadata
import json
import secrets
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from agent.runner import run_agent
from control_plane.storage import Store
from server.admission import Admission
from server.config import ROOT, Settings
from server.engine import Engine
from server.errors import ServiceError
from server.metrics import Metrics
from server.models import AgentRequest, Chat, Credit, KeyCreate, Price, Rotate, TenantCreate, TenantPatch
from server.service import ModelService


def create_app(settings=None):
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app):
        store, engine, metrics = Store(settings), Engine(settings), Metrics()
        admission = Admission(settings.config['gateway_slots'], settings.config['gateway_queue'], settings.config['queue_timeout_seconds'])
        service = ModelService(settings, store, engine, admission, metrics)
        app.state.service = service
        await service.reconcile(restart=True)

        async def maintain():
            while True:
                await asyncio.sleep(2)
                await service.reconcile()
                if not settings.contract_test:
                    await asyncio.to_thread(metrics.sample_gpu, settings.data_dir)
        maintenance = asyncio.create_task(maintain())
        try:
            yield
        finally:
            maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance
            await engine.close()

    app = FastAPI(title='LLM Service', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def service():
        return app.state.service

    def bearer(request):
        header = request.headers.get('authorization', '')
        parts = header.split(' ', 1)
        return parts[1] if len(parts) == 2 and parts[0].lower() == 'bearer' else ''

    def user(request):
        return service().store.authenticate(bearer(request))

    def admin(request):
        if not settings.admin_key:
            raise ServiceError(503, 'management_disabled', 'Set a private ADMIN_API_KEY to enable management')
        raw = bearer(request)
        if not raw or not secrets.compare_digest(raw.encode(), settings.admin_key.encode()):
            raise ServiceError(401, 'admin_auth_required')

    async def body(request, model):
        chunks, length = [], 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > 65536:
                raise ServiceError(413, 'request_too_large')
            chunks.append(chunk)
        try:
            value = json.loads(b''.join(chunks), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            return model.model_validate(value).model_dump(exclude_none=True)
        except (ValueError, TypeError, ValidationError):
            # Never reflect raw inputs (which may contain secrets) in validation errors.
            raise ServiceError(422, 'invalid_request') from None

    def idem(request):
        key = request.headers.get('idempotency-key')
        if key is not None and (not key or len(key) > 200):
            raise ServiceError(400, 'invalid_idempotency_key')
        return key

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        if error.status in (401, 402, 403, 429):
            service().metrics.counters['rejected'] += 1
            service().metrics.reasons[error.code] += 1
        return JSONResponse(error.body(), status_code=error.status,
                            headers={'Cache-Control': 'no-store', **error.headers})

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, error):
        return JSONResponse(ServiceError(503, 'storage_unavailable').body(), status_code=503)

    @app.get('/healthz')
    async def health():
        ready = await service().engine.ready()
        return JSONResponse({'status': 'ready' if ready else 'not_ready'}, status_code=200 if ready else 503)

    @app.get('/info')
    async def info(request: Request):
        user(request)
        return {'contract': 'llm-lab/1.0', 'credential_contract': 'credential-service/1',
                'model_id': settings.model, 'model_revision': settings.revision, 'profile': settings.profile,
                'engine': 'cpu-fixture' if settings.contract_test else 'sglang',
                'engine_version': 'controller-mock/1' if settings.contract_test else importlib.metadata.version('sglang'),
                'config': settings.config, 'mode': 'contract-test' if settings.contract_test else 'live',
                'management_enabled': bool(settings.admin_key)}

    @app.get('/v1/models')
    async def models(request: Request):
        user(request)
        return {'object': 'list', 'data': [{'id': settings.model, 'object': 'model'}]}

    @app.get('/metrics')
    async def metrics(request: Request):
        user(request)
        return service().metrics.snapshot(service().admission)

    @app.post('/v1/chat/completions')
    async def chat(request: Request):
        who = user(request)
        data = await body(request, Chat)
        generation = service().generation(who, data, idem=idem(request))
        if data['stream']:
            events = await generation.stream(request)
            return StreamingResponse(events, media_type='text/event-stream', headers={
                'X-Request-ID': generation.rid, 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
        result = await generation.complete(request)
        return JSONResponse(result, headers={'X-Request-ID': generation.rid, 'Cache-Control': 'no-store'})

    @app.post('/agent/run')
    async def agent(request: Request):
        who = user(request)
        data = await body(request, AgentRequest)
        rid, result = await run_agent(service(), who, data, idem(request), request)
        return JSONResponse(result, headers={'X-Request-ID': rid, 'Cache-Control': 'no-store'})

    def usage_query(request, tid):
        query = request.query_params
        try:
            cursor, limit = int(query.get('cursor', '0')), int(query.get('limit', '100'))
            if cursor < 0 or not 1 <= limit <= 200:
                raise ValueError()
            times = []
            for key in ('from', 'to'):
                raw = query.get(key)
                if raw:
                    parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
                    if parsed.tzinfo is None:
                        raise ValueError()
                    times.append(parsed.astimezone(timezone.utc).isoformat())
                else:
                    times.append(None)
        except ValueError:
            raise ServiceError(422, 'invalid_usage_query') from None
        return service().store.usage(tid, cursor, limit, *times)

    @app.get('/v1/usage')
    async def own_usage(request: Request):
        who = user(request)
        if set(request.query_params) - {'cursor', 'limit', 'from', 'to'}:
            raise ServiceError(403, 'tenant_scope_not_allowed')
        return usage_query(request, who['tenant_id'])

    @app.get('/admin/tenants')
    async def tenants(request: Request):
        admin(request)
        return service().store.tenants()

    @app.post('/admin/tenants', status_code=201)
    async def create_tenant(request: Request):
        admin(request)
        return service().store.create_tenant(await body(request, TenantCreate))

    @app.patch('/admin/tenants/{tid}')
    async def patch_tenant(tid: str, request: Request):
        admin(request)
        return service().store.patch_tenant(tid, await body(request, TenantPatch))

    @app.post('/admin/tenants/{tid}/credits')
    async def credits(tid: str, request: Request):
        admin(request)
        data = await body(request, Credit)
        return service().store.credit(tid, data['delta_micro_usd'], data['reason'], idem(request))

    @app.get('/admin/tenants/{tid}/keys')
    async def keys(tid: str, request: Request):
        admin(request)
        return service().store.keys(tid)

    @app.post('/admin/tenants/{tid}/keys', status_code=201)
    async def create_key(tid: str, request: Request):
        admin(request)
        data = await body(request, KeyCreate)
        return service().store.create_key(tid, data['name'], data.get('expires_at'))

    @app.post('/admin/keys/{kid}/rotate')
    async def rotate(kid: str, request: Request):
        admin(request)
        data = await body(request, Rotate)
        return service().store.rotate(kid, data.get('expires_at'))

    @app.post('/admin/keys/{kid}/revoke')
    async def revoke(kid: str, request: Request):
        admin(request)
        return service().store.revoke(kid)

    @app.get('/admin/pricing')
    async def pricing(request: Request):
        admin(request)
        return service().store.price(settings.model)

    @app.put('/admin/pricing')
    async def set_pricing(request: Request):
        admin(request)
        data = await body(request, Price)
        if data['model'] != settings.model:
            raise ServiceError(404, 'model_not_found')
        return service().store.set_price(data)

    @app.get('/admin/usage')
    async def all_usage(request: Request):
        admin(request)
        tid = request.query_params.get('tenant_id')
        if not tid:
            raise ServiceError(422, 'tenant_id_required')
        return usage_query(request, tid)

    @app.get('/admin/audit')
    async def audits(request: Request):
        admin(request)
        try:
            cursor = int(request.query_params.get('cursor', 0))
            if cursor < 0:
                raise ValueError()
        except ValueError:
            raise ServiceError(422, 'invalid_cursor') from None
        return service().store.audits(cursor)

    security_headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                        'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
                        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}

    @app.get('/console')
    async def console():
        return HTMLResponse((ROOT / 'console/index.html').read_text(encoding='utf-8'), headers=security_headers)

    @app.get('/console/{asset}')
    async def assets(asset: str):
        if asset not in ('app.js', 'style.css'):
            raise ServiceError(404, 'not_found')
        return Response((ROOT / 'console' / asset).read_bytes(), media_type='text/javascript' if asset.endswith('.js') else 'text/css', headers=security_headers)

    return app
