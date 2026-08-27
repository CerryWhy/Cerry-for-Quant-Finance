"""Genera il PDF della metodologia da ``METODOLOGIA.md``.

Perche' uno script nel repo e non un comando a mano
---------------------------------------------------
Il PDF e' un artefatto derivato: ogni volta che il modello cambia, il Markdown cambia e
il PDF resta indietro. Tenere il generatore nel repository fa due cose: rende il PDF
**riproducibile** da chiunque con un comando, e rende visibile — in diff — ogni scelta
tipografica, invece di seppellirla in una sessione di lavoro che nessuno ritrova.

Uso
---
::

    python docs/md2pdf.py                        # docs/METODOLOGIA.md -> docs/Metodologia-value-quant-app.pdf
    python docs/md2pdf.py --html                 # si ferma all'HTML, utile per rivedere il CSS
    python docs/md2pdf.py --input ALTRO.md --output altro.pdf

Dipendenze
----------
``markdown`` per la conversione, piu' un browser headless per l'impaginazione::

    pip install markdown
    pip install playwright && playwright install chromium     # consigliato

Il PDF viene prodotto in due modi, in ordine di preferenza:

1. **Playwright** — l'unico che dia numeri di pagina veri, perche' i piedi di pagina si
   passano al protocollo di stampa. Se e' installato viene usato;
2. **Chromium da riga di comando** (``--print-to-pdf``) — funziona ovunque ci sia Chrome
   o Chromium, ma senza numeri di pagina: la riga di comando non espone i template di
   intestazione. Lo script lo dichiara quando ripiega su questa strada.

Su Windows lo script cerca Chrome nei percorsi consueti; si puo' forzare con
``--browser "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"``.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional, Sequence, Tuple

try:
    import markdown
except ImportError:  # pragma: no cover - dipendenza opzionale
    markdown = None  # type: ignore[assignment]


HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_INPUT = os.path.join(HERE, "METODOLOGIA.md")
DEFAULT_OUTPUT = os.path.join(HERE, "Metodologia-value-quant-app.pdf")

TITLE = "Metodologia"
SUBTITLE = "Come il modello misura la qualità di un'azienda e ne stima il valore"
PROJECT = "value-quant-app"

#: Percorsi in cui cercare un browser headless, in ordine.
BROWSER_CANDIDATES = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


# ---------------------------------------------------------------------------
# Tipografia
# ---------------------------------------------------------------------------
#
# Le scelte, in breve:
#
# * corpo in **Charter** (Matthew Carter, 1987), disegnata per restare leggibile dove la
#   risoluzione e' bassa: e' la ragione per cui funziona bene in stampa. Su Windows e
#   macOS il ripiego e' Georgia, che ha la stessa vocazione;
# * titoli in un sans neutro, per contrasto con la serif del corpo senza che il documento
#   diventi decorativo. Una metodologia si legge, non si ammira;
# * misura di riga intorno ai 90 caratteri: piu' larga stanca, piu' stretta spezza le
#   tabelle, che qui sono il contenuto principale;
# * un solo colore d'accento (blu petrolio), usato per gerarchia e non per enfasi. Il
#   rosso e' riservato ai numeri negativi.

CSS = """
:root {
  --ink:        #16181d;
  --ink-soft:   #454b57;
  --ink-faint:  #7c8494;
  --accent:     #14505f;
  --accent-soft:#e8f0f2;
  --amber:      #9a6407;
  --rule:       #d9dde4;
  --rule-soft:  #eceef2;
  --surface:    #f7f8fa;
}

@page {
  size: A4;
  margin: 22mm 19mm 20mm 19mm;
}
@page :first {
  margin: 0;
}

* { box-sizing: border-box; }

html {
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

body {
  font-family: "Bitstream Charter", Charter, Georgia, "Times New Roman", serif;
  font-size: 10.4pt;
  line-height: 1.62;
  color: var(--ink);
  margin: 0;
  hyphens: auto;
  -webkit-hyphens: auto;
  text-align: justify;
}

/* --- Copertina --------------------------------------------------------- */

.cover {
  position: relative;
  height: 297mm;
  padding: 42mm 24mm 22mm 24mm;
  page-break-after: always;
  display: flex;
  flex-direction: column;
  text-align: left;
}
.cover::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 11mm;
  background: var(--accent);
}
.cover-project {
  font-family: "DejaVu Sans Mono", "Liberation Mono", monospace;
  font-size: 9.5pt;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 26mm;
}
.cover-title {
  font-family: "Liberation Sans", Helvetica, Arial, sans-serif;
  font-size: 46pt;
  line-height: 1.02;
  font-weight: 700;
  letter-spacing: -0.022em;
  color: var(--ink);
  margin: 0 0 7mm 0;
}
.cover-rule {
  width: 34mm;
  height: 2.4pt;
  background: var(--amber);
  margin-bottom: 8mm;
}
.cover-subtitle {
  font-size: 14pt;
  line-height: 1.42;
  color: var(--ink-soft);
  max-width: 132mm;
  margin: 0;
  text-align: left;
}
.cover-spacer { flex: 1; }
.cover-modules {
  border-top: 0.6pt solid var(--rule);
  padding-top: 6mm;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4mm 10mm;
  font-size: 9.6pt;
  color: var(--ink-soft);
  text-align: left;
}
.cover-modules strong {
  font-family: "Liberation Sans", Helvetica, Arial, sans-serif;
  display: block;
  color: var(--accent);
  font-size: 9pt;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: 0.8mm;
}
.cover-meta {
  margin-top: 8mm;
  font-size: 9pt;
  color: var(--ink-faint);
  font-family: "DejaVu Sans Mono", "Liberation Mono", monospace;
  text-align: left;
}

/* --- Titoli ------------------------------------------------------------ */

h1, h2, h3, h4 {
  font-family: "Liberation Sans", Helvetica, Arial, sans-serif;
  color: var(--ink);
  text-align: left;
  hyphens: none;
  -webkit-hyphens: none;
  page-break-after: avoid;
  break-after: avoid;
}

h1 {
  font-size: 21pt;
  letter-spacing: -0.015em;
  margin: 0 0 6mm 0;
  padding-bottom: 3mm;
  border-bottom: 1.6pt solid var(--accent);
  page-break-before: always;
}
h1:first-of-type { page-break-before: avoid; }

/* Il titolo di copertina e' un h1 ma non ne vuole il filetto: ce l'ha suo, in ambra. */
h1.cover-title {
  border-bottom: none;
  padding-bottom: 0;
  page-break-before: avoid;
}

h2 {
  font-size: 15.4pt;
  letter-spacing: -0.012em;
  margin: 9mm 0 3.5mm 0;
  padding-bottom: 2mm;
  border-bottom: 0.6pt solid var(--rule);
  page-break-before: always;
}

h3 {
  font-size: 11.8pt;
  color: var(--accent);
  margin: 7mm 0 2.5mm 0;
}

h4 {
  font-size: 10.6pt;
  color: var(--ink-soft);
  margin: 5mm 0 2mm 0;
  font-weight: 700;
}

p { margin: 0 0 3.2mm 0; orphans: 2; widows: 2; }

strong { font-weight: 700; }
em { font-style: italic; }

a { color: var(--accent); text-decoration: none; }

hr {
  border: none;
  border-top: 0.6pt solid var(--rule-soft);
  margin: 7mm 0;
}

/* --- Liste ------------------------------------------------------------- */

ul, ol { margin: 0 0 3.4mm 0; padding-left: 6.5mm; }
li { margin-bottom: 1.5mm; }
li > ul, li > ol { margin-top: 1.5mm; margin-bottom: 0; }
li::marker { color: var(--ink-faint); }

/* --- Tabelle: il contenuto principale di questo documento -------------- */

table {
  width: 100%;
  border-collapse: collapse;
  margin: 3.5mm 0 5mm 0;
  font-size: 9.1pt;
  font-family: "Liberation Sans", Helvetica, Arial, sans-serif;
  page-break-inside: avoid;
  break-inside: avoid;
  text-align: left;
}
thead th {
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 8.4pt;
  font-weight: 700;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  text-align: left;
  padding: 2mm 2.4mm;
  border-bottom: 1pt solid var(--accent);
  vertical-align: bottom;
}
tbody td {
  padding: 1.8mm 2.4mm;
  border-bottom: 0.5pt solid var(--rule-soft);
  vertical-align: top;
  line-height: 1.45;
}
tbody tr:last-child td { border-bottom: 0.8pt solid var(--rule); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
thead th.num { text-align: right; }
tbody tr:nth-child(even) { background: #fbfcfd; }

/* --- Codice e formule -------------------------------------------------- */

code {
  font-family: "DejaVu Sans Mono", "Liberation Mono", Consolas, monospace;
  font-size: 8.8pt;
  background: var(--surface);
  padding: 0.4mm 1.1mm;
  border-radius: 1.6pt;
  color: var(--ink);
}

pre {
  background: var(--surface);
  border-left: 2.2pt solid var(--accent);
  padding: 3.2mm 4mm;
  margin: 3.5mm 0 4.5mm 0;
  font-size: 8.8pt;
  line-height: 1.5;
  overflow-x: hidden;
  white-space: pre-wrap;
  page-break-inside: avoid;
  break-inside: avoid;
  text-align: left;
}
pre code { background: none; padding: 0; font-size: inherit; }

blockquote {
  margin: 3.5mm 0;
  padding: 0.5mm 0 0.5mm 4.5mm;
  border-left: 2.2pt solid var(--amber);
  color: var(--ink-soft);
  font-style: italic;
}
blockquote p:last-child { margin-bottom: 0; }

/* --- Indice ------------------------------------------------------------ */

.toc {
  page-break-after: always;
  padding-top: 2mm;
}
.toc h2 {
  page-break-before: avoid;
  margin-top: 0;
}
.toc ol { list-style: none; padding-left: 0; counter-reset: toc; }
.toc li {
  counter-increment: toc;
  display: flex;
  align-items: baseline;
  gap: 2.5mm;
  padding: 1.6mm 0;
  border-bottom: 0.4pt dotted var(--rule);
  font-family: "Liberation Sans", Helvetica, Arial, sans-serif;
  font-size: 10pt;
}
.toc li::before {
  content: counter(toc);
  font-family: "DejaVu Sans Mono", "Liberation Mono", monospace;
  font-size: 8.6pt;
  color: var(--accent);
  min-width: 6mm;
}
.toc a { color: var(--ink); }

/* --- Dettagli ---------------------------------------------------------- */

/* I numeri negativi in tabella si leggono prima se hanno un colore proprio. */
td.negative { color: #9b2226; }

/* Le note fra parentesi quadre nel testo originale sono riferimenti: attenuarle. */
.dim { color: var(--ink-faint); }
"""

FOOTER_TEMPLATE = """
<div style="width:100%;font-family:Helvetica,Arial,sans-serif;font-size:7.6pt;
            color:#7c8494;padding:0 19mm;display:flex;justify-content:space-between;">
  <span>{project} &middot; {title}</span>
  <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>
"""

HEADER_TEMPLATE = '<div style="height:0"></div>'


# ---------------------------------------------------------------------------
# Conversione
# ---------------------------------------------------------------------------


NUMERIC_CELL = re.compile(
    r"^\s*(?:[\u2212\u2013\u2014-]?\s*[\u20ac$\u00a3]?\s*[\d.,]+\s*"
    r"(?:%|x|B|M|K|mld|mln|bp|pt|anni|anno)?"
    r"(?:\s*[-\u2013\u2014]\s*[\d.,]+\s*(?:%|x)?)?)\s*$"
)

#: Celle che non portano informazione di allineamento: un dato assente sta bene in una
#: colonna di numeri come in una di testo, e non deve spostare la decisione.
NEUTRAL_CELL = frozenset({"", "—", "–", "-", "n/d", "n.d.", "\u2014"})

#: Quota di celle numeriche oltre la quale la colonna e' considerata numerica.
NUMERIC_COLUMN_SHARE = 0.6

_CELL = re.compile(r"<(t[dh])((?:\s[^>]*)?)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_ROW = re.compile(r"<tr((?:\s[^>]*)?)>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TABLE = re.compile(r"<table((?:\s[^>]*)?)>(.*?)</table>", re.DOTALL | re.IGNORECASE)


def _plain(cell_html: str) -> str:
    """Testo di una cella, senza marcatura."""
    return html.unescape(re.sub(r"<[^>]+>", "", cell_html)).strip()


def _align_numeric_columns(body: str) -> str:
    """Allinea a destra le colonne di numeri, header compreso.

    Il Markdown non dichiara l'allineamento delle colonne, e in un documento fatto di
    soglie e percentuali le cifre allineate a sinistra si confrontano male: l'occhio deve
    poter scorrere la colonna.

    La decisione e' **per colonna** e non per cella. Deciderla cella per cella sembra piu'
    preciso ed e' peggio: in una colonna come "35% (profittabilita')" / "20%" / "15%" la
    prima cella resterebbe a sinistra e le altre a destra, e la colonna si legge peggio di
    come si leggeva prima. Una colonna ha un allineamento, non tanti.

    Una colonna e' numerica se almeno il 60% delle sue celle informative lo e'. I trattini
    e i "n/d" non contano: un dato assente non dice nulla sull'allineamento.
    """
    def process_table(match: re.Match) -> str:
        attributes, content = match.group(1), match.group(2)
        rows = list(_ROW.finditer(content))
        if not rows:
            return match.group(0)

        parsed: List[List[Tuple[str, str, str]]] = []
        for row in rows:
            parsed.append([
                (cell.group(1).lower(), cell.group(2), cell.group(3))
                for cell in _CELL.finditer(row.group(2))
            ])

        width = max((len(row) for row in parsed), default=0)
        numeric_columns = set()
        for index in range(width):
            informative = 0
            numeric = 0
            for row in parsed:
                if index >= len(row) or row[index][0] != "td":
                    continue
                text = _plain(row[index][2])
                if text in NEUTRAL_CELL:
                    continue
                informative += 1
                if NUMERIC_CELL.match(text):
                    numeric += 1
            if informative >= 2 and numeric / informative >= NUMERIC_COLUMN_SHARE:
                numeric_columns.add(index)

        if not numeric_columns:
            return match.group(0)

        rebuilt: List[str] = []
        cursor = 0
        for row, cells in zip(rows, parsed):
            rebuilt.append(content[cursor:row.start()])
            inner = row.group(2)
            pieces: List[str] = []
            position = 0
            for index, cell in enumerate(_CELL.finditer(inner)):
                pieces.append(inner[position:cell.start()])
                tag, attrs, value = cell.group(1), cell.group(2), cell.group(3)
                if index in numeric_columns:
                    classes = ["num"]
                    text = _plain(value)
                    if text.startswith(("\u2212", "-", "\u2013")) and any(c.isdigit() for c in text):
                        classes.append("negative")
                    attrs = f'{attrs} class="{" ".join(classes)}"'
                pieces.append(f"<{tag}{attrs}>{value}</{tag}>")
                position = cell.end()
            pieces.append(inner[position:])
            rebuilt.append(f"<tr{row.group(1)}>{''.join(pieces)}</tr>")
            cursor = row.end()
        rebuilt.append(content[cursor:])
        return f"<table{attributes}>{''.join(rebuilt)}</table>"

    return _TABLE.sub(process_table, body)


def _cover(subtitle: str, source_lines: int) -> str:
    moduli = (
        ("Modulo 0", "Profili di settore: industriale, banca, assicurazione, REIT"),
        ("Modulo 1", "Quality Score: redditività, consistenza, solidità"),
        ("Modulo 2", "Valuation: DCF, reverse DCF, EPV, residual income"),
        ("Modulo 3", "Backtest point-in-time della strategia quality + value"),
    )
    blocchi = "".join(
        f"<div><strong>{nome}</strong>{html.escape(testo)}</div>" for nome, testo in moduli
    )
    return f"""
<section class="cover">
  <div class="cover-project">{html.escape(PROJECT)}</div>
  <h1 class="cover-title">{html.escape(TITLE)}</h1>
  <div class="cover-rule"></div>
  <p class="cover-subtitle">{html.escape(subtitle)}</p>
  <div class="cover-spacer"></div>
  <div class="cover-modules">{blocchi}</div>
  <div class="cover-meta">{source_lines} righe di documentazione &middot; generato da docs/md2pdf.py</div>
</section>
"""


def _wrap_toc(body: str) -> str:
    """Isola l'indice in un contenitore, per dargli una pagina e uno stile propri.

    La sezione va dal titolo "Indice" al primo separatore orizzontale successivo. Il
    separatore si cerca con una regex e non con una stringa fissa perche' Markdown emette
    ``<hr>`` in HTML5 e ``<hr />`` in XHTML, e a seconda della versione della libreria si
    ottiene l'uno o l'altro: cercarne uno solo faceva perdere il contenitore in silenzio.
    """
    heading = re.search(r"<h2[^>]*>\s*Indice\s*</h2>", body, flags=re.IGNORECASE)
    if heading is None:
        return body
    separator = re.search(r"<hr\s*/?>", body[heading.end():], flags=re.IGNORECASE)
    if separator is None:
        return body
    start, end = heading.start(), heading.end() + separator.start()
    return (
        f'{body[:start]}<div class="toc">{body[start:end]}</div>{body[end:]}'
    )


def _strip_leading_title(text: str) -> str:
    """Toglie il titolo di primo livello: sulla copertina c'e' gia'."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[:index] + lines[index + 1:])
        if line.strip() and not line.startswith(("<!--", "<a ")):
            break
    return text


def convert(source: str) -> str:
    """Markdown -> documento HTML completo, con CSS incorporato."""
    if markdown is None:
        raise SystemExit(
            "Manca il pacchetto 'markdown': eseguire `pip install markdown`."
        )

    text = _strip_leading_title(source)
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "attr_list", "md_in_html", "sane_lists"],
        output_format="html5",
    )
    body = _align_numeric_columns(body)

    body = _wrap_toc(body)

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>{html.escape(TITLE)} &middot; {html.escape(PROJECT)}</title>
<style>{CSS}</style>
</head>
<body>
{_cover(SUBTITLE, len(source.splitlines()))}
<div>
{body}
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Stampa
# ---------------------------------------------------------------------------


def _find_browser(explicit: Optional[str] = None) -> Optional[str]:
    for candidate in ([explicit] if explicit else []) + list(BROWSER_CANDIDATES):
        if not candidate:
            continue
        if os.path.isfile(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _print_with_playwright(
    html_path: str,
    output: str,
    browser_path: Optional[str] = None,
) -> bool:
    """Stampa con Playwright: e' l'unica strada che dia numeri di pagina.

    Se i browser di Playwright non sono installati (o sono di una versione diversa da
    quella attesa dalla libreria) si ripiega sul Chrome di sistema passandolo come
    ``executable_path``: serve la libreria per i piedi di pagina, non il suo browser.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as engine:
            try:
                browser = engine.chromium.launch()
            except Exception:
                fallback = _find_browser(browser_path)
                if fallback is None:
                    raise
                browser = engine.chromium.launch(executable_path=fallback)
            page = browser.new_page()
            page.goto(f"file://{html_path}", wait_until="load")
            page.pdf(
                path=output,
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template=HEADER_TEMPLATE,
                footer_template=FOOTER_TEMPLATE.format(project=PROJECT, title=TITLE),
                margin={"top": "22mm", "bottom": "20mm", "left": "19mm", "right": "19mm"},
            )
            browser.close()
        return True
    except Exception as exc:  # pragma: no cover - dipende dall'ambiente
        print(f"  Playwright non utilizzabile ({exc}); provo con Chromium.")
        return False


def _print_with_chromium(html_path: str, output: str, browser: Optional[str]) -> bool:
    binary = _find_browser(browser)
    if binary is None:
        return False
    command = [
        binary, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--generate-pdf-document-outline",
        f"--print-to-pdf={output}", f"file://{html_path}",
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=180)
    except Exception as exc:  # pragma: no cover - dipende dall'ambiente
        print(f"  Chromium ha fallito: {exc}")
        return False
    print(
        "  Nota: senza Playwright il PDF non ha numeri di pagina — la riga di comando di\n"
        "  Chromium non espone i template di piede. `pip install playwright` li aggiunge."
    )
    return os.path.exists(output)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Genera il PDF della metodologia.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Markdown di partenza.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="PDF di destinazione.")
    parser.add_argument("--html", action="store_true",
                        help="Salva solo l'HTML, senza stampare il PDF.")
    parser.add_argument("--browser", default=None,
                        help="Percorso di Chrome/Chromium, se non viene trovato da solo.")
    options = parser.parse_args(list(argv) if argv is not None else None)

    if not os.path.exists(options.input):
        print(f"File non trovato: {options.input}")
        return 1

    with open(options.input, encoding="utf-8") as handle:
        source = handle.read()

    document = convert(source)
    print(f"Metodologia: {len(source.splitlines())} righe -> {len(document):,} byte di HTML")

    if options.html:
        target = os.path.splitext(options.output)[0] + ".html"
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(document)
        print(f"  scritto {target}")
        return 0

    handle = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    handle.write(document)
    handle.close()
    try:
        if _print_with_playwright(handle.name, options.output, options.browser):
            pass
        elif _print_with_chromium(handle.name, options.output, options.browser):
            pass
        else:
            print(
                "Nessun browser headless disponibile. Installare Chrome o Chromium,\n"
                "oppure `pip install playwright && playwright install chromium`."
            )
            return 1
    finally:
        os.unlink(handle.name)

    size = os.path.getsize(options.output)
    print(f"  scritto {options.output} ({size / 1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
