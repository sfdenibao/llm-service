"""Run the unmodified public HTTP checker against an owned local CPU stack."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.support import LocalStack


async def run(stack):
    source=ROOT/'tools/upstream/soft_check.py'
    spec=importlib.util.spec_from_file_location('public_soft_check',source)
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    report=await checker.run_checks(stack.base,stack.fixture,stack.env['API_KEY'],stack.env['ADMIN_API_KEY'],
        stack.env['FIXTURE_CONTROL_KEY'],stack.env['MODEL_ID'],stack.env['MODEL_REVISION'])
    report['execution_environment']='local-windows-cpu' if sys.platform=='win32' else 'local-linux-cpu'
    report['github_actions_run']=False
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    if args.out.exists():raise SystemExit('Use a new evidence directory')
    stack=LocalStack(args.out)
    try:
        stack.start()
        report=asyncio.run(run(stack))
        (args.out/'soft-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':report['passed'],'summary':report['summary'],'github_actions_run':False,'official_score':False}))
        return 0 if report['passed'] else 1
    finally:stack.stop()


if __name__=='__main__':raise SystemExit(main())
