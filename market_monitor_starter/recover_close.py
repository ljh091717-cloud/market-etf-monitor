"""Explicit dated recovery archive. Never replaces latest/reference/state or emits alerts.

Run: python recover_close.py --session-date YYYY-MM-DD
The output is docs/data/recovered/YYYY-MM-DD.json, not a fresh trading snapshot.
"""
import argparse
from datetime import date, datetime, time, timedelta, timezone
import math

import collector as core
import monitor_runner as m


def recover(session_date, now):
    import yfinance as yf
    day = date.fromisoformat(session_date)
    if day.isoformat() != session_date:
        raise ValueError('Use an exact YYYY-MM-DD session date')
    anchor = datetime.combine(day, time(12), tzinfo=core.NY)
    session, _ = core.exchange_schedule(anchor)
    if session is None or now < session[1] + timedelta(minutes=5):
        raise ValueError('Recovery requires a completed exchange session')
    config = m.read_json(m.CONFIG)
    if not config or config.get('schema_version') != 2:
        raise ValueError('Valid monitor_config.json is required')
    destination = m.DATA / 'recovered' / f'{session_date}.json'
    if destination.exists():
        raise ValueError(f'Recovery archive already exists: {destination}; refusing overwrite')
    snapshots = {}
    week_final = m.last_session_of_week(anchor)
    for symbol in config['us']['symbols']:
        frame = core.history(yf.Ticker(symbol), symbol, period='2y', interval='1d',
                             auto_adjust=False, prepost=False, actions=True, timeout=25)
        frame = frame.dropna(subset=['Open', 'High', 'Low', 'Close']).sort_index()
        rows = frame.loc[m.day_index(frame) == day]
        if len(rows) != 1:
            raise ValueError(f'{symbol}: exactly one provider daily bar required for {day}')
        bar = rows.iloc[0]
        if not all(math.isfinite(float(bar[c])) and float(bar[c]) > 0
                   for c in ('Open', 'High', 'Low', 'Close', 'Adj Close')):
            raise ValueError(f'{symbol}: invalid provider daily OHLC/adjusted close')
        if not bar['Low'] <= min(bar['Open'], bar['Close']) <= max(bar['Open'], bar['Close']) <= bar['High']:
            raise ValueError(f'{symbol}: inconsistent provider daily OHLC')
        if not m.today_actions(frame, day)['complete']:
            raise ValueError(f'{symbol}: corporate action fields missing')
        prior = frame.loc[m.day_index(frame) < day]
        if prior.empty or (day - m.day_index(prior)[-1]).days > 7:
            raise ValueError(f'{symbol}: missing prior-session reference history')
        ref = m.daily_metrics(prior)
        snapshots[symbol] = m.read_symbol(symbol, anchor, 'end_of_day', ref, config,
                                          week_final, daily_frame=frame)
        snapshots[symbol]['provider_daily_bar'] = {
            'timestamp': rows.index[0].isoformat(),
            **{c: float(bar[c]) for c in ('Open', 'High', 'Low', 'Close', 'Adj Close')},
        }
    payload = {
        'schema_version': 2, 'mode': 'historical_end_of_day_recovery',
        'source': 'Yahoo Finance via yfinance; unofficial daily bars, may be revised',
        'recovered_session_date': session_date, 'reference_session_date': session_date,
        'retrieved_at_utc': now.isoformat(), 'config_revision': config['revision'],
        'verified_regular_session': False, 'use_for_intraday_alerts': False,
        'market': {'ny_date': session_date, 'ny_close': session[1].isoformat(),
                   'is_open_at_collection': False},
        'symbols': snapshots,
        'comparison': {'kind': 'historical_recovery_not_a_new_price_event',
                       'new_crossings': [], 'recovered_conditions': [], 'active_conditions': []},
        'limitations': ['Dated archive only; never replaces latest.json or notification state',
                        'References reconstructed at retrieval, not fixed before the recovered session',
                        'Current configuration labels retained for audit, not historical alert replay'],
    }
    # All symbols must validate before the single archive write.
    m.write_json(destination, payload)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-date', required=True)
    args = parser.parse_args()
    try:
        destination = recover(args.session_date, datetime.now(timezone.utc))
    except Exception as exc:
        raise SystemExit(f'Recovery failed; live snapshots unchanged: {exc}') from exc
    print(f'Recovered dated archive: {destination}; no live snapshot or alerts updated')


if __name__ == '__main__':
    main()
