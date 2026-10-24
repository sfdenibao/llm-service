CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tenants (
 tenant_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
 balance_micro_usd INTEGER NOT NULL CHECK(balance_micro_usd >= 0),
 reserved_micro_usd INTEGER NOT NULL DEFAULT 0 CHECK(reserved_micro_usd >= 0),
 token_limit INTEGER NOT NULL, used_tokens INTEGER NOT NULL DEFAULT 0,
 reserved_tokens INTEGER NOT NULL DEFAULT 0 CHECK(reserved_tokens >= 0),
 rpm_limit INTEGER NOT NULL, concurrency_limit INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
 key_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
 name TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL, masked_key TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, expires_at TEXT
);
CREATE TABLE IF NOT EXISTS pricing (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, pricing_version TEXT UNIQUE NOT NULL,
 model TEXT NOT NULL, input_micro_usd_per_token INTEGER NOT NULL,
 output_micro_usd_per_token INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL,
 tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), key_id TEXT NOT NULL REFERENCES api_keys(key_id),
 route TEXT NOT NULL, parent_request_id TEXT, model TEXT NOT NULL, status TEXT NOT NULL,
 pricing_version TEXT NOT NULL, input_price INTEGER NOT NULL, output_price INTEGER NOT NULL,
 reserved_micro_usd INTEGER NOT NULL, reserved_tokens INTEGER NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER, cost_micro_usd INTEGER,
 trusted_input INTEGER, trusted_output INTEGER, trusted_cached INTEGER,
 idempotency_hash TEXT, body_hash TEXT NOT NULL, engine_started INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, usage_state TEXT NOT NULL DEFAULT 'unknown',
 created_at TEXT NOT NULL, created_epoch REAL NOT NULL, finished_at TEXT,
 error_code TEXT, queue_ms REAL, duration_ms REAL,
 UNIQUE(tenant_id, route, idempotency_hash)
);
CREATE INDEX IF NOT EXISTS requests_tenant_time ON requests(tenant_id, created_epoch);
CREATE TABLE IF NOT EXISTS adjustments (
 adjustment_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
 delta_micro_usd INTEGER NOT NULL, reason TEXT NOT NULL, idempotency_hash TEXT NOT NULL,
 body_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(tenant_id, idempotency_hash)
);
CREATE TABLE IF NOT EXISTS audit (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, tenant_id TEXT,
 resource_id TEXT, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
PRAGMA user_version = 1;
