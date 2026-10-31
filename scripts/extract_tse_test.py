"""Diagnostic only: inspect publicly served market data formats; does not republish data."""
import csv
import io
import json
import re
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urljoin

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; market-data-format-check/1.0)", "Accept": "application/json,text/html,*/*"}


def download(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as response:
        body = response.read(5_000_000)
        return response.status, response.geturl(), body.decode('utf-8-sig', errors='replace')


class TableReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.current = None
        self.row = None
        self.cell = None
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        if tag == 'script': self.in_script = True
        if tag == 'table':
            if self.current is None: self.current = []
        elif self.current is not None and tag == 'tr': self.row = []
        elif self.row is not None and tag in ('td', 'th'): self.cell = []

    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'script': self.in_script = False
        if tag in ('td', 'th') and self.cell is not None and self.row is not None:
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        if tag == 'tr' and self.row is not None and self.current is not None:
            if self.row: self.current.append(self.row)
            self.row = None
        if tag == 'table' and self.current is not None:
            self.tables.append(self.current)
            self.current = None


def walk(obj, depth=0, path='$', stats=None):
    if stats is None: stats = Counter()
    if depth > 8: return stats
    if isinstance(obj, dict):
        if depth < 3: print('  object', path, 'keys:', list(obj)[:22])
        for k, v in obj.items():
            stats[str(k)] += 1
            walk(v, depth + 1, path + '.' + str(k), stats)
    elif isinstance(obj, list):
        if depth < 3:
            print('  list', path, 'length:', len(obj), 'first type:', type(obj[0]).__name__ if obj else 'empty')
            if obj and isinstance(obj[0], list): print('  first row preview:', [str(x)[:48] for x in obj[0][:12]])
        for v in obj[:min(len(obj), 100)]: walk(v, depth + 1, path + '[*]', stats)
    return stats


def inspect_json():
    print('=== nikkei225jp JSON endpoint ===')
    url = 'https://nikkei225jp.com/_data/_nfsDATA/json_DAY/VOL.json'
    try:
        status, final, raw = download(url)
        print('HTTP:', status, 'bytes:', len(raw), 'final:', final)
        try: data = json.loads(raw)
        except json.JSONDecodeError:
            print('Not valid JSON; first 120 characters:', repr(raw[:120])); return
        keys = walk(data)
        print('Most common JSON keys:', keys.most_common(30))
        print('JSON structure inspected: YES; market-price extraction verified: NO (schema requires validation)')
    except Exception as exc:
        print('JSON fetch FAILED:', type(exc).__name__, str(exc)[:280])


def inspect_volume():
    print('\n=== stock-marketdata HTML volume table ===')
    url = 'https://stock-marketdata.com/trading-volume.html'
    try:
        status, final, raw = download(url)
        print('HTTP:', status, 'bytes:', len(raw), 'final:', final)
        parser = TableReader(); parser.feed(raw)
        print('Tables found:', len(parser.tables))
        for i, rows in enumerate(parser.tables):
            print(f'Table {i}: rows={len(rows)}')
            for row in rows[:min(5, len(rows))]: print('  ', row[:15])
            plausible = [r for r in rows if r and re.search(r'20\d{2}[/-]\d{1,2}[/-]\d{1,2}', r[0])]
            print('Rows with ISO-style dates in first cell:', len(plausible))
            if plausible:
                print('Date first / last:', plausible[0][0], plausible[-1][0])
                if len(plausible[0]) >= 10:
                    print('Candidate 3-market volume row identified: YES (manual unit / column verification still required)')
                else:
                    print('Candidate table is too narrow to map all market columns')
            else:
                print('No parseable dated rows. Page may render table via JS.')
    except Exception as exc:
        print('Volume fetch FAILED:', type(exc).__name__, str(exc)[:280])


if __name__ == '__main__':
    inspect_json()
    inspect_volume()
    print('\nDIAGNOSTIC COMPLETE: HTTP success does not prove reuse rights or validated extraction.')
