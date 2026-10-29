"""Bounded retries for provider throttling; never retry invalid market data."""
import sys
import time


def history(ticker, symbol, **kwargs):
    delays = (10, 30)
    for attempt in range(3):
        try:
            # yfinance otherwise swallows some errors and returns an empty frame.
            return ticker.history(**kwargs, raise_errors=True)
        except Exception as exc:
            throttled = (type(exc).__name__ == 'YFRateLimitError' or
                         any(s in str(exc).lower() for s in ('too many requests', 'rate limit', '429')))
            context = f'{symbol} interval={kwargs.get("interval")} attempt={attempt + 1}/3'
            if not throttled or attempt == 2:
                raise RuntimeError(f'Yahoo history failed: {context}; {type(exc).__name__}: {exc}') from exc
            delay = delays[attempt]
            print(f'Yahoo throttled: {context}; retry in {delay}s', file=sys.stderr)
            time.sleep(delay)
