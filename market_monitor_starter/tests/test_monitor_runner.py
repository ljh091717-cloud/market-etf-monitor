import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
import pandas as pd
import monitor_runner as m

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.open = datetime(2026,10,19,13,30,tzinfo=timezone.utc)
        self.session = (self.open, self.open+timedelta(hours=6,minutes=30))
        self.config = {'us': {'drop_event_symbols':['QQQ','VOO'], 'daily_drop_thresholds_pct':[-2,-3,-5],
            'drawdown_thresholds_pct':[-10,-15,-20,-30], 'price_bands':{'QQQM':[['first',301,304],['second',293,296],['third',280,285]]}}}
        self.actions = {'complete':True,'dividends':0,'splits':0}
    def test_preopen(self):
        self.assertEqual(m.session_mode(self.open-timedelta(minutes=20), self.session),'preopen_reference')
    def test_no_early_collection(self):
        self.assertIsNone(m.session_mode(self.open+timedelta(minutes=10), self.session))
    def test_hourly_collection(self):
        self.assertEqual(m.session_mode(self.open+timedelta(minutes=50),self.session),'regular_session')
    def test_after_close(self):
        self.assertEqual(m.session_mode(self.session[1]+timedelta(minutes=20),self.session),'end_of_day')
    def test_after_close_window(self):
        self.assertIsNone(m.session_mode(self.session[1]+timedelta(minutes=50),self.session))
    def test_holiday(self):
        self.assertIsNone(m.session_mode(self.open,None))
    def test_half_day(self):
        early=(self.open,self.open+timedelta(hours=3,minutes=30))
        self.assertEqual(m.session_mode(early[1]+timedelta(minutes=20),early),'end_of_day')
        self.assertIsNone(m.session_mode(early[1]+timedelta(minutes=80),early))
    def test_test_mode(self):
        self.assertEqual(m.session_mode(self.open,None,True),'manual_test_not_for_trading')
    def test_dividend_guard(self):
        e=m.event_conditions('QQQ',95,{'last_close':100,'highest_close_252':120},self.config,dict(self.actions,dividends=5))
        self.assertTrue(e['mechanical_action_guard']);self.assertEqual(e['conditions'],{})
    def test_missing_actions_guard(self):
        e=m.event_conditions('QQQ',95,{'last_close':100,'highest_close_252':120},self.config,dict(self.actions,complete=False))
        self.assertEqual(e['conditions'],{})
    def test_crossing(self):
        ref={'last_close':100,'highest_close_252':110}
        e=m.event_conditions('QQQ',97,ref,self.config,self.actions)
        self.assertTrue(e['conditions']['QQQ:daily:-2'])
        self.assertFalse(e['conditions']['QQQ:daily:-5'])
    def test_no_qqqm_duplicate_drawdown(self):
        e=m.event_conditions('QQQM',294,{'last_close':310,'highest_close_252':340},self.config,self.actions)
        self.assertTrue(e['conditions']['QQQM:band:second'])
        self.assertFalse(any('daily' in k for k in e['conditions']))
    def payload(self, condition, revision='1', day='2026-10-19'):
        return {'mode':'regular_session','config_revision':revision,'reference_session_date':day,
            'generated_utc':'2026-10-19T14:20:00+00:00', 'symbols':{'QQQ':{'event_evaluation':{'conditions':{'QQQ:daily:-2':condition}}}}}
    def test_initial_baseline(self):
        r=m.comparison(None,self.payload(True));self.assertEqual(r['new_crossings'],[])
        self.assertEqual(r['kind'],'initial_baseline')
    def test_sampled_crossing(self):
        r=m.comparison(self.payload(False),self.payload(True));self.assertEqual(r['new_crossings'],['QQQ:daily:-2'])
    def test_no_repeat(self):
        self.assertEqual(m.comparison(self.payload(True),self.payload(True))['new_crossings'],[])
    def test_config_reset_not_event(self):
        r=m.comparison(self.payload(False),self.payload(True,revision='2'));self.assertEqual(r['new_crossings'],[])
    def test_daily_reset_not_event(self):
        r=m.comparison(self.payload(False),self.payload(True,day='2026-10-20'));self.assertEqual(r['new_crossings'],[])
    def test_recovery_is_only_sample(self):
        r=m.comparison(self.payload(True),self.payload(False));self.assertIn('NOT verified',r['recovery_confirmation'])
    def frame(self):
        idx=pd.bdate_range(end='2026-10-16',periods=300,tz='America/New_York')
        close=pd.Series(range(1,301),index=idx,dtype=float)+100
        return pd.DataFrame({'Open':close,'High':close+1,'Low':close-1,'Close':close,'Adj Close':close},index=idx)
    def test_daily_metrics(self):
        r=m.daily_metrics(self.frame());self.assertEqual(r['last_close'],400)
        self.assertEqual(r['sma_50'],375.5);self.assertEqual(r['sma_200'],300.5)
        self.assertEqual(r['highest_close_252'],400);self.assertAlmostEqual(r['atr_14'],2)
    def test_missing_adjusted_close_rejected(self):
        with self.assertRaises(ValueError):m.daily_metrics(self.frame().drop(columns=['Adj Close']))
    def test_short_daily_history_rejected(self):
        with self.assertRaises(ValueError):m.daily_metrics(self.frame().iloc[:200])
    def test_adjustment_normalizes_latest(self):
        f=self.frame();f['Adj Close']=f['Close']*0.5
        self.assertEqual(m.adjusted_frame(f)['Close'].iloc[-1],400)

class LateReferenceTests(unittest.TestCase):
    """Exercise the persisted comparison through main, without live collection."""
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.config = {'schema_version': 2, 'revision': '1', 'us': {
            'symbols': ['QQQM'], 'drop_event_symbols': [],
            'daily_drop_thresholds_pct': [], 'drawdown_thresholds_pct': [],
            'price_bands': {'QQQM': [['first', 301, 304]]}}}
        self.config_path = self.data / 'config.json'
        m.write_json(self.config_path, self.config)
        self.addCleanup(setattr, m.core, 'TARGET_BANDS', m.core.TARGET_BANDS)

    def observe(self, price, day='2026-10-19', hour=14, preopen=False):
        now = datetime.fromisoformat(f'{day}T{hour:02d}:20:00+00:00')
        start = now.replace(hour=13, minute=30)
        session = (start, start + timedelta(hours=6, minutes=30))
        refs = {'session_date': day, 'fixed_at_utc': start.isoformat(),
                'fixed_before_open': preopen,
                'symbols': {'QQQM': {'last_close': 310, 'highest_close_252': 340}}}
        # A same-day reference remains cached, including fixed_before_open=False.
        cached = m.read_json(self.data / 'reference_us.json', {})
        if cached.get('session_date') != day:
            m.write_json(self.data / 'reference_us.json', refs)
        evaluation = m.event_conditions('QQQM', price, refs['symbols']['QQQM'],
                                       self.config, {'complete': True, 'dividends': 0, 'splits': 0})
        with patch.object(m, 'DATA', self.data), patch.object(m, 'CONFIG', self.config_path), \
                patch.object(m, 'datetime') as clock, \
                patch.object(m.core, 'exchange_schedule', return_value=(session, None)), \
                patch.object(m, 'read_symbol', return_value={'last_price': price, 'event_evaluation': evaluation}), \
                patch.object(m, 'outputs'), patch('sys.argv', ['monitor_runner.py']), \
                patch('yfinance.Ticker') as ticker:
            clock.now.return_value = now
            m.main()
            ticker.assert_not_called()
        result = m.read_json(self.data / 'latest.json')
        self.assertEqual(result['reference_fixed_before_open'], preopen)
        return result['comparison']

    def test_late_first_active_sample_is_baseline(self):
        result = self.observe(303)
        self.assertEqual(result['kind'], 'late_reference_initialization_not_a_new_price_event')
        self.assertIn('QQQM:band:first', result['active_conditions'])
        self.assertEqual(result['new_crossings'], [])

    def test_late_entry_and_repeated_sample(self):
        self.assertEqual(self.observe(305)['new_crossings'], [])
        result = self.observe(303, hour=15)
        self.assertEqual(result['kind'], 'sampled_change')
        self.assertEqual(result['new_crossings'], ['QQQM:band:first'])
        self.assertEqual(self.observe(303, hour=16)['new_crossings'], [])

    def test_late_active_sample_exit_then_reentry(self):
        self.observe(303)
        self.assertIn('QQQM:band:first', self.observe(305, hour=15)['recovered_conditions'])
        self.assertEqual(self.observe(303, hour=16)['new_crossings'], ['QQQM:band:first'])

    def test_session_change_resets_then_allows_crossing(self):
        self.observe(305)
        result = self.observe(303, day='2026-10-20')
        self.assertEqual(result['kind'], 'late_reference_initialization_not_a_new_price_event')
        self.assertEqual(result['new_crossings'], [])
        self.observe(305, day='2026-10-20', hour=15)
        self.assertEqual(self.observe(303, day='2026-10-20', hour=16)['new_crossings'], ['QQQM:band:first'])

    def test_preopen_reference_behavior_unchanged(self):
        result = self.observe(305, preopen=True)
        self.assertEqual(result['kind'], 'initial_baseline')
        self.assertEqual(result['new_crossings'], [])
        self.assertEqual(self.observe(303, hour=15, preopen=True)['new_crossings'], ['QQQM:band:first'])
        self.assertEqual(self.observe(303, hour=16, preopen=True)['new_crossings'], [])

    def test_manual_previous_is_not_a_baseline(self):
        self.observe(305)
        previous = m.read_json(self.data / 'latest.json')
        previous['mode'] = 'manual_test_not_for_trading'
        m.write_json(self.data / 'latest.json', previous)
        self.assertEqual(self.observe(303, hour=15)['new_crossings'], [])

    def test_config_change_resets_late_reference(self):
        self.observe(305)
        self.config['revision'] = '2'
        m.write_json(self.config_path, self.config)
        self.assertEqual(self.observe(303, hour=15)['new_crossings'], [])

if __name__=='__main__':unittest.main()
