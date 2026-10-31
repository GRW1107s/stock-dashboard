"""Generate jp-sectors.json from 17 NEXT FUNDS TOPIX-17 ETFs and 1306.T.
Market prices and trade volumes are ETF proxies, NOT TOPIX-17 index levels or total sector volumes.
No missing daily close, return, or volume is manufactured.
"""
import datetime as dt
import json
import math
import pathlib
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
SECTORS = {
    '1617.T': '食品', '1618.T': 'エネルギー資源', '1619.T': '建設・資材',
    '1620.T': '素材・化学', '1621.T': '医薬品', '1622.T': '自動車・輸送機',
    '1623.T': '鉄鋼・非鉄', '1624.T': '機械', '1625.T': '電機・精密',
    '1626.T': '情報通信・サービスその他', '1627.T': '電力・ガス',
    '1628.T': '運輸・物流', '1629.T': '商社・卸売', '1630.T': '小売',
    '1631.T': '銀行', '1632.T': '金融（除く銀行）', '1633.T': '不動産'
}
BENCHMARK = '1306.T'  # NEXT FUNDS TOPIX ETF; an ETF-price proxy, not the TOPIX index.
CALC = 250
PUBLISH = 200

def num(x):
    try:
        y=float(x)
        return y if math.isfinite(y) else None
    except (TypeError,ValueError):
        return None

def download(symbol):
    end=int(time.time())+86400
    start=end-420*86400
    query=urllib.parse.urlencode({'period1':start,'period2':end,'interval':'1d','events':'history'})
    last=None
    for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
        for attempt in range(2):
            try:
                url=f'https://{host}/v8/finance/chart/{urllib.parse.quote(symbol)}?{query}'
                req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; PersonalJapanSectorDashboard/1.0)','Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=25) as res:
                    data=json.load(res)['chart']['result'][0]
                quote=data['indicators']['quote'][0]
                tz=ZoneInfo('Asia/Tokyo')
                now=dt.datetime.now(tz)
                parsed={}
                for stamp,close,low,vol in zip(data['timestamp'],quote['close'],quote['low'],quote['volume']):
                    close=num(close)
                    if close is None or close<=0: continue
                    day=dt.datetime.fromtimestamp(stamp,tz).date()
                    if day==now.date() and now.time()<dt.time(15,50): continue
                    parsed[day.isoformat()]={'close':close,'low':num(low),'volume':num(vol)}
                if len(parsed)<35: raise ValueError(f'too few sessions {len(parsed)}')
                dates=sorted(parsed)[-CALC:]
                print(f'{symbol}: {len(dates)} calculation sessions')
                return {d:parsed[d] for d in dates}
            except Exception as exc:
                last=exc
                time.sleep(attempt+1)
    raise RuntimeError(f'{symbol}: {last}')

def enrich(prices):
    dates=sorted(prices)
    series={}
    state='normal'
    attempt_days=0
    base_low=None
    for i,day in enumerate(dates):
        x=prices[day]
        prev=prices[dates[i-1]] if i else None
        change=(x['close']/prev['close']-1)*100 if prev else None
        volume=(x['volume']/prev['volume']-1)*100 if prev and prev['volume'] and x['volume'] is not None else None
        judge=None
        if change is not None and volume is not None and volume>0:
            if change>0: judge='buy'
            elif change<=-0.2: judge='sell'
        # Identical simplified FTD approximation as main dashboard: low is mandatory.
        low=x['low']
        prior=[prices[d]['close'] for d in dates[max(0,i-60):i]]
        if low is not None and prior:
            if state in ('normal','confirmed') and x['close']<=max(prior)*0.95:
                state,base_low,attempt_days='correction',low,0
            elif state=='correction':
                if change is not None and change>0:
                    p=prices[dates[i-1]]['low'] if i else None
                    if p is not None: state,base_low,attempt_days='attempt',p,1
            elif state=='attempt':
                if low<base_low:
                    state,base_low,attempt_days='correction',low,0
                else:
                    attempt_days+=1
                    if attempt_days>=4 and change is not None and change>=1.25 and volume is not None and volume>0:
                        judge,state='ftd','confirmed'
        series[day]={'change':round(change,4) if change is not None else None,'volume':round(volume,4) if volume is not None else None,'judge':judge,'close':x['close']}
    return series

def main():
    raw = {}
    failures = {}
    for symbol in [BENCHMARK, *SECTORS]:
        try:
            raw[symbol] = download(symbol)
        except Exception as exc:
            failures[symbol] = str(exc)
            print('WARNING: no ETF history for', symbol, repr(exc))
    if not any(s in raw for s in SECTORS):
        raise RuntimeError('No Japanese sector ETF history available; refusing to publish blank fabricated data')
    enriched = {s: enrich(p) for s, p in raw.items()}
    # Benchmark sessions determine trading dates; sparse ETFs remain missing, never forward-filled.
    dates = sorted(raw[BENCHMARK] if BENCHMARK in raw else set().union(*(set(p) for p in raw.values())), reverse=True)[:PUBLISH]
    rows = []
    for day in dates:
        values={s:enriched.get(s,{}).get(day) for s in SECTORS}
        topix=enriched.get(BENCHMARK,{}).get(day)
        topix_change=topix['change'] if topix else None
        ranking=[]
        for s, v in values.items():
            if v and topix_change is not None and v['change'] is not None:
                v={k:val for k,val in v.items() if k!='close'}
                v['vs_topix']=round(v['change']-topix_change,4)
                values[s]=v
                ranking.append((s,v['vs_topix']))
            elif v:
                values[s]={k:val for k,val in v.items() if k!='close'}
        for rank,(s,_) in enumerate(sorted(ranking,key=lambda x:x[1],reverse=True),1):
            values[s]['rank']=rank
        rows.append({'date':day,'topix_change':topix_change,'sectors':values})
    result={'updated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'names':SECTORS, 'rows':rows,
            'source':'Yahoo Finance unofficial daily prices / ETF-traded volume (not TOPIX-17 index levels)',
            'benchmark':BENCHMARK, 'missing_symbols':failures,
            'note':'TOPIX-17に連動する各ETFの市場価格・ETF出来高に基づく簡易判定です。TOPIX-17指数や業種全体の売買代金・売買高そのものではありません。ETFの取引が少ない日には指標が不安定になり得ます。'}
    (ROOT/'jp-sectors.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(f'WROTE jp-sectors.json: {len(rows)} rows, {len(SECTORS)} ETF symbols; latest={rows[0]["date"]}; failed={list(failures)}')

if __name__=='__main__': main()
