from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit
import ipaddress
import yaml

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'Qwen/Qwen2.5-7B-Instruct'
REVISION = 'a09a35458c702b33eeacc393d103063234e8bc28'


@dataclass
class Settings:
    model: str = MODEL
    revision: str = REVISION
    host: str = '127.0.0.1'
    port: int = 8000
    engine_url: str = 'http://127.0.0.1:30001'
    profile: str = 'optimized'
    contract_test: bool = False
    data_dir: Path = field(default_factory=lambda: ROOT / '.runtime/data')
    api_key: str = field(default='', repr=False)
    admin_key: str = field(default='', repr=False)
    pepper: str = field(default='', repr=False)
    config: dict = field(default_factory=dict)

    @classmethod
    def load(cls):
        profile = os.environ.get('BENCH_PROFILE', 'optimized')
        if profile not in ('baseline', 'optimized'):
            raise ValueError('BENCH_PROFILE must be baseline or optimized')
        config = yaml.safe_load((ROOT / 'configs' / (profile + '.yaml')).read_text())
        for env, key, cast in [('GATEWAY_SLOTS', 'gateway_slots', int),
                               ('GATEWAY_QUEUE', 'gateway_queue', int),
                               ('QUEUE_TIMEOUT_SECONDS', 'queue_timeout_seconds', float),
                               ('EXECUTION_TIMEOUT_SECONDS', 'execution_timeout_seconds', float)]:
            if env in os.environ:
                config[key] = cast(os.environ[env])
        slots, queue = config['gateway_slots'], config['gateway_queue']
        if not (1 <= slots <= 64 and 0 <= queue <= 112 and slots + queue <= 120):
            raise ValueError('Invalid gateway capacity')
        if min(config['queue_timeout_seconds'], config['execution_timeout_seconds']) <= 0:
            raise ValueError('Timeouts must be positive')
        engine_url = os.environ.get('ENGINE_BASE_URL', 'http://127.0.0.1:30001').rstrip('/')
        parsed = urlsplit(engine_url)
        if (parsed.scheme != 'http' or not ipaddress.ip_address(parsed.hostname).is_loopback
                or not parsed.port or parsed.path or parsed.query or parsed.fragment or parsed.username):
            raise ValueError('Engine must be an explicit loopback HTTP origin')
        api, admin, pepper = (os.environ.get(k, '') for k in ('API_KEY', 'ADMIN_API_KEY', 'KEY_HASH_SECRET'))
        if len(api) < 32 or len(pepper) < 32:
            raise ValueError('API_KEY and KEY_HASH_SECRET require at least 32 characters')
        if admin and (len(admin) < 32 or admin == api):
            raise ValueError('ADMIN_API_KEY must be independent and at least 32 characters')
        return cls(model=os.environ.get('MODEL_ID', MODEL), revision=os.environ.get('MODEL_REVISION', REVISION),
                   host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')),
                   engine_url=engine_url, profile=profile, contract_test=os.environ.get('CONTRACT_TEST') == '1',
                   data_dir=Path(os.environ.get('DATA_DIR', ROOT / '.runtime/data')).resolve(),
                   api_key=api, admin_key=admin, pepper=pepper, config=config)
