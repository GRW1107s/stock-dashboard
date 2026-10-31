"""One-time diagnostics of public webpages. Not an automated collection pipeline.
No content is saved; prints HTTP status and structured-data candidates only.
"""
import re
import urllib.request
import urllib.error
from html.parser import HTMLParser

SITES = {
    'nikkei225jp': 'https://nikkei225jp.com/data/vol.php',
    'stock-marketdata': 'https://stock-marketdata.com/trading-volume.html',
    'kabubusho': 'https://kabubusho.com/indices.html',
}

class Inspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = 0
        self.scripts = []
        self.rows = 0
        self.headers = []
        self.in_th = False
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == 'table': self.tables += 1
        if tag == 'tr': self.rows += 1
        if tag == 'script' and d.get('src'): self.scripts.append(d['src'])
        if tag == 'th': self.in_th = True
    def handle_endtag(self, tag):
        if tag == 'th': self.in_th = False
    def handle_data(self, data):
        if self.in_th and data.strip(): self.headers.append(data.strip()[:40])

for name, url in SITES.items():
    print('\n===', name, '===')
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent':'Mozilla/5.0 (compatible; MarketDashboardSourceCheck/1.0)',
            'Accept':'text/html',
        })
        with urllib.request.urlopen(req, timeout=20) as res:
            body = res.read(2_000_000).decode('utf-8', errors='replace')
            print('HTTP:', res.status, 'final URL:', res.url, 'bytes:', len(body))
        parser = Inspector(); parser.feed(body)
        print('HTML tables:', parser.tables, 'table rows:', parser.rows)
        print('Table headings:', parser.headers[:28])
        print('External JS files:', parser.scripts[:8])
        for key in ('プライム', 'スタンダード', 'グロース', '出来高', '売買高', '指数'):
            print(f'Keyword {key}:', body.count(key))
        print('JSON-LD:', bool(re.search(r'application/ld\\+json', body)))
        if parser.tables == 0:
            print('NOTE: No HTML tables; data may be rendered by JavaScript.')
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print('FAILED:', type(exc).__name__, str(exc)[:250])
print('\nDIAGNOSTIC ONLY: successful HTTP retrieval is not permission to reuse data.')
