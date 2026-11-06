set -euo pipefail
target=/home/daytona/llm-cold-validation
test ! -e "$target"
mkdir -p "$target"
cd "$target"
git init -q
git remote add origin https://github.com/sfdenibao/llm-service.git
git fetch --depth=1 origin 5d1d2eea583baec41b047366a2a9e84a6aaf75d3 >/dev/null 2>&1
git checkout --detach 5d1d2eea583baec41b047366a2a9e84a6aaf75d3 >/dev/null 2>&1
test "$(git rev-parse HEAD)" = 5d1d2eea583baec41b047366a2a9e84a6aaf75d3
mkdir -p .runtime results/cold-start-2026-11-06
python3 - <<'PY'
import os,secrets,shlex
from pathlib import Path
p=Path('.runtime/practice.env')
values={k:secrets.token_urlsafe(40) for k in ('API_KEY','ADMIN_API_KEY','KEY_HASH_SECRET')}
values.update(HOST='127.0.0.1',PORT='8000',BENCH_PROFILE='baseline',HF_HOME='/opt/llm-cache/huggingface',PIP_FIND_LINKS='/opt/llm-cache/wheels',PIP_CACHE_DIR=str(Path('.runtime/pip-cache').resolve()),SETUP_TIMEOUT_SECONDS='540',PIP_PROGRESS_BAR='off',PYTHONUNBUFFERED='1')
p.write_text(''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in values.items()));p.chmod(0o600)
PY
source .runtime/practice.env
validation_deadline=$(( $(date +%s) + 650 ))
bounded() {
  limit=$1
  shift
  left=$(( validation_deadline - $(date +%s) ))
  if test "$left" -le 0; then return 124; fi
  if test "$left" -lt "$limit"; then limit=$left; fi
  timeout --signal=TERM --kill-after=5s "$limit" "$@"
}
python3 - <<'PY'
import datetime,hashlib,json,os,platform,subprocess,sysconfig
from pathlib import Path
out=Path('results/cold-start-2026-11-06')
absent={p:not Path(p).exists() for p in ('.venv','.venv-gpu','.venv-bench','.runtime/data/processes.json','.runtime/pip-cache')}
assert all(absent.values()),absent
data={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'fresh_paths_absent':absent,'gpu':subprocess.check_output(['nvidia-smi','--query-gpu=name,uuid,memory.total,driver_version','--format=csv,noheader'],text=True).strip(),'python':platform.python_version(),'platform':platform.platform(),'python_headers_present_before_setup':Path(sysconfig.get_path('include'),'Python.h').is_file(),'hf_home':os.environ['HF_HOME'],'model_cache_scope':'Platform-provided HF model cache and wheel directory are retained; project, virtualenvs and pip download cache are new. This does not claim every wheel or model was downloaded from the Internet.','pip_cache_dir':os.environ['PIP_CACHE_DIR'],'pip_find_links':os.environ['PIP_FIND_LINKS'],'locks':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('.').glob('requirements*.lock')}}
(out/'preflight.json').write_text(json.dumps(data,indent=2))
print(json.dumps({'fresh_paths_absent':absent,'commit':data['commit'],'gpu':data['gpu']}),flush=True)
PY
finish() {
  result=$?
  trap - EXIT
  set +e
  timeout 40 bash setup.sh --stop > .runtime/cold-stop.txt 2>&1
  stop_result=$?
  export COLD_EXIT_CODE="$result" COLD_STOP_EXIT="$stop_result"
  python3 - <<'PY'
import datetime,hashlib,json,os,subprocess
from pathlib import Path
out=Path('results/cold-start-2026-11-06');logs=out/'logs';logs.mkdir(exist_ok=True)
private=[os.environ[k].encode() for k in ('API_KEY','ADMIN_API_KEY','KEY_HASH_SECRET')]
files=list(Path('.runtime').glob('cold-*.txt'))+list(Path('.runtime').glob('cold-*.json'))+list(Path('.runtime/data').glob('*.log'))
files+=list(Path('.runtime').rglob('install*.log'))+list(Path('.runtime').rglob('pip*.log'))
files+=list(Path('.runtime').glob('setup-*/**/*.log'))
for p in sorted(set(files)):
 if p.is_file():
  value=p.read_bytes()
  for token in private:value=value.replace(token,b'[REDACTED]')
  (logs/str(p.relative_to('.runtime')).replace('/','__')).write_bytes(value)
manifest=Path('.runtime/data/processes.json')
state=json.loads(manifest.read_text()) if manifest.exists() else {'processes':[]}
summary={'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'run_exit_code':int(os.environ['COLD_EXIT_CODE']),'stop_exit_code':int(os.environ['COLD_STOP_EXIT']),'owned_processes_after_stop':state['processes'],'gateway_pid_exists':Path('.runtime/data/gateway.pid').exists(),'gpu_snapshot':subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,utilization.gpu','--format=csv,noheader'],text=True).strip(),'official_evaluation':False,'cloud_machine_still_allocated':True}
(out/'completion.json').write_text(json.dumps(summary,indent=2))
leaks=[str(p.relative_to(out)) for p in out.rglob('*') if p.is_file() and any(token in p.read_bytes() for token in private)]
assert not leaks,'credential leak in '+','.join(leaks)
(out/'credential-scan.json').write_text(json.dumps({'passed':True,'scope':'Exact runtime credential values scanned; all values omitted','files':sum(p.is_file() for p in out.rglob('*'))},indent=2))
summary['validation_passed'] = (summary['run_exit_code'] == 0 and summary['stop_exit_code'] == 0 and not summary['owned_processes_after_stop'] and not summary['gateway_pid_exists'])
(out/'completion.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary),flush=True)
assert summary['validation_passed'], 'Validation or owned-process cleanup failed'
PY
  evidence_result=$?
  if test "$result" = 0 && test "$evidence_result" != 0; then result=$evidence_result; fi
  exit "$result"
}
trap finish EXIT
date -u +%FT%TZ
echo baseline-cold > .runtime/cold-stage.txt
begin=$(date +%s)
set +e
bounded 570 bash setup.sh > .runtime/cold-baseline-setup.txt 2>&1
baseline_exit=$?
set -e
elapsed=$(( $(date +%s) - begin ))
printf '{"profile":"baseline","seconds":%s,"exit_code":%s,"fresh_virtualenv":true}\n' "$elapsed" "$baseline_exit" > .runtime/cold-baseline-timing.json
test "$baseline_exit" = 0
tail -n 3 .runtime/cold-baseline-setup.txt
bounded 35 .venv-bench/bin/python tools/public-2026-11-06/selftest.py check --base-url http://127.0.0.1:8000 --verify-tokens --out results/cold-start-2026-11-06/baseline-selftest.json > .runtime/cold-baseline-selftest.txt 2>&1
echo optimized-switch > .runtime/cold-stage.txt
export BENCH_PROFILE=optimized SETUP_TIMEOUT_SECONDS=95
begin=$(date +%s)
bounded 115 bash setup.sh > .runtime/cold-optimized-setup.txt 2>&1
elapsed=$(( $(date +%s) - begin ))
printf '{"profile":"optimized","seconds":%s,"exit_code":0,"fresh_virtualenv":false}\n' "$elapsed" > .runtime/cold-optimized-timing.json
bounded 35 .venv-bench/bin/python tools/public-2026-11-06/selftest.py check --base-url http://127.0.0.1:8000 --verify-tokens --out results/cold-start-2026-11-06/optimized-selftest.json > .runtime/cold-optimized-selftest.txt 2>&1
echo verification > .runtime/cold-stage.txt
export TEST_RESULTS="$PWD/results/cold-start-2026-11-06/installer-test"
bounded 40 .venv-gpu/bin/python -m unittest tests.test_bootstrap_install.BootstrapInstallTests.test_real_pip_recovers_after_local_wheel_body_read_timeout > .runtime/cold-installer-test.txt 2>&1
bounded 35 .venv-gpu/bin/python scripts/check_control_plane.py --base-url http://127.0.0.1:8000 --suite live --out results/cold-start-2026-11-06/control-live.json > .runtime/cold-control-live.txt 2>&1
bounded 80 .venv-gpu/bin/python - <<'PY'
import datetime,hashlib,importlib.metadata,json,os,subprocess,time
from pathlib import Path
import httpx,psutil
out=Path('results/cold-start-2026-11-06')
checks=[]
for name,files in (('.venv-gpu',['requirements-native-linux-py312.lock']),('.venv-bench',['requirements-benchmark.lock','requirements.lock'])):
 digest=hashlib.sha256(b''.join(Path(p).read_bytes() for p in files)).hexdigest()
 marker=Path(name)/'.dependencies-sha256'
 assert marker.read_text()==digest
 run=subprocess.run([str(Path(name)/'bin/python'),'-m','pip','check'],capture_output=True,text=True,timeout=20)
 checks.append({'environment':name,'exit_code':run.returncode,'output':run.stdout.strip(),'marker_matches_lock':True})
 assert run.returncode==0
(out/'dependency-checks.json').write_text(json.dumps(checks,indent=2))
with httpx.Client(timeout=10,trust_env=False) as c:
 headers={'Authorization':'Bearer '+os.environ['API_KEY']}
 for endpoint in ('info','metrics','v1/usage'):
  r=c.get('http://127.0.0.1:8000/'+endpoint,headers=headers);r.raise_for_status()
  (out/(endpoint.replace('/','-')+'.json')).write_text(json.dumps(r.json(),indent=2))
 r=c.get('http://127.0.0.1:30001/get_server_info');r.raise_for_status();native=r.json();native=native.get('server_args',native)
 keys=['model_path','served_model_name','attention_backend','dtype','context_length','max_running_requests','max_total_tokens','disable_radix_cache','sampling_defaults']
 state=json.loads(Path('.runtime/data/processes.json').read_text());engine=[]
 for item in state['processes']:
  if item.get('role')=='engine':
   p=psutil.Process(item['pid']);assert abs(p.create_time()-item['create_time'])<.01
   engine.append({'pid':p.pid,'executable':p.exe(),'command':p.cmdline()})
 assert engine
 audit={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'engines':engine,'native':{k:native[k] for k in keys if k in native},'software':{k:importlib.metadata.version(k) for k in ('sglang','torch','transformers','pip')}}
 (out/'runtime-audit.json').write_text(json.dumps(audit,indent=2))
before=json.loads(Path('.runtime/data/processes.json').read_text())
start=time.monotonic();run=subprocess.run(['bash','setup.sh'],capture_output=True,text=True,timeout=30);duration=time.monotonic()-start
after=json.loads(Path('.runtime/data/processes.json').read_text())
assert run.returncode==0 and before['processes']==after['processes']
(out/'idempotency.json').write_text(json.dumps({'seconds':duration,'exit_code':run.returncode,'owned_processes_preserved':True},indent=2))
print('Cold baseline, optimized switch, real service checks, dependency markers and idempotency verified',flush=True)
PY
echo complete > .runtime/cold-stage.txt
