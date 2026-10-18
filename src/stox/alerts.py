"""Holdings alerts: price (trailing stop), model (negative outlook) and event (adverse filings).

These are rule-based warnings that deterioration may be under way. They are not predictions that a
security will fall, and they cannot catch every decline.
"""
from __future__ import annotations
import shutil
import subprocess
import numpy as np
import pandas as pd

ADVERSE_8K_ITEMS = {
    '1.03': 'bankruptcy or receivership', '2.04': 'triggering event accelerating debt', '2.06': 'material impairment',
    '3.01': 'delisting or listing-standard notice', '4.01': 'change of certifying accountant',
    '4.02': 'non-reliance on previously issued financials', '5.02': 'director or officer departure',
}
EVENT_LOOKBACK_DAYS = 30


def trailing_stop(bars, purchased, multiple, window=14):
    """Highest close since purchase minus `multiple` x ATR(window). Returns None without enough data."""
    if bars is None or len(bars) < window + 2:
        return None
    b = bars.sort_values('session').reset_index(drop=True)
    prev = b.close.shift(1)
    tr = pd.concat([b.high - b.low, (b.high - prev).abs(), (b.low - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window).mean()
    since = b[b.session >= purchased]
    if since.empty or not np.isfinite(atr.iloc[-1]):
        return None
    peak = float(since.close.max())
    stop = peak - multiple * float(atr.iloc[-1])
    return {'stop': stop, 'peak_close': peak, 'atr': float(atr.iloc[-1]), 'last_close': float(b.close.iloc[-1]), 'multiple': multiple}


def adverse_events(filings, now, lookback=EVENT_LOOKBACK_DAYS):
    found = []
    cutoff = pd.Timestamp(now) - pd.Timedelta(days=lookback)
    for f in filings or []:
        if not str(f.get('form', '')).startswith('8-K'):
            continue
        try:
            when = pd.Timestamp(f.get('available_at'))
        except (ValueError, TypeError):
            continue
        if when.tzinfo is None:
            when = when.tz_localize('UTC')
        limit = cutoff.tz_convert('UTC') if cutoff.tzinfo else cutoff.tz_localize('UTC')
        if when < limit:
            continue
        items = {i.strip() for i in str(f.get('items', '')).split(',') if i.strip()}
        for item in sorted(items & ADVERSE_8K_ITEMS.keys()):
            found.append({'item': item, 'meaning': ADVERSE_8K_ITEMS[item], 'filed': str(f.get('filingDate', ''))})
    return found


def add_alerts(holdings, cfg, store, percentiles, now):
    """Annotate each holding review with trailing stop and A/B/C alerts."""
    for h in holdings:
        alerts = []
        stop = None
        try:
            bars = store.bars(h['symbol']) if h.get('resolution_status') == 'resolved' else None
            stop = trailing_stop(bars, h['purchased'], cfg.stop_atr_multiple)
        except Exception:
            stop = None
        if stop and stop['last_close'] < stop['stop']:
            alerts.append({'kind': 'PRICE', 'message': f"Closed {stop['last_close']:.2f}, below trailing stop {stop['stop']:.2f}"})
        current = h.get('current_forecasts', [])
        p30 = next((r for r in current if r['horizon'] == 30), None)
        if p30 and p30['p_gain'] < .45 and p30['median_return'] < 0:
            alerts.append({'kind': 'MODEL', 'message': f"30D outlook negative: P(gain) {p30['p_gain']:.0%}, median {p30['median_return']:+.1%}"})
        if h.get('status') in ('EXIT REVIEW', 'MATERIAL NEGATIVE CHANGE', 'DETERIORATING'):
            alerts.append({'kind': 'MODEL', 'message': f"Review status {h['status']}: {h.get('reason', '')}"})
        pct = (percentiles or {}).get(h['symbol'])
        if pct is not None and pct <= .2:
            alerts.append({'kind': 'MODEL', 'message': f'Alpha ranker places it in the bottom {max(1, round(pct * 100))}% of scored securities'})
        for event in adverse_events((h.get('evidence') or {}).get('filings', []), now):
            alerts.append({'kind': 'EVENT', 'message': f"8-K item {event['item']} ({event['meaning']}) filed {event['filed']}"})
        h['trailing_stop'] = stop
        h['alerts'] = alerts
        h['alert_level'] = 'ALERT' if alerts else 'OK'
    return holdings


def notify(holdings):
    """Desktop notification via notify-send when available; silent otherwise."""
    flagged = [h for h in holdings if h.get('alert_level') == 'ALERT']
    exe = shutil.which('notify-send')
    if not flagged or not exe:
        return False
    body = '; '.join(f"{h['symbol']}: {h['alerts'][0]['message']}" for h in flagged)[:300]
    try:
        subprocess.run([exe, 'STOX holdings alert', body], timeout=5, check=False)
        return True
    except Exception:
        return False
