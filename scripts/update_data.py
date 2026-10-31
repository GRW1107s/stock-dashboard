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
YAHOO = {'sp500': ('^GSPC', 'America/New_York'), 'nasdaq': ('^IXIC', 'America/New_York')}


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
                    result[date.isoformat()] = {'close': close, 'low': valid_number(low) or close, 'volume': valid_number(volume)}
                if len(result) < 30:
                    raise ValueError(f'Only {len(result)} records')
                print(f'Yahoo {symbol}: {len(result)} sessions')
                return result
            except Exception as exc:
                errors.append(str(exc))
                time.sleep(attempt + 1)
    raise RuntimeError(f'Yahoo {symbol}: {errors}')


def enrich(raw):
    dates = sorted(raw)
    result = {}
    state = 'normal'
    attempt_day = 0
    attempt_low = recent_low = None
    for i, date in enumerate(dates):
        item = raw[date]
        prev = raw[dates[i - 1]] if i else None
        pct = (item['close'] / prev['close'] - 1) * 100 if prev else None
        v, pv = item['volume'], prev['volume'] if prev else None
        vol_pct = (v / pv - 1) * 100 if v is not None and pv is not None and pv > 0 else None
        judge = 'buy' if pct is not None and pct > 0 and vol_pct is not None and vol_pct > 0 else ('sell' if pct is not None and pct <= -0.2 and vol_pct is not None and vol_pct > 0 else None)
        prior_high = max((raw[d]['close'] for d in dates[max(0, i - 60):i]), default=item['close'])
        correction = item['close'] <= prior_high * 0.95
        ftd = False
        if state in ('normal', 'confirmed'):
            if correction:
                state = 'correction'
                recent_low = item['low']
        elif state == 'correction':
            recent_low = min(recent_low, item['low'])
            if pct is not None and pct > 0:
                state, attempt_day, attempt_low = 'attempt', 1, recent_low
        elif state == 'attempt':
            if item['low'] < attempt_low:
                state, recent_low, attempt_day = 'correction', item['low'], 0
            else:
                attempt_day += 1
                if attempt_day >= 4 and pct is not None and pct >= 1.25 and vol_pct is not None and vol_pct > 0:
                    ftd, state = True, 'confirmed'
        result[date] = {'change': round(pct, 6) if pct is not None else None, 'volume': round(vol_pct, 6) if vol_pct is not None else None, 'judge': 'ftd' if ftd else judge}
    return result


def load_tse_market_volume():
    """Load explicitly verified JPX market-segment share volume, in units of 10,000 shares.
    A separate source-validation process is needed for automatic downloads.
    """
    path = ROOT / 'data' / 'tse_market_volume.csv'
    if not path.exists():
        print('WARNING: TSE volume CSV missing; panel will be empty')
        return []
    rows = []
    with path.open(encoding='utf-8-sig', newline='') as f:
        for raw in csv.DictReader(f):
            day = raw['date']
            dt.date.fromisoformat(day)
            item = {'date': day}
            for key in ('prime', 'standard', 'growth'):
                val = raw.get(key, '').strip()
                item[key] = int(val) if val else None
                pctval = raw.get(key + '_pct', '').strip()
                item[key + '_pct'] = float(pctval) if pctval else None
            rows.append(item)
    return sorted(rows, key=lambda x: x['date'], reverse=True)




def load_tse_market_indices(market_volume):
    """Optional manually licensed/verified close + low prices per segment.
    CSV headers: date,prime,standard,growth (optional: prime_low,standard_low,growth_low).
    No automatic market-index feed is implied.
    """
    path = ROOT / 'data' / 'tse_market_index.csv'
    if not path.exists():
        print('INFO: market index close CSV not found; P/S/G price and judgement unavailable')
        return {}
    raw = {key: {} for key in ('prime', 'standard', 'growth')}
    with path.open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            day = row['date'].strip()
            dt.date.fromisoformat(day)
            for key in raw:
                close = valid_number(row.get(key))
                if close is None:
                    continue
                raw[key][day] = {'close': close,
                                 'low': valid_number(row.get(key + '_low')) or close,
                                 'volume': None}
    volume_by_date = {row['date']: row for row in market_volume}
    result = {}
    for key in raw:
        dates = sorted(raw[key])
        # Calculate volume change from the matching market-wide exchange volume,
        # not index-constituent volume.
        data = {}
        for i, day in enumerate(dates):
            current = raw[key][day]['close']
            prior = raw[key][dates[i-1]]['close'] if i else None
            price_change = (current / prior - 1) * 100 if prior else None
            volume_change = volume_by_date.get(day, {}).get(key + '_pct')
            judge = None
            if price_change is not None and volume_change is not None and volume_change > 0:
                judge = 'buy' if price_change > 0 else ('sell' if price_change <= -0.2 else None)
            data[day] = {'change': round(price_change, 6) if price_change is not None else None,
                         'volume': volume_change, 'judge': judge}
        result[key] = data
    return result

def main():
    data = {}
    for key in ('sp500', 'nasdaq'):
        data[key] = enrich(yahoo_fetch(*YAHOO[key]))
    market_volume = load_tse_market_volume()
    tse_index_data = load_tse_market_indices(market_volume)
    # Japan sessions are sourced from the verified market-volume CSV and any
    # previously published TSE data; do not fabricate trading dates.
    old_path = ROOT / 'data.json'
    previous_tse = []
    if old_path.exists():
        try:
            previous_tse = json.loads(old_path.read_text(encoding='utf-8')).get('tse_rows', [])
        except (ValueError, OSError, TypeError):
            pass
    japanese_dates = sorted(set(item['date'] for item in market_volume) |
                            set(item['date'] for item in previous_tse if isinstance(item, dict) and item.get('date')))
    usa = sorted(set(data['sp500']) & set(data['nasdaq']), reverse=True)
    rows = []
    for us_day in usa:
        index = bisect.bisect_right(japanese_dates, us_day)
        jp_day = japanese_dates[index] if index < len(japanese_dates) else None
        rows.append({'date': us_day, 'japan_date': jp_day,
                     'sp500': data['sp500'].get(us_day),
                     'nasdaq': data['nasdaq'].get(us_day)})
    if len(rows) < 25:
        raise ValueError('Too few US index records')
    tse_rows = []
    for item in market_volume:
        date = item['date']
        out = {'date': date}
        for key in ('prime', 'standard', 'growth'):
            current = tse_index_data.get(key, {}).get(date)
            out[key] = current or {'change': None, 'volume': item.get(key + '_pct'), 'judge': None}
            out[key]['shares_10000'] = item.get(key)
        tse_rows.append(out)
    # Preserve any existing historical market rows when the local CSV is limited.
    old_path = ROOT / 'data.json'
    if old_path.exists():
        try:
            previous_rows = json.loads(old_path.read_text(encoding='utf-8')).get('tse_rows', [])
            known = {r['date'] for r in tse_rows}
            tse_rows.extend(r for r in previous_rows if isinstance(r, dict) and r.get('date') and r['date'] not in known)
        except (ValueError, OSError, TypeError, KeyError):
            pass
    tse_rows.sort(key=lambda r: r['date'], reverse=True)
    result = {'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'source': 'Yahoo Finance (US indices); verified TSE market-volume CSV', 'rows': rows, 'tse_market_volume': market_volume, 'tse_rows': tse_rows}
    (ROOT / 'data.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    print(f'Saved {len(rows)} rows; TSE market days: {len(tse_rows)}')


if __name__ == '__main__':
    main()
