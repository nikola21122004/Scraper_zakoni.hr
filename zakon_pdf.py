#!/usr/bin/env python3
"""Zakon PDF — sve u jednom fajlu (Python 3.10+).
Instalacija: python3 -m pip install requests beautifulsoup4 reportlab
Pokretanje: python3 zakon_pdf_sve_u_jednom.py
Tkinter dolazi uz Python za macOS/Windows sa python.org.
Font se učitava iz fonts/DejaVuSans.ttf uz ovaj program.
PDF je izvoz dostupnog HTML-a; potpunost provjerite na izvorniku.
Tabele se pretvaraju u tekst, slike se ne prenose.
"""
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
    font = Path(__file__).resolve().parent / 'fonts' / 'DejaVuSans.ttf'
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


import multiprocessing
import queue
import time
import webbrowser
import subprocess
import os
import traceback

def worker(kind, payload, events):
    """Mreža, HTML i PDF rade u zasebnom procesu; Tk ostaje slobodan."""
    try:
        with requests.Session() as session:
            session.headers['User-Agent'] = 'ZakonPDF/2.0 (personal document export)'
            if kind == 'results':
                if payload.startswith(('http://', 'https://')):
                    soup, url = fetch(session, payload)
                    title, _ = extract(soup)
                    value = [(title, url)]
                else:
                    soup, _ = fetch(session, BASE + '/search', {'q': payload, 't': '0', 'fs': '1'})
                    value = parse_results(soup)
            else:
                url, path = payload
                soup, url = fetch(session, url)
                title, blocks = extract(soup)
                value = Path(path).resolve()
                write_pdf(title, blocks, url, value)
        events.put((kind, value))
    except Exception as exc:
        events.put(('error', str(exc)))

class App:
    def __init__(self, root):
        self.root = root
        self.results = []
        self.busy = False
        self.context = multiprocessing.get_context('spawn')
        self.events = self.context.Queue()
        self.process = None
        self.started = 0
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.saved = None
        self.selected_index = None
        self.output_dir = Path.home() / 'Downloads' / 'ZakonPDF'
        root.report_callback_exception = self.callback_error
        root.title('Zakoni • Pretraga i PDF')
        root.geometry('780x560')
        root.minsize(600, 440)
        main = ttk.Frame(root, padding=22)
        main.pack(fill='both', expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)
        ttk.Label(main, text='Pronađite zakon', font=('', 22, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(main, text='Unesite naziv zakona ili direktnu zakon.hr adresu.').grid(row=1, column=0, sticky='w', pady=(6, 16))
        search = ttk.Frame(main)
        search.grid(row=2, column=0, sticky='ew')
        search.columnconfigure(0, weight=1)
        self.query = ttk.Entry(search, font=('', 13))
        self.query.grid(row=0, column=0, sticky='ew', padx=(0, 10))
        self.query.bind('<Return>', lambda e: self.search())
        self.search_button = ttk.Button(search, text='Pretraži', command=self.search)
        self.search_button.grid(row=0, column=1)
        ttk.Label(main, text='Rezultati — izaberite zakon sa liste:').grid(row=3, column=0, sticky='w', pady=(20, 8))
        frame = ttk.Frame(main)
        frame.grid(row=4, column=0, sticky='nsew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.list = ttk.Treeview(frame, columns=('title',), show='headings', selectmode='browse')
        self.list.heading('title', text='Naziv zakona')
        self.list.column('title', width=580, minwidth=250)
        self.list.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.list.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.list.configure(yscrollcommand=scroll.set)
        self.list.bind('<<TreeviewSelect>>', self.selection_changed)
        self.list.bind('<Double-1>', lambda e: self.download())
        self.status = tk.StringVar(value='Primjer pretrage: Zakon o radu')
        ttk.Label(main, textvariable=self.status, wraplength=700).grid(row=5, column=0, sticky='w', pady=(10, 5))
        self.progress = ttk.Progressbar(main, mode='indeterminate')
        self.progress.grid(row=6, column=0, sticky='ew', pady=(0, 12))
        actions = ttk.Frame(main)
        actions.grid(row=7, column=0, sticky='ew')
        self.download_button = ttk.Button(actions, text='Sačuvaj PDF', command=self.download, state='disabled')
        self.download_button.pack(side='right')
        self.open_button = ttk.Button(actions, text='Otvori PDF', command=self.open_pdf, state='disabled')
        self.open_button.pack(side='left')
        self.folder_button = ttk.Button(actions, text='Otvori folder', command=self.open_folder)
        self.folder_button.pack(side='left', padx=8)
        ttk.Label(main, text='PDF se čuva u Downloads/ZakonPDF. Dugme Otvori PDF radi nakon čuvanja.', foreground='#666666').grid(row=8, column=0, sticky='w', pady=(14, 0))
        self.query.focus_set()
        root.after(100, self.poll)

    def selection_changed(self, event=None):
        selection = self.list.selection()
        if selection:
            self.selected_index = int(selection[0])
        self.download_button.configure(state='normal' if self.selected_index is not None and not self.busy else 'disabled')

    def callback_error(self, kind, value, tb):
        traceback.print_exception(kind, value, tb)
        messagebox.showerror('Greška u interfejsu', str(value), parent=self.root)


    def run(self, kind, payload, status):
        self.busy = True
        self.search_button.configure(state='disabled')
        self.download_button.configure(state='disabled')
        self.query.configure(state='disabled')
        self.status.set(status)
        self.progress.start(12)

        self.started = time.monotonic()
        self.process = self.context.Process(target=worker, args=(kind, payload, self.events), daemon=True)
        try:
            self.process.start()
        except Exception as exc:
            self.events.put(('error', str(exc)))

    def close(self):
        if self.process and self.process.is_alive():
            self.process.terminate()
        if self.process and self.process.pid:
            self.process.join(timeout=0.2)
        self.events.close()
        self.root.destroy()

    def poll(self):
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            if self.busy and time.monotonic() - self.started > 120:
                if self.process and self.process.is_alive():
                    self.process.terminate()
                self.events.put(('error', 'Operacija traje predugo. Pokušajte ponovo.'))
            elif self.busy and self.process and self.process.exitcode not in (None, 0):
                self.events.put(('error', 'Radni proces je prekinut. Pokušajte ponovo.'))
        else:
            if self.process and self.process.pid:
                self.process.join(timeout=0)
            self.busy = False
            self.progress.stop()
            self.search_button.configure(state='normal')
            self.query.configure(state='normal')
            if kind == 'results':
                self.results = value
                self.list.delete(*self.list.get_children())
                for i, (title, url) in enumerate(value):
                    self.list.insert('', 'end', iid=str(i), values=(title,))
                self.status.set(f'Pronađeno: {len(value)}. Izaberite zakon i kliknite „Sačuvaj PDF“.' if value else 'Nema rezultata. Probajte precizniji naziv ili direktan URL.')
                if value:
                    self.list.selection_set('0')
            elif kind == 'saved':
                self.saved = Path(value)
                if not self.saved.is_file() or self.saved.stat().st_size == 0:
                    self.status.set('PDF nije pronađen nakon izvoza.')
                    messagebox.showerror('Greška', 'PDF nije uspješno sačuvan.', parent=self.root)
                    self.root.after(100, self.poll)
                    return
                self.open_button.configure(state='normal')
                self.status.set(f'PDF je sačuvan: {value}')
                messagebox.showinfo('PDF je spreman', f'Sačuvano u:\n{value}', parent=self.root)
            else:
                self.status.set('Operacija nije uspjela. Možete pokušati ponovo.')
                messagebox.showerror('Greška', value, parent=self.root)
            self.selection_changed()
        self.root.after(100, self.poll)

    def search(self):
        if self.busy:
            return
        query = self.query.get().strip()
        if not query:
            messagebox.showinfo('Unesite naziv', 'Unesite naziv zakona ili zakon.hr URL.', parent=self.root)
            return

        # Stari rezultati se uklanjaju da ne ostanu poslije neuspjele nove pretrage.
        self.results = []
        self.selected_index = None
        self.list.delete(*self.list.get_children())
        self.run('results', query, 'Pretražujem zakon.hr…')

    def download(self):
        if self.busy:
            return
        selection = self.list.selection()
        index = int(selection[0]) if selection else self.selected_index
        if index is None or not 0 <= index < len(self.results):
            messagebox.showinfo('Izaberite zakon', 'Prvo kliknite na zakon u listi.', parent=self.root)
            return
        title, url = self.results[index]
        safe_title = ''.join(c for c in title if c.isalnum() or c in ' -_').strip()[:100] or 'zakon'
        # Direktno čuvanje izbjegava zavisnost od macOS Save dijaloga.
        path = self.output_dir / (safe_title + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.pdf')
        self.run('saved', (url, str(path)), 'Preuzimam tekst i pravim PDF…')

    def open_path(self, path):
        try:
            path = Path(path).resolve()
            if not path.exists():
                raise FileNotFoundError(f'Fajl ili folder ne postoji: {path}')
            if sys.platform == 'darwin':
                subprocess.run(['open', str(path)], check=True, capture_output=True, text=True, timeout=10)
            elif sys.platform == 'win32':
                os.startfile(str(path))
            else:
                subprocess.run(['xdg-open', str(path)], check=True, capture_output=True, text=True, timeout=10)
        except Exception as exc:
            messagebox.showerror('Otvaranje nije uspjelo', str(exc), parent=self.root)

    def open_pdf(self):
        if self.saved:
            self.open_path(self.saved)
        else:
            messagebox.showinfo('PDF još nije sačuvan', 'Prvo kliknite Sačuvaj PDF.', parent=self.root)

    def open_folder(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror('Folder nije dostupan', str(exc), parent=self.root)
            return
        self.open_path(self.output_dir)



if __name__ == '__main__':
    multiprocessing.freeze_support()
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        raise SystemExit('Tkinter nije dostupan. Koristite Python za macOS/Windows sa python.org.')
    root = tk.Tk()
    App(root)
    root.mainloop()
