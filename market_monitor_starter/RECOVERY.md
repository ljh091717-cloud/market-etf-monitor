# Provider throttling and dated closing recovery

Yahoo history requests retry rate-limit errors at most three times, with 10 and
30 seconds between attempts. Other errors fail immediately. Errors identify the
symbol, interval and attempt; invalid collections still retain the last valid
snapshot. A retry cannot guarantee that a provider quota will have reset.

End-of-day collection uses one daily request and one weekly request per symbol
when references are cached. It does not request 5-minute bars or fetch the weekly
history twice. Closing prices come from the requested session's daily Close.
The unavailable 5-minute timestamp/age fields are null in closing snapshots.
The schedules, price bands, daily thresholds and late-reference comparison rule
are unchanged.

## Recover a missed session without changing live state

From this directory, with requirements.txt installed:

```sh
python recover_close.py --session-date 2026-10-29
```

This validates an exchange session that has closed, fetches Yahoo daily and weekly
history, requires an exact daily bar for every configured symbol, and writes
`docs/data/recovered/2026-10-29.json` only after all symbols pass validation.
Future/non-session dates, missing bars, invalid prices and existing archive paths
are rejected. Historical bars after the requested date are excluded from daily
calculations. Very old sessions may fail because only two years of daily history
are fetched; the script never substitutes another date.

The archive records the session date separately from retrieval time, source URLs,
raw daily OHLC/adjusted close, and reconstructed indicators. It is explicitly
`historical_end_of_day_recovery`, is not a fresh trading snapshot, and has no new
crossings or recovered-condition events. It never writes latest.json,
previous.json, reference_us.json, state_us.json or analysis_state_us.json, even
when those files contain newer data. It does not invoke AI, notification delivery
or deployment. Publishing this dated data archive is separate from deploying the
collector code. References and labels are reconstructed using the configuration
at retrieval; this is not replay of historical alert decisions.

## Verification

```sh
python -m unittest discover -s tests -v
python -m pytest tests -q
```

pytest additionally runs the existing function-style indicator tests. Tests use
mock provider data and temporary directories; no live quotes or alerts are sent.
