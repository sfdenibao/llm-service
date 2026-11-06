"""Configuration for standalone GPU experiments, independent of gateway credentials."""
from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import os
from urllib.parse import urlsplit

import yaml

from server.config import MODEL, REVISION, ROOT


@contextmanager
def cache_environment(saved):
    # Hub libraries resolve these settings at import time, so enter before loading a case.
    names = ('HF_HOME', 'HF_HUB_CACHE', 'HUGGINGFACE_HUB_CACHE')
    inherited = {} if any(name in os.environ for name in names) else {
        name: value for name, value in (saved or {}).items()
        if name in ('HF_HOME', 'HF_HUB_CACHE')
    }
    try:
        os.environ.update(inherited)
        yield
    finally:
        for name in inherited:
            os.environ.pop(name, None)


@dataclass
class ExperimentSettings:
    model: str
    revision: str
    profile: str
    config: dict
    engine_url: str

    @classmethod
    def load(cls):
        profile = os.environ.get('BENCH_PROFILE', 'optimized')
        if profile not in ('baseline', 'optimized'):
            raise ValueError('BENCH_PROFILE must be baseline or optimized')
        config = yaml.safe_load((ROOT / 'configs' / (profile + '.yaml')).read_text())
        engine_url = os.environ.get('ENGINE_BASE_URL', 'http://127.0.0.1:30001').rstrip('/')
        parsed = urlsplit(engine_url)
        if (parsed.scheme != 'http' or not ipaddress.ip_address(parsed.hostname).is_loopback
                or not parsed.port or parsed.path or parsed.query or parsed.fragment or parsed.username):
            raise ValueError('Engine must be an explicit loopback HTTP origin')
        return cls(model=os.environ.get('MODEL_ID', MODEL),
                   revision=os.environ.get('MODEL_REVISION', REVISION),
                   profile=profile, config=config, engine_url=engine_url)
