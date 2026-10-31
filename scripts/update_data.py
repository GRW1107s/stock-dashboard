import bisect
import csv
import datetime as dt
import io
import json
import math
import os
import pathlib
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
YAHOO = {'sp500': ('^GSPC', 'America/New_York'), 'nasdaq': ('^IXIC', 'America/New_York'), 'nikkei': ('^N225', 'Asia/Tokyo')}
STOOQ = {'nikkei': '^NKX', 'topix': '^TPX'}


def valid_number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) and x > 0 else None
    except (TypeError, ValueError):
        return None


def yahoo_fetch(symbol, zone):
    params = urllib.parse.urlencode({'range': '5y', 'interval': '1d'})
    errors = []
    now = dt.datetime.now(ZoneInfo(zone))
    for host in ('query1.finance.yahoo.com', 'query2.finance.yahoo.com'):
        url = f'https://{host}/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}'
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=25) as response:
                    chart = json.load(response)['chart']['result'][0]
                quote = chart['indicators']['quote'][0]
                result = {}
                for ts, close, low, volume in zip(chart['timestamp'], quote['close'], quote['low'], quote['volume']):
                    close = valid_number(close)
                    if close is None:
                        continue
                    date = dt.datetime.fromtimestamp(ts, ZoneInfo(zone)).date()
                    close_time = dt.time(16, 15) if zone == 'America/New_York' else dt.time(15, 45)
                    if date == now.date() and now.time() < close_time:
                        continue
                    result[date.isoformat()] = {'close': close, 'low': valid_number(low), 'volume': valid_number(volume)}
                if len(result) < 30:
                    raise ValueError(f'Only {len(result)} records')
                print(f'Yahoo {symbol}: {len(result)} sessions')
                return result
            except Exception as exc:
                errors.append(str(exc))
                time.sleep(attempt + 1)
    raise RuntimeError(f'Yahoo {symbol}: {errors}')


def stooq_fetch(symbol):
    today = dt.datetime.now(ZoneInfo('Asia/Tokyo'))
    params = {'s': symbol, 'i': 'd', 'd1': (today.date() - dt.timedelta(days=1850)).strftime('%Y%m%d'), 'd2': today.strftime('%Y%m%d')}
    api_key = os.getenv('STOOQ_API_KEY', '').strip()
    if api_key:
        params['apikey'] = api_key
    url = 'https://stooq.com/q/d/l/?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; market-dashboard/1.0)', 'Accept': 'text/csv'})
    with urllib.request.urlopen(req, timeout=25) as response:
        body = response.read(4_000_000).decode('utf-8-sig', errors='replace')
    if not body.lstrip().lower().startswith('date,'):
        raise ValueError('Stooq returned a non-CSV response (possibly API key / access restriction): ' + body[:120].replace('\n', ' '))
    records = {}
    for row in csv.DictReader(io.StringIO(body)):
        close = valid_number(row.get('Close'))
        if close is None:
            continue
        date = row['Date']
        dt.date.fromisoformat(date)
        if date == today.date().isoformat() and today.time() < dt.time(15, 45):
            continue
        records[date] = {'close': close, 'low': valid_number(row.get('Low')), 'volume': valid_number(row.get('Volume'))}
    if len(records) < 30:
        raise ValueError(f'Stooq {symbol}: insufficient data ({len(records)})')
    print(f'Stooq {symbol}: {len(records)} sessions, {sum(v["volume"] is not None for v in records.values())} with volume')
    return records


def enrich(raw):
    """IBD-inspired rally attempt / FTD, with Day-1 intraday low as fixed stop.

    No FTD is generated when intraday lows or volume are unavailable.
    A rally attempt ends immediately on any subsequent day's intraday
    undercut of its Day-1 low, even if that day closes higher.
    """
    dates = sorted(raw)
    result = {}
    state = 'normal'
    attempt_day = 0
    attempt_low = None
    recent_low = None

    for i, date in enumerate(dates):
        item = raw[date]
        prev = raw[dates[i - 1]] if i else None
        close, low = item['close'], item['low']
        pct = (close / prev['close'] - 1) * 100 if prev else None
        volume = item['volume']
        prev_volume = prev['volume'] if prev else None
        vol_pct = (volume / prev_volume - 1) * 100 if volume is not None and prev_volume is not None and prev_volume > 0 else None
        judge = ('buy' if pct is not None and pct > 0 and vol_pct is not None and vol_pct > 0
                 else 'sell' if pct is not None and pct <= -0.20 and vol_pct is not None and vol_pct > 0
                 else None)
        prior_closes = [raw[d]['close'] for d in dates[max(0, i - 60):i]]
        prior_high = max(prior_closes, default=close)
        in_correction = close <= prior_high * 0.95
        ftd = False

        if state in ('normal', 'confirmed'):
            if in_correction:
                state = 'correction'
                recent_low = low
                attempt_day = 0
                attempt_low = None
        elif state == 'correction':
            if low is not None:
                recent_low = min(recent_low, low) if recent_low is not None else low
            # A closing up-day begins the attempt. Only a genuine day's low
            # can provide the reference: never substitute the close for Low.
            if pct is not None and pct > 0 and low is not None:
                state = 'attempt'
                attempt_day = 1
                attempt_low = low  # FTD rally Day 1's low, fixed for this attempt
        elif state == 'attempt':
            if low is None:
                # Do not produce a confirmation with an unverifiable low.
                state = 'correction'
                recent_low = None
                attempt_low = None
                attempt_day = 0
            elif low < attempt_low:
                state = 'correction'
                recent_low = low
                attempt_low = None
                attempt_day = 0
                # This day is not reused as a new Day 1: look for a later up day.
            else:
                attempt_day += 1
                if (attempt_day >= 4 and pct is not None and pct >= 1.25
                        and vol_pct is not None and vol_pct > 0):
                    ftd = True
                    state = 'confirmed'
                    attempt_low = None
                    attempt_day = 0

        result[date] = {
            'change': round(pct, 6) if pct is not None else None,
            'volume': round(vol_pct, 6) if vol_pct is not None else None,
            'judge': 'ftd' if ftd else judge,
        }
    return result


def prior_japan_data():
    path = ROOT / 'data.json'
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        result = {}
        for row in data.get('rows', []):
            day = row.get('japan_date')
            if not day:
                continue
            result[day] = {key: row.get(key) for key in ('nikkei', 'topix')}
        return result
    except (OSError, ValueError, TypeError):
        return {}


def main():
    data = {}
    for key in ('sp500', 'nasdaq'):
        data[key] = enrich(yahoo_fetch(*YAHOO[key]))
    for key in ('nikkei', 'topix'):
        try:
            data[key] = enrich(stooq_fetch(STOOQ[key]))
        except Exception as exc:
            print(f'WARNING: Stooq {key} unavailable: {exc}')
            if key == 'nikkei':
                try:
                    data[key] = enrich(yahoo_fetch(*YAHOO[key]))
                except Exception as fallback_error:
                    print(f'WARNING: Yahoo Nikkei unavailable: {fallback_error}')
                    data[key] = {}
            else:
                data[key] = {}
    previous = prior_japan_data()
    usa = sorted(set(data['sp500']) & set(data['nasdaq']), reverse=True)
    japanese_dates = sorted(set(data['nikkei']) | set(data['topix']) | set(previous))
    rows = []
    for us_day in usa:
        index = bisect.bisect_right(japanese_dates, us_day)
        jp_day = japanese_dates[index] if index < len(japanese_dates) else None
        row = {'date': us_day, 'japan_date': jp_day}
        for key in ('sp500', 'nasdaq'):
            row[key] = data[key].get(us_day)
        for key in ('nikkei', 'topix'):
            current = data[key].get(jp_day) if jp_day else None
            fallback = previous.get(jp_day, {}).get(key) if jp_day else None
            row[key] = current if current is not None else fallback
        rows.append(row)
    if len(rows) < 25:
        raise ValueError('Too few US index records')
    result = {'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'source': 'Yahoo Finance (US), Stooq (JP, when available); Yahoo fallback Nikkei', 'rows': rows}
    (ROOT / 'data.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    print(f'Saved {len(rows)} rows; TOPIX latest available: {next((r["japan_date"] for r in rows if r["topix"]), "none")}')


if __name__ == '__main__':
    main()
