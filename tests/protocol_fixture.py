"""TEST ONLY: signal-gated HTTP fixture, UTF-8 fragmentation, and synthetic Agent responses.

Production server never imports this module and cannot access its control credential.
The official fixture is unchanged and used separately for the 18 public checks.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import threading
from http.server import ThreadingHTTPServer

source=Path(__file__).resolve().parents[1]/'tools/upstream/credential_fixture.py'
spec=importlib.util.spec_from_file_location('original_fixture',source)
original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
GATE=threading.Event()
MODE=''


class Handler(original.Handler):
    def do_POST(self):
        global MODE
        if self.path in ('/__test/configure','/__test/release'):
            if not self.allowed():return self.send(401,{})
            value=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            if self.path=='/__test/configure':
                MODE=value.get('mode','');GATE.clear()
            else:GATE.set()
            return self.send(200,{'ok':True})
        if self.path!='/v1/chat/completions' or not MODE:return super().do_POST()
        data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
        rid=data.get('rid');mode=MODE
        with original.LOCK:original.CALLS.append({'rid':rid,'stream':bool(data.get('stream')),'mode':mode})
        usage={'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}
        if mode=='agent':
            messages=data['messages']
            if messages[-1]['content'].startswith('lookup_order tool result: '):
                content=messages[-1]['content'].split(': ',1)[1]
            else:
                match=re.search(r'order\s+([A-Za-z0-9-]+)',messages[-1]['content'],re.I)
                content=json.dumps({'name':'lookup_order','arguments':{'order_id':match.group(1) if match else 'missing'}})
            return self.send(200,{'id':rid,'model':original.MODEL,'choices':[{'message':{'role':'assistant','content':content},'finish_reason':'stop'}],'usage':usage})
        if not data.get('stream'):
            if mode=='gate':GATE.wait(10)
            return self.send(200,{'id':rid,'model':original.MODEL,'choices':[{'message':{'role':'assistant','content':'fixture result'},'finish_reason':'stop'}],'usage':usage})
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        def encode(v):return ('data: '+(v if isinstance(v,str) else json.dumps(v,ensure_ascii=False))+'\n\n').encode()
        try:
            raw=encode({'id':rid,'choices':[{'delta':{'content':'第一段'},'finish_reason':None}]})
            # Split in the middle of a multibyte Chinese codepoint, not just between events.
            i=raw.index('第'.encode())+1
            for part in (raw[:i],raw[i:i+1],raw[i+1:]):self.wfile.write(part);self.wfile.flush()
            if mode=='gate':GATE.wait(10)
            if mode=='invalid_json':
                self.wfile.write(b'data: {broken\n\n');self.wfile.flush();return
            tail=encode({'id':rid,'choices':[{'delta':{'content':'第二段'},'finish_reason':None}]})
            tail+=encode({'id':rid,'choices':[{'delta':{},'finish_reason':'stop'}]})
            tail+=encode({'id':rid,'choices':[],'usage':usage})
            if mode!='missing_done':tail+=encode('[DONE]')
            self.wfile.write(tail);self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass


if __name__=='__main__':
    ThreadingHTTPServer(('127.0.0.1',int(os.environ['FIXTURE_PORT'])),Handler).serve_forever()
