from __future__ import annotations
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

SCHEMA = 5

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)

def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()

class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'state.sqlite', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA synchronous=FULL')
        self.commits = 0
        self._storage_checked = 0
        self._storage_usage = 0
        try:
            self.migrate()
        except BaseException:
            self.db.close()
            raise

    def migrate(self):
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA:
            raise RuntimeError('Database was created by a newer STOX-AI version')
        if version == SCHEMA:
            return
        # Upgrade only while no bootstrap writer owns this directory.
        print(f'[migration] upgrading schema {version} -> {SCHEMA}; existing records retained',flush=True)
        with self.lock(), self.transaction():
            self._migrate_schema()
        print('[migration] complete',flush=True)

    def _migrate_schema(self):
        statements = '''
        CREATE TABLE IF NOT EXISTS security_master(asset_id TEXT PRIMARY KEY,market_symbol TEXT,provider_symbol TEXT,security_type TEXT,status TEXT,tradable INTEGER,queryable INTEGER,reason TEXT,metadata_hash TEXT,payload TEXT,updated_at TEXT,present INTEGER,intended_candidate INTEGER);
        CREATE INDEX IF NOT EXISTS security_symbol ON security_master(market_symbol);
        CREATE TABLE IF NOT EXISTS security_identifiers(entity_id TEXT,kind TEXT,value TEXT,source TEXT,role TEXT,effective_at TEXT,observed_at TEXT,PRIMARY KEY(entity_id,kind,value,source,role,effective_at));
        CREATE INDEX IF NOT EXISTS typed_identifier ON security_identifiers(kind,value);
        CREATE TABLE IF NOT EXISTS security_quarantine(asset_id TEXT PRIMARY KEY,metadata_hash TEXT,reason TEXT,quarantined_at TEXT,retry_after TEXT);
        CREATE TABLE IF NOT EXISTS price_history_owners(ticker TEXT PRIMARY KEY,asset_id TEXT,bound_at TEXT);
        CREATE TABLE IF NOT EXISTS checkpoints(scope TEXT PRIMARY KEY, value TEXT NOT NULL, succeeded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS health(provider TEXT PRIMARY KEY, status TEXT, detail TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS records(kind TEXT, record_id TEXT, revision TEXT, available_at TEXT, ingested_at TEXT, payload TEXT, PRIMARY KEY(kind,record_id,revision));
        CREATE INDEX IF NOT EXISTS records_time ON records(kind,available_at);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT, metadata TEXT);
        CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY, horizon INTEGER, created_at TEXT, status TEXT, path TEXT, metadata TEXT);
        CREATE TABLE IF NOT EXISTS forecasts(id TEXT PRIMARY KEY, issued_at TEXT, symbol TEXT, entry_session TEXT, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS forecasts_symbol_time ON forecasts(symbol,issued_at DESC);
        CREATE TABLE IF NOT EXISTS entries(forecast_id TEXT PRIMARY KEY REFERENCES forecasts(id), price REAL CHECK(price>0), recorded_at TEXT, source TEXT);
        CREATE TABLE IF NOT EXISTS outcomes(forecast_id TEXT REFERENCES forecasts(id), horizon INTEGER, session TEXT, net_return REAL, payload TEXT, PRIMARY KEY(forecast_id,horizon));
        CREATE TABLE IF NOT EXISTS holdings(id INTEGER PRIMARY KEY, symbol TEXT, purchased TEXT, price REAL CHECK(price>0), shares REAL CHECK(shares>0), forecast_id TEXT, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS holding_reviews(run_id TEXT, holding_id INTEGER, payload TEXT, PRIMARY KEY(run_id,holding_id));
        CREATE TABLE IF NOT EXISTS budgets(provider TEXT, day TEXT, calls INTEGER, PRIMARY KEY(provider,day));
        CREATE TABLE IF NOT EXISTS evaluations(model_id TEXT, kind TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS forecast_snapshots(signature TEXT PRIMARY KEY, created_at TEXT NOT NULL, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS forecast_snapshot_members(signature TEXT REFERENCES forecast_snapshots(signature), symbol TEXT, forecast_id TEXT REFERENCES forecasts(id), PRIMARY KEY(signature,symbol));
        CREATE TABLE IF NOT EXISTS universe_screen(symbol TEXT PRIMARY KEY, asset_id TEXT NOT NULL, session TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS watchlist(symbol TEXT PRIMARY KEY, added_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS model_trials(candidate TEXT PRIMARY KEY, incumbent TEXT NOT NULL, horizon INTEGER NOT NULL, started_at TEXT NOT NULL, status TEXT NOT NULL, policy TEXT NOT NULL, diagnostics TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trial_forecasts(candidate TEXT, symbol TEXT, entry_session TEXT, target_session TEXT NOT NULL, issued_at TEXT NOT NULL, p REAL NOT NULL, expected REAL NOT NULL, incumbent_p REAL NOT NULL, incumbent_expected REAL NOT NULL, friction REAL NOT NULL, PRIMARY KEY(candidate,symbol,entry_session));
        CREATE TABLE IF NOT EXISTS trial_entries(candidate TEXT, symbol TEXT, entry_session TEXT, price REAL NOT NULL CHECK(price>0), recorded_at TEXT NOT NULL, PRIMARY KEY(candidate,symbol,entry_session));
        CREATE TABLE IF NOT EXISTS trial_outcomes(candidate TEXT, symbol TEXT, entry_session TEXT, target REAL NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(candidate,symbol,entry_session));
        CREATE INDEX IF NOT EXISTS trial_pending ON trial_forecasts(target_session,symbol);


        '''
        for statement in statements.split(';'):
            if statement.strip():
                self.db.execute(statement)
        # Trigger bodies have internal semicolons; execute each as a whole.
        for table, name in [('forecasts','forecast'),('entries','entry'),('outcomes','outcome'),('trial_forecasts','trial_forecast'),('trial_entries','trial_entry'),('trial_outcomes','trial_outcome')]:
            for operation in ('UPDATE','DELETE'):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{name}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable {name}'); END")
        holding_columns={r[1] for r in self.db.execute('PRAGMA table_info(holdings)')}
        for name,definition in [('security_id','TEXT'),('input_kind',"TEXT DEFAULT 'ticker'"),('resolution_status',"TEXT DEFAULT 'unverified'")]:
            if name not in holding_columns:
                self.db.execute(f'ALTER TABLE holdings ADD COLUMN {name} {definition}')
        for statement in (
            "CREATE INDEX IF NOT EXISTS asset_identifier_lookup ON security_identifiers(kind,value,entity_id) WHERE source='alpaca_assets'",
            "CREATE TABLE IF NOT EXISTS feed_jobs(scope TEXT PRIMARY KEY, job_key TEXT NOT NULL, start TEXT NOT NULL, target TEXT NOT NULL, window_start TEXT NOT NULL, window_end TEXT NOT NULL, cursor TEXT, status TEXT NOT NULL, pages INTEGER NOT NULL DEFAULT 0, processed INTEGER NOT NULL DEFAULT 0, inserted INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0, resets INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS feed_pages(scope TEXT,job_key TEXT,window_start TEXT,cursor_hash TEXT,next_hash TEXT,processed INTEGER,committed_at TEXT,PRIMARY KEY(scope,job_key,window_start,cursor_hash))",
            "CREATE TABLE IF NOT EXISTS action_sources(record_id TEXT,raw_hash TEXT,revision TEXT,first_available_at TEXT,PRIMARY KEY(record_id,raw_hash))",
            "CREATE TABLE IF NOT EXISTS stages(run_id TEXT,name TEXT,status TEXT,started_at TEXT,updated_at TEXT,finished_at TEXT,progress TEXT,PRIMARY KEY(run_id,name))",
            "CREATE INDEX IF NOT EXISTS stages_recent ON stages(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS runs_recent ON runs(started_at DESC)",
            "CREATE INDEX IF NOT EXISTS runs_success ON runs(status,started_at DESC)",
            "CREATE TABLE IF NOT EXISTS bar_state(symbol TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, summary TEXT)",
            "CREATE TABLE IF NOT EXISTS runtime_meta(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)",
        ):
            self.db.execute(statement)
        self.db.execute(f'PRAGMA user_version={SCHEMA}')

    @contextlib.contextmanager
    def transaction(self):
        """Helpers join a caller-owned transaction; only its owner commits/rolls back."""
        owner = not self.db.in_transaction
        if owner:
            self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            if owner:
                self.db.rollback()
            raise
        else:
            if owner:
                try:
                    self.db.commit()
                except BaseException:
                    self.db.rollback()
                    raise
                self.commits += 1

    def meta(self, key, value):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO runtime_meta VALUES(?,?,?)', (key,encode(value),now_iso()))

    @contextlib.contextmanager
    def lock(self):
        with (self.root / 'run.lock').open('a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another STOX-AI process is writing this data directory') from None
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def checkpoint(self, scope):
        row = self.db.execute('SELECT value FROM checkpoints WHERE scope=?', (scope,)).fetchone()
        return row[0] if row else None

    def invalidate_checkpoint(self, scope, reason):
        previous = self.checkpoint(scope)
        if previous is not None:
            self.record('checkpoint_invalidations', scope, {'previous': previous, 'reason': reason}, now_iso())
            with self.transaction():
                self.db.execute('DELETE FROM checkpoints WHERE scope=?', (scope,))

    def advance(self, scope, value):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)', (scope,str(value),now_iso()))

    def health(self, provider, status, detail):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO health VALUES(?,?,?,?)', (provider,status,detail,now_iso()))

    def record(self, kind, record_id, payload, available_at, ingested_at=None):
        revision = digest(payload)
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?)',
                            (kind,str(record_id),revision,str(available_at),ingested_at or now_iso(),encode(payload)))
        return revision

    def records(self, kind, cutoff=None, concepts=None, forms=None, symbol=None, fields=None):
        """Latest revision of each record.

        Optional filters (concepts, forms, symbol) and the `fields` projection run inside SQLite, so a caller that
        needs only a few facts, filing forms or fields never decodes the whole table into Python objects."""
        limit = pd.Timestamp(cutoff) if cutoff is not None else None
        names = [f for f in (fields or ()) if f != 'available_at']
        for name in names:
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                raise ValueError('Invalid record field name')
        conditions, args = ['kind=?'], [kind]
        for path, values in (('concept', concepts), ('form', forms)):
            if values:
                values = list(values)
                conditions.append(f"json_extract(payload,'$.{path}') IN ({','.join('?' * len(values))})")
                args.extend(values)
        if symbol is not None:
            conditions.append("json_extract(payload,'$.symbol')=?")
            args.append(symbol)
        projection = ('record_id,available_at,ingested_at' + ''.join(f",json_extract(payload,'$.{n}') AS \"{n}\"" for n in names)) if fields else '*'
        sql = f"SELECT {projection} FROM records WHERE {' AND '.join(conditions)} ORDER BY available_at,ingested_at"
        latest = {}
        try:
            for row in self.db.execute(sql, args):
                if limit is None or pd.Timestamp(row['available_at']) <= limit:
                    latest[row['record_id']] = ({'available_at': row['available_at'], **{n: row[n] for n in names}} if fields
                                                else json.loads(row['payload']))
        except sqlite3.OperationalError:
            # SQLite without JSON support: same result, filtered in Python.
            latest = {}
            for row in self.db.execute('SELECT * FROM records WHERE kind=? ORDER BY available_at,ingested_at', (kind,)):
                if limit is not None and pd.Timestamp(row['available_at']) > limit:
                    continue
                payload = json.loads(row['payload'])
                if (concepts and payload.get('concept') not in concepts) or (forms and payload.get('form') not in forms):
                    continue
                if symbol is not None and payload.get('symbol') != symbol:
                    continue
                latest[row['record_id']] = ({'available_at': row['available_at'], **{n: payload.get(n) for n in names}} if fields else payload)
        return list(latest.values())

    def recent_by_symbol(self, kind, cutoff=None, per_symbol=6, since_days=None):
        """The newest `per_symbol` records for every symbol, selected inside SQLite (evidence needs only a few)."""
        limit = pd.Timestamp(cutoff) if cutoff is not None else None
        sql = """WITH latest AS (
                   SELECT payload, available_at, ingested_at, json_extract(payload,'$.symbol') AS sym,
                          ROW_NUMBER() OVER (PARTITION BY record_id ORDER BY available_at DESC, ingested_at DESC) AS version
                   FROM records WHERE kind=? AND available_at>=?),
                 ranked AS (
                   SELECT payload, available_at, ingested_at,
                          ROW_NUMBER() OVER (PARTITION BY sym ORDER BY available_at DESC, ingested_at DESC) AS recency
                   FROM latest WHERE version=1)
                 SELECT payload, available_at FROM ranked WHERE recency<=? ORDER BY available_at, ingested_at"""
        try:
            since = (limit - pd.Timedelta(days=since_days)).isoformat() if (since_days and limit is not None) else ''
            rows = [json.loads(r['payload']) for r in self.db.execute(sql, (kind, since, int(per_symbol)))
                    if limit is None or pd.Timestamp(r['available_at']) <= limit]
            return rows
        except sqlite3.OperationalError:
            kept, counts = [], {}
            for payload in reversed(self.records(kind, cutoff)):
                key = payload.get('symbol')
                counts[key] = counts.get(key, 0) + 1
                if counts[key] <= per_symbol:
                    kept.append(payload)
            return list(reversed(kept))

    def budget(self, provider, maximum, day=None):
        day = day or now_iso()[:10]
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO budgets VALUES(?,?,0)', (provider,day))
            changed = self.db.execute('UPDATE budgets SET calls=calls+1 WHERE provider=? AND day=? AND calls<?', (provider,day,maximum)).rowcount
        return bool(changed)

    def bars(self, symbol):
        path = self.root / 'warehouse' / 'bars' / f'{digest(symbol)[:20]}.parquet'
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    def put_bars(self, symbol, frame):
        if frame.empty:
            return
        path = self.root / 'warehouse' / 'bars' / f'{digest(symbol)[:20]}.parquet'
        path.parent.mkdir(parents=True, exist_ok=True)
        old = self.bars(symbol)
        merged = pd.concat([old,frame], ignore_index=True).drop_duplicates('session',keep='last').sort_values('session')
        with self.transaction():
            if not old.empty:
                prior = old.set_index('session')
                for row in frame.to_dict('records'):
                    s = row['session']
                    if s in prior.index and any(prior.loc[s,k] != row[k] for k in ('open','high','low','close','volume')):
                        self.record('bar_revision',f'{symbol}:{s}',prior.loc[s].to_dict(),prior.loc[s,'available_at'])
                        self.record('bar_correction',f'{symbol}:{s}',{'symbol':symbol,'session':s,'correction_available_at':row['ingested_at'],'prior':prior.loc[s].to_dict(),'revised':row},row['ingested_at'])
        tmp = path.with_suffix('.tmp')
        merged.to_parquet(tmp,index=False,compression='zstd')
        with tmp.open('rb') as file:
            os.fsync(file.fileno())
        os.replace(tmp,path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        self._cache_bars(symbol,merged,path)

    def _cache_bars(self, symbol, frame, path):
        stat=path.stat()
        last=frame.sort_values('session').iloc[-1]
        summary={'symbol':symbol,'rows':len(frame),'first_session':str(frame.session.min()),
                 'last_session':str(last.session),'close':float(last.close),
                 'dollar_volume':float((frame.sort_values('session').close*frame.sort_values('session').volume).tail(60).median())}
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO bar_state VALUES(?,?,?,?)',
                            (symbol,stat.st_size,stat.st_mtime_ns,encode(summary)))
        return summary

    def bar_summary(self,symbol):
        path=self.root/'warehouse'/'bars'/f'{digest(symbol)[:20]}.parquet'
        if not path.exists(): return None
        stat=path.stat()
        cached=self.db.execute('SELECT * FROM bar_state WHERE symbol=?',(symbol,)).fetchone()
        if cached and cached['size']==stat.st_size and cached['mtime_ns']==stat.st_mtime_ns:
            return json.loads(cached['summary'])
        frame=self.bars(symbol)
        return self._cache_bars(symbol,frame,path) if not frame.empty else None

    def warehouse_summary(self):
        import duckdb
        path = self.root / 'warehouse' / 'bars'
        if not path.exists() or not list(path.glob('*.parquet')):
            return []
        cached=self.db.execute('SELECT summary FROM bar_state').fetchall()
        if len(cached)==len(list(path.glob('*.parquet'))):
            summaries=[json.loads(r[0]) for r in cached]
            return [(r['symbol'],r['rows'],r['last_session']) for r in summaries]
        with duckdb.connect() as db:
            return db.execute('SELECT symbol,count(*),max(session) FROM read_parquet(?) GROUP BY symbol ORDER BY symbol', [str(path/'*.parquet')]).fetchall()

    def storage_check(self, soft_gb, hard_gb):
        # Disk-free safety is checked every call; the expensive directory walk at most once/minute.
        if time.monotonic() - self._storage_checked > 60 or not self._storage_checked:
            self._storage_usage = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
            self._storage_checked = time.monotonic()
        usage = self._storage_usage
        free = shutil.disk_usage(self.root).free
        if usage >= hard_gb * 1e9 or free < 500_000_000:
            raise RuntimeError('Storage safety limit reached; free space before ingestion')
        return {'bytes':usage,'free_bytes':free,'soft_limit_exceeded':usage >= soft_gb * 1e9}

    def close(self):
        self.db.close()

