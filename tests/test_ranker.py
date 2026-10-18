import numpy as np
import pandas as pd
import pytest
from stox import ranker
from stox.alerts import ADVERSE_8K_ITEMS, add_alerts, adverse_events, trailing_stop
from stox.config import Config


def synthetic_labels(sessions=520, symbols=40, signal=0.04, seed=3, horizon=30):
    """Panel where momentum_60 genuinely predicts excess return (signal>0) or nothing (signal=0)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2020-01-01', periods=sessions)
    rows = []
    for d in dates:
        market = rng.normal(.01, .04)
        mom = rng.normal(size=symbols)
        mom120 = rng.normal(size=symbols)
        excess = signal * mom + rng.normal(0, .08, symbols)
        for i in range(symbols):
            rows.append({'symbol': f'S{i:02d}', 'session': d.date().isoformat(), 'horizon': horizon,
                         'label_end': (d + pd.Timedelta(days=horizon)).date().isoformat(),
                         'momentum_60': mom[i] + 10 * market, 'momentum_120': mom120[i], 'volatility': abs(rng.normal(.3, .05)),
                         'target': market + excess[i], 'benchmark_SPY': market})
    return pd.DataFrame(rows)


@pytest.fixture
def rcfg(tmp_path):
    c = Config(tmp_path / 'state')
    c.ranker_min_cohorts = 3
    c.ranker_max_rows = 40000
    return c


def test_session_ranks_remove_market_level_shifts():
    frame = synthetic_labels(sessions=5)
    base = ranker.session_ranks(frame, ['momentum_60'])
    shifted = frame.copy()
    shifted.loc[shifted.session == shifted.session.iloc[0], 'momentum_60'] += 50
    assert np.allclose(base.to_numpy(), ranker.session_ranks(shifted, ['momentum_60']).to_numpy())


def test_real_signal_is_found_and_validated(rcfg):
    artifact = ranker.train_horizon(synthetic_labels(signal=.04), 30, rcfg, '2024-01-01T00:00:00+00:00')
    meta = artifact['metadata']
    assert meta['holdout']['n_cohorts'] >= 3
    assert meta['holdout']['ic_mean'] > 0
    assert meta['holdout']['top_mean'] > meta['holdout']['random_mean']
    assert meta['validated'], meta['reasons']


def test_pure_noise_is_not_validated(rcfg):
    artifact = ranker.train_horizon(synthetic_labels(signal=0.0, seed=11), 30, rcfg, '2024-01-01T00:00:00+00:00')
    assert not artifact['metadata']['validated']
    assert artifact['metadata']['reasons']


def test_holdout_is_purged_from_training(rcfg):
    meta = ranker.train_horizon(synthetic_labels(), 30, rcfg, '2024-01-01T00:00:00+00:00')['metadata']
    assert pd.Timestamp(meta['train_end']) < pd.Timestamp(meta['holdout_start'])


def test_cohorts_do_not_overlap():
    sessions = pd.bdate_range('2021-01-01', periods=300)
    chosen = ranker.cohort_sessions(sessions, 30)
    gaps = [(b - a).days for a, b in zip(chosen, chosen[1:])]
    assert gaps and min(gaps) >= 35


def test_too_little_history_returns_none(rcfg):
    assert ranker.train_horizon(synthetic_labels(sessions=15), 30, rcfg, 'x') is None


def test_verdict_requires_enough_cohorts(rcfg):
    ok, reasons = ranker.verdict({'n_cohorts': 2}, rcfg)
    assert not ok and 'cohorts' in reasons[0]


class FakeStore:
    def __init__(self, bars):
        self._bars = bars

    def bars(self, symbol):
        return self._bars


def make_bars(closes):
    n = len(closes)
    return pd.DataFrame({'session': [d.date().isoformat() for d in pd.bdate_range('2025-01-02', periods=n)],
                         'close': closes, 'high': np.array(closes) + 1, 'low': np.array(closes) - 1, 'open': closes})


def test_trailing_stop_uses_peak_close_since_purchase():
    bars = make_bars([100 + i for i in range(40)] + [135, 120])
    stop = trailing_stop(bars, bars.session.iloc[20], 2.5)
    assert stop['peak_close'] == 139.0 and stop['stop'] < stop['peak_close']
    assert stop['last_close'] == 120.0


def test_adverse_8k_detection_respects_items_and_dates():
    now = '2025-06-30T00:00:00+00:00'
    filings = [{'form': '8-K', 'items': '2.02,9.01', 'available_at': '2025-06-20T12:00:00+00:00', 'filingDate': '2025-06-20'},
               {'form': '8-K', 'items': '4.02', 'available_at': '2025-06-25T12:00:00+00:00', 'filingDate': '2025-06-25'},
               {'form': '8-K', 'items': '1.03', 'available_at': '2025-01-10T12:00:00+00:00', 'filingDate': '2025-01-10'},
               {'form': '10-K', 'items': '4.02', 'available_at': '2025-06-26T12:00:00+00:00', 'filingDate': '2025-06-26'}]
    found = adverse_events(filings, now)
    assert [f['item'] for f in found] == ['4.02']
    assert set(ADVERSE_8K_ITEMS) >= {'1.03', '4.02', '3.01'}


def test_add_alerts_flags_price_model_and_event(rcfg):
    bars = make_bars([100 + i for i in range(40)] + [140, 110])
    holding = {'symbol': 'AAA', 'purchased': bars.session.iloc[10], 'resolution_status': 'resolved', 'status': 'EXIT REVIEW', 'reason': 'x',
               'current_forecasts': [{'horizon': 30, 'p_gain': .40, 'median_return': -.02}],
               'evidence': {'filings': [{'form': '8-K', 'items': '3.01', 'available_at': '2025-03-01T00:00:00+00:00', 'filingDate': '2025-03-01'}]}}
    add_alerts([holding], rcfg, FakeStore(bars), {'AAA': .1}, '2025-03-10T00:00:00+00:00')
    kinds = {a['kind'] for a in holding['alerts']}
    assert kinds == {'PRICE', 'MODEL', 'EVENT'} and holding['alert_level'] == 'ALERT'


def test_add_alerts_quiet_holding_has_no_alerts(rcfg):
    bars = make_bars([100 + i * .5 for i in range(60)])
    holding = {'symbol': 'BBB', 'purchased': bars.session.iloc[5], 'resolution_status': 'resolved', 'status': 'HEALTHY', 'reason': '',
               'current_forecasts': [{'horizon': 30, 'p_gain': .6, 'median_return': .02}], 'evidence': {'filings': []}}
    add_alerts([holding], rcfg, FakeStore(bars), {'BBB': .7}, '2025-04-01T00:00:00+00:00')
    assert holding['alerts'] == [] and holding['alert_level'] == 'OK'


def test_config_rejects_bad_ranker_values(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('[stox]\nranker_min_cohorts = 1\n')
    with pytest.raises(ValueError):
        Config.load(str(path))


def test_engine_integration_runs_ranker_and_renders_sections(cfg, monkeypatch):
    """Full synthetic engine run: ranker trains on the demo panel, sections render, JSON stays finite."""
    from stox.demo import seed
    from stox.engine import run
    monkeypatch.setattr(ranker, 'MIN_CROSS_SECTION', 5)
    monkeypatch.setattr(ranker, 'MIN_TRAIN_ROWS', 500)
    cfg.horizons = (30,)
    cfg.ranker_horizons = (30,)
    cfg.ranker_min_cohorts = 3
    stamp = seed(cfg)
    report, path = run(cfg, offline=True, force_train=True, timestamp=stamp, synthetic=True)
    section = report['ranker']
    assert section['status'] in ('VALIDATED', 'RESEARCH - NOT VALIDATED'), section
    assert '30' in section['horizons'] and 'holdout' in section['horizons']['30']
    assert all(p['symbol'].startswith('DEMO') for p in section['top'])
    holding = report['holdings'][0]
    assert 'alerts' in holding and holding['alert_level'] in ('OK', 'ALERT')
    page = (path / 'report.html').read_text(encoding='utf-8')
    assert 'alpha ranker' in page and 'Your holdings' in page
    assert page.index('alpha ranker') < page.index('Primary 30D')
