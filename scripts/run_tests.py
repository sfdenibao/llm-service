"""Save real CPU unittest outcomes, retaining every failed run."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


class Results(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.rows=[]
    def addSuccess(self,test):super().addSuccess(test);self.rows.append({'test_id':test.id(),'passed':True,'actual':'All assertions passed'})
    def addFailure(self,test,err):super().addFailure(test,err);self.rows.append({'test_id':test.id(),'passed':False,'actual':'Assertion failed; see local test log'})
    def addError(self,test,err):super().addError(test,err);self.rows.append({'test_id':test.id(),'passed':False,'actual':'Execution error; see local test log'})


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=False);os.environ['TEST_RESULTS']=str(args.out.resolve())
    started=datetime.now(timezone.utc).isoformat()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py',top_level_dir=str(ROOT))
    with (args.out/'unittest.txt').open('w',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=Results).run(suite)
    commit=subprocess.run(['git','rev-parse','--verify','HEAD'],cwd=ROOT,capture_output=True,text=True)
    report={'schema':'llm-local-regression/1','suite':'CPU HTTP integration','commit_sha':commit.stdout.strip() or None,
            'execution_environment':sys.platform,'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),
            'tests_run':result.testsRun,'passed':result.wasSuccessful(),'checks':result.rows,'official_score':False,
            'not_tested':['Real GPU inference','Linux SIGKILL when running on Windows','GitHub Actions','Browser console','Performance SLO']}
    (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful()}))
    if not result.wasSuccessful():print((args.out/'unittest.txt').read_text(encoding='utf-8')[-12000:])
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
