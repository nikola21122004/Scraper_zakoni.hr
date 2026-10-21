#!/usr/bin/env python3
"""Pretraga javno dostupnih zakona na zakon.hr i izvoz u PDF."""
import argparse
from datetime import datetime
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

BASE = 'https://www.zakon.hr'


def valid_url(url):
    p = urlsplit(url)
    return (p.scheme == 'https' and p.hostname in {'zakon.hr', 'www.zakon.hr'}
            and not p.username and not p.password and p.port in (None, 443))


def fetch(session, url, params=None):
    # Pratimo samo preusmjerenja unutar istog sajta.
    for _ in range(6):
        if not valid_url(url):
            raise ValueError('Dozvoljene su samo HTTPS adrese sa zakon.hr.')
        response = session.get(url, params=params, timeout=(10, 60), allow_redirects=False)
        params = None
        if response.is_redirect:
            url = urljoin(response.url, response.headers['Location'])
            continue
        response.raise_for_status()
        if 'html' not in response.headers.get('Content-Type', '').lower():
            raise ValueError('Server nije vratio HTML stranicu.')
        response.encoding = 'utf-8'
        return BeautifulSoup(response.text, 'html.parser'), response.url
    raise ValueError('Previše preusmjerenja.')


def parse_results(soup):
    results, seen = [], set()
    for a in soup.select('a.rezultat-pretrage-title[href]'):
        url = urljoin(BASE, a['href'])
        if valid_url(url) and re.match(r'^/z/\d+/', urlsplit(url).path) and url not in seen:
            seen.add(url)
            results.append((a.get_text(' ', strip=True), url))
    return results


def extract(soup):
    title = soup.select_one('.main-zakon-header h1')
    body = soup.select_one('#pretrazitekst.tekst-zakona')
    if title is None or body is None:
        raise ValueError('Tekst zakona nije dostupan. Moguća prijava ili promjena strukture sajta.')
    for node in body.select('script, style, button, .cms-block-tools, .cms-block-actions'):
        node.decompose()
    blocks = []
    header = soup.select_one('.main-zakon-header')
    for p in header.select(':scope > p'):
        text = p.get_text(' ', strip=True)
        if text and text != 'Uživajte...':
            blocks.append(('meta', text))
    for node in body.select('h1,h2,h3,h4,h5,h6,p,li,table'):
        if node.find_parent(['table', 'li']) is not None:
            continue
        text = node.get_text(' ', strip=True).replace('\xa0', ' ')
        if text.strip():
            kind = 'heading' if node.name.startswith('h') or 'cms-zakon-clanak' in node.get('class', []) else 'body'
            blocks.append((kind, text))
    if not any(re.search(r'Članak\s+\d+', text) for _, text in blocks):
        raise ValueError('Nisu pronađeni članci zakona; PDF nije napravljen.')
    return title.get_text(' ', strip=True), blocks


def write_pdf(title, blocks, url, destination):
    font = Path(__file__).parent / 'fonts' / 'DejaVuSans.ttf'
    if not font.is_file():
        raise ValueError('Nedostaje fonts/DejaVuSans.ttf. Raspakujte cijeli ZIP paket.')
    pdfmetrics.registerFont(TTFont('ZakonSans', str(font)))
    styles = {
        'title': ParagraphStyle('title', fontName='ZakonSans', fontSize=18, leading=24, spaceAfter=15),
        'heading': ParagraphStyle('heading', fontName='ZakonSans', fontSize=11, leading=16, spaceBefore=12, spaceAfter=6),
        'body': ParagraphStyle('body', fontName='ZakonSans', fontSize=9.5, leading=14, spaceAfter=7),
        'meta': ParagraphStyle('meta', fontName='ZakonSans', fontSize=8, leading=12, textColor=colors.HexColor('#555555'), spaceAfter=5),
    }
    story = [Paragraph(escape(title), styles['title']),
             Paragraph(escape('Izvor: ' + url), styles['meta']),
             Paragraph('Preuzeto: ' + datetime.now().astimezone().isoformat(timespec='seconds'), styles['meta']),
             Paragraph('PDF napravljen iz dostupnog HTML teksta. Nije službeno izdanje niti potvrda potpunosti ili važenja propisa.', styles['meta']), Spacer(1, 12)]
    story += [Paragraph(escape(text), styles[kind]) for kind, text in blocks]

    def footer(canvas, doc):
        canvas.setFont('ZakonSans', 8)
        canvas.drawRightString(A4[0] - 45, 25, str(doc.page))

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Ne ostavljamo polovičan PDF ako izvoz ne uspije.
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.pdf', delete=False) as f:
        temp = Path(f.name)
    try:
        SimpleDocTemplate(str(temp), pagesize=A4, leftMargin=45, rightMargin=45,
                          topMargin=45, bottomMargin=45, title=title, author='Zakon.hr - HTML izvoz').build(story, onFirstPage=footer, onLaterPages=footer)
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('upit', nargs='?', help='Naziv zakona ili direktan zakon.hr URL')
    parser.add_argument('--izlaz', type=Path, default=Path('pdf_zakoni'), help='Izlazni direktorijum')
    args = parser.parse_args()
    query = (args.upit or input('Unesite naziv zakona ili zakon.hr URL: ')).strip()
    if not query:
        raise ValueError('Unos je prazan.')
    with requests.Session() as session:
        session.headers['User-Agent'] = 'ZakonPDF/1.0 (personal document export)'
        if query.startswith(('https://', 'http://')):
            url = query
        else:
            soup, _ = fetch(session, BASE + '/search', {'q': query, 't': '0', 'fs': '1'})
            results = parse_results(soup)
            if not results:
                raise ValueError('Nema rezultata na prvoj stranici. Probajte precizniji naziv ili direktan URL zakona.')
            for i, (title, link) in enumerate(results, 1):
                print(f'{i}. {title}\n   {link}')
            while True:
                choice = input('Izaberite broj zakona (Enter za odustajanje): ').strip()
                if not choice:
                    return
                if choice.isdigit() and 1 <= int(choice) <= len(results):
                    url = results[int(choice) - 1][1]
                    break
                print('Unesite jedan od ponuđenih brojeva.')
        soup, url = fetch(session, url)
        title, blocks = extract(soup)
        slug = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode()
        slug = re.sub(r'[^a-zA-Z0-9]+', '_', slug).strip('_')[:100] or 'zakon'
        destination = args.izlaz / f'{slug}_{datetime.now():%Y%m%d_%H%M%S_%f}.pdf'
        write_pdf(title, blocks, url, destination)
        print(f'PDF je sačuvan: {destination.resolve()}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, requests.RequestException, OSError) as exc:
        print(f'Greška: {exc}', file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print('\nPrekinuto.', file=sys.stderr)
        sys.exit(130)
