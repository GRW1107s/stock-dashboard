import bisect
import datetime as dt
import json
import math
import pathlib
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = {
    'sp500': ('^GSPC', 'America/New_York'),
    'nasdaq': ('^IXIC', 'America/New_York'),
    'nikkei': ('^N225', 'Asia/Tokyo'),
    'topix': ('^TOPX', 'Asia/Tokyo'),
}


def fetch(symbol, zone):
    params = urllib.parse.urlencode({'range': '5y', 'interval': '1d'})
    errors = []
    now = dt.datetime.now(ZoneInfo(zone))
    for host in ('query1.finance.yahoo.com', 'query2.finance.yahoo.com'):
        url = f'https://{host}/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}'
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    payload = json.load(resp)
                chart = payload['chart']['result'][0]
                quotes = chart['indicators']['quote'][0]
                records = {}
                for timestamp, close, low, volume in zip(chart['timestamp'], quotes['close'], quotes['low'], quotes['volume']):
                    if close is None or not math.isfinite(close) or close <= 0:
                        continue
                    date = dt.datetime.fromtimestamp(timestamp, ZoneInfo(zone)).date()
                    # Avoid unfinished current session.
                    market_close = dt.time(16, 15) if zone == 'America/New_York' else dt.time(15, 45)
                    if date == now.date() and now.time() < market_close:
                        continue
                    records[date.isoformat()] = {
                        'close': float(close),
                        'low': float(low) if low is not None and math.isfinite(low) and low > 0 else float(close),
                        'volume': float(volume) if volume is not None and math.isfinite(volume) and volume > 0 else None,
                    }
                if len(records) < 30:
                    raise ValueError(f'Only {len(records)} prices')
                print(f'{symbol}: {len(records)} sessions, valid volumes: {sum(v["volume"] is not None for v in records.values())}')
                return records
            except Exception as exc:
                errors.append(repr(exc))
                time.sleep(2 + attempt * 3)
    raise RuntimeError(f'Failed to fetch {symbol}: {errors}')


def enrich(raw):
    dates = sorted(raw)
    result = {}
    state = 'normal'
    attempt_day = 0
    attempt_low = None
    recent_low = None
    for i, date in enumerate(dates):
        item = raw[date]
        previous = raw[dates[i-1]] if i else None
        pct = (item['close'] / previous['close'] - 1) * 100 if previous else None
        v = item['volume']
        pv = previous['volume'] if previous else None
        vol_pct = (v / pv - 1) * 100 if v is not None and pv is not None and pv > 0 else None
        judge = 'buy' if pct is not None and pct > 0 and vol_pct is not None and vol_pct > 0 else ('sell' if pct is not None and pct <= -0.2 and vol_pct is not None and vol_pct > 0 else None)
        prior_high = max((raw[d]['close'] for d in dates[max(0, i-60):i]), default=item['close'])
        correction = item['close'] <= prior_high * 0.95
        ftd = False
        if state in ('normal', 'confirmed'):
            if correction:
                state = 'correction'
                recent_low = item['low']
        elif state == 'correction':
            recent_low = min(recent_low, item['low'])
            if pct is not None and pct > 0:
                state = 'attempt'
                attempt_day = 1
                attempt_low = recent_low
        elif state == 'attempt':
            if item['low'] < attempt_low:
                state = 'correction'
                recent_low = item['low']
                attempt_day = 0
            else:
                attempt_day += 1
                if attempt_day >= 4 and pct is not None and pct >= 1.25 and vol_pct is not None and vol_pct > 0:
                    ftd = True
                    state = 'confirmed'
        result[date] = {
            'change': round(pct, 6) if pct is not None else None,
            'volume': round(vol_pct, 6) if vol_pct is not None else None,
            'judge': 'ftd' if ftd else judge,
        }
    return result


def main():
    data = {name: enrich(fetch(*cfg)) for name, cfg in SOURCES.items()}
    usa = sorted(set(data['sp500']) & set(data['nasdaq']), reverse=True)
    japan = sorted(set(data['nikkei']) & set(data['topix']))
    rows = []
    for us_day in usa:
        j = bisect.bisect_right(japan, us_day)
        jp_day = japan[j] if j < len(japan) else None
        row = {'date': us_day, 'japan_date': jp_day}
        for key in SOURCES:
            d = data[key].get(jp_day if key in ('nikkei', 'topix') else us_day) if (jp_day or key in ('sp500', 'nasdaq')) else None
            row[key] = d
        rows.append(row)
    if len(rows) < 25:
        raise ValueError('Too few common US dates')
    output = {'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
              'source': 'Yahoo Finance chart (unofficial)', 'rows': rows}
    (ROOT / 'data.json').write_text(json.dumps(output, ensure_ascii=False), encoding='utf-8')
    print(f'Saved {len(rows)} rows')


if __name__ == '__main__':
    main()
