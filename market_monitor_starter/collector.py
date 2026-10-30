"""US-market monitoring snapshot for GitHub Actions + GitHub Pages.

Public output: docs/data/latest.json. Do NOT include private portfolio details.

Normal executions only publish if the US stock market is open and Yahoo
Finance returned fresh regular-session 5-minute data for all watched tickers.
Manual --test permits stale data for an infrastructure smoke test; consumers
must never interpret a test snapshot as a fresh trading signal.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from provider import history

NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
WATCHLIST = ("QQQ", "QQQM", "VOO")
TARGET_BANDS = {
    "QQQM": [("first", 301.0, 304.0), ("second", 293.0, 296.0), ("third", 280.0, 285.0)],
    "VOO": [("first", 695.0, 700.0), ("second", 670.0, 680.0), ("third", 645.0, 655.0)],
}
OUTPUT = Path("docs/data/latest.json")


def ema_last(values: list[float], length: int) -> float | None:
    """TradingView-like EMA with initial SMA seed over `length` closes."""
    if len(values) < length:
        return None
    if any(not math.isfinite(float(v)) for v in values):
        raise ValueError("Non-finite weekly close in EMA input")
    result = sum(values[:length]) / length
    alpha = 2.0 / (length + 1)
    for value in values[length:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def rsi_wilder_last(values: list[float], length: int = 14) -> float | None:
    """Wilder RSI seeded with arithmetic averages of first length changes."""
    if len(values) <= length:
        return None
    diffs = [float(b) - float(a) for a, b in zip(values[:-1], values[1:])]
    gains = [max(x, 0.0) for x in diffs]
    losses = [max(-x, 0.0) for x in diffs]
    avg_gain = sum(gains[:length]) / length
    avg_loss = sum(losses[:length]) / length
    for gain, loss in zip(gains[length:], losses[length:]):
        avg_gain = ((length - 1) * avg_gain + gain) / length
        avg_loss = ((length - 1) * avg_loss + loss) / length
    if avg_loss == 0.0 and avg_gain == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def indicator_summary(closes: list[float]) -> dict:
    values = {f"ema_{n}": maybe_round(ema_last(closes, n), 4) for n in (20, 50, 100, 200)}
    values["rsi_14"] = maybe_round(rsi_wilder_last(closes, 14), 3)
    values["sample_weeks"] = len(closes)
    return values


def maybe_round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def price_band(price: float, bands: list[tuple[str, float, float]]) -> dict:
    """Avoid labelling a price below a band as inside that band."""
    if price > bands[0][2]:
        return {"status": "above_first", "description": "1차 구간 위"}
    for i, (name, lo, hi) in enumerate(bands):
        if lo <= price <= hi:
            return {"status": f"inside_{name}", "description": f"{name} 구간 내부"}
        if i + 1 < len(bands):
            next_name, _, next_high = bands[i + 1]
            if next_high < price < lo:
                return {
                    "status": f"below_{name}_above_{next_name}",
                    "description": f"{name} 구간 아래·{next_name} 구간 위",
                }
    if price < bands[-1][1]:
        return {"status": "below_third", "description": "3차 구간 아래"}
    raise ValueError(f"Unclassified market price {price}")


def iso_week(value) -> tuple[int, int]:
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        t = t.tz_localize(NY)
    ny = t.tz_convert(NY).isocalendar()
    return int(ny.year), int(ny.week)


def quote_as_utc(timestamp) -> datetime:
    t = pd.Timestamp(timestamp)
    if t.tzinfo is None:
        # yfinance normally returns a timezone-aware index; fail rather than guess.
        raise ValueError("Intraday data had no timezone")
    return t.tz_convert("UTC").to_pydatetime()


def read_ticker(symbol: str, now: datetime, *, test_mode: bool, closing_price=None) -> dict:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    last_timestamp = None
    age_minutes = None
    if closing_price is None:
        bars = history(ticker, symbol, period="5d", interval="5m", prepost=False, auto_adjust=False, timeout=25)
        if bars.empty:
            raise RuntimeError(f"{symbol}: empty intraday data")
        bars = bars.dropna(subset=["Close"])
        last_timestamp = quote_as_utc(bars.index[-1])
        price = float(bars["Close"].iloc[-1])
        age_minutes = (now - last_timestamp).total_seconds() / 60.0
        is_today = last_timestamp.astimezone(NY).date() == now.astimezone(NY).date()
        if not test_mode and (not is_today or not (-2.0 <= age_minutes <= 50.0)):
            raise RuntimeError(f"{symbol}: stale or invalid quote: age={age_minutes:.1f}m, today={is_today}")
    else:
        price = float(closing_price)
    if not math.isfinite(price) or price <= 0:
        raise RuntimeError(f"{symbol}: invalid last price")
    week = history(ticker, symbol, period="max", interval="1wk", prepost=False, auto_adjust=False, timeout=25)
    if week.empty:
        raise RuntimeError(f"{symbol}: empty weekly history")
    weekly = week["Close"].dropna().astype(float)
    this_week = iso_week(now)
    finished = [(stamp, float(c)) for stamp, c in weekly.items() if iso_week(stamp) < this_week]
    if len(finished) < 200:
        raise RuntimeError(f"{symbol}: only {len(finished)} completed weeks; EMA200 unavailable")
    if not test_mode:
        last_prior_week = pd.Timestamp(finished[-1][0]).date()
        if (now.astimezone(NY).date() - last_prior_week).days > 15:
            raise RuntimeError(f"{symbol}: completed weekly candles are stale")
    completed_closes = [item[1] for item in finished]
    closed = indicator_summary(completed_closes)
    forming = indicator_summary(completed_closes + [price])
    forming["week_in_progress"] = now.astimezone(NY).date().isocalendar().week

    gaps = {}
    for n in (20, 50, 100, 200):
        v = forming[f"ema_{n}"]
        gaps[f"ema_{n}_gap_pct"] = maybe_round(100.0 * (price / v - 1.0), 3) if v else None

    result = {
        "symbol": symbol,
        "currency": "USD",
        "last_price": round(price, 4),
        "last_5m_bar_start_utc": last_timestamp.isoformat() if last_timestamp else None,
        "last_5m_bar_start_ny": last_timestamp.astimezone(NY).isoformat() if last_timestamp else None,
        "data_age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "regular_session_only": True,
        "weekly_indicators": {
            "last_completed_week": {
                "week_date": str(pd.Timestamp(finished[-1][0]).date()),
                **closed,
            },
            "current_week_provisional": forming,
            "provisional_price_to_ema_gap_pct": gaps,
        },
    }
    if symbol in TARGET_BANDS:
        bands = TARGET_BANDS[symbol]
        result["buy_watch"] = {
            "price_band_status": price_band(price, bands),
            "bands": [{"level": name, "low": lo, "high": hi} for name, lo, hi in bands],
            "not_an_automatic_buy_signal": True,
        }
    return result


def exchange_schedule(now_utc: datetime):
    import pandas_market_calendars as mcal

    # NYSE is used as a shared U.S. equity-market calendar for these ETFs.
    # It includes holidays and scheduled early closes.
    calendar = mcal.get_calendar("NYSE")
    etdate = now_utc.astimezone(NY).date()
    schedule = calendar.schedule(start_date=etdate, end_date=etdate)
    if schedule.empty:
        return None, False
    open_utc = schedule["market_open"].iloc[0].to_pydatetime()
    close_utc = schedule["market_close"].iloc[0].to_pydatetime()
    return (open_utc, close_utc), open_utc <= now_utc < close_utc


def export_github_output(is_published: bool):
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with open(destination, "a", encoding="utf-8") as fh:
            fh.write(f"publish={'true' if is_published else 'false'}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Manual smoke test: publishes a TEST snapshot even when the market is closed")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    schedule, is_open = exchange_schedule(now)
    if not args.test and not is_open:
        print("No publication: US equity market is closed (holiday/weekend/early close/off-hours).")
        export_github_output(False)
        return

    market = {
        "calendar": "NYSE (U.S. equities proxy)",
        "is_open_at_collection": is_open,
        "ny_date": now.astimezone(NY).date().isoformat(),
        "ny_open": schedule[0].astimezone(NY).isoformat() if schedule else None,
        "ny_close": schedule[1].astimezone(NY).isoformat() if schedule else None,
    }
    try:
        symbols = {symbol: read_ticker(symbol, now, test_mode=args.test) for symbol in WATCHLIST}
    except Exception as exc:
        export_github_output(False)
        raise SystemExit(f"Data invalid: refusing to overwrite published snapshot: {exc}") from exc
    payload = {
        "schema_version": 1,
        "source": "Yahoo Finance via yfinance (unofficial; may be delayed)",
        "mode": "manual_test_not_for_trading" if args.test else "regular_session",
        "verified_regular_session": is_open and not args.test,
        "generated_utc": now.isoformat(),
        "generated_new_york": now.astimezone(NY).isoformat(),
        "generated_kst": now.astimezone(KST).isoformat(),
        "weekly_history_adjustment": "auto_adjust=False (unadjusted close; provider may incorporate splits)",
        "week_policy": "last_completed_week excludes current NY ISO week; provisional recomputed using latest regular-session 5m price",
        "quote_note": "5-minute candle timestamp is its START, not an exchange-confirmed real-time tick. Prices may be delayed.",
        "market": market,
        "symbols": symbols,
        "disclaimer": "Research snapshot only; not real-time execution data, individualized advice or an automatic trading signal.",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Published candidate: {OUTPUT}, mode={payload['mode']}, time={payload['generated_new_york']}")
    export_github_output(True)


if __name__ == "__main__":
    main()
