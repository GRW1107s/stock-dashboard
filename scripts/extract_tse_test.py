"""Diagnostics: verify parsed TSE market-volume rows and inspect index source availability.
No site data is written to disk or republished. Check source terms before production use.
"""
import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HEADERS = {"User-Agent":"Mozilla/5.0 (compatible; market-dashboard-source-check/1.0)","Accept":"text/html,application/json,*/*"}


def get(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=25) as response:
        content = response.read(5_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
        return response.status, response.geturl(), content.decode(charset, errors="replace")


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables=[]; self.table=None; self.row=None; self.cell=None
    def handle_starttag(self, tag, attrs):
        if tag == 'table' and self.table is None: self.table=[]
        elif tag == 'tr' and self.table is not None: self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=[]
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split())); self.cell=None
        elif tag == 'tr' and self.row is not None:
            if self.row: self.table.append(self.row)
            self.row=None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table); self.table=None


def parse_day(s):
    m = re.fullmatch(r'\s*(20\d{2})年(\d{1,2})月(\d{1,2})日\s*',s)
    if not m: return None
    return dt.date(*map(int,m.groups())).isoformat()


def num(s):
    return int(s.replace(',','').strip())


def inspect_volume():
    print('=== MARKET VOLUME VALIDATION ===')
    status, url, html = get('https://stock-marketdata.com/trading-volume.html')
    print('HTTP:',status,'URL:',url)
    p=Tables();p.feed(html)
    print('HTML tables:',len(p.tables))
    accepted=[]
    for table in p.tables:
        for row in table:
            if len(row)<10: continue
            day=parse_day(row[0])
            if not day: continue
            try:
                values={name: num(row[idx]) for name,idx in [('P',1),('S',4),('G',7)]}
                changes={name: float(row[idx].replace('%','')) for name,idx in [('P',3),('S',6),('G',9)]}
                if any(v<=0 for v in values.values()): raise ValueError('non-positive volume')
                if any(not -100 <= v <= 1000 for v in changes.values()): raise ValueError('unexpected percentage')
                accepted.append((day,values,changes))
            except ValueError as e:
                print('Skipped invalid row date:',day, str(e))
    accepted.sort(key=lambda x:x[0])
    print('PARSED VOLUME ROWS:',len(accepted))
    if accepted:
        print('DATE RANGE:',accepted[0][0],'to',accepted[-1][0])
        for day, values, changes in accepted[-3:]:
            print('CHECK:',day,'P/S/G volumes:',values,'reported percent:',changes)
        if len(accepted)>=2:
            day, current, reported=accepted[-1]
            previous=accepted[-2][1]
            print('LATEST PERCENT CROSS-CHECK (rounding tolerance 0.1pp):')
            for k in ('P','S','G'):
                calculated=(current[k]/previous[k]-1)*100
                ok=abs(calculated-reported[k])<=0.1
                print(k,'computed',round(calculated,2),'reported',reported[k],'consistent',ok)
    print('VOLUME PARSING:', 'PASS' if len(accepted)>=20 else 'INCOMPLETE')


def inspect_index():
    print('\n=== INDEX SOURCE AVAILABILITY ===')
    page='https://nikkei225jp.com/data/vol.php'
    status, final, html=get(page)
    print('Page HTTP:',status,'bytes:',len(html))
    # Use URLs explicitly present in the source page; no fabricated endpoints.
    urls=re.findall(r'<script[^>]*\bsrc=[\'\"]([^\'\"]+)[\'\"]',html,re.I)
    match=[u for u in urls if 'VOL.json' in u or 'ajax_NDY_min.js' in u]
    print('RELEVANT REFERENCED ASSETS:',match)
    for link in match[:3]:
        url=urllib.parse.urljoin(page,link.replace('&amp;','&'))
        try:
            status, final, body=get(url)
            print('ASSET',urllib.parse.urlsplit(url).path,'HTTP:',status,'bytes:',len(body))
            if 'VOL.json' in url:
                try:
                    data=json.loads(body)
                    print('JSON top-level type:',type(data).__name__)
                    if isinstance(data,dict):print('JSON keys:',list(data)[:20])
                    if isinstance(data,list):print('JSON list length:',len(data),'first item type:',type(data[0]).__name__ if data else '-')
                except json.JSONDecodeError:
                    print('Asset was not valid JSON')
        except Exception as exc:
            print('ASSET FAILURE',urllib.parse.urlsplit(url).path,type(exc).__name__,str(exc)[:160])
    print('INDEX PRICE EXTRACTION: NOT YET VERIFIED')


if __name__=='__main__':
    try: inspect_volume()
    except Exception as exc: print('VOLUME ERROR:',type(exc).__name__,str(exc))
    try: inspect_index()
    except Exception as exc: print('INDEX ERROR:',type(exc).__name__,str(exc))
    print('DIAGNOSTIC ONLY. Confirm permission before recurring download and republication.')
