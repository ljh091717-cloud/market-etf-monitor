from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import collector as core
import monitor_runner as m
import provider
import recover_close as recovery


def daily_frame():
    index = pd.bdate_range(end='2026-10-29', periods=300, tz=core.NY)
    close = pd.Series(100.0, index=index)
    return pd.DataFrame({'Open': close, 'High': close + 1, 'Low': close - 1,
                         'Close': close, 'Adj Close': close, 'Dividends': 0, 'Stock Splits': 0})


def weekly_frame():
    index = pd.date_range(end='2026-10-26', periods=260, freq='W-MON', tz=core.NY)
    return pd.DataFrame({'Close': 100.0}, index=index)


class RetryTests(unittest.TestCase):
    @patch('provider.time.sleep')
    def test_rate_limit_then_success(self, sleep):
        ticker = Mock()
        frame = daily_frame()
        ticker.history.side_effect = [RuntimeError('Too Many Requests. Rate limited.'), frame]
        self.assertIs(provider.history(ticker, 'QQQ', interval='1d'), frame)
        sleep.assert_called_once_with(10)
        self.assertTrue(ticker.history.call_args.kwargs['raise_errors'])

    @patch('provider.time.sleep')
    def test_exhaustion_is_bounded_and_identifies_request(self, sleep):
        ticker = Mock()
        ticker.history.side_effect = RuntimeError('429 rate limited')
        with self.assertRaisesRegex(RuntimeError, 'QQQ interval=1wk attempt=3/3'):
            provider.history(ticker, 'QQQ', interval='1wk')
        self.assertEqual(ticker.history.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [10, 30])

    @patch('provider.time.sleep')
    def test_other_errors_not_retried(self, sleep):
        ticker = Mock()
        ticker.history.side_effect = ValueError('invalid timezone')
        with self.assertRaisesRegex(RuntimeError, 'invalid timezone'):
            provider.history(ticker, 'VOO', interval='1d')
        self.assertEqual(ticker.history.call_count, 1)
        sleep.assert_not_called()


class ClosingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.config = {'schema_version': 2, 'revision': 'test', 'us': {
            'symbols': ['QQQ'], 'drop_event_symbols': ['QQQ'],
            'daily_drop_thresholds_pct': [-2], 'drawdown_thresholds_pct': [-10], 'price_bands': {}}}
        self.config_path = self.data / 'config.json'
        m.write_json(self.config_path, self.config)
        self.now = datetime(2026, 10, 29, 22, 30, tzinfo=timezone.utc)
        self.anchor = self.now.replace(hour=17)
        self.daily = daily_frame()
        self.weekly = weekly_frame()
        self.calls = []
        self.addCleanup(setattr, core, 'TARGET_BANDS', core.TARGET_BANDS)
        for name, value in [('DATA', self.data), ('CONFIG', self.config_path)]:
            p = patch.object(m, name, value)
            p.start()
            self.addCleanup(p.stop)

    def history(self, ticker, symbol, **kwargs):
        self.calls.append(kwargs['interval'])
        if kwargs['interval'] == '1d':
            return self.daily
        if kwargs['interval'] == '1wk':
            return self.weekly
        self.fail('EOD must not fetch intraday bars')

    def test_eod_uses_one_daily_one_weekly(self):
        ref = m.daily_metrics(self.daily.iloc[:-1])
        with patch.object(core, 'history', side_effect=self.history):
            result = m.read_symbol('QQQ', self.anchor, 'end_of_day', ref, self.config, False)
        self.assertEqual(self.calls, ['1d', '1wk'])
        self.assertEqual(result['last_price'], 100)
        self.assertEqual(result['close_session_date'], '2026-10-29')
        self.assertIsNone(result['last_5m_bar_start_utc'])
        self.assertEqual(result['weekly_indicators']['current_week_provisional'],
                         core.indicator_summary([100.0] * 260))

    def test_week_final_closes_provisional_week(self):
        with patch.object(core, 'history', side_effect=self.history):
            result = m.read_symbol('QQQ', self.anchor, 'end_of_day',
                                   m.daily_metrics(self.daily.iloc[:-1]), self.config, True)
        self.assertIsNone(result['weekly_indicators']['current_week_provisional'])
        self.assertEqual(result['weekly_indicators']['last_completed_week']['week_date'], '2026-10-29')

    def test_missing_daily_close_fails_before_weekly_fetch(self):
        self.daily = self.daily.iloc[:-1]
        with patch.object(core, 'history', side_effect=self.history), self.assertRaisesRegex(ValueError, 'not yet available'):
            m.read_symbol('QQQ', self.anchor, 'end_of_day', {}, self.config, False)
        self.assertEqual(self.calls, ['1d'])

    def test_regular_session_still_rejects_stale_intraday_data(self):
        bars = pd.DataFrame({'Close': [100.0]},
                            index=pd.DatetimeIndex(['2026-10-28T15:00:00-04:00']))
        with patch.object(core, 'history', return_value=bars) as history, \
                self.assertRaisesRegex(RuntimeError, 'stale or invalid quote'):
            core.read_ticker('QQQ', self.anchor, test_mode=False)
        self.assertEqual(history.call_count, 1)

    def test_recovery_ignores_later_daily_bars(self):
        later = self.daily.iloc[[-1]].copy()
        later.index = pd.DatetimeIndex(['2026-10-30'], tz=core.NY)
        later.loc[:, ['Open', 'High', 'Low', 'Close', 'Adj Close']] = 999.0
        self.daily = pd.concat([self.daily, later])
        with patch.object(core, 'history', side_effect=self.history):
            path = recovery.recover('2026-10-29', self.now)
        result = m.read_json(path)['symbols']['QQQ']
        self.assertEqual(result['last_price'], 100)
        self.assertEqual(result['daily_close_indicators']['last_close'], 100)

    def test_main_failure_retains_latest_previous_and_state(self):
        before = {'mode': 'regular_session', 'market': {'ny_date': '2026-10-29'}}
        paths = [self.data / name for name in ('latest.json', 'previous.json', 'state_us.json')]
        for path in paths:
            m.write_json(path, before)
        saved = [p.read_bytes() for p in paths]
        with patch.object(m, 'datetime') as clock, patch('sys.argv', ['monitor_runner.py']), \
                patch.object(m, 'references', return_value={'symbols': {'QQQ': {}}, 'session_date': '2026-10-29'}), \
                patch.object(m, 'read_symbol', side_effect=RuntimeError('429 rate limited')), \
                patch.object(m, 'outputs') as outputs, self.assertRaisesRegex(SystemExit, 'latest valid snapshot retained'):
            clock.now.return_value = self.now.replace(hour=21)
            m.main()
        self.assertEqual([p.read_bytes() for p in paths], saved)
        outputs.assert_called_with(publish=False, save=False)

    def test_recovery_archives_date_and_preserves_newer_live_data(self):
        paths = [self.data / name for name in ('latest.json', 'previous.json', 'reference_us.json', 'state_us.json')]
        for path in paths:
            m.write_json(path, {'reference_session_date': '2026-10-30', 'last_price': 999})
        saved = [p.read_bytes() for p in paths]
        with patch.object(core, 'history', side_effect=self.history):
            path = recovery.recover('2026-10-29', self.now)
        result = m.read_json(path)
        self.assertEqual(self.calls, ['1d', '1wk'])
        self.assertEqual([p.read_bytes() for p in paths], saved)
        self.assertEqual(result['recovered_session_date'], '2026-10-29')
        self.assertEqual(result['mode'], 'historical_end_of_day_recovery')
        self.assertNotIn('generated_utc', result)
        self.assertFalse(result['use_for_intraday_alerts'])
        self.assertEqual(result['comparison']['new_crossings'], [])
        self.assertEqual(result['symbols']['QQQ']['provider_daily_bar']['Close'], 100)
        with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
            recovery.recover('2026-10-29', self.now)

    def test_recovery_rejects_future_and_non_session(self):
        for day in ['2026-10-30', '2026-10-25']:
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, 'completed exchange session'):
                recovery.recover(day, self.now)

    def test_recovery_does_not_substitute_another_day(self):
        self.daily = self.daily.iloc[:-1]
        with patch.object(core, 'history', side_effect=self.history), self.assertRaisesRegex(ValueError, 'exactly one'):
            recovery.recover('2026-10-29', self.now)
        self.assertFalse((self.data / 'recovered').exists())

    def test_recovery_invalid_price_does_not_write(self):
        self.daily.loc[self.daily.index[-1], 'Close'] = float('inf')
        with patch.object(core, 'history', side_effect=self.history), self.assertRaisesRegex(ValueError, 'invalid provider'):
            recovery.recover('2026-10-29', self.now)
        self.assertFalse((self.data / 'recovered').exists())

    def test_recovery_partial_failure_does_not_write(self):
        self.config['us']['symbols'].append('VOO')
        m.write_json(self.config_path, self.config)
        def history(ticker, symbol, **kwargs):
            if symbol == 'VOO':
                raise RuntimeError('rate limited')
            return self.history(ticker, symbol, **kwargs)
        with patch.object(core, 'history', side_effect=history), self.assertRaisesRegex(RuntimeError, 'rate limited'):
            recovery.recover('2026-10-29', self.now)
        self.assertFalse((self.data / 'recovered').exists())


if __name__ == '__main__':
    unittest.main()
