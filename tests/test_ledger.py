import importlib.util
from pathlib import Path
import pandas as pd

spec = importlib.util.spec_from_file_location('paper_ledger', Path(__file__).resolve().parents[1] / 'scripts' / 'paper_ledger.py')
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


def report():
    pick = lambda rank, symbol, close: {'rank': rank, 'symbol': symbol, 'close': close}
    return {'ranker': {'top': [pick(1, 'AAA', 100.0), pick(2, 'BBB', 50.0)], 'momentum_top': [pick(1, 'CCC', 20.0)]}}


def bars(start, closes):
    days = pd.date_range(start, periods=len(closes), freq='D')
    return pd.DataFrame({'session': [d.date().isoformat() for d in days], 'close': closes})


def test_log_is_idempotent_per_day(tmp_path):
    path = tmp_path / 'ledger.csv'
    rows = ledger.picks_from_report(report(), '2026-01-02')
    assert len(rows) == 3
    assert ledger.append_new(path, rows) == 3
    assert ledger.append_new(path, rows) == 0
    assert len(ledger.read_ledger(path)) == 3
    assert ledger.append_new(path, ledger.picks_from_report(report(), '2026-01-03')) == 3


def test_scoring_skips_immature_and_scores_matured_entries():
    rows = ledger.picks_from_report(report(), '2026-01-02')
    series = {'SPY': bars('2026-01-01', [100 + i * .1 for i in range(120)]),
              'AAA': bars('2026-01-01', [100 + i * .5 for i in range(120)]),   # beats SPY
              'BBB': bars('2026-01-01', [50 - i * .1 for i in range(120)]),    # lags SPY
              'CCC': bars('2026-01-01', [20 + i * .05 for i in range(20)])}    # data ends before it matures
    scored = ledger.score_ledger(rows, lambda s: series.get(s))
    assert set(scored.symbol) == {'AAA', 'BBB'} and set(scored.horizon) == {30, 60, 90}
    aaa = scored[(scored.symbol == 'AAA') & (scored.horizon == 30)].iloc[0]
    assert aaa.excess > 0 and scored[(scored.symbol == 'BBB') & (scored.horizon == 30)].iloc[0].excess < 0


def test_summary_counts_cohorts_and_compares_lists():
    scored = pd.DataFrame([
        {'logged_on': '2026-01-02', 'list': 'ranker', 'horizon': 30, 'symbol': 'A', 'ret': .05, 'spy': .01, 'excess': .04},
        {'logged_on': '2026-01-02', 'list': 'momentum', 'horizon': 30, 'symbol': 'C', 'ret': .03, 'spy': .01, 'excess': .02},
        {'logged_on': '2026-02-02', 'list': 'ranker', 'horizon': 30, 'symbol': 'A', 'ret': -.02, 'spy': .01, 'excess': -.03},
        {'logged_on': '2026-02-02', 'list': 'momentum', 'horizon': 30, 'symbol': 'C', 'ret': .01, 'spy': .01, 'excess': .0}])
    summary, cohorts, versus = ledger.summarize(scored)
    assert set(summary.cohorts) == {2}
    assert round(versus.iloc[0].mean_difference, 4) == -.005
    assert ledger.summarize(pd.DataFrame())[0].empty
