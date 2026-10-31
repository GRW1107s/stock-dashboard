#!/usr/bin/env python3
"""Fetch QQQ/SPY daily (unadjusted) closing prices and create GitHub Pages data.json.

Uses Yahoo Finance's public chart endpoint (not an official supported data API).
No user secrets or Python third-party dependencies are required.
"""
import datetime as dt
import json
import math
import pathlib
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (compatible; StockDashboard/1.0; daily-price-table)"


def fetch_chart(symbol):
    params = urllib.parse.urlencode({"range": "5y", "interval": "1d"})
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{symbol}?{params}"
        for attempt in range(2):
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": UA, "Accept": "application/json"}
                )
                with urllib.request.urlopen(request, timeout=25) as response:
                    payload = json.load(response)
                result = (payload.get("chart") or {}).get("result")
                if not result:
                    raise ValueError(f"empty chart result: {str(payload)[:200]}")
                info = result[0]
                timestamps = info.get("timestamp", [])
                quotes = (info.get("indicators") or {}).get("quote", [])
                closes = quotes[0].get("close", []) if quotes else []
                if len(timestamps) != len(closes):
                    raise ValueError("timestamps and closes have different lengths")
                prices = {}
                ny_tz = ZoneInfo("America/New_York")
                now_ny = dt.datetime.now(dt.timezone.utc).astimezone(ny_tz)
                for stamp, close in zip(timestamps, closes):
                    if close is None or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
                        continue
                    market_day = dt.datetime.fromtimestamp(stamp, tz=ny_tz).date()
                    # Avoid displaying a still-forming daily candle when run during US market hours.
                    if market_day == now_ny.date() and now_ny.time() < dt.time(16, 15):
                        continue
                    prices[market_day.isoformat()] = float(close)
                if len(prices) < 30:
                    raise ValueError(f"too few usable prices for {symbol}: {len(prices)}")
                print(f"{symbol}: {len(prices)} trading days from {host}")
                return prices
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as err:
                errors.append(f"{host} attempt {attempt+1}: {err}")
                time.sleep(2 + attempt * 3)
    raise RuntimeError(f"Unable to download {symbol} prices: {' | '.join(errors)}")


def pct_changes(prices):
    """Compute close-to-previous-available-trading-day price returns."""
    days = sorted(prices)
    result = {}
    for previous, current in zip(days, days[1:]):
        result[current] = (prices[current] / prices[previous] - 1) * 100
    return result


def build_rows(qqq_prices, spy_prices):
    qqq = pct_changes(qqq_prices)
    spy = pct_changes(spy_prices)
    rows = []
    for date in sorted(qqq.keys() & spy.keys(), reverse=True):
        rows.append({"date": date, "qqq": round(qqq[date], 6), "spy": round(spy[date], 6)})
    if len(rows) < 30:
        raise ValueError(f"too few matching days: {len(rows)}")
    newest = dt.date.fromisoformat(rows[0]["date"])
    if (dt.datetime.now(dt.timezone.utc).date() - newest).days > 10:
        raise ValueError(f"data appears stale: newest date {newest}")
    return rows


def main():
    qqq = fetch_chart("QQQ")
    spy = fetch_chart("SPY")
    rows = build_rows(qqq, spy)
    result = {
        "source": "Yahoo Finance chart endpoint (unadjusted closing price)",
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "rows": rows,
    }
    output = ROOT / "data.json"
    output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {output}: {len(rows)} days, last date {rows[0]['date']}")


if __name__ == "__main__":
    main()
