import unittest
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

if __name__=='__main__':unittest.main()
