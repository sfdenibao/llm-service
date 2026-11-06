import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import re
import signal
import sys
import threading
import traceback
from experiments.common import manifest, require_cuda, write_json
from experiments.config import ExperimentSettings, cache_environment
from server.supervisor import stopped_services


class ExperimentTerminated(RuntimeError):
    pass


@contextmanager
def termination_guard():
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous=signal.getsignal(signal.SIGTERM)
    def terminate(signum, frame):
        # Allow case finally blocks to finish even if the controller sends TERM again.
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        raise ExperimentTerminated('Experiment interrupted by SIGTERM')
    signal.signal(signal.SIGTERM,terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM,previous)


def failure_details(error):
    # Keep stack frames and the real failure reason, without locals or runtime credentials.
    detail=''.join(traceback.TracebackException.from_exception(error, capture_locals=False).format())
    for name,value in os.environ.items():
        if value and re.search(r'KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL',name,re.I):
            detail=detail.replace(value,'[REDACTED]')
    detail=re.sub(r'(?i)(Bearer\s+)\S+',r'\1[REDACTED]',detail)
    return detail[-64000:]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=('kv','prefix','attention'),required=True)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    info=manifest(args.case);write_json(out/'manifest.json',info)
    passed=False
    with termination_guard():
        try:
            if os.environ.get('CONTRACT_TEST')=='1':
                raise RuntimeError('Mechanism experiments require a real GPU environment')
            require_cuda(info)
            settings=ExperimentSettings.load()
            # These experiments own the same GPU. Stop only this project's processes first.
            with stopped_services() as saved_cache, cache_environment(saved_cache):
                if args.case=='kv':
                    from experiments.kv import run
                elif args.case=='attention':
                    from experiments.attention import run
                else:
                    from experiments.prefix import run
                passed=run(out,info,settings)
            info['complete']=True
        except Exception as error:
            info['complete']=False
            write_json(out/'failure.json',{'type':type(error).__name__,'traceback':failure_details(error)})
            info['failure']={'type':type(error).__name__,'evidence':'failure.json','message':'Experiment interrupted; inspect failure.json. No success is claimed.'}
            print('Experiment incomplete: '+type(error).__name__,file=sys.stderr)
        finally:write_json(out/'manifest.json',info)
    return 0 if info['complete'] and passed else 1


if __name__=='__main__':raise SystemExit(main())
