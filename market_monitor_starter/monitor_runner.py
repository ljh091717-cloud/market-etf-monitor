"""Scheduled US snapshots, not a real-time detector or order system.

Keep private positions/budgets out of this public repository. Runtime tests write
only test.json; failed collections never replace the latest valid snapshot.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import collector as core

DATA = Path('docs/data')
CONFIG = Path('monitor_config.json')


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def outputs(**values):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
            for key, value in values.items():
                f.write(f'{key}={str(value).lower()}\n')


def session_mode(now, session, test=False):
    if test:
        return 'manual_test_not_for_trading'
    if session is None:
        return None
    start, end = session
    if start - timedelta(minutes=30) <= now < start:
        return 'preopen_reference'
    if start + timedelta(minutes=45) <= now < end:
        return 'regular_session'
    if end + timedelta(minutes=15) <= now <= end + timedelta(minutes=45):
        return 'end_of_day'
    return None


def day_index(frame):
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is None:
        raise ValueError('Daily history lacks timezone')
    return idx.tz_convert(core.NY).date


def adjusted_frame(frame):
    """Distribution-adjusted past OHLC normalized to last row's raw close."""
    if 'Adj Close' not in frame or frame.empty:
        raise ValueError('Adjusted daily close unavailable')
    factor = frame['Adj Close'].astype(float) / frame['Close'].astype(float)
    if not (factor > 0).all() or not all(math.isfinite(x) for x in factor):
        raise ValueError('Invalid adjustment factor')
    result = frame.copy()
    for column in ('Open', 'High', 'Low', 'Close'):
        result[column] = frame[column].astype(float) * factor / factor.iloc[-1]
    return result


def daily_metrics(frame):
    if len(frame) < 252:
        raise ValueError('Need at least 252 completed daily observations')
    frame = adjusted_frame(frame)
    close = frame['Close']
    previous = close.shift(1)
    tr = pd.concat([frame['High'] - frame['Low'], (frame['High'] - previous).abs(),
                    (frame['Low'] - previous).abs()], axis=1).max(axis=1)
    atr = float(tr.iloc[:14].mean())
    for x in tr.iloc[14:]:
        atr = (13 * atr + float(x)) / 14
    window = close.iloc[-252:]
    return {
        'as_of_session': str(day_index(frame)[-1]),
        'last_close': float(close.iloc[-1]),
        'sma_50': float(close.iloc[-50:].mean()),
        'sma_200': float(close.iloc[-200:].mean()),
        'atr_14': atr,
        'highest_close_252': float(window.max()),
        'highest_close_252_date': str(window.idxmax().date()),
        'adjustment': 'Adj Close factor applied to OHLC, normalized to last completed raw Close',
        'source': 'Yahoo Finance via yfinance; daily observations may be revised'
    }


def today_actions(frame, today):
    rows = frame.loc[day_index(frame) == today]
    missing = [key for key in ('Dividends', 'Stock Splits') if key not in frame]
    return {'complete': not missing,
            'dividends': float(rows['Dividends'].sum()) if 'Dividends' in rows else None,
            'splits': float(rows['Stock Splits'].sum()) if 'Stock Splits' in rows else None,
            'limitation': 'Only provider-reported corporate actions are checked'}


def references(now, symbols, session, test=False):
    import yfinance as yf
    today = now.astimezone(core.NY).date()
    cached = read_json(DATA / 'reference_us.json', {})
    valid = cached.get('session_date') == str(today) and set(symbols) <= set(cached.get('symbols', {}))
    if valid and not test:
        return cached
    result = {'session_date': str(today), 'fixed_at_utc': now.isoformat(),
              'fixed_before_open': bool(session and now < session[0]), 'symbols': {}}
    for symbol in symbols:
        frame = yf.Ticker(symbol).history(period='2y', interval='1d', auto_adjust=False,
                                          prepost=False, actions=True, timeout=25)
        frame = frame.dropna(subset=['Open', 'High', 'Low', 'Close']).sort_index()
        prior = frame.loc[day_index(frame) < today]
        if prior.empty or (today - day_index(prior)[-1]).days > 7:
            raise ValueError(f'{symbol}: stale daily references')
        result['symbols'][symbol] = daily_metrics(prior)
    if not test:
        write_json(DATA / 'reference_us.json', result)
    return result


def event_conditions(symbol, price, ref, config, actions):
    protected = not actions['complete'] or bool(actions['dividends']) or bool(actions['splits'])
    pct_day = 100 * (price / ref['last_close'] - 1)
    pct_high = 100 * (price / ref['highest_close_252'] - 1)
    conditions = {}
    if symbol in config['us']['drop_event_symbols'] and not protected:
        for level in config['us']['daily_drop_thresholds_pct']:
            conditions[f'{symbol}:daily:{level:g}'] = pct_day <= level
        for level in config['us']['drawdown_thresholds_pct']:
            conditions[f'{symbol}:high252:{level:g}'] = pct_high <= level
    bands = config['us']['price_bands'].get(symbol)
    if bands and not protected:
        for name, low, high in bands:
            conditions[f'{symbol}:band:{name}'] = low <= price <= high
            conditions[f'{symbol}:below:{name}'] = price < low
    return {'previous_close_change_pct': pct_day, 'high252_drawdown_pct': pct_high,
            'mechanical_action_guard': protected, 'corporate_actions': actions,
            'drop_evaluation_status': 'suspended_for_corporate_action_or_missing_fields' if protected else 'available',
            'conditions': conditions}


def comparison(previous, payload):
    current = {key: value for v in payload['symbols'].values()
               for key, value in v['event_evaluation']['conditions'].items()}
    if not previous or previous.get('mode') == 'manual_test_not_for_trading':
        return {'kind': 'initial_baseline', 'active_conditions': [k for k, v in current.items() if v],
                'new_crossings': [], 'recovered_conditions': []}
    if (previous.get('config_revision') != payload['config_revision'] or
            previous.get('reference_session_date') != payload['reference_session_date']):
        return {'kind': 'reference_or_configuration_reset', 'active_conditions': [k for k, v in current.items() if v],
                'new_crossings': [], 'recovered_conditions': []}
    old = {key: value for v in previous.get('symbols', {}).values()
           for key, value in v.get('event_evaluation', {}).get('conditions', {}).items()}
    # A transition is known only between two observations. No invented crossing time.
    return {'kind': 'sampled_change', 'previous_generated_utc': previous.get('generated_utc'),
            'active_conditions': [k for k, v in current.items() if v],
            'new_crossings': [k for k, v in current.items() if v and k in old and not old[k]],
            'recovered_conditions': [k for k, v in current.items() if not v and old.get(k, False)],
            'recovery_confirmation': 'sample only; 15-minute continuous hold NOT verified'}


def last_session_of_week(now):
    import pandas_market_calendars as mcal
    day = now.astimezone(core.NY).date()
    monday = day - timedelta(days=day.weekday())
    table = mcal.get_calendar('NYSE').schedule(start_date=monday, end_date=monday + timedelta(days=4))
    return not table.empty and table.index[-1].date() == day


def read_symbol(symbol, now, mode, ref, config, week_final):
    import yfinance as yf
    test = mode == 'manual_test_not_for_trading'
    result = core.read_ticker(symbol, now, test_mode=test)
    frame = yf.Ticker(symbol).history(period='2y' if mode == 'end_of_day' else '5d',
                                     interval='1d', prepost=False, auto_adjust=False, actions=True, timeout=25)
    frame = frame.dropna(subset=['Open', 'High', 'Low', 'Close']).sort_index()
    today = now.astimezone(core.NY).date()
    actions = today_actions(frame, today)
    result['price_source_url'] = f'https://finance.yahoo.com/quote/{symbol}/history/'
    result['daily_reference'] = ref
    result['price_status'] = 'regular-session 5m bar Close, may be delayed or still forming'
    if mode == 'end_of_day':
        complete = frame.loc[day_index(frame) <= today]
        if complete.empty or day_index(complete)[-1] != today:
            raise ValueError(f'{symbol}: today daily closing bar not yet available')
        result['last_price'] = float(complete['Close'].iloc[-1])
        result['close_session_date'] = str(today)
        result['price_status'] = 'post-close provider daily bar; not exchange-certified and may be revised'
        result['daily_close_indicators'] = daily_metrics(complete)
        # Recompute from weekly history and the daily closing bar, not an after-hours tick.
        week = yf.Ticker(symbol).history(period='max', interval='1wk', prepost=False, auto_adjust=False, timeout=25)
        current = core.iso_week(now)
        finished = [(stamp, float(c)) for stamp, c in week['Close'].dropna().items() if core.iso_week(stamp) < current]
        closes = [c for _, c in finished] + [result['last_price']]
        summary = core.indicator_summary(closes)
        if week_final:
            result['weekly_indicators']['last_completed_week'] = {'week_date': str(today), **summary}
            result['weekly_indicators']['current_week_provisional'] = None
        else:
            result['weekly_indicators']['current_week_provisional'] = summary
        result['weekly_indicators']['week_closed_in_this_snapshot'] = week_final
        result['weekly_indicators']['provisional_price_to_ema_gap_pct'] = {
            f'ema_{n}_gap_pct': 100 * (result['last_price'] / summary[f'ema_{n}'] - 1)
            for n in (20, 50, 100, 200) if summary[f'ema_{n}']}
    if symbol in config['us']['price_bands']:
        bands = config['us']['price_bands'][symbol]
        result['buy_watch'] = {'price_band_status': core.price_band(result['last_price'], bands),
                              'bands': bands, 'not_an_automatic_buy_signal': True}
    result['event_evaluation'] = event_conditions(symbol, result['last_price'], ref, config, actions)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    config = read_json(CONFIG)
    if not config or config.get('schema_version') != 2:
        raise SystemExit('Valid monitor_config.json is required')
    core.TARGET_BANDS = config['us']['price_bands']
    session, _ = core.exchange_schedule(now)
    mode = session_mode(now, session, args.test)
    if mode is None:
        outputs(publish=False, save=False)
        print('Outside collection window; no AI call or publication')
        return
    symbols = config['us']['symbols']
    try:
        refs = references(now, symbols, session, args.test)
        if mode == 'preopen_reference':
            outputs(publish=False, save=True)
            print('Pre-open reference update only; no market report')
            return
        week_final = mode == 'end_of_day' and last_session_of_week(now)
        snapshots = {s: read_symbol(s, now, mode, refs['symbols'][s], config, week_final) for s in symbols}
        payload = {
            'schema_version': 2, 'source': 'Yahoo Finance via yfinance; unofficial',
            'mode': mode, 'config_revision': config['revision'],
            'generated_utc': now.isoformat(), 'generated_kst': now.astimezone(core.KST).isoformat(),
            'generated_new_york': now.astimezone(core.NY).isoformat(),
            'reference_session_date': refs['session_date'], 'reference_fixed_at_utc': refs['fixed_at_utc'],
            'reference_fixed_before_open': refs['fixed_before_open'],
            'verified_regular_session': mode == 'regular_session',
            'market': {'ny_date': str(now.astimezone(core.NY).date()),
                       'is_open_at_collection': bool(session and session[0] <= now < session[1]),
                       'ny_open': session[0].astimezone(core.NY).isoformat() if session else None,
                       'ny_close': session[1].astimezone(core.NY).isoformat() if session else None,
                       'last_session_of_week_closed': week_final},
            'symbols': snapshots,
            'limitations': ['No independent event detector, real-time feed, orders, or delivery receipts',
                           '15-minute rearm and 60-minute delivery cooldown are not implemented',
                           'Breadth, yields, VIX/VXN, time-matched volume, and calendars require separate verified sources',
                           'Weekly EMA/RSI use provider split-adjusted, non-dividend-adjusted closes; daily baselines use recorded adjusted prices']}
        previous = read_json(DATA / 'latest.json')
        payload['comparison'] = comparison(previous, payload)
        # Late initialization is a baseline, but later same-session samples can cross.
        if not refs['fixed_before_open'] and payload['comparison']['kind'] != 'sampled_change':
            payload['comparison']['new_crossings'] = []
            payload['comparison']['kind'] = 'late_reference_initialization_not_a_new_price_event'
        if args.test:
            write_json(DATA / 'test.json', payload)
            outputs(publish=True, save=False)
        else:
            if previous and previous.get('mode') != 'manual_test_not_for_trading':
                write_json(DATA / 'previous.json', previous)
            write_json(DATA / 'latest.json', payload)
            write_json(DATA / 'state_us.json', {
                'last_check_utc': now.isoformat(), 'reference_date': refs['session_date'],
                'config_revision': config['revision'],
                'last_prices': {s: p['last_price'] for s, p in snapshots.items()},
                'active_conditions': payload['comparison']['active_conditions'],
                'last_notification': None, 'delivery_status': 'not observable by collector',
                'current_judgment': 'not assessed by collector',
                'rearm_status': 'not implemented; hourly snapshots do not prove a continuous hold',
                'next_check': 'next scheduled US observation; no off-schedule trigger'})
            outputs(publish=True, save=True)
        print(f'Snapshot prepared: mode={mode}, revision={config["revision"]}')
    except Exception as exc:
        outputs(publish=False, save=False)
        raise SystemExit(f'Collection invalid; latest valid snapshot retained: {exc}') from exc


if __name__ == '__main__':
    main()
