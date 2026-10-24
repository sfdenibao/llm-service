import argparse
from pathlib import Path
import sys
from experiments.common import manifest, require_cuda, write_json
from server.config import Settings
from server.locking import exclusive_lock
from server.supervisor import stop


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=('kv','prefix','attention'),required=True)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    info=manifest(args.case);write_json(out/'manifest.json',info)
    passed=False
    try:
        torch=require_cuda(info)
        settings=Settings.load()
        if settings.contract_test:raise RuntimeError('Mechanism experiments require a real GPU environment')
        # These experiments own the same GPU. Stop only this project's processes first.
        with exclusive_lock(settings.data_dir/'supervisor.lock'):
            stop(settings.data_dir)
            if args.case=='kv':
                from experiments.kv import run
            elif args.case=='attention':
                from experiments.attention import run
            else:
                from experiments.prefix import run
            passed=run(out,info,settings)
        info['complete']=True
    except Exception as error:
        info['failure']={'type':type(error).__name__,'message':'Experiment interrupted; inspect local execution log. No success is claimed.'}
        print('Experiment incomplete: '+type(error).__name__,file=sys.stderr)
    finally:write_json(out/'manifest.json',info)
    return 0 if info['complete'] and passed else 1


if __name__=='__main__':raise SystemExit(main())
