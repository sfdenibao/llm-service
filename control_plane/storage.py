"""Single-host durable ledger. Every read/check/write invariant has one transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid

from server.config import ROOT
from server.errors import ServiceError


def now():
    return datetime.now(timezone.utc).isoformat()


def ident(prefix):
    return prefix + '_' + uuid.uuid4().hex


def body_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


class Store:
    def __init__(self, settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = settings.data_dir / 'ledger.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript((ROOT / 'migrations/001_initial.sql').read_text())
        with self.tx() as db:
            digest = self.digest('pepper-verification')
            stored = db.execute("SELECT value FROM metadata WHERE name='pepper_check'").fetchone()
            if stored and not hmac.compare_digest(stored['value'], digest):
                raise ValueError('KEY_HASH_SECRET changed for existing DATA_DIR')
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('pepper_check',?)", (digest,))
            if not db.execute('SELECT 1 FROM pricing WHERE model=?', (settings.model,)).fetchone():
                prices = json.loads((ROOT / 'configs/pricing.json').read_text(encoding='utf-8'))
                self._price(db, settings.model, prices['input_micro_usd_per_token'], prices['output_micro_usd_per_token'])
            tenant = db.execute("SELECT tenant_id FROM tenants WHERE tenant_id='tenant_benchmark'").fetchone()
            if not tenant:
                self._tenant(db, 'benchmark', 10**12, 10**12, 100000, 64, 'tenant_benchmark')
            digest = self.digest(settings.api_key)
            # The bootstrap key is created once. A revoked key is never reactivated on setup.
            if not db.execute('SELECT 1 FROM api_keys WHERE key_hash=?', (digest,)).fetchone():
                self._key(db, 'tenant_benchmark', 'environment benchmark', None, settings.api_key)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def tx(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def digest(self, raw):
        return hmac.new(self.settings.pepper.encode(), raw.encode(), hashlib.sha256).hexdigest()

    def audit(self, db, event, tenant=None, resource=None, detail=None):
        db.execute('INSERT INTO audit(event,tenant_id,resource_id,detail,created_at) VALUES(?,?,?,?,?)',
                   (event, tenant, resource, json.dumps(detail or {}, ensure_ascii=False), now()))

    def _tenant(self, db, name, balance, tokens, rpm, concurrency, tid=None):
        tid = tid or ident('tenant')
        db.execute('''INSERT INTO tenants(tenant_id,name,balance_micro_usd,token_limit,rpm_limit,
                      concurrency_limit,created_at) VALUES(?,?,?,?,?,?,?)''',
                   (tid, name, balance, tokens, rpm, concurrency, now()))
        self.audit(db, 'tenant_created', tid, tid)
        return dict(db.execute('SELECT * FROM tenants WHERE tenant_id=?', (tid,)).fetchone())

    def create_tenant(self, data):
        with self.tx() as db:
            return self._tenant(db, data['name'], data['balance_micro_usd'], data['token_limit'],
                                data['rpm_limit'], data['concurrency_limit'])

    def tenants(self):
        with self.connect() as db:
            return {'items': [dict(x) for x in db.execute('SELECT * FROM tenants ORDER BY created_at,tenant_id')]}

    def tenant(self, db, tid):
        row = db.execute('SELECT * FROM tenants WHERE tenant_id=?', (tid,)).fetchone()
        if row is None:
            raise ServiceError(404, 'not_found')
        return dict(row)

    def patch_tenant(self, tid, changes):
        allowed = {'name', 'status', 'token_limit', 'rpm_limit', 'concurrency_limit'}
        if not changes or set(changes) - allowed:
            raise ServiceError(422, 'invalid_tenant_fields')
        with self.tx() as db:
            self.tenant(db, tid)
            sql = ','.join(k + '=?' for k in changes)
            db.execute('UPDATE tenants SET ' + sql + ' WHERE tenant_id=?', (*changes.values(), tid))
            self.audit(db, 'tenant_updated', tid, tid, changes)
            return self.tenant(db, tid)

    def _key(self, db, tid, name, expires, raw=None):
        self.tenant(db, tid)
        raw = raw or ('sk_' + secrets.token_urlsafe(32))
        kid = ident('key')
        db.execute('''INSERT INTO api_keys(key_id,tenant_id,name,key_hash,masked_key,created_at,expires_at)
                      VALUES(?,?,?,?,?,?,?)''', (kid, tid, name, self.digest(raw), raw[:7] + '…' + raw[-4:], now(), expires))
        self.audit(db, 'key_created', tid, kid)
        return {'key_id': kid, 'api_key': raw, 'status': 'active', 'expires_at': expires}

    def create_key(self, tid, name, expires=None):
        with self.tx() as db:
            return self._key(db, tid, name, expires)

    def keys(self, tid):
        with self.connect() as db:
            self.tenant(db, tid)
            return {'items': [dict(r) for r in db.execute('''SELECT key_id,tenant_id,name,masked_key,status,
                created_at,expires_at FROM api_keys WHERE tenant_id=? ORDER BY created_at,key_id''', (tid,))]}

    def rotate(self, kid, expires=None):
        with self.tx() as db:
            key = db.execute('SELECT * FROM api_keys WHERE key_id=?', (kid,)).fetchone()
            if not key:
                raise ServiceError(404, 'not_found')
            if key['status'] != 'active':
                raise ServiceError(409, 'key_not_active')
            new = self._key(db, key['tenant_id'], key['name'], expires)
            db.execute("UPDATE api_keys SET status='revoked' WHERE key_id=?", (kid,))
            self.audit(db, 'key_rotated', key['tenant_id'], kid, {'replacement_key_id': new['key_id']})
            return {**new, 'replaced_key_id': kid, 'replaced_key_status': 'revoked'}

    def revoke(self, kid):
        with self.tx() as db:
            key = db.execute('SELECT * FROM api_keys WHERE key_id=?', (kid,)).fetchone()
            if not key:
                raise ServiceError(404, 'not_found')
            if key['status'] != 'revoked':
                db.execute("UPDATE api_keys SET status='revoked' WHERE key_id=?", (kid,))
                self.audit(db, 'key_revoked', key['tenant_id'], kid)
            return {'key_id': kid, 'status': 'revoked'}

    def authenticate(self, raw):
        if not raw or len(raw) > 1024:
            raise ServiceError(401, 'invalid_api_key')
        with self.connect() as db:
            key = db.execute('SELECT * FROM api_keys WHERE key_hash=?', (self.digest(raw),)).fetchone()
            if not key or key['status'] != 'active' or (key['expires_at'] and key['expires_at'] <= now()):
                raise ServiceError(401, 'invalid_api_key')
            tenant = self.tenant(db, key['tenant_id'])
            if tenant['status'] != 'active':
                raise ServiceError(403, 'tenant_disabled')
            return {'tenant_id': key['tenant_id'], 'key_id': key['key_id']}

    def _price(self, db, model, inp, out):
        version = ident('price')
        db.execute('''INSERT INTO pricing(pricing_version,model,input_micro_usd_per_token,
                      output_micro_usd_per_token,created_at) VALUES(?,?,?,?,?)''', (version, model, inp, out, now()))
        self.audit(db, 'pricing_updated', resource=version, detail={'model': model, 'input': inp, 'output': out})
        return dict(db.execute('SELECT * FROM pricing WHERE pricing_version=?', (version,)).fetchone())

    def price(self, model):
        with self.connect() as db:
            row = db.execute('SELECT * FROM pricing WHERE model=? ORDER BY sequence DESC LIMIT 1', (model,)).fetchone()
            if not row:
                raise ServiceError(404, 'model_not_found')
            return dict(row)

    def set_price(self, data):
        with self.tx() as db:
            return self._price(db, data['model'], data['input_micro_usd_per_token'], data['output_micro_usd_per_token'])

    def credit(self, tid, delta, reason, idem):
        if not idem:
            raise ServiceError(400, 'idempotency_key_required')
        digest, bh = self.digest(idem), body_hash({'delta': delta, 'reason': reason})
        with self.tx() as db:
            tenant = self.tenant(db, tid)
            old = db.execute('SELECT * FROM adjustments WHERE tenant_id=? AND idempotency_hash=?', (tid, digest)).fetchone()
            if old:
                if old['body_hash'] != bh:
                    raise ServiceError(409, 'idempotency_conflict')
                return {'adjustment_id': old['adjustment_id'], 'balance_micro_usd': tenant['balance_micro_usd']}
            balance = tenant['balance_micro_usd'] + delta
            if balance < tenant['reserved_micro_usd'] or balance > 9_000_000_000_000_000:
                raise ServiceError(402, 'insufficient_quota')
            aid = ident('adjustment')
            db.execute('INSERT INTO adjustments VALUES(?,?,?,?,?,?,?)', (aid, tid, delta, reason, digest, bh, now()))
            db.execute('UPDATE tenants SET balance_micro_usd=? WHERE tenant_id=?', (balance, tid))
            self.audit(db, 'credit_adjustment', tid, aid, {'delta_micro_usd': delta, 'reason': reason})
            return {'adjustment_id': aid, 'balance_micro_usd': balance}

    def _idempotency(self, db, tid, route, idem, bh):
        if not idem:
            return
        old = db.execute('SELECT * FROM requests WHERE tenant_id=? AND route=? AND idempotency_hash=?',
                         (tid, route, self.digest(idem))).fetchone()
        if old:
            code = 'idempotency_conflict' if old['body_hash'] != bh else (
                'request_in_progress' if old['active'] else 'request_already_completed')
            raise ServiceError(409, code, headers={'X-Request-ID': old['request_id']})

    def check_idempotency(self, who, route, idem, payload):
        with self.connect() as db:
            self._idempotency(db, who['tenant_id'], route, idem, body_hash(payload))

    def reserve(self, who, route, payload, count, max_tokens, idem=None, parent=None):
        tid, kid = who['tenant_id'], who['key_id']
        bh = body_hash(payload)
        with self.tx() as db:
            self._idempotency(db, tid, route, idem, bh)
            tenant = self.tenant(db, tid)
            if tenant['status'] != 'active':
                raise ServiceError(403, 'tenant_disabled')
            # Agent child calls share the parent concurrency slot; tokens/fees still reserve independently.
            if not parent:
                active = db.execute('SELECT count(*) FROM requests WHERE tenant_id=? AND active=1 AND parent_request_id IS NULL', (tid,)).fetchone()[0]
                if active >= tenant['concurrency_limit']:
                    raise ServiceError(429, 'concurrency_limit', headers={'Retry-After': '1'})
                recent = db.execute('SELECT count(*) FROM requests WHERE tenant_id=? AND parent_request_id IS NULL AND created_epoch>?', (tid, time.time() - 60)).fetchone()[0]
                if recent >= tenant['rpm_limit']:
                    raise ServiceError(429, 'rpm_limit', headers={'Retry-After': '60'})
            price = db.execute('SELECT * FROM pricing WHERE model=? ORDER BY sequence DESC LIMIT 1', (self.settings.model,)).fetchone()
            amount = count * price['input_micro_usd_per_token'] + max_tokens * price['output_micro_usd_per_token']
            tokens = count + max_tokens
            if tenant['balance_micro_usd'] - tenant['reserved_micro_usd'] < amount or tenant['used_tokens'] + tenant['reserved_tokens'] + tokens > tenant['token_limit']:
                raise ServiceError(402, 'insufficient_quota')
            rid = ident('req')
            db.execute('UPDATE tenants SET reserved_micro_usd=reserved_micro_usd+?,reserved_tokens=reserved_tokens+? WHERE tenant_id=?', (amount, tokens, tid))
            db.execute('''INSERT INTO requests(request_id,tenant_id,key_id,route,parent_request_id,model,status,
                pricing_version,input_price,output_price,reserved_micro_usd,reserved_tokens,idempotency_hash,
                body_hash,created_at,created_epoch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (rid, tid, kid, route, parent, self.settings.model, 'reserved', price['pricing_version'],
                 price['input_micro_usd_per_token'], price['output_micro_usd_per_token'], amount, tokens,
                 self.digest(idem) if idem else None, bh, now(), time.time()))
            return rid

    def engine_started(self, rid):
        with self.tx() as db:
            db.execute("UPDATE requests SET engine_started=1,status='running' WHERE request_id=?", (rid,))

    def journal(self, rid, usage):
        inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
        cached = usage.get('prompt_tokens_details', {}).get('cached_tokens', 0)
        if not all(type(x) is int and x >= 0 for x in (inp, out, cached)) or cached > inp:
            raise ServiceError(502, 'invalid_engine_usage')
        with self.tx() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (rid,)).fetchone()
            if inp + out > row['reserved_tokens'] or inp * row['input_price'] + out * row['output_price'] > row['reserved_micro_usd']:
                raise ServiceError(502, 'engine_usage_exceeds_reservation')
            if row['trusted_input'] is not None and (row['trusted_input'], row['trusted_output']) != (inp, out):
                raise ServiceError(502, 'inconsistent_engine_usage')
            db.execute('UPDATE requests SET trusted_input=?,trusted_output=?,trusted_cached=? WHERE request_id=?', (inp, out, cached, rid))

    def _settle(self, db, row, status, error=None, unknown_release=False):
        if row['usage_state'] in ('exact', 'not_started', 'unknown_released'):
            return
        known = row['trusted_input'] is not None
        entered = row['engine_started'] == 1
        if not known and entered and not unknown_release:
            db.execute("UPDATE requests SET active=0,status='pending_reconciliation',error_code=?,finished_at=? WHERE request_id=?", (error, now(), row['request_id']))
            return
        inp = row['trusted_input'] if known else (None if entered else 0)
        out = row['trusted_output'] if known else (None if entered else 0)
        cost = inp * row['input_price'] + out * row['output_price'] if known else (None if entered else 0)
        usage_state = 'exact' if known else ('unknown_released' if entered else 'not_started')
        db.execute('''UPDATE tenants SET balance_micro_usd=balance_micro_usd-?, used_tokens=used_tokens+?,
            reserved_micro_usd=reserved_micro_usd-?,reserved_tokens=reserved_tokens-? WHERE tenant_id=?''',
            (cost or 0, (inp or 0) + (out or 0), row['reserved_micro_usd'], row['reserved_tokens'], row['tenant_id']))
        db.execute('''UPDATE requests SET input_tokens=?,output_tokens=?,cached_tokens=?,cost_micro_usd=?,
            reserved_micro_usd=0,reserved_tokens=0,active=0,status=?,usage_state=?,finished_at=?,error_code=? WHERE request_id=?''',
            (inp, out, row['trusted_cached'] if known else (None if entered else 0), cost, status, usage_state, now(), error, row['request_id']))
        if entered and not known:
            self.audit(db, 'credit_adjustment', row['tenant_id'], row['request_id'],
                       {'reason': 'usage_unknown_after_interruption', 'released_micro_usd': row['reserved_micro_usd'],
                        'unbilled_input_tokens': None, 'unbilled_output_tokens': None, 'unbilled_cost_micro_usd': None})

    def finish(self, rid, status, error=None):
        with self.tx() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (rid,)).fetchone()
            self._settle(db, row, status, error)

    def timings(self, rid, queue_ms, duration_ms):
        with self.tx() as db:
            db.execute('UPDATE requests SET queue_ms=?,duration_ms=? WHERE request_id=?', (queue_ms, duration_ms, rid))

    def unfinished(self, restart=False):
        with self.connect() as db:
            sql = "SELECT * FROM requests WHERE usage_state='unknown'"
            if not restart:
                sql += " AND active=0 AND status='pending_reconciliation' AND created_epoch<?"
                return [dict(r) for r in db.execute(sql, (time.time() - 5,))]
            return [dict(r) for r in db.execute(sql)]

    def recover(self, rid):
        with self.tx() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (rid,)).fetchone()
            self._settle(db, row, 'interrupted', 'recovered_after_interruption', unknown_release=True)

    def usage(self, tid, cursor=0, limit=100, from_date=None, to_date=None):
        with self.connect() as db:
            db.execute('BEGIN')  # One read snapshot for details, totals and grouped views.
            tenant = self.tenant(db, tid)
            clauses, args = ['tenant_id=?'], [tid]
            if from_date:
                clauses.append('created_at>=?'); args.append(from_date)
            if to_date:
                clauses.append('created_at<=?'); args.append(to_date)
            where = ' AND '.join(clauses)
            columns = '''sequence,request_id,route,parent_request_id,tenant_id,key_id,model,status,
                input_tokens,output_tokens,cached_tokens,cost_micro_usd,pricing_version,created_at,finished_at,
                usage_state,reserved_micro_usd,reserved_tokens,error_code,queue_ms,duration_ms'''
            rows = [dict(x) for x in db.execute(f'SELECT {columns} FROM requests WHERE {where} AND sequence>? ORDER BY sequence LIMIT ?', (*args, cursor, limit + 1))]
            total = dict(db.execute(f'''SELECT count(*) request_count,coalesce(sum(input_tokens),0) input_tokens,
                coalesce(sum(output_tokens),0) output_tokens,coalesce(sum(cost_micro_usd),0) cost_micro_usd,
                coalesce(sum(status='cancelled'),0) cancelled_count,
                coalesce(sum(status IN ('failed','timed_out','interrupted','pending_reconciliation')),0) failed_count,
                coalesce(sum(usage_state IN ('unknown','unknown_released')),0) unknown_usage_count FROM requests WHERE {where}''', args).fetchone())
            total.update({k: tenant[k] for k in ('balance_micro_usd', 'reserved_micro_usd', 'used_tokens', 'reserved_tokens')})
            groups = [dict(x) for x in db.execute(f'''SELECT key_id,model,substr(created_at,1,10) date,
                count(*) request_count,coalesce(sum(input_tokens),0) input_tokens,
                coalesce(sum(output_tokens),0) output_tokens,coalesce(sum(cost_micro_usd),0) cost_micro_usd
                FROM requests WHERE {where} GROUP BY key_id,model,date ORDER BY date,key_id''', args)]
            return {'summary': total, 'items': rows[:limit], 'groups': groups,
                    'next_cursor': rows[limit - 1]['sequence'] if len(rows) > limit else None}

    def audits(self, cursor=0, limit=100):
        with self.connect() as db:
            rows = [dict(x) for x in db.execute('SELECT * FROM audit WHERE sequence>? ORDER BY sequence LIMIT ?', (cursor, limit + 1))]
            for r in rows:
                r['detail'] = json.loads(r['detail'])
            return {'items': rows[:limit], 'next_cursor': rows[limit - 1]['sequence'] if len(rows) > limit else None}
