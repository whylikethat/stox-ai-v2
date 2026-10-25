#!/usr/bin/env python3
"""Paper-trade ledger for STOX: collect your own live evidence.

    python scripts/paper_ledger.py log      # record the latest report's ranker and momentum top 10
    python scripts/paper_ledger.py score    # score matured entries against SPY and against each other

Options: --root PATH (data root, default .stox) and --ledger PATH (default <root>/paper_ledger.csv).

The ledger is a plain CSV. Entries use the report's closing price as the entry price, so scored results are
approximate: no spreads, no next-open fill, no taxes. Judge a list only after several matured cohorts.
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

HORIZONS = (30, 60, 90)
FIELDS = ['logged_on', 'list', 'rank', 'symbol', 'entry_close']
MAX_STALE_DAYS = 7


def read_ledger(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def picks_from_report(report, today):
    ranker = report.get('ranker') or {}
    rows = []
    for name, key in (('ranker', 'top'), ('momentum', 'momentum_top')):
        for pick in ranker.get(key) or []:
            rows.append({'logged_on': today, 'list': name, 'rank': pick['rank'], 'symbol': pick['symbol'],
                         'entry_close': f"{pick['close']:.4f}"})
    return rows


def append_new(path, rows):
    path = Path(path)
    existing = {(r['logged_on'], r['list'], r['symbol']) for r in read_ledger(path)}
    new = [r for r in rows if (r['logged_on'], r['list'], r['symbol']) not in existing]
    if new:
        fresh = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if fresh:
                writer.writeheader()
            writer.writerows(new)
    return len(new)


def close_on_or_after(bars, day):
    """First close on or after an ISO date, as (close, session), or None."""
    if bars is None or len(bars) == 0:
        return None
    later = bars[bars.session >= day]
    if later.empty:
        return None
    first = later.iloc[0]
    return float(first.close), str(first.session)


def score_ledger(rows, bars_for, horizons=HORIZONS, benchmark='SPY'):
    """One scored row per matured (entry, horizon). Entries that have not matured yet are skipped."""
    spy_bars = bars_for(benchmark)
    out = []
    for row in rows:
        spy_entry = close_on_or_after(spy_bars, row['logged_on'])
        if spy_entry is None:
            continue
        bars, entry = bars_for(row['symbol']), float(row['entry_close'])
        for horizon in horizons:
            target = (date.fromisoformat(row['logged_on']) + timedelta(days=horizon)).isoformat()
            end, spy_end = close_on_or_after(bars, target), close_on_or_after(spy_bars, target)
            if end is None or spy_end is None:
                continue
            if (date.fromisoformat(end[1]) - date.fromisoformat(target)).days > MAX_STALE_DAYS:
                continue
            ret, spy_ret = end[0] / entry - 1, spy_end[0] / spy_entry[0] - 1
            out.append({'logged_on': row['logged_on'], 'list': row['list'], 'horizon': horizon, 'symbol': row['symbol'],
                        'ret': ret, 'spy': spy_ret, 'excess': ret - spy_ret})
    return pd.DataFrame(out)


def summarize(scored):
    """Per-cohort and per-list summaries; the unit of evidence is the cohort (one logged day), not the pick."""
    if scored.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    cohorts = (scored.groupby(['list', 'horizon', 'logged_on'])
               .agg(ret=('ret', 'mean'), excess=('excess', 'mean'), hit=('excess', lambda s: float((s > 0).mean())), picks=('symbol', 'size'))
               .reset_index())
    summary = (cohorts.groupby(['list', 'horizon'])
               .agg(cohorts=('logged_on', 'nunique'), mean_return=('ret', 'mean'), mean_excess_vs_spy=('excess', 'mean'), hit_rate=('hit', 'mean'))
               .reset_index())
    wide = cohorts.pivot_table(index=['horizon', 'logged_on'], columns='list', values='excess')
    versus = pd.DataFrame()
    if {'ranker', 'momentum'} <= set(wide.columns):
        diff = (wide['ranker'] - wide['momentum']).dropna().rename('ranker_minus_momentum').reset_index()
        versus = diff.groupby('horizon').agg(cohorts=('logged_on', 'nunique'), mean_difference=('ranker_minus_momentum', 'mean')).reset_index()
    return summary, cohorts, versus


def main(argv=None):
    parser = argparse.ArgumentParser(description='STOX paper-trade ledger')
    parser.add_argument('command', choices=('log', 'score'))
    parser.add_argument('--root', default='.stox')
    parser.add_argument('--ledger', default=None)
    args = parser.parse_args(argv)
    root = Path(args.root)
    ledger = Path(args.ledger) if args.ledger else root / 'paper_ledger.csv'
    if args.command == 'log':
        report_path = root / 'reports' / 'latest.json'
        if not report_path.exists():
            print(f'No report at {report_path}. Run `stox run` first.')
            return 1
        rows = picks_from_report(json.loads(report_path.read_text(encoding='utf-8')), date.today().isoformat())
        if not rows:
            print('The latest report has no ranker picks to log (status NO MODEL or ranker disabled).')
            return 1
        print(f'Logged {append_new(ledger, rows)} new entries to {ledger}')
        return 0
    rows = read_ledger(ledger)
    if not rows:
        print(f'Ledger {ledger} is empty. Run `paper_ledger.py log` after each `stox run`.')
        return 1
    from stox.storage import Store
    store, cache = Store(root), {}

    def bars_for(symbol):
        if symbol not in cache:
            try:
                cache[symbol] = store.bars(symbol)
            except Exception:
                cache[symbol] = None
        return cache[symbol]

    scored = score_ledger(rows, bars_for)
    if scored.empty:
        print('No entries have matured yet (the earliest scoring point is 30 days after logging).')
        return 0
    summary, cohorts, versus = summarize(scored)
    pd.set_option('display.width', 200)
    print('Per list and horizon (the unit of evidence is the cohort, one logged day):')
    print(summary.round(4).to_string(index=False))
    if not versus.empty:
        print('\nRanker minus momentum, mean excess per cohort:')
        print(versus.round(4).to_string(index=False))
    print('\nA list is worth trusting only after several matured cohorts that beat SPY AND momentum. Entries use report closes.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
