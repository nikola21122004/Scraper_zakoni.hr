# Pretraga zakona i izvoz u PDF

Potrebni su Python 3.10+ i internet. Raspakujte cijeli paket, uključujući fonts.

U terminalu otvorite raspakovani direktorijum i pokrenite:

```sh
python -m pip install -r requirements.txt
python zakon_pdf.py
```

Unesite npr. `Zakon o radu`, a zatim broj rezultata. PDF se čuva u direktorijumu `pdf_zakoni`.
Na macOS/Linux sistemima po potrebi koristite `python3` umjesto `python`.

Možete unijeti i naziv kao argument:

```sh
python zakon_pdf.py "Zakon o radu"
python zakon_pdf.py "https://www.zakon.hr/z/307/zakon-o-radu" --izlaz moji_pdf
```

Program koristi pretragu zakon.hr sa filterom Zakoni i važeći propisi, prikazuje
rezultate sa prve stranice i preuzima samo izabrani dokument. Ako nema rezultata,
unesite precizniji naziv ili direktan URL. Dostupan HTML tekst pretvara u novi PDF;
ne koristi servis sajta za preuzimanje njihovog PDF-a. Nema zaobilaženja prijave,
pretplate ili zaštite od automatizovanog pristupa.

PDF sadrži izvor i vrijeme preuzimanja, čuva dijakritiku i osnovni redoslijed
odlomaka. Složene tabele pretvara u tekst, slike i originalni raspored ne prenosi.
Pronađeni članci nisu dokaz da je tekst potpun: provjerite rezultat prema izvorniku.
Izvoz ne potvrđuje pravnu važnost niti zamjenjuje službenu objavu.

Ako sajt promijeni HTML, prilagodite selektore u parse_results() i extract().
HTTP greške, nedostupan tekst i nevažeći unosi imaju poruke o grešci.
Koristite u skladu sa uslovima sajta; program nema masovno preuzimanje.

Font DejaVu Sans i njegova licenca nalaze se u fonts/.
