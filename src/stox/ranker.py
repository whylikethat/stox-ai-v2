"""Cross-sectional alpha ranker: orders securities by expected excess return over SPY.

The calibrated probability models answer "how likely is a gain?". This module answers the question
that decides profit: which securities are likely to beat the market? It is trained on per-session
percentile ranks (so it learns relative strength, not market regime), evaluated on a purged holdout of
non-overlapping cohorts against momentum and random baselines, and is never labelled VALIDATED unless
those pre-declared checks pass. Outputs are rankings plus measured holdout evidence, never guarantees.
"""
from __future__ import annotations
import math
import time
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from threadpoolctl import threadpool_limits
from .features import FAMILIES, SECTOR_ETFS, make_labels

BENCHMARKS = {'SPY', 'QQQ', 'IWM'} | set(SECTOR_ETFS)
CORE_FAMILIES = ('price', 'price_advanced', 'sector_proxy')
OPTIONAL_FAMILIES = ('fundamentals', 'earnings')
ALGORITHMS = ('ridge', 'histogram')
MIN_CROSS_SECTION = 30
EMBARGO_DAYS = 5
MIN_TRAIN_ROWS = 2000
VERDICT_VERSION = 2  # bump when the validation rules change so stored models are re-judged


def feature_columns(frame, min_coverage=.5):
    """Core price families always; fundamentals/earnings only when well covered in training data."""
    columns = [c for family in CORE_FAMILIES for c in FAMILIES[family]]
    for family in OPTIONAL_FAMILIES:
        columns.extend(c for c in FAMILIES[family] if c in frame.columns and frame[c].notna().mean() >= min_coverage)
    return [c for c in dict.fromkeys(columns) if c in frame.columns]


def session_ranks(frame, columns):
    """Per-session percentile ranks; NaN stays NaN. Removes market-wide level shifts by construction."""
    return frame[columns].groupby(frame['session']).rank(pct=True)


def make_panel(labels, horizon, cfg):
    d = labels[(labels.horizon == horizon) & (~labels.symbol.isin(BENCHMARKS))].copy()
    d = d[np.isfinite(d.target.astype(float)) & np.isfinite(d.benchmark_SPY.astype(float))]
    # Point-in-time eligibility, as the probability models apply it: judge each row by the price, liquidity and
    # history it had on that session, never by today's values.
    for column, limit in (('close', cfg.min_price), ('dollar_volume', cfg.min_dollar_volume), ('history_count', cfg.min_history)):
        if column in d.columns:
            d = d[d[column] >= limit]
    if 'missing_recent' in d.columns:
        d = d[d.missing_recent == 0]
    if d.empty:
        return d, pd.DataFrame(), []
    d = d[d.groupby('session').symbol.transform('size') >= MIN_CROSS_SECTION].copy()
    if d.empty:
        return d, pd.DataFrame(), []
    columns = feature_columns(d)
    d['excess'] = d.target.astype(float) - d.benchmark_SPY.astype(float)
    d['y'] = d.groupby('session').excess.rank(pct=True) - .5
    d['t'] = pd.to_datetime(d.session)
    d['end'] = pd.to_datetime(d.label_end)
    ranks = session_ranks(d, columns)
    cap = int(cfg.ranker_max_rows)
    if len(d) > cap:
        keep = d.sample(n=cap, random_state=7).index
        d, ranks = d.loc[keep], ranks.loc[keep]
    order = d.sort_values('t').index
    return d.loc[order], ranks.loc[order], columns


def _matrix(kind, X):
    return (X.fillna(.5) if kind == 'ridge' else X).to_numpy(dtype=float)


def _fit(kind, X, y):
    model = Ridge(alpha=10.0) if kind == 'ridge' else HistGradientBoostingRegressor(
        max_iter=120, learning_rate=.05, max_depth=3, min_samples_leaf=300, l2_regularization=1.0, random_state=0)
    with threadpool_limits(limits=2):
        model.fit(_matrix(kind, X), y.to_numpy(dtype=float))
    return model


def _predict(kind, model, X):
    with threadpool_limits(limits=2):
        return model.predict(_matrix(kind, X))


def cohort_sessions(sessions, horizon):
    """Non-overlapping evaluation sessions: spaced by the label horizon plus an embargo."""
    chosen, next_ok = [], None
    for s in sorted(pd.Timestamp(x) for x in set(sessions)):
        if next_ok is None or s >= next_ok:
            chosen.append(s)
            next_ok = s + pd.Timedelta(days=horizon + EMBARGO_DAYS)
    return chosen


def tstat(values):
    x = np.asarray([v for v in values if np.isfinite(v)], dtype=float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def evaluate(frame, predictions, horizon, top_n, seed=11):
    """Top-N excess return per independent cohort versus momentum, random and universe baselines."""
    rng = np.random.default_rng(seed)
    f = frame.assign(pred=np.asarray(predictions, dtype=float))
    rows = []
    for s in cohort_sessions(f.t.unique(), horizon):
        g = f[f.t == s]
        if len(g) < max(top_n * 2, MIN_CROSS_SECTION):
            continue
        ic = spearmanr(g.pred, g.excess).statistic
        top = g.nlargest(top_n, 'pred')
        mom = g.dropna(subset=['momentum_120']).nlargest(top_n, 'momentum_120') if 'momentum_120' in g else g.iloc[0:0]
        values = g.excess.to_numpy()
        rand = float(np.mean([values[rng.choice(len(values), top_n, replace=False)].mean() for _ in range(100)]))
        rows.append((float(ic), float(top.excess.mean()), float((top.excess > 0).mean()),
                     float(mom.excess.mean()) if len(mom) else float('nan'), rand, float(values.mean())))
    if not rows:
        return {'n_cohorts': 0}
    a = np.array(rows, dtype=float)
    has_momentum = bool(np.isfinite(a[:, 3]).any())
    return {'n_cohorts': len(rows), 'ic_mean': float(np.nanmean(a[:, 0])), 'ic_t': tstat(a[:, 0]),
            'top_mean': float(np.nanmean(a[:, 1])), 'top_t': tstat(a[:, 1]), 'top_hit_rate': float(np.nanmean(a[:, 2])),
            'momentum_mean': float(np.nanmean(a[:, 3])) if has_momentum else None,
            'momentum_t': tstat(a[:, 3]) if has_momentum else None,
            'random_mean': float(np.nanmean(a[:, 4])), 'universe_mean': float(np.nanmean(a[:, 5])),
            'edge_vs_random': float(np.nanmean(a[:, 1] - a[:, 4])), 'edge_random_t': tstat(a[:, 1] - a[:, 4]),
            'edge_vs_momentum': float(np.nanmean(a[:, 1] - a[:, 3])) if has_momentum else None,
            'edge_momentum_t': tstat(a[:, 1] - a[:, 3]) if has_momentum else None}


def verdict(metrics, cfg):
    """Pre-declared checks. Failing any of them keeps the ranker in research status."""
    n = metrics.get('n_cohorts', 0)
    reasons = []
    if n < cfg.ranker_min_cohorts:
        reasons.append(f'only {n} independent holdout cohorts; {cfg.ranker_min_cohorts} required')
        return False, reasons
    if metrics['ic_mean'] <= 0 or metrics['ic_t'] < 1.0:
        reasons.append('holdout rank correlation is not reliably positive')
    if metrics['top_mean'] <= 0 or metrics['top_t'] < 1.0:
        reasons.append('holdout top-N excess return over SPY is not reliably positive')
    if metrics.get('edge_vs_random') is None or metrics['edge_vs_random'] <= 0 or metrics.get('edge_random_t', 0) < 1.0:
        reasons.append('top-N does not reliably beat random selection from the same universe')
    edge_momentum = metrics.get('edge_vs_momentum')
    if edge_momentum is not None and edge_momentum <= 0:
        reasons.append('top-N does not beat the plain 120-day momentum baseline')
    return not reasons, reasons


def train_horizon(labels, horizon, cfg, trained_at):
    d, R, columns = make_panel(labels, horizon, cfg)
    if d.empty or d.t.nunique() < 20:
        return None
    sessions = sorted(d.t.unique())
    hold_start = pd.Timestamp(sessions[int(len(sessions) * (1 - cfg.ranker_holdout_fraction))])
    embargo = pd.Timedelta(days=EMBARGO_DAYS)
    train = (d.end < hold_start - embargo).to_numpy()
    hold = (d.t >= hold_start).to_numpy()
    if train.sum() < MIN_TRAIN_ROWS or hold.sum() < MIN_CROSS_SECTION * 3:
        return None
    # Algorithm choice uses development folds inside the training period only.
    tr_sessions = sorted(d.t[train].unique())
    dev_start = pd.Timestamp(tr_sessions[int(len(tr_sessions) * .75)])
    sub_train = train & (d.end < dev_start - embargo).to_numpy()
    sub_val = train & (d.t >= dev_start).to_numpy()
    dev_scores = {}
    for kind in ALGORITHMS:
        if sub_train.sum() < MIN_TRAIN_ROWS // 2:
            dev_scores[kind] = float('-inf')
            continue
        model = _fit(kind, R[sub_train], d.y[sub_train])
        e = evaluate(d[sub_val], _predict(kind, model, R[sub_val]), horizon, cfg.ranker_top_n)
        dev_scores[kind] = e.get('ic_mean', float('-inf')) if e.get('n_cohorts', 0) else float('-inf')
    kind = max(ALGORITHMS, key=lambda k: (dev_scores[k], k == 'ridge'))
    holdout_model = _fit(kind, R[train], d.y[train])
    holdout = evaluate(d[hold], _predict(kind, holdout_model, R[hold]), horizon, cfg.ranker_top_n)
    validated, reasons = verdict(holdout, cfg)
    final = _fit(kind, R, d.y)
    metadata = {'horizon': horizon, 'algorithm': kind, 'columns': columns, 'trained_at': trained_at,
                'rows': int(len(d)), 'sessions': len(sessions), 'train_end': str(d.end[train].max().date()),
                'holdout_start': str(hold_start.date()), 'verdict_version': VERDICT_VERSION, 'development_ic': {k: (None if not np.isfinite(v) else float(v)) for k, v in dev_scores.items()},
                'holdout': holdout, 'validated': bool(validated), 'reasons': reasons,
                'target': 'net return minus SPY return, converted to per-session percentile rank',
                'caveat': 'Current-universe history has survivorship bias; holdout metrics are an upper bound.'}
    return {'kind': kind, 'model': final, 'columns': columns, 'metadata': metadata}


def _path(directory, horizon):
    return Path(directory) / f'ranker-{horizon}.joblib'


def _save(directory, horizon, artifact):
    path = _path(directory, horizon)
    tmp = path.with_suffix('.tmp')
    joblib.dump(artifact, tmp)
    tmp.replace(path)


def _load(directory, horizon):
    try:
        return joblib.load(_path(directory, horizon))
    except Exception:
        return None


def _stale(directory, horizon, cfg, now, force):
    artifact = _load(directory, horizon)
    if force or artifact is None:
        return True
    if artifact['metadata'].get('verdict_version') != VERDICT_VERSION:
        return True
    try:
        age = (pd.Timestamp(now) - pd.Timestamp(artifact['metadata']['trained_at'])).days
    except Exception:
        return True
    return age >= cfg.retrain_days


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def select_top(current, scores, rows, assets, cfg, key='alpha_score'):
    """Risk-capped selection: common stocks, adequate data, price floor, downside cap, then alpha score."""
    types = {a['symbol']: a.get('security_type') for a in assets}
    names = {a['symbol']: a.get('name', a['symbol']) for a in assets}
    by = {(r['symbol'], r['horizon']): r for r in rows}
    closes = current.set_index('symbol').close.to_dict()
    picks, passed = [], 0
    for symbol, composite in scores.sort_values(ascending=False).items():
        base = by.get((symbol, 30)) or next((r for (s, h), r in by.items() if s == symbol), None)
        if base is None or types.get(symbol) != 'common' or base.get('data_quality_status') != 'adequate':
            continue
        if closes.get(symbol, 0) < cfg.min_price or base['p_loss_10'] > cfg.ranker_max_p_loss_10:
            continue
        passed += 1
        if len(picks) < cfg.ranker_top_n:
            horizons = {str(h): {'median_return': by[(symbol, h)]['median_return'], 'p_gain': by[(symbol, h)]['p_gain'],
                                 'p_loss_10': by[(symbol, h)]['p_loss_10']} for h in cfg.horizons if (symbol, h) in by}
            picks.append({'rank': len(picks) + 1, 'symbol': symbol, 'name': names.get(symbol, symbol), 'close': float(closes[symbol]),
                          key: float(composite), 'model_estimates': horizons, 'volatility': base.get('volatility'),
                          'event_warning': base.get('event_warning')})
    return picks, passed


def run_ranker(cfg, store, cal, features, labels, current, rows, assets, started, force=False):
    """Return (report_section, percentiles). Never raises: a ranker fault must not block forecasts."""
    if not cfg.ranker_enabled:
        return {'status': 'DISABLED', 'top': [], 'horizons': {}}, {}
    try:
        return _run(cfg, store, cal, features, labels, current, rows, assets, started, force)
    except Exception as exc:  # isolated by design; reported as a warning by the engine
        return {'status': 'ERROR', 'error': f'{type(exc).__name__}: {exc}', 'top': [], 'horizons': {}}, {}


def _log(message):
    print(f'[ranker] {message}', flush=True)


def _run(cfg, store, cal, features, labels, current, rows, assets, started, force):
    t0 = time.monotonic()
    horizons = [h for h in cfg.ranker_horizons if h in cfg.horizons]
    directory = Path(store.root) / 'ranker'
    directory.mkdir(parents=True, exist_ok=True)
    stale = [h for h in horizons if _stale(directory, h, cfg, started, force)]
    skipped = []
    if stale:
        _log(f'horizons due for training: {stale}; time budget {cfg.ranker_time_budget_seconds}s')
        if labels is None or labels.empty:
            _log('building labels; this can take several minutes on a laptop')
            labels = make_labels(features, store, cal, cfg.horizons, cfg.friction_bps)
            if not labels.empty:
                labels = labels[pd.to_datetime(labels.label_available_at, utc=True) <= pd.Timestamp(started)]
            _log(f'labels ready: {len(labels):,} rows after {time.monotonic() - t0:.0f}s')
        for h in stale:
            if labels.empty:
                break
            if time.monotonic() - t0 > cfg.ranker_time_budget_seconds:
                skipped.append(h)
                _log(f'{h}D skipped: time budget reached; it will be retried on the next run')
                continue
            began = time.monotonic()
            _log(f'{h}D: training and validating')
            artifact = train_horizon(labels, h, cfg, started)
            if artifact:
                _save(directory, h, artifact)
                _log(f"{h}D: {'VALIDATED' if artifact['metadata']['validated'] else 'not validated'} after {time.monotonic() - began:.0f}s")
            else:
                _log(f'{h}D: not enough labelled history yet')
    models = {h: a for h in horizons if (a := _load(directory, h))}
    if not models:
        return {'status': 'NO MODEL', 'top': [], 'momentum_top': [], 'horizons': {}, 'skipped_horizons': skipped,
                'note': 'Not enough labelled history, or too few securities per session, to train the ranker yet; bootstrap more history.'}, {}
    cur = current[~current.symbol.isin(BENCHMARKS)].copy()
    latest = cur.session.max()
    cur = cur[cur.session == latest]
    zs, percentiles = [], {}
    for h, artifact in models.items():
        cols = artifact['columns']
        X = cur.reindex(columns=cols)
        ranks = X.rank(pct=True)
        pred = pd.Series(_predict(artifact['kind'], artifact['model'], ranks), index=cur.symbol.to_numpy())
        zs.append((pred - pred.mean()) / (pred.std() or 1.0))
        percentiles[h] = pred.rank(pct=True)
    composite = pd.concat(zs, axis=1).mean(axis=1)
    pct = composite.rank(pct=True).to_dict()
    top, passed = select_top(cur, composite, rows, assets, cfg)
    momentum = cur.set_index('symbol').momentum_120.dropna() if 'momentum_120' in cur.columns else pd.Series(dtype=float)
    momentum_top = select_top(cur, momentum, rows, assets, cfg, key='momentum_120')[0] if len(momentum) else []
    validated = all(m['metadata']['validated'] for m in models.values()) and len(models) == len(horizons)
    meta = {str(h): m['metadata'] for h, m in models.items()}
    section = {'status': 'VALIDATED' if validated else 'RESEARCH - NOT VALIDATED', 'top': top, 'momentum_top': momentum_top, 'horizons': meta,
               'skipped_horizons': skipped, 'scored': int(len(composite)), 'passed_filters': passed,
               'risk_cap_p_loss_10_30d': cfg.ranker_max_p_loss_10,
               'note': 'Ranked by expected excess return over SPY, then filtered by price floor, adequate data, common stock and a downside cap. '
                       'VALIDATED requires beating random selection AND the plain momentum baseline on a purged holdout. '
                       'Return magnitudes shown are the probability models\' estimates and are unvalidated unless stated; rely on the holdout evidence.'}
    _log(f'done in {time.monotonic() - t0:.0f}s; status {section["status"]}')
    return _clean(section), pct
