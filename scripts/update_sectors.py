"""Create sectors.json from 11 Select Sector SPDR ETFs and SPY.
Uses ~250 trading sessions for calculations; publishes 200 sessions.
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
SECTORS = {'XLC':'コミュニケーション','XLY':'一般消費財','XLP':'生活必需品','XLE':'エネルギー','XLF':'金融','XLV':'ヘルスケア','XLI':'資本財','XLB':'素材','XLRE':'不動産','XLK':'情報技術','XLU':'公益事業'}
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
                req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; PersonalSectorDashboard/1.0)','Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=25) as res:
                    data=json.load(res)['chart']['result'][0]
                quote=data['indicators']['quote'][0]
                tz=ZoneInfo('America/New_York')
                now=dt.datetime.now(tz)
                parsed={}
                for stamp,close,low,vol in zip(data['timestamp'],quote['close'],quote['low'],quote['volume']):
                    close=num(close)
                    if close is None or close<=0: continue
                    day=dt.datetime.fromtimestamp(stamp,tz).date()
                    if day==now.date() and now.time()<dt.time(16,15): continue
                    parsed[day.isoformat()]={'close':close,'low':num(low),'volume':num(vol)}
                if len(parsed)<210: raise ValueError(f'too few sessions {len(parsed)}')
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
    attempt_history=[]
    for i,day in enumerate(dates):
        x=prices[day]
        prev=prices[dates[i-1]] if i else None
        change=(x['close']/prev['close']-1)*100 if prev else None
        volume=(x['volume']/prev['volume']-1)*100 if prev and prev['volume'] and x['volume'] is not None else None
        judge=None
        ra_day=None
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
                    if p is not None:
                        state,base_low,attempt_days='attempt',p,1
                        ra_day=1
                        attempt_history=[day]
            elif state=='attempt':
                if low<base_low:
                    # Hide the whole RA sequence once this rally attempt has failed.
                    for failed_day in attempt_history:
                        series[failed_day]['ra_day']=None
                    attempt_history=[]
                    state,base_low,attempt_days='correction',low,0
                else:
                    attempt_days+=1
                    ra_day=attempt_days
                    attempt_history.append(day)
                    if attempt_days>=4 and change is not None and change>=1.25 and volume is not None and volume>0:
                        judge,state='ftd','confirmed'
                        attempt_history=[]  # Keep completed RA labels visible.
        series[day]={'change':round(change,4) if change is not None else None,'volume':round(volume,4) if volume is not None else None,'judge':judge,'close':x['close'],'ra_day':ra_day}
    # DD count: previous 25 sessions, dropping a DD after a 5% recovery.
    # This is a transparent simplified count; IBD stalling-day criteria are excluded.
    active_dd=[]
    for i,day in enumerate(dates):
        close=prices[day]['close']
        active_dd=[(idx,price) for idx,price in active_dd
                   if i-idx<25 and close < price*1.05]
        rec=series[day]
        if rec['judge']=='sell':
            active_dd.append((i,close))
            rec['dd_number']=len(active_dd)
        rec['dd_count']=len(active_dd)
    return series

def main():
    raw={s:download(s) for s in [*SECTORS,'SPY']}
    enriched={s:enrich(p) for s,p in raw.items()}
    # Use the union of ETF sessions; never invent or forward-fill market closes.
    dates=sorted(set().union(*(set(p) for p in raw.values())),reverse=True)[:PUBLISH]
    rows=[]
    for day in dates:
        values={s:enriched[s].get(day) for s in SECTORS}
        spy=enriched['SPY'].get(day)
        spy_change=spy['change'] if spy else None
        ranking=[]
        for s, v in values.items():
            if v and spy_change is not None and v['change'] is not None:
                v={k:val for k,val in v.items() if k!='close'}
                v['vs_spy']=round(v['change']-spy_change,4)
                values[s]=v
                ranking.append((s,v['vs_spy']))
            elif v:
                values[s]={k:val for k,val in v.items() if k!='close'}
        for rank,(s,_) in enumerate(sorted(ranking,key=lambda x:x[1],reverse=True),1):
            values[s]['rank']=rank
        rows.append({'date':day,'spy_change':spy_change,'sectors':values})
    result={'updated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'names':SECTORS,'rows':rows,'note':'ETF出来高に基づく独自の簡易判定。セクター構成銘柄全体の売買高ではありません。'}
    (ROOT/'sectors.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(f'WROTE sectors.json: {len(rows)} rows, {len(SECTORS)} ETFs; latest={rows[0]["date"]}')

if __name__=='__main__': main()
