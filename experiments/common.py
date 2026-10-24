from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
from server.config import ROOT


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def tensor_hash(value):
    import torch
    return hashlib.sha256(value.detach().contiguous().view(torch.uint8).cpu().numpy().tobytes()).hexdigest()


def write_json(path, value):
    text=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if len(text.encode())>256000:raise ValueError('Evidence JSON exceeds 256000 bytes')
    if path.is_symlink():raise ValueError('Evidence cannot be a symbolic link')
    path.write_text(text,encoding='utf-8',newline='\n')


def append_row(path,value):
    with path.open('a',encoding='utf-8',newline='\n') as f:f.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n')
    if path.stat().st_size>256000:raise ValueError('Raw evidence exceeds 256000 bytes')


def manifest(case):
    commit=subprocess.run(['git','rev-parse','--verify','HEAD'],cwd=ROOT,capture_output=True,text=True)
    packages={}
    for p in ('torch','transformers','sglang'):
        try:packages[p]=importlib.metadata.version(p)
        except importlib.metadata.PackageNotFoundError:packages[p]=None
    return {'case':case,'time':datetime.now(timezone.utc).isoformat(),'commit':commit.stdout.strip() or None,
            'model':None,'revision':None,'gpu':None,'software':packages,'cuda':None,'complete':False,'parameters':{}}


def require_cuda(info):
    import torch
    if not torch.cuda.is_available():raise RuntimeError('CUDA is not available')
    gpu=subprocess.run(['nvidia-smi','--query-gpu=name,uuid,driver_version,memory.total','--format=csv,noheader'],check=True,capture_output=True,text=True,timeout=10)
    info.update(gpu=gpu.stdout.rstrip(),cuda=torch.version.cuda)
    return torch
