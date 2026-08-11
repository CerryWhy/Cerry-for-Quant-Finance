# value-quant-app

Strumenti di analisi fondamentale in ottica *value investing*.
Al momento il progetto contiene un solo modulo, autonomo e testabile da riga di comando.

## Quality Score (`backend/models/quality_score.py`)

Scarica i bilanci annuali di un titolo con `yfinance` (conto economico, stato
patrimoniale, rendiconto finanziario) e calcola un punteggio di qualita' 0-100.

### Metriche calcolate anno per anno

| Categoria | Metriche |
|---|---|
| Redditivita' | ROIC = NOPAT / (Debito Totale + Equity − Cassa), ROE, ROA, margine lordo/operativo/netto |
| Cassa | Owner Earnings = Utile Netto + D&A − CapEx di mantenimento − Δ Capitale Circolante, e Owner Earnings / Ricavi |
| Solidita' | Debt/Equity, Debt/EBITDA, Interest Coverage, Current Ratio |
| Consistenza | deviazione standard, coefficiente di variazione e % di anni in crescita per ogni serie storica |

### Punteggio finale

Media pesata di tre categorie, con pesi personalizzabili
(default **40%** profittabilita'/ROIC, **30%** consistenza, **30%** solidita' di bilancio).
Ogni componente e' normalizzata 0-100 con soglie esplicite, riportate nell'output
insieme al valore grezzo, cosi' il punteggio resta ispezionabile.
Se una categoria non e' calcolabile, il suo peso viene ridistribuito sulle altre.

### Uso

```bash
pip install -r requirements.txt

python backend/models/quality_score.py            # AAPL di default
python backend/models/quality_score.py MSFT KO    # piu' ticker
python backend/models/quality_score.py --self-test
```

Da codice:

```python
from backend.models.quality_score import calculate_quality_score, format_report

result = calculate_quality_score("AAPL", weights={"profitability": 0.5,
                                                 "consistency": 0.25,
                                                 "balance_sheet": 0.25})
print(result["quality_score"], result["rating"])
print(format_report(result))
```

Il dizionario restituito (serializzabile in JSON) contiene: `quality_score`, `rating`,
`category_scores` con il dettaglio delle componenti, `metrics` anno per anno,
`consistency`, `averages` e `data_quality`.

### Dati stimati e mancanti

Il modulo non solleva eccezioni sui dati incompleti: calcola quello che puo' e
registra tutto in `data_quality`, distinguendo fra `estimated` (approssimazioni) e
`missing` (dati assenti). Le approssimazioni tipiche sono:

- **CapEx di mantenimento**: non e' separabile in bilancio, si usa il **CapEx totale**
  come proxy — gli Owner Earnings risultano quindi prudenziali;
- EBIT approssimato col reddito operativo, o stimato come utile ante imposte + oneri finanziari;
- EBITDA stimato come EBIT + D&A quando non riportato;
- debito totale ricostruito come debito a breve + lungo termine;
- Δ capitale circolante stimata dallo stato patrimoniale se manca nel rendiconto finanziario;
- aliquota fiscale effettiva sostituita col default (25%) quando anomala o non calcolabile.

**Nota sullo storico:** il modulo richiede 10 esercizi, ma yfinance ne espone
tipicamente 4-5 per ticker. Vengono usati quelli disponibili e il numero effettivo
e' riportato in `data_quality.years_available`.

### Test

```bash
python tests/test_quality_score.py    # offline, bilanci sintetici, nessuna rete
```
