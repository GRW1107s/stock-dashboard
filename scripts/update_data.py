
import datetime as dt
import json
import math
import pathlib
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]

SYMBOLS = {
    "sp500": "^GSPC",
    "nasdaq": "^IXIC"
}

def fetch_prices(symbol):
    params = urllib.parse.urlencode({
        "range": "5y",
        "interval": "1d"
    })

    ny = ZoneInfo("America/New_York")
    now = dt.datetime.now(ny)
    errors = []

    for host in ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]:
        url = f"https://{host}/v8/finance/chart/{symbol}?{params}"

        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "application/json"
                    }
                )

                with urllib.request.urlopen(req, timeout=25) as response:
                    data = json.load(response)

                chart = data["chart"]["result"][0]
                timestamps = chart["timestamp"]
                closes = chart["indicators"]["quote"][0]["close"]

                prices = {}

                for ts, close in zip(timestamps, closes):
                    if close is None or not math.isfinite(close) or close <= 0:
                        continue

                    day = dt.datetime.fromtimestamp(ts, ny).date()

                    # 当日の取引が終了する前のデータは除外
                    if day == now.date() and now.time() < dt.time(16, 15):
                        continue

                    prices[day.isoformat()] = float(close)

                if len(prices) < 30:
                    raise ValueError("価格データが不足しています")

                print(f"{symbol}: {len(prices)} days fetched")
                return prices

            except Exception as e:
                errors.append(str(e))
                time.sleep(2 + attempt * 3)

    raise RuntimeError(f"{symbol} の取得に失敗: {errors}")


def calculate_returns(prices):
    days = sorted(prices.keys())
    returns = {}

    for i in range(1, len(days)):
        previous = prices[days[i - 1]]
        current = prices[days[i]]

        returns[days[i]] = (current / previous - 1) * 100

    return returns


def main():
    series = {}

    for name, symbol in SYMBOLS.items():
        prices = fetch_prices(symbol)
        series[name] = calculate_returns(prices)

    common_dates = sorted(
        set(series["sp500"]) & set(series["nasdaq"]),
        reverse=True
    )

    rows = []

    for day in common_dates:
        rows.append({
            "date": day,
            "sp500": round(series["sp500"][day], 6),
            "nasdaq": round(series["nasdaq"][day], 6)
        })

    if len(rows) < 30:
        raise ValueError("共通する営業日データが不足しています")

    latest = dt.date.fromisoformat(rows[0]["date"])
    if (dt.datetime.now(dt.timezone.utc).date() - latest).days > 10:
        raise ValueError("取得データが古すぎます")

    output = {
        "updated_at": dt.datetime.now(
            dt.timezone.utc
        ).isoformat(timespec="seconds"),
        "source": "Yahoo Finance",
        "rows": rows
    }

    path = ROOT / "data.json"

    path.write_text(
        json.dumps(output, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"Saved {len(rows)} rows to {path}")


if __name__ == "__main__":
    main()
