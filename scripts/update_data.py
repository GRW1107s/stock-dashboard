
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


def fetch_data(symbol):
    params = urllib.parse.urlencode({
        "range": "5y",
        "interval": "1d"
    })

    ny = ZoneInfo("America/New_York")
    now = dt.datetime.now(ny)
    errors = []

    for host in [
        "query1.finance.yahoo.com",
        "query2.finance.yahoo.com"
    ]:
        url = (
            f"https://{host}/v8/finance/chart/"
            f"{urllib.parse.quote(symbol)}?{params}"
        )

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
                quote = chart["indicators"]["quote"][0]

                closes = quote["close"]
                volumes = quote["volume"]

                result = {}

                for ts, close, volume in zip(
                    timestamps, closes, volumes
                ):
                    if (
                        close is None
                        or not math.isfinite(close)
                        or close <= 0
                    ):
                        continue

                    day = dt.datetime.fromtimestamp(ts, ny).date()

                    # NY時間で当日の取引終了前なら除外
                    if (
                        day == now.date()
                        and now.time() < dt.time(16, 15)
                    ):
                        continue

                    valid_volume = None
                    if (
                        volume is not None
                        and math.isfinite(volume)
                        and volume > 0
                    ):
                        valid_volume = float(volume)

                    result[day.isoformat()] = {
                        "close": float(close),
                        "volume": valid_volume
                    }

                if len(result) < 30:
                    raise ValueError("データ不足")

                print(f"{symbol}: {len(result)} days fetched")
                return result

            except Exception as e:
                errors.append(str(e))
                time.sleep(2 + attempt * 3)

    raise RuntimeError(
        f"{symbol} の取得に失敗しました: {errors}"
    )


def calculate_returns(data):
    days = sorted(data.keys())
    result = {}

    for i in range(1, len(days)):
        day = days[i]
        prev_day = days[i - 1]

        current = data[day]
        previous = data[prev_day]

        price_change = (
            current["close"] / previous["close"] - 1
        ) * 100

        current_volume = current["volume"]
        previous_volume = previous["volume"]

        volume_change = None

        if (
            current_volume is not None
            and previous_volume is not None
            and previous_volume > 0
        ):
            volume_change = (
                current_volume / previous_volume - 1
            ) * 100

        result[day] = {
            "price": round(price_change, 6),
            "volume": (
                round(volume_change, 6)
                if volume_change is not None
                else None
            )
        }

    return result


def main():
    series = {}

    for name, symbol in SYMBOLS.items():
        data = fetch_data(symbol)
        series[name] = calculate_returns(data)

    common_dates = sorted(
        set(series["sp500"]) & set(series["nasdaq"]),
        reverse=True
    )

    rows = []

    for day in common_dates:
        sp = series["sp500"][day]
        nas = series["nasdaq"][day]

        rows.append({
            "date": day,
            "sp500": sp["price"],
            "sp500_volume": sp["volume"],
            "nasdaq": nas["price"],
            "nasdaq_volume": nas["volume"]
        })

    if len(rows) < 30:
        raise ValueError("共通する日付が不足しています")

    latest = dt.date.fromisoformat(rows[0]["date"])
    today = dt.datetime.now(dt.timezone.utc).date()

    if (today - latest).days > 10:
        raise ValueError("取得したデータが古すぎます")

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
