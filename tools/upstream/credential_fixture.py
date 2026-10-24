"""Controller-owned deterministic HTTP model for credential contract tests.

Only the model is simulated. Submitted authentication, persistence, accounting,
quota and streaming implementations run unchanged. Never used for GPU scoring.
"""
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
PLAN = {'prompt_tokens': 100, 'completion_tokens': 50, 'delay': 0.0, 'omit_usage': False, 'fail_before': False, 'pause_after_usage': 0.0}
CALLS = []
ABORTED = set()
CONTROL_KEY = os.environ.get('FIXTURE_CONTROL_KEY', '')
MODEL = os.environ.get('FIXTURE_MODEL', 'Qwen/Qwen2.5-1.5B-Instruct')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # Never print model or credential-bearing traffic.
    def send(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == '/health': return self.send(200, {'status': 'ready'})
        if self.path == '/v1/models': return self.send(200, {'data': [{'id': MODEL}]})
        if self.path == '/__fixture/state':
            if not self.allowed(): return self.send(401, {})
            with LOCK: value = {'calls': list(CALLS), 'aborted': sorted(ABORTED)}
            return self.send(200, value)
        return self.send(404, {})
    def allowed(self):
        return bool(CONTROL_KEY) and secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+CONTROL_KEY)
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length > 1_000_000: return self.send(413, {})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict): return self.send(400, {})
        except (ValueError, TypeError): return self.send(400, {})
        if self.path == '/__fixture/configure':
            if not self.allowed(): return self.send(401, {})
            if set(data)-set(PLAN): return self.send(400, {})
            with LOCK: PLAN.update(data)
            return self.send(200, {'ok': True})
        if self.path == '/tokenize':
            with LOCK: count = PLAN['prompt_tokens']
            return self.send(200, {'count': count, 'input_ids': [1]*count, 'token_ids': [1]*count})
        if self.path == '/abort_request':
            with LOCK: ABORTED.add(str(data.get('rid')))
            return self.send(200, {'ok': True})
        if self.path != '/v1/chat/completions': return self.send(404, {})
        rid = str(data.get('rid', 'missing'))
        with LOCK:
            plan = dict(PLAN)
            CALLS.append({'rid': rid, 'prompt_tokens': plan['prompt_tokens'], 'completion_tokens': plan['completion_tokens'], 'stream': bool(data.get('stream')), 'failed': plan['fail_before'], 'usage_omitted': plan['omit_usage']})
        if plan['fail_before']: return self.send(503, {'error': {'message': 'fixture unavailable'}})
        usage = {'prompt_tokens': plan['prompt_tokens'], 'completion_tokens': plan['completion_tokens'], 'total_tokens': plan['prompt_tokens']+plan['completion_tokens']}
        if not data.get('stream'):
            time.sleep(plan['delay'])
            value = {'id': rid, 'model': MODEL, 'choices': [{'index': 0, 'message': {'role':'assistant','content':'fixture result'}, 'finish_reason':'stop'}]}
            if not plan['omit_usage']: value['usage'] = usage
            return self.send(200, value)
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        def event(obj):
            raw = obj if isinstance(obj,str) else json.dumps(obj)
            self.wfile.write(('data: '+raw+'\n\n').encode()); self.wfile.flush()
        try:
            event({'id':rid,'choices':[{'index':0,'delta':{'role':'assistant','content':'fixture '},'finish_reason':None}]})
            # Emit first content before a controlled delay, allowing real cancellation.
            time.sleep(plan['delay'])
            with LOCK: cancelled = rid in ABORTED
            if cancelled: return
            event({'id':rid,'choices':[{'index':0,'delta':{'content':'result'},'finish_reason':None}]})
            event({'id':rid,'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})
            if not plan['omit_usage']:
                event({'id':rid,'choices':[],'usage':usage})
                time.sleep(plan['pause_after_usage'])
            event('[DONE]')
        except (BrokenPipeError, ConnectionResetError): pass

if __name__ == '__main__':
    if len(CONTROL_KEY) < 32: raise SystemExit('FIXTURE_CONTROL_KEY required')
    ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('FIXTURE_PORT', '30001'))), Handler).serve_forever()
