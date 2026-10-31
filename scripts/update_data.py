"""US indices and Japanese market-index daily history + TSE market volume.
Third-party data ingestion requires permission and explicit opt-in.
"""
import bisect
import datetime as dt
import json
import math
import os
import pathlib
import re
import time
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
MARKETS = {'prime': 'TsePrimeMarketIndex', 'standard': 'TseStandardMarketIndex', 'growth': 'TseGrowthMarketIndex'}
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; PersonalMarketDashboard/1.0)', 'Accept': 'application/json,text/html,*/*'}
FETCH_US_SESSIONS = 250
PUBLISH_SESSIONS = 200

ALLOW = os.getenv('ALLOW_TSE_THIRD_PARTY_DATA', '').lower() in ('true', '1', 'yes')


def fetch(url, timeout=25):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(5_000_000).decode(response.headers.get_content_charset() or 'utf-8', 'replace')


def numeric(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def us_prices(symbol):
    # Fetch ~400 calendar days (usually >250 trading sessions), without 5-year history.
    end = int(time.time()) + 86400
    start = end - 400 * 86400
    params = urllib.parse.urlencode({'period1': start, 'period2': end, 'interval': '1d'})
    now = dt.datetime.now(ZoneInfo('America/New_York'))
    last_error = None
    for server in ('query1.finance.yahoo.com', 'query2.finance.yahoo.com'):
        for attempt in range(2):
            try:
                url = f'https://{server}/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}'
                response = json.loads(fetch(url))['chart']['result'][0]
                q = response['indicators']['quote'][0]
                prices = {}
                for timestamp, close, low, volume in zip(response['timestamp'], q['close'], q['low'], q['volume']):
                    close = numeric(close)
                    if close is None or close <= 0:
                        continue
                    date = dt.datetime.fromtimestamp(timestamp, ZoneInfo('America/New_York')).date()
                    if date == now.date() and now.time() < dt.time(16, 15):
                        continue
                    prices[date.isoformat()] = {'close': close, 'low': numeric(low), 'volume': numeric(volume)}
                if len(prices) < 30:
                    raise ValueError('Too few sessions')
                dates = sorted(prices)
                prices = {day: prices[day] for day in dates[-FETCH_US_SESSIONS:]}
                print(f'YAHOO {symbol}: {len(prices)} calculation sessions (published {PUBLISH_SESSIONS})')
                return prices
            except Exception as e:
                last_error = e
                time.sleep(attempt + 1)
    raise RuntimeError(f'Cannot fetch US series {symbol}: {last_error}')


def enrich_us(prices):
    dates = sorted(prices)
    series = {}
    state, attempt_days, base_low = 'normal', 0, None
    for i, date in enumerate(dates):
        item = prices[date]
        prev = prices[dates[i - 1]] if i else None
        change = (item['close'] / prev['close'] - 1) * 100 if prev else None
        v, pv = item['volume'], prev['volume'] if prev else None
        vchange = (v / pv - 1) * 100 if v is not None and pv is not None and pv > 0 else None
        judge = classify(change, vchange)
        # Conservative simplified FTD: never substitute close for missing intraday low.
        low = item['low']
        prior = [prices[d]['close'] for d in dates[max(0, i-60):i]]
        if low is not None and prior:
            if state in ('normal', 'confirmed') and item['close'] <= max(prior) * 0.95:
                state, base_low, attempt_days = 'correction', low, 0
            elif state == 'correction':
                if change is not None and change > 0:
                    # Day 1 anchor is the LOW of the preceding correction session.
                    base_low = prices[dates[i - 1]]['low'] if i else None
                    state, attempt_days = ('attempt', 1) if base_low is not None else ('correction', 0)
            elif state == 'attempt':
                if low < base_low:
                    state, base_low, attempt_days = 'correction', low, 0
                else:
                    attempt_days += 1
                    if attempt_days >= 4 and change is not None and change >= 1.25 and vchange is not None and vchange > 0:
                        judge, state = 'ftd', 'confirmed'
        series[date] = {'change': round(change, 6) if change is not None else None,
                        'volume': round(vchange, 6) if vchange is not None else None,
                        'judge': judge}
    return series


def classify(change, volume):
    if change is None or volume is None or volume <= 0:
        return None
    if change > 0:
        return 'buy'
    if change <= -0.20:
        return 'sell'
    return None


class SimpleTables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.table, self.row, self.cell = [], None, None, None
    def handle_starttag(self, tag, attrs):
        if tag == 'table' and self.table is None:
            self.table = []
        elif tag == 'tr' and self.table is not None:
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []
    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def fetch_tse_volume():
    html = fetch('https://stock-marketdata.com/trading-volume.html')
    parser = SimpleTables(); parser.feed(html)
    out = {}
    for table in parser.tables:
        for cells in table:
            if len(cells) < 10:
                continue
            match = re.fullmatch(r'\s*(20\d{2})年(\d{1,2})月(\d{1,2})日\s*', cells[0])
            if not match:
                continue
            date = dt.date(*map(int, match.groups())).isoformat()
            try:
                amounts = [int(cells[i].replace(',', '').strip()) for i in (1, 4, 7)]
                pcts = [float(cells[i].strip().replace('%', '')) for i in (3, 6, 9)]
            except (ValueError, IndexError):
                continue
            if min(amounts) <= 0:
                continue
            out[date] = {key: {'shares_10000': amount, 'volume': pct} for key, amount, pct
                         in zip(MARKETS, amounts, pcts)}
    if len(out) < 10:
        raise ValueError(f'TSE volume rows insufficient: {len(out)}')
    # Cross-check reported percentage to preceding row for latest 3 days.
    dates = sorted(out)
    for day, prev in zip(dates[-3:], dates[-4:-1]):
        for key in MARKETS:
            a, b = out[prev][key]['shares_10000'], out[day][key]['shares_10000']
            diff = abs((b/a-1)*100 - out[day][key]['volume'])
            if diff > 0.11:
                raise ValueError(f'TSE volume mismatch {day} {key}: {diff:.3f}pp')
    print(f'TSE MARKET VOLUME: {len(out)} sessions, through {dates[-1]}')
    return out


def fetch_tse_indices():
    """Read daily close and reported daily % change for the three exact market indices.

    The table also contains Prime Composite, Standard TOP20, and Growth Core.
    Their columns must NOT be confused with the three requested market indices.
    """
    html = fetch('https://stock-marketdata.com/index-prime-standard-growth')
    parser = SimpleTables()
    parser.feed(html)
    out = {}
    date_pattern = re.compile(r'\s*(20\d{2})年(\d{1,2})月(\d{1,2})日\s*')
    for table in parser.tables:
        for cells in table:
            if len(cells) < 19:
                continue
            match = date_pattern.fullmatch(cells[0].replace('\xa0', ' '))
            if not match:
                continue
            day = dt.date(*map(int, match.groups())).isoformat()
            record = {}
            # [date, prime (close,diff,pct), prime composite,
            #  standard (close,diff,pct), standard top20,
            #  growth (close,diff,pct), growth core]
            for key, close_idx, pct_idx in [('prime', 1, 3), ('standard', 7, 9), ('growth', 13, 15)]:
                close = numeric(cells[close_idx].replace(',', '').strip())
                change = numeric(cells[pct_idx].replace('%', '').replace('−', '-').replace('＋', '+').strip())
                if close is None or close <= 0 or change is None or abs(change) > 30:
                    raise ValueError(f'Invalid {key} market-index row on {day}: {cells[:19]}')
                record[key] = {'date': day, 'close': close, 'reported_change_pct': change}
            if day in out and out[day] != record:
                raise ValueError(f'Duplicate conflicting index date {day}')
            out[day] = record
    if len(out) < 20:
        raise ValueError(f'Insufficient TSE index history from the table: {len(out)} rows')
    days = sorted(out)
    print(f'TSE INDEX HISTORY: {len(out)} sessions, {days[0]} through {days[-1]}')
    print('TSE LATEST INDEX:', {key: (out[days[-1]][key]['close'], out[days[-1]][key]['reported_change_pct']) for key in MARKETS})
    return out


def existing_published():
    # The Actions runner is ephemeral. Preserve history from prior published data.
    candidates = []
    file = ROOT / 'data.json'
    if file.exists():
        try:
            candidates.append(json.loads(file.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
    if ALLOW:
        try:
            candidates.append(json.loads(fetch('https://grw1107s.github.io/stock-dashboard/data.json?t=' + str(int(time.time())))))
        except Exception as exc:
            print('WARN: existing Pages data unavailable:', exc)
    return candidates


def prepare_tse(previous, volume, indices):
    # Keep accumulated snapshots only, NEVER backfill index prices with flat closes.
    history = {}
    for doc in previous:
        for rec in doc.get('tse_rows', []):
            if isinstance(rec, dict) and re.fullmatch(r'20\d\d-\d\d-\d\d', str(rec.get('date', ''))):
                day = rec['date']
                if day not in history:
                    history[day] = {key: dict(rec.get(key) or {}) for key in MARKETS}
        for key, records in (doc.get('tse_index_snapshots') or {}).items():
            if key not in MARKETS or not isinstance(records, dict):
                continue
            for day, record in records.items():
                close = numeric(record.get('close')) if isinstance(record, dict) else None
                if close and close > 0:
                    history.setdefault(day, {}).setdefault(key, {})['close'] = close
                    history[day][key]['low'] = numeric(record.get('low'))
    for date, data in volume.items():
        history.setdefault(date, {})
        for key, item in data.items():
            history[date].setdefault(key, {}).update(item)
    for date, records in indices.items():
        history.setdefault(date, {})
        for key, record in records.items():
            history[date].setdefault(key, {}).update(record)
    # Calculate change from actual consecutive index CLOSES, or an explicitly reported one-day change.
    for key in MARKETS:
        price_days = sorted(day for day in history if numeric(history[day].get(key, {}).get('close')) is not None)
        for idx, day in enumerate(price_days):
            rec = history[day][key]
            reported = numeric(rec.get('reported_change_pct'))
            # Prefer the explicit day-over-day percentage supplied by the same
            # historical table. Do not require prior day data for its first row.
            if reported is not None:
                rec['change'] = reported
            elif idx and dt.date.fromisoformat(day) - dt.date.fromisoformat(price_days[idx - 1]) <= dt.timedelta(days=5):
                prev = history[price_days[idx - 1]][key]['close']
                rec['change'] = round((rec['close'] / prev - 1) * 100, 6)
            else:
                rec['change'] = numeric(rec.get('change'))
    final = []
    for day in sorted(history, reverse=True)[:PUBLISH_SESSIONS]:
        out = {'date': day}
        for key in MARKETS:
            rec = history[day].get(key, {})
            price_change = numeric(rec.get('change'))
            volume_change = numeric(rec.get('volume'))
            # Never label FTD without a defensible, tested local rebound history.
            out[key] = {'change': price_change, 'volume': volume_change,
                        'judge': classify(price_change, volume_change),
                        'shares_10000': rec.get('shares_10000')}
        final.append(out)
    kept_days = set(sorted(history, reverse=True)[:PUBLISH_SESSIONS])
    snapshots = {key: {day: {'close': history[day][key]['close'], 'low': history[day][key].get('low')}
                       for day in kept_days if key in history[day] and numeric(history[day][key].get('close')) is not None}
                 for key in MARKETS}
    return final, snapshots


def main():
    previous = existing_published()
    us = {key: enrich_us(us_prices(symbol)) for key, symbol in [('sp500', '^GSPC'), ('nasdaq', '^IXIC')]}
    volume, indices = {}, {}
    if ALLOW:
        try:
            volume = fetch_tse_volume()
        except Exception as exc:
            print('WARNING TSE volume ingestion failed:', repr(exc))
        try:
            indices = fetch_tse_indices()
        except Exception as exc:
            print('WARNING TSE index ingestion failed:', repr(exc))
    else:
        print('TSE third-party ingestion disabled: set ALLOW_TSE_THIRD_PARTY_DATA=true ONLY after permission is confirmed')
    tse_rows, snapshots = prepare_tse(previous, volume, indices)
    japan_days = sorted(r['date'] for r in tse_rows if r['date'])
    us_days = sorted(set(us['sp500']) & set(us['nasdaq']), reverse=True)
    rows = []
    for day in us_days[:PUBLISH_SESSIONS]:
        loc = bisect.bisect_right(japan_days, day)
        # Japan next session relative to a US trading date, no same-day alignment.
        japan = japan_days[loc] if loc < len(japan_days) else None
        rows.append({'date': day, 'japan_date': japan,
                     'sp500': us['sp500'][day], 'nasdaq': us['nasdaq'][day]})
    if len(rows) < 25:
        raise ValueError('US market history insufficient')
    result = {'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
              'source': {'us': 'Yahoo Finance unofficial', 'japan_indices': 'stock-marketdata.com index history (opt-in)',
                         'japan_volume': 'stock-marketdata.com (opt-in)'},
              'tse_enabled': ALLOW, 'rows': rows, 'tse_rows': tse_rows,
              'tse_index_snapshots': snapshots}
    (ROOT / 'data.json').write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print('WROTE data.json: US rows', len(rows), 'JP rows', len(tse_rows), 'JP index snapshots', {k: len(v) for k,v in snapshots.items()})


if __name__ == '__main__':
    main()
