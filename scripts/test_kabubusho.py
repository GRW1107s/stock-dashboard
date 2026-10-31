"""Read-only diagnostics for Kabubusho market-index data availability.
Does not persist, publish or scrape historical index values.
"""
import re
import json
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse
from urllib.error import HTTPError, URLError

BASE = 'https://kabubusho.com/indices.html'
KEYWORDS = ('プライム市場指数', 'スタンダード市場指数', 'グロース市場指数', '東証プライム', '東証スタンダード', '東証グロース')
class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(); self.sources=[]
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script' and a.get('src'): self.sources.append(a['src'])

def fetch(url):
    request = Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; MarketIndexAvailabilityCheck/1.0)', 'Accept':'text/html,application/javascript,application/json,*/*'})
    with urlopen(request, timeout=15) as r:
        data = r.read(2_000_000)
        return r.status, data.decode('utf-8', errors='replace'), r.geturl()

def main():
    print('READ-ONLY DIAGNOSTIC — no data will be published or saved')
    try:
        status, html, final = fetch(BASE)
        print('PAGE', status, 'bytes', len(html.encode('utf-8')), 'URL', final)
    except (HTTPError, URLError, TimeoutError) as e:
        print('PAGE FAILED', type(e).__name__, str(e)); raise SystemExit(1)
    scripts = Scripts(); scripts.feed(html)
    relevant = [urljoin(BASE, s) for s in scripts.sources if urlparse(urljoin(BASE,s)).hostname == 'kabubusho.com']
    print('SAME-ORIGIN JS:', relevant)
    print('PAGE MATCHES:', {k:html.count(k) for k in KEYWORDS})
    for src in relevant:
        print('\nJS:',src)
        try:
            status, body, _ = fetch(src)
            print('status',status,'chars',len(body))
            print('market-index matches:',{k:body.count(k) for k in KEYWORDS})
            # Only print URL/path references, never large data payloads
            refs = re.findall(r'(?:fetch\s*\(\s*|url\s*:\s*|\.open\s*\(\s*[\x27\"]GET[\x27\"]\s*,\s*)[\x27\"`]([^\x27\"`]+)',body)
            print('FETCH REFERENCES:',refs[:30])
            possible = re.findall(r'[\x27\"`]([^\x27\"`]{1,180}(?:\.json|\.csv|/api/)[^\x27\"`]{0,100})[\x27\"`]',body, flags=re.I)
            print('DATA PATH CANDIDATES:',list(dict.fromkeys(possible))[:30])
            for term in ('東証プライム','東証スタンダード','東証グロース'):
                pos=body.find(term)
                if pos>=0: print('LABEL',term,'NEAR',re.sub(r'\s+',' ',body[max(0,pos-80):pos+95])[:175])
        except (HTTPError, URLError, TimeoutError) as e:
            print('JS FAILED',type(e).__name__,str(e))
    print('\nResult: inspect references and confirm authorized reuse before implementing scheduled ingestion.')
if __name__ == '__main__': main()
