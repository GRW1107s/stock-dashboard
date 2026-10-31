"""One-run read-only diagnostics for P/S/G index and market volume sources.
No data is saved or published. Verify licensing before reuse.
"""
import datetime as dt
import json
import re
import urllib.request
import urllib.error
from html.parser import HTMLParser
from urllib.parse import urljoin

UA = {"User-Agent":"Mozilla/5.0 (compatible; market-source-diagnostics/1.0)","Accept":"application/json,text/html,*/*"}

def fetch(url):
    req=urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req,timeout=25) as r:
        raw=r.read(4_000_000)
        return r.status, r.geturl(),raw.decode(r.headers.get_content_charset() or 'utf-8','replace')

class Tables(HTMLParser):
    def __init__(self): super().__init__(); self.tables=[];self.table=None;self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='table' and self.table is None:self.table=[]
        elif tag=='tr' and self.table is not None:self.row=[]
        elif tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:self.row.append(' '.join(''.join(self.cell).split()));self.cell=None
        elif tag=='tr' and self.row is not None:
            if self.row:self.table.append(self.row)
            self.row=None
        elif tag=='table' and self.table is not None:self.tables.append(self.table);self.table=None

def volume():
    print('\n=== VOLUME: P/S/G ===',flush=True)
    status,url,html=fetch('https://stock-marketdata.com/trading-volume.html')
    p=Tables();p.feed(html)
    rows=[]
    for table in p.tables:
        for r in table:
            if len(r)<10:continue
            m=re.fullmatch(r'\s*(20\d\d)年(\d{1,2})月(\d{1,2})日\s*',r[0])
            if not m:continue
            try:
                day=dt.date(*map(int,m.groups())).isoformat()
                amounts=[int(r[i].replace(',','')) for i in (1,4,7)]
                pct=[float(r[i].strip().rstrip('%')) for i in (3,6,9)]
                if min(amounts)<=0:continue
                rows.append((day,amounts,pct))
            except ValueError:continue
    rows.sort()
    print('HTTP:',status,'parsed sessions:',len(rows))
    if rows:
        print('range:',rows[0][0],rows[-1][0])
        for row in rows[-3:]:print('sample:',row)
    if len(rows)>1:
        a,b=rows[-2:]
        diffs=[round((b[1][i]/a[1][i]-1)*100-b[2][i],3) for i in range(3)]
        print('P/S/G difference vs reported percentage (pp):',diffs)
    return len(rows)>=20

def inspect_json(path):
    url='https://kabubusho.com'+path
    status,final,body=fetch(url)
    print('path',path,'HTTP',status,'bytes',len(body))
    try:data=json.loads(body)
    except json.JSONDecodeError:
        print('not JSON, prefix:',repr(body[:90]));return False
    if isinstance(data,dict):
        print('keys:',list(data)[:30]);elements=[]
        for k,v in data.items():
            if isinstance(v,list):elements+=v[:3];print('list',k,'count',len(v))
            elif isinstance(v,dict):elements.append(v)
    elif isinstance(data,list):
        print('records:',len(data));elements=data[:5]
    else:elements=[]
    for x in elements[:5]:
        s=json.dumps(x,ensure_ascii=False)
        print('sample:',s[:650])
    search=json.dumps(data,ensure_ascii=False)
    print('matches P/S/G:',{q:search.count(q) for q in ['プライム','スタンダード','グロース','0500','0501','0502']})
    return True

def indices():
    print('\n=== INDEX API: kabubusho ===',flush=True)
    out=[]
    for path in ['/api/indices','/api/relative-strength/indices']:
        try:out.append(inspect_json(path))
        except Exception as e:print(path,'ERROR',type(e).__name__,str(e)[:150])
    print('\n=== INDEX PAGE: nikkei225jp ===')
    page='https://nikkei225jp.com/data/vol.php'
    try:
        status,final,html=fetch(page)
        print('page HTTP:',status)
        assets=re.findall(r'<script[^>]*\bsrc=[\'\"]([^\'\"]+)',html,re.I)
        for asset in [x for x in assets if ('VOL.json' in x or 'ajax_NDY' in x)][:3]:
            full=urljoin(page,asset.replace('&amp;','&'))
            try:
                status,url,body=fetch(full)
                print('linked asset:',full,'HTTP',status,'size',len(body),'preview',body[:180])
            except Exception as e:print('linked asset:',full,'ERROR',type(e).__name__,str(e)[:120])
    except Exception as e:print('page ERROR',repr(e))
    return any(out)

if __name__=='__main__':
    try:v=volume()
    except Exception as e:print('volume ERROR:',repr(e));v=False
    i=indices()
    print('\n=== SUMMARY ===')
    print('Market volume:', 'PARSED' if v else 'NOT CONFIRMED')
    print('Index API JSON:', 'REACHABLE (VERIFY DATA & PERMISSIONS)' if i else 'NOT CONFIRMED')
    print('No production data changed. Access does not imply permission to redistribute.')
