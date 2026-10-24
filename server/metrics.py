from collections import Counter, deque
import time
import json
import subprocess


class Metrics:
    def __init__(self):
        self.started = time.monotonic()
        self.counters = Counter({k: 0 for k in ('completed', 'rejected', 'cancelled', 'timed_out', 'failed', 'abort_acknowledged', 'abort_failed')})
        self.reasons = Counter()
        self.recent = deque(maxlen=200)
        self.gpu = None

    def snapshot(self, admission):
        return {'active': admission.active, 'queued': admission.queued, 'counters': dict(self.counters),
                'rejection_reasons': dict(self.reasons), 'uptime_seconds': time.monotonic() - self.started,
                'gpu_memory': self.gpu, 'recent_requests': list(self.recent)}

    def sample_gpu(self, data_dir):
        from server.supervisor import owned
        try:
            manifest=json.loads((data_dir/'processes.json').read_text())
            pids=set()
            for item in manifest.get('processes',[]):
                process=owned(item)
                if process:
                    pids.add(process.pid)
                    pids.update(p.pid for p in process.children(recursive=True))
            result=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader,nounits'],
                                  capture_output=True,text=True,timeout=3,check=True)
            values=[]
            for line in result.stdout.splitlines():
                parts=line.split(',')
                if len(parts)==2 and parts[0].strip().isdigit() and int(parts[0]) in pids:
                    values.append(int(parts[1].strip()))
            current=sum(values) if values else None
            previous=(self.gpu or {}).get('peak_observed_mib') or 0
            self.gpu={'current_mib':current,'peak_observed_mib':max(previous,current) if current is not None else previous or None,
                      'scope':'owned processes','source':'nvidia-smi','observed':bool(values)}
        except (OSError,ValueError,subprocess.SubprocessError):
            self.gpu={'current_mib':None,'source':'nvidia-smi','observed':False}
