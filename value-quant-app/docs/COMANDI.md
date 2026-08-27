# Comandi — guida rapida

Riferimento pratico a `run_analysis.py`: tutte le opzioni, le combinazioni che servono
davvero, e cosa controllare quando un numero sembra strano.

---

## Sintassi

```bash
python run_analysis.py [TICKER...] [opzioni]
```

Senza ticker analizza `AAPL`. I ticker si separano con uno spazio, maiuscole o minuscole
è indifferente. Le azioni con classi multiple usano il trattino: `BRK-B`, non `BRK.B`.

> **Su Windows/PowerShell**: i comandi si scrivono **senza** il punto esclamativo davanti.
> Il `!` serve solo dentro le celle di Colab o Jupyter.

---

## Tutte le opzioni

| Flag | Argomento | Default | Cosa fa |
|---|---|---|---|
| `--buffett` | — | off | Criteri e tasso di sconto di Buffett, più la scorecard |
| `--sector` | `industrial` \| `bank` \| `insurance` \| `reit` \| `utility` \| `energy` | auto | Forza il profilo invece del riconoscimento automatico |
| `--sec` | flag | off | Completa le voci mancanti con i depositi XBRL della SEC (solo emittenti USA) |
| `--overrides` | percorso | — | File JSON/YAML con voci di bilancio inserite a mano |
| `--fdic` | flag | off | CET1 e prestiti deteriorati veri dalle segnalazioni FDIC (banche USA) |
| `--capitalize-rd` | flag | off | Tratta la R&S come investimento invece che come costo (Damodaran) |
| `--rd-life` | intero | `5` | Vita utile della R&S capitalizzata: 5 software, 10 farmaceutico |
| `--backtest` | — | off | Backtest della strategia (serve un minimo di **3 titoli**) |
| `--sweep` | — | off | Sweep dei parametri; attiva da solo `--backtest`. **Lento** |
| `--top` | numero | `5` | Quanti titoli tiene in portafoglio il backtest |
| `--years` | numero | `10` | Esercizi di bilancio da richiedere |
| `--growth` | decimale | auto | Impone la crescita nel DCF (`0.06` = 6%, accetta valori negativi) |
| `--wacc` | decimale | auto | Impone il tasso di sconto (`0.09` = 9%) |
| `--out` | cartella | `output` | Dove salvare grafici e JSON |
| `--lang` | `it` \| `en` | `en` | Lingua delle etichette dei grafici |
| `--no-charts` | — | off | Salta i grafici: molto più veloce |
| `--show` | — | off | Apre i grafici a schermo oltre a salvarli |
| `--json` | — | off | Salva `analisi.json` con tutti i risultati |
| `--demo` | — | off | Dati sintetici, senza rete: serve a vedere l'output |
| `-h`, `--help` | — | — | Stampa l'aiuto ed esce |

---

## Ricette

### Analizzare un titolo

```bash
python run_analysis.py KO                 # analisi completa + grafici
python run_analysis.py KO --no-charts     # solo i report a schermo, molto più veloce
python run_analysis.py KO --buffett        # con i criteri di Berkshire e la scorecard
```

### Confrontare più titoli

```bash
python run_analysis.py KO PG WMT MCD
```

Aggiunge la tabella di riepilogo, la heatmap dei punteggi e la matrice qualità/sconto.

> Confronta titoli **dello stesso profilo**. Un 82 di una banca e un 82 di un industriale
> non sono la stessa cosa: sono misurazioni su scale diverse. La colonna *Profilo* nella
> tabella di riepilogo ti dice quale è stato applicato a ciascuno.

### Stress test — l'uso più utile di tutti

Quando un fair value sembra troppo generoso, rilancia imponendo ipotesi che ritieni
realistiche e guarda quanto resta del margine:

```bash
python run_analysis.py STLA --growth 0 --no-charts       # e se non crescesse più?
python run_analysis.py BZU.MI --growth -0.05 --no-charts # e se calasse del 5% l'anno?
python run_analysis.py KO --wacc 0.12 --no-charts        # e se il capitale costasse di più?
```

Se lo sconto sopravvive, la tesi ha basi. Se evapora, sai che dipendeva tutta da
quell'ipotesi.

### Backtest della strategia

```bash
python run_analysis.py KO PG WMT MCD JNJ --backtest              # minimo 3 titoli
python run_analysis.py KO PG WMT MCD JNJ --backtest --top 3      # portafoglio da 3
python run_analysis.py KO PG WMT MCD JNJ --sweep                 # + griglia di parametri
```

`--sweep` rifà il backtest per ogni cella della griglia (25 esecuzioni): su un universo
ampio possono volerci minuti.

### Quando yfinance non trova una voce

Non sono "tanti dati mancanti": sono poche voci ad alto impatto. Misurato sul modello:

| Voce mancante | Cosa fa cadere |
|---|---|
| `Net Loan` | impieghi/depositi e costo del credito → copertura della categoria credito dal 55% al 20% |
| CET1, NPL (mai in bilancio) | la categoria patrimoniale si ferma al 55%: serve `--fdic` |
| `Total Debt` | 3 regole Buffett su 7 |
| `Goodwill` | il capitale tangibile diventa una stima |
| storia di 4-5 anni | consistenza debole, R&S capitalizzata che estrapola |

Due strade, cumulabili:

```bash
python run_analysis.py JPM --sec                          # SEC EDGAR, emittenti USA
python run_analysis.py ENI.MI --overrides dati/miei.json  # a mano, qualunque mercato
python run_analysis.py JPM --sec --overrides dati/miei.json
```

**`--sec`** legge i depositi XBRL dalla SEC: gratuito, ufficiale, nessuna chiave API, e
porta **10+ anni di storia** invece di 4-5. La SEC richiede di identificarsi, quindi
prima del primo uso:

```bash
# Windows PowerShell
$env:SEC_USER_AGENT = "Nome Cognome nome@dominio.it"
# Linux / macOS / Colab
export SEC_USER_AGENT="Nome Cognome nome@dominio.it"
```

Senza questa variabile la SEC risponde `403` e il modello lo dichiara invece di fallire
in silenzio. I dati scaricati restano in cache per una settimana (`SEC_CACHE_DIR` per
cambiare cartella). Copre solo chi deposita presso la SEC: emittenti americani e ADR con
20-F, non Borsa Italiana.

**`--fdic`** aggiunge i **ratios di vigilanza veri** per le banche americane: CET1,
Tier 1, leverage ratio e prestiti deteriorati, che non stanno nei bilanci consolidati.
Sono componenti del profilo bancario: senza di essi la categoria patrimoniale copre il
55% del peso previsto e lo dichiara, con essi arriva al 100%.

```bash
python run_analysis.py JPM --fdic
```

C'è una difficoltà reale: **la holding quotata non è l'entità assicurata**. JPMorgan
Chase & Co. è la holding; chi deposita alla FDIC è JPMorgan Chase Bank, N.A., con un
proprio numero di certificato, e i gruppi grandi controllano più banche. Il modello cerca
per nome e dichiara quale istituto ha scelto, ma la strada affidabile è trovare il
certificato una volta e fissarlo:

```bash
python backend/models/datasources.py --fdic-search jpmorgan     # elenca i candidati
python backend/models/datasources.py --fdic 628                 # verifica i ratios
```

poi nel file di override, accanto alle voci di bilancio:

```json
{"JPM": {"fdic_cert": 628}}
```

**Se i ratios risultano tutti "non disponibile"**, i codici del Call Report sono cambiati:
il comando `--fdic 628` stampa i campi ricevuti che potrebbero corrispondere, e la mappa
si aggiorna in `FDIC_FIELDS` dentro `backend/models/datasources.py`. È una scelta
deliberata: i codici FDIC non sono verificabili offline, e una mappa sbagliata in silenzio
sarebbe peggio di un errore dichiarato.

**`--overrides`** prende un file JSON con le voci scritte a mano — vedi
`dati/override-esempio.json`. Le etichette sono quelle di yfinance, si copiano dal
bilancio:

```json
{
  "JPM": {
    "balance_sheet": {
      "Net Loan": {"2024": 1310000000000, "2023": 1250000000000}
    }
  }
}
```

Funziona su qualunque mercato e senza rete. Precedenza: **override > SEC > yfinance**. La
SEC riempie solo i buchi e non sostituisce ciò che c'è già; quando le due fonti divergono
di oltre il 5% su uno stesso numero la differenza viene dichiarata invece di essere
risolta d'ufficio. Ogni riga aggiunta compare in `QUALITA' DEL DATO` con la sua
provenienza.

### REIT e immobiliari

Per un REIT l'ammortamento degli immobili e' una finzione contabile: un palazzo ben
tenuto non perde valore. L'utile netto ne esce schiacciato, e con lui ROE e margine
netto. Il profilo usa **FFO** e **AFFO**, le due misure del settore:

```bash
python run_analysis.py O SPG                        # profilo REIT automatico
python run_analysis.py O --sector industrial        # per vedere quanto cambia il metro
python run_analysis.py VNO --sector reit            # forzato a mano
```

Il riconoscimento scatta se gli immobili superano il 40% dell'attivo. Se il bilancio non
espone una voce immobiliare separata, il ripiego e' euristico (immobilizzazioni oltre il
70% dell'attivo **e** ammortamenti oltre il 20% dei ricavi) e viene dichiarato: su una
utility o un industriale ad alta intensita' di capitale puo' sbagliare, e in quel caso si
forza con `--sector industrial`.

Nella valutazione il DCF sconta gli **AFFO** invece degli Owner Earnings, e l'EPV esce
dalla sintesi: assume crescita zero, mentre i canoni seguono l'inflazione.

### Utility regolate ed energia

Due settori in cui le metriche standard misurano qualcosa che non è quello che sembra,
per ragioni opposte.

In una **utility regolata** il rendimento non lo decide il mercato ma il regolatore, che
autorizza un ROE (tipicamente 9-10,5%) sul capitale investito nella rete. Un ROIC basso
non è debolezza competitiva e uno alto non sarebbe un moat: sarebbe un'anomalia destinata
a essere riportata in tariffa. Le domande diventano: il rendimento concesso viene
conseguito, la rate base cresce, il debito è sostenibile (`FFO / debito`, la metrica che
usano le agenzie di rating).

In un **E&P** l'utile dell'anno misura in buona parte dove stava il prezzo del greggio, e
l'attivo si consuma mentre lo si sfrutta: chi non rimpiazza le riserve può mostrare utili
eccellenti per anni — sono gli utili della liquidazione. Il profilo usa **EBITDAX** (che
neutralizza la scelta contabile fra *successful efforts* e *full cost*), il margine di
cassa, `Debt/EBITDAX` e `CapEx / flusso operativo`.

```bash
python run_analysis.py NEE DUK SO                   # utility, riconosciute da sole
python run_analysis.py EOG DVN                      # E&P
python run_analysis.py NEE --sector industrial      # per vedere quanto cambia il metro
```

Il riconoscimento usa due voci **esclusive**: gli *attivi regolatori* (costi che il
regolatore ha autorizzato a recuperare in tariffa) esistono solo dove la tariffa è
amministrata, la *spesa di esplorazione* solo in chi cerca idrocarburi. Se il bilancio non
le espone, `--sec` spesso le recupera dai depositi XBRL; altrimenti si forza con
`--sector`.

**Un limite che il profilo dichiara:** per l'E&P riserve provate, produzione e PV-10 non
stanno nei prospetti finanziari — vivono nelle tabelle supplementari del 10-K. Senza di
essi il modello misura la generazione di cassa e la leva, non il valore delle riserve, che
per un E&P è l'attivo principale. Per la utility mancano l'*allowed ROE* e la rate base
ufficiale, che vivono nei procedimenti tariffari: la rate base è approssimata dalle
immobilizzazioni nette.

### Banche, assicurazioni, holding

Il profilo si riconosce da solo dal **peso** delle voci di bilancio: banca se i depositi
superano il 20% dell'attivo o il margine di interesse il 30% dei ricavi; assicurazione se
i premi superano il 20% dei ricavi o le riserve tecniche il 10% dell'attivo.

```bash
python run_analysis.py JPM BAC C                    # profilo bancario automatico
python run_analysis.py BRK-B                        # assicurazione/holding automatica
python run_analysis.py BRK-B --sector insurance     # forzato a mano
python run_analysis.py JPM --sector industrial      # per vedere quanto cambia il metro
```

La riga `Profilo di analisi` in testa al report dice quale profilo è stato applicato, e
`QUALITA' DEL DATO` dice perché. Se un marcatore era presente ma troppo piccolo per
contare — succede con gli industriali ricchi di liquidità, che espongono un margine di
interesse dell'1% dei ricavi — la nota lo dichiara. Se il profilo scelto non convince,
`--sector` lo forza.

### Aziende che vivono di ricerca

Spesare la R&S gonfia il ROIC (il capitale investito ignora anni di sviluppo) e deprime
gli Owner Earnings (la ricerca di crescita è sottratta per intero dall'utile). Con
`--capitalize-rd` la ricerca diventa un investimento ammortizzato:

```bash
python run_analysis.py GOOGL                         # R&S spesata (principio contabile)
python run_analysis.py GOOGL --capitalize-rd         # R&S capitalizzata su 5 anni
python run_analysis.py PFE --capitalize-rd --rd-life 10   # vita utile del farmaceutico
```

L'uso corretto è lanciarlo **due volte** e leggere la differenza: le soglie di punteggio
sono tarate su bilanci non rettificati, quindi i due punteggi complessivi non sono
confrontabili fra loro. Quello che si confronta è ROIC e Owner Earnings prima e dopo. Il
report aggiunge un blocco `CAPITALIZZAZIONE DELLA R&S` con spesa, ammortamento e asset
anno per anno.

Non serve su chi non fa ricerca: senza la voce in bilancio l'opzione non cambia nulla e
lo dichiara. Sui finanziari viene ignorata.

### Grafici

```bash
python run_analysis.py KO --lang it                 # etichette in italiano
python run_analysis.py KO --show                    # apre le finestre oltre a salvare
python run_analysis.py KO --out risultati_2026      # cartella di destinazione
python run_analysis.py --demo                       # tutti i grafici con dati sintetici
```

### Salvare i dati grezzi

```bash
python run_analysis.py KO PG WMT --json --out risultati
```

Scrive `risultati/analisi.json` con punteggi, metriche anno per anno, valutazione,
scorecard e (se richiesto) il backtest.

### Il comando completo

```bash
python run_analysis.py KO PG WMT MCD JNJ --buffett --backtest --json --out risultati
```

---

## Moduli singoli

Ogni modulo funziona anche da solo:

```bash
python backend/models/quality_score.py AAPL MSFT
python backend/models/valuation.py AAPL --growth 0.06 --wacc 0.09
python backend/models/backtest.py AAPL MSFT KO PG JNJ V MA HD
python backend/models/visualize.py --lang it        # demo di tutti i grafici
```

E i test, che girano offline senza rete:

```bash
python tests/test_quality_score.py
python tests/test_valuation.py
python tests/test_backtest.py
python tests/test_sectors.py
python tests/test_buffett.py
python tests/test_research.py
python tests/test_reit.py
python tests/test_datasources.py
python tests/test_utility_energy.py
python tests/test_pipeline.py
```

---

## Su Google Colab

Dentro una cella di notebook i comandi vogliono il `!` davanti:

```python
!git clone -b claude/quality-score-module-s3347v https://github.com/CerryWhy/Cerry-for-Quant-Finance.git
%cd Cerry-for-Quant-Finance/value-quant-app
!pip install -q yfinance
!python run_analysis.py KO --buffett

from IPython.display import Image
Image("output/ko_tearsheet.png")
```

---

## Cosa viene prodotto

Con `KO PG --backtest --json`:

```
output/
├── ko_qualita.png              radar del profilo di qualità
├── ko_storico.png              serie storiche affiancate
├── ko_valutazione.png          football field dei metodi
├── ko_sensitivita.png          superficie 3D di sensitività
├── ko_tearsheet.png            pagina riassuntiva
├── pg_*.png                    gli stessi per il secondo titolo
├── universo_qualita.png        heatmap dei punteggi
├── universo_qualita_valore.png matrice qualità / sconto
├── backtest_equity.png         curva di capitale vs benchmark
└── analisi.json                tutti i risultati in forma strutturata
```

I nomi dei file usano il ticker in minuscolo.

---

## Diagnostica: quando un numero sembra assurdo

Prima di credere a un risultato strano, controlla in quest'ordine:

| # | Cosa guardare | Cosa significa se è storto |
|---|---|---|
| 1 | `Prezzo di mercato` | Corrisponde a quello che sai? Le borse europee quotano a volte in centesimi |
| 2 | `Esercizi analizzati` | Con 2-3 anni la consistenza non ha significato statistico |
| 3 | `media pesata di N metodi` | Se `N = 1` il fair value non ha controlli incrociati |
| 4 | `Owner Earnings base` | È positivo e plausibile rispetto agli utili che conosci? |
| 5 | `QUALITA' DEL DATO` | Quante voci mancano? Su holding e conglomerati saranno molte |
| 6 | `Profilo di analisi` | È quello giusto per quell'azienda? |
| 7 | `Copertura` | Sotto il 60% il punteggio poggia su una parte delle metriche previste |
| 8 | Punteggi per categoria | Quale trascina giù il totale, e ha senso per quel tipo di azienda? |

**Il caso ciclico** merita un controllo a parte. Guarda la tabella *Metriche anno per anno*:
se ROIC e margini degli ultimi due anni sono ai massimi della serie, stai normalizzando su
un picco — e in quel caso **sia il punteggio sia lo sconto sono gonfiati dalla stessa
causa**. Non sono due conferme indipendenti: è una sola ipotesi contata due volte.

**Il margine di sicurezza esplode** quando il fair value tende a zero: si calcola
dividendo per il fair value, quindi un `−13.000%` non significa "cara 130 volte", significa
che il denominatore è quasi nullo.

**Il punteggio altissimo su un bilancio incompleto** è il caso più insidioso, perché non
sembra un errore. Ogni categoria riporta su quante componenti è calcolata (`su 5/6
componenti (65% del peso)`) e il totale riporta la copertura complessiva. Quando le
componenti mancanti sono le più severe — il ROIC, il debito — quelle che restano sono le
migliori, e il punteggio sale invece di scendere. Nella tabella di riepilogo un asterisco
accanto al punteggio segnala la stessa cosa: `91.9*` e `78.3` non sono confrontabili.

---

## Rigenerare il PDF della metodologia

`docs/METODOLOGIA.md` è la fonte; il PDF è un artefatto derivato e va rifatto quando il
Markdown cambia:

```bash
pip install markdown
python docs/md2pdf.py                    # -> docs/Metodologia-value-quant-app.pdf
python docs/md2pdf.py --html             # si ferma all'HTML, per rivedere il CSS
```

Serve un browser headless per l'impaginazione. Con **Playwright** installato
(`pip install playwright && playwright install chromium`) il PDF esce con i numeri di
pagina; con il solo Chrome o Chromium di sistema esce senza, perché la riga di comando di
Chromium non espone i template di piede — lo script lo dichiara quando ripiega. Se il
browser non viene trovato da solo:

```bash
python docs/md2pdf.py --browser "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

---

## Errori frequenti

| Sintomo | Causa | Rimedio |
|---|---|---|
| `!pip non riconosciuto` | il `!` serve solo su Colab | toglilo nel terminale |
| `python non riconosciuto` (Windows) | l'eseguibile si chiama `py` | usa `py run_analysis.py ...` |
| `ModuleNotFoundError` | dipendenze non installate, o su un Python diverso | `py -m pip install -r requirements.txt` |
| Backtest saltato | meno di 3 titoli | aggiungi ticker |
| Tutto `n/d` | ticker inesistente o rete bloccata | controlla il ticker; le classi multiple usano il trattino |
| Grafici non generati | matplotlib assente | `pip install matplotlib` |

---

## Approfondimenti

- **[Metodologia completa](METODOLOGIA.md)** — cosa calcola ogni modulo, con quali formule,
  e come si leggono i numeri ([PDF](Metodologia-value-quant-app.pdf))
- **[README](../README.md)** — panoramica del progetto
