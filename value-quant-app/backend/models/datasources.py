"""Fonti dati aggiuntive: SEC EDGAR (XBRL) e override manuali.

Perche' esiste questo modulo
----------------------------
yfinance e' gratuito e fa quello che puo', ma su alcune voci **non arriva mai**, e non
sono voci marginali: sono quelle da cui dipendono interi blocchi di giudizio. Misurato
sul modello:

* manca ``Net Loan`` (impieghi netti) -> cadono **due componenti su tre** della categoria
  "capitale e rischio di credito" del profilo bancario: impieghi/depositi e costo del
  credito. La copertura di quella categoria scende al 40%;
* manca ``Total Debt`` -> cadono **tre regole Buffett su sette**: rendimento sul capitale
  tangibile, poco o nessun debito, debito ripagabile con gli Owner Earnings;
* mancano ``Goodwill`` e gli immateriali -> il capitale tangibile diventa una stima;
* la storia si ferma a 4-5 esercizi -> le metriche di consistenza perdono significato e
  la capitalizzazione della R&S deve estrapolare gli anni che non ci sono.

Con **cinque o sei voci per emittente** si copre quasi tutto. Il problema e' mirato, non
sistemico, e per questo vale la pena risolverlo alla fonte invece di aggiungere proxy.

Il principio: arricchire i prospetti, non le formule
---------------------------------------------------
Le fonti di questo modulo **non** producono metriche: producono **righe di bilancio nel
formato di yfinance**, con le etichette che gli alias di ``quality_score`` gia'
riconoscono. Le righe mancanti vengono aggiunte ai tre prospetti, e da quel punto in poi
tutto il resto del modello lavora come sempre.

La conseguenza e' che nessuna formula viene duplicata e nessun calcolo a valle cambia
comportamento: il ROIC, gli Owner Earnings, il residual income e le soglie di punteggio
restano quelli, e vedono semplicemente meno buchi. Anche le derivazioni esistenti
continuano a funzionare da sole — fornendo ``Long Term Debt`` e ``Current Debt`` si
attiva il fallback che ricostruisce il debito totale, senza scrivere una riga in piu'.

Precedenza delle fonti
----------------------
1. **override manuale** — sovrascrive sempre: se lo scrivi a mano e' perche' sai che il
   dato automatico manca o e' sbagliato;
2. **SEC EDGAR** — riempie i buchi, non sostituisce cio' che c'e' gia'. Il modello e'
   tarato e testato su yfinance: cambiare la fonte primaria di numeri che gia' esistono
   richiederebbe di riverificare tutto, mentre riempire i buchi e' additivo e sicuro.
   Quando le due fonti divergono di molto su un dato presente in entrambe, la cosa viene
   **segnalata** invece di essere risolta d'ufficio: e' un'informazione, non un conflitto
   da nascondere;
3. **yfinance** — la base.

Ogni riga aggiunta viene dichiarata in ``data_quality`` con la sua provenienza. Un dato
di origine diversa non e' un problema; un dato di origine ignota si'.

Limiti dichiarati
-----------------
SEC EDGAR copre solo chi deposita presso la SEC: emittenti americani e ADR esteri con
20-F. Per Borsa Italiana e gli altri mercati europei la strada e' l'override manuale.
I tag XBRL non sono uniformi fra emittenti: questo modulo prova piu' tag per ogni voce e
dichiara quale ha funzionato, ma su un emittente con tassonomia inusuale puo' non
trovare nulla.

Uso
---
::

    from models.datasources import enrich_financials

    financials = fetch_financials("JPM")
    financials = enrich_financials(financials, "JPM", overrides_path="dati/miei.json")

Da riga di comando l'arricchimento si attiva con ``--sec`` e ``--overrides FILE``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import pandas as pd
except ImportError:  # pragma: no cover - ambiente senza pandas
    pd = None  # type: ignore[assignment]

try:
    from .quality_score import _DataQuality, _normalize_label, _row_index, _to_float
except ImportError:  # esecuzione come script standalone
    from quality_score import (  # type: ignore[no-redef]
        _DataQuality,
        _normalize_label,
        _row_index,
        _to_float,
    )


__all__ = [
    "SEC_TAGS",
    "enrich_financials",
    "fetch_sec_companyfacts",
    "load_overrides",
    "sec_rows",
]


#: La SEC richiede un User-Agent che identifichi chi chiama, con un contatto. Senza,
#: risponde 403. Si puo' personalizzare con la variabile d'ambiente ``SEC_USER_AGENT``.
DEFAULT_USER_AGENT = "value-quant-app (contatto: impostare SEC_USER_AGENT)"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

#: La SEC chiede di non superare le 10 richieste al secondo.
SEC_MIN_INTERVAL = 0.12

#: Form da cui accettare i dati annuali, in ordine di preferenza.
ANNUAL_FORMS = ("10-K", "20-F", "40-F", "10-K/A", "20-F/A")

#: Durata in giorni entro cui un periodo XBRL e' considerato un esercizio annuale.
#: Gli esercizi 52/53 settimane (molta distribuzione americana) stanno in questa forbice.
ANNUAL_DAYS = (300, 400)

#: Tag XBRL us-gaap per ogni voce, con l'etichetta yfinance da produrre.
#:
#: L'ordine dei tag conta: il primo disponibile vince. Le voci sono quelle che yfinance
#: lascia scoperte piu' spesso, non tutto il bilancio — questo modulo tappa buchi, non
#: sostituisce la fonte.
#:
#: ``statement`` dice in quale prospetto va inserita la riga; ``label`` e' l'etichetta
#: che gli alias di ``quality_score`` e ``sectors`` riconoscono.
SEC_TAGS: Tuple[Dict[str, Any], ...] = (
    # --- Voci bancarie: le piu' costose quando mancano ----------------------
    {
        "field": "net_loans", "statement": "balance_sheet", "label": "Net Loan",
        "tags": (
            "LoansAndLeasesReceivableNetReportedAmount",
            "LoansAndLeasesReceivableNetOfDeferredIncome",
            "NotesReceivableNet",
            "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss",
            "LoansAndLeasesReceivableGrossCarryingAmount",
        ),
    },
    {
        "field": "total_deposits", "statement": "balance_sheet", "label": "Total Deposits",
        "tags": ("Deposits", "DepositsDomestic", "InterestBearingDepositLiabilities"),
    },
    {
        "field": "credit_provision", "statement": "income_statement",
        "label": "Credit Losses Provision",
        "tags": (
            "ProvisionForLoanLeaseAndOtherLosses",
            "ProvisionForCreditLossesExpenseReversal",
            "ProvisionForLoanAndLeaseLosses",
            "AllowanceForCreditLossesChangeInAccountingPrinciple",
        ),
    },
    {
        "field": "net_interest_income", "statement": "income_statement",
        "label": "Net Interest Income",
        "tags": (
            "InterestIncomeExpenseNet",
            "InterestIncomeExpenseAfterProvisionForLoanLoss",
        ),
    },
    {
        "field": "non_interest_expense", "statement": "income_statement",
        "label": "Total Non Interest Expense",
        "tags": ("NoninterestExpense", "OtherNoninterestExpense"),
    },
    {
        "field": "non_interest_income", "statement": "income_statement",
        "label": "Total Non Interest Income",
        "tags": ("NoninterestIncome", "OtherNoninterestIncome"),
    },
    # --- Debito: la voce che fa cadere tre regole Buffett su sette ----------
    # Non esiste un tag unico per il debito totale: si forniscono le componenti e il
    # fallback di extract_fundamentals lo ricostruisce da solo.
    {
        "field": "long_term_debt", "statement": "balance_sheet", "label": "Long Term Debt",
        "tags": (
            "LongTermDebtNoncurrent",
            "LongTermDebt",
            "LongTermNotesPayable",
        ),
    },
    {
        "field": "current_debt", "statement": "balance_sheet", "label": "Current Debt",
        "tags": (
            "LongTermDebtCurrent",
            "DebtCurrent",
            "ShortTermBorrowings",
            "OtherShortTermBorrowings",
        ),
    },
    # --- Capitale tangibile -------------------------------------------------
    {
        "field": "goodwill", "statement": "balance_sheet", "label": "Goodwill",
        "tags": ("Goodwill",),
    },
    {
        "field": "intangibles", "statement": "balance_sheet",
        "label": "Other Intangible Assets",
        "tags": (
            "FiniteLivedIntangibleAssetsNet",
            "IntangibleAssetsNetExcludingGoodwill",
        ),
    },
    # --- Voci generali che a volte mancano ----------------------------------
    {
        "field": "total_assets", "statement": "balance_sheet", "label": "Total Assets",
        "tags": ("Assets",),
    },
    {
        "field": "equity", "statement": "balance_sheet", "label": "Stockholders Equity",
        "tags": (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    },
    {
        "field": "revenue", "statement": "income_statement", "label": "Total Revenue",
        "tags": (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ),
    },
    {
        "field": "net_income", "statement": "income_statement", "label": "Net Income",
        "tags": ("NetIncomeLoss", "ProfitLoss"),
    },
    {
        "field": "research_development", "statement": "income_statement",
        "label": "Research And Development",
        "tags": ("ResearchAndDevelopmentExpense",),
    },
    {
        "field": "d_and_a", "statement": "cash_flow",
        "label": "Depreciation And Amortization",
        "tags": (
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "Depreciation",
        ),
    },
    {
        "field": "capex", "statement": "cash_flow", "label": "Capital Expenditure",
        "tags": (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsForCapitalImprovements",
        ),
    },
    {
        "field": "dividends_paid", "statement": "cash_flow", "label": "Cash Dividends Paid",
        "tags": (
            "PaymentsOfDividendsCommonStock",
            "PaymentsOfDividends",
        ),
    },
    # --- Voci immobiliari (profilo REIT) -----------------------------------
    {
        "field": "real_estate", "statement": "balance_sheet", "label": "Real Estate",
        "tags": (
            "RealEstateInvestmentPropertyNet",
            "RealEstateInvestmentPropertyAtCost",
        ),
    },
    # --- Voci assicurative -------------------------------------------------
    {
        "field": "premiums_earned", "statement": "income_statement",
        "label": "Total Premiums Earned",
        "tags": ("PremiumsEarnedNet", "PremiumsWrittenNet"),
    },
    {
        "field": "policy_benefits", "statement": "income_statement",
        "label": "Policyholder Benefits Gross",
        "tags": (
            "PolicyholderBenefitsAndClaimsIncurredNet",
            "LiabilityForClaimsAndClaimsAdjustmentExpensePropertyCasualtyLiability",
        ),
    },
)

#: Voci il cui segno in XBRL e' opposto alla convenzione di yfinance. Il CapEx e i
#: dividendi in XBRL sono importi positivi (pagamenti), mentre nel rendiconto di
#: yfinance sono negativi (uscite di cassa). Il modello usa il valore assoluto in
#: entrambi i casi, ma la coerenza del segno rende leggibile la tabella anno per anno.
NEGATED_FIELDS = frozenset({"capex", "dividends_paid"})

_last_request = 0.0


# ---------------------------------------------------------------------------
# 1. SEC EDGAR
# ---------------------------------------------------------------------------


def _user_agent() -> str:
    return os.environ.get("SEC_USER_AGENT") or DEFAULT_USER_AGENT


def _cache_dir(explicit: Optional[str] = None) -> str:
    """Cartella della cache: i companyfacts di un grande emittente pesano decine di MB."""
    path = explicit or os.environ.get("SEC_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "value-quant-app", "sec"
    )
    os.makedirs(path, exist_ok=True)
    return path


def _get_json(url: str, quality: _DataQuality, timeout: float = 30.0) -> Optional[Any]:
    """GET con User-Agent, rate limit e nessuna eccezione propagata."""
    global _last_request
    elapsed = time.monotonic() - _last_request
    if elapsed < SEC_MIN_INTERVAL:
        time.sleep(SEC_MIN_INTERVAL - elapsed)
    request = urllib.request.Request(url, headers={
        "User-Agent": _user_agent(),
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            _last_request = time.monotonic()
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        _last_request = time.monotonic()
        if exc.code == 403:
            quality.miss(
                "SEC EDGAR ha risposto 403: serve un User-Agent che identifichi chi "
                "chiama. Impostare la variabile d'ambiente SEC_USER_AGENT "
                "(es. \"Nome Cognome nome@dominio.it\")."
            )
        elif exc.code == 404:
            quality.miss(f"SEC EDGAR: risorsa non trovata ({url}).")
        else:
            quality.miss(f"SEC EDGAR: errore HTTP {exc.code}.")
    except Exception as exc:  # pragma: no cover - dipende dalla rete
        _last_request = time.monotonic()
        quality.miss(f"SEC EDGAR non raggiungibile: {exc}")
    return None


def _resolve_cik(
    ticker: str,
    quality: _DataQuality,
    cache_dir: Optional[str] = None,
) -> Optional[int]:
    """Numero CIK di un ticker, dalla mappa ufficiale della SEC (messa in cache)."""
    path = os.path.join(_cache_dir(cache_dir), "company_tickers.json")
    mapping: Optional[Any] = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                mapping = json.load(handle)
        except Exception:  # pragma: no cover - cache corrotta
            mapping = None
    if mapping is None:
        mapping = _get_json(SEC_TICKERS_URL, quality)
        if mapping is None:
            return None
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(mapping, handle)
        except Exception:  # pragma: no cover - filesystem in sola lettura
            pass

    wanted = ticker.upper().replace(".", "-")
    for entry in (mapping or {}).values():
        if str(entry.get("ticker", "")).upper() == wanted:
            return int(entry["cik_str"])
    quality.miss(
        f"{ticker.upper()} non compare fra gli emittenti SEC: e' normale per un titolo "
        "non americano. Per i mercati europei la strada e' l'override manuale."
    )
    return None


def fetch_sec_companyfacts(
    ticker: str,
    quality: Optional[_DataQuality] = None,
    *,
    cache_dir: Optional[str] = None,
    max_age_days: float = 7.0,
) -> Optional[Dict[str, Any]]:
    """Scarica (o rilegge dalla cache) i ``companyfacts`` XBRL di un emittente.

    Una sola richiesta per emittente, che contiene **tutti** i fatti depositati: e' un
    file grosso (decine di MB per una grande banca) ma evita venti richieste separate, e
    la cache lo rende gratuito dalla seconda volta.

    Returns:
        Il JSON della SEC, oppure ``None`` se il ticker non e' un emittente SEC o la
        rete non risponde. Non solleva eccezioni.
    """
    quality = quality if quality is not None else _DataQuality()
    cik = _resolve_cik(ticker, quality, cache_dir)
    if cik is None:
        return None

    path = os.path.join(_cache_dir(cache_dir), f"CIK{cik:010d}.json")
    if os.path.exists(path):
        age_days = (time.time() - os.path.getmtime(path)) / 86400.0
        if age_days <= max_age_days:
            try:
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:  # pragma: no cover - cache corrotta
                pass

    facts = _get_json(SEC_FACTS_URL.format(cik=cik), quality)
    if facts is None:
        return None
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(facts, handle)
    except Exception:  # pragma: no cover - filesystem in sola lettura
        pass
    return facts


def _annual_values(entries: Sequence[Mapping[str, Any]]) -> Dict[int, float]:
    """Estrae i valori **annuali** da una serie di fatti XBRL.

    Tre filtri, in quest'ordine:

    1. solo i form annuali (10-K, 20-F e le loro rettifiche): i 10-Q sono trimestrali;
    2. per le voci di flusso (che hanno ``start``) solo i periodi di durata annuale, per
       non confondere un trimestre o un semestre con un esercizio. Le voci di stato
       patrimoniale non hanno ``start``: sono istantanee e passano;
    3. a parita' di esercizio vince il deposito **piu' recente**, che e' il dato
       rettificato: e' la stessa convenzione di yfinance, che espone i bilanci rivisti.
    """
    best: Dict[int, Tuple[str, float]] = {}
    for entry in entries:
        form = str(entry.get("form", ""))
        if form not in ANNUAL_FORMS:
            continue
        end = str(entry.get("end", ""))
        if len(end) < 4:
            continue
        start = entry.get("start")
        if start:
            try:
                from datetime import date
                y1, m1, d1 = (int(part) for part in str(start).split("-")[:3])
                y2, m2, d2 = (int(part) for part in end.split("-")[:3])
                duration = (date(y2, m2, d2) - date(y1, m1, d1)).days
            except Exception:  # pragma: no cover - date malformate
                continue
            if not (ANNUAL_DAYS[0] <= duration <= ANNUAL_DAYS[1]):
                continue
        value = _to_float(entry.get("val"))
        if value is None:
            continue
        year = int(end[:4])
        filed = str(entry.get("filed", ""))
        previous = best.get(year)
        if previous is None or filed >= previous[0]:
            best[year] = (filed, value)
    return {year: value for year, (_, value) in best.items()}


def sec_rows(
    facts: Mapping[str, Any],
    quality: Optional[_DataQuality] = None,
) -> Dict[str, Dict[str, Dict[int, float]]]:
    """Converte i companyfacts in righe di bilancio pronte per i tre prospetti.

    Returns:
        ``{prospetto: {etichetta: {anno: valore}}}`` con le etichette che gli alias del
        modello riconoscono. Le voci per cui nessun tag ha prodotto dati sono assenti.
    """
    quality = quality if quality is not None else _DataQuality()
    us_gaap = ((facts.get("facts") or {}).get("us-gaap") or {})
    output: Dict[str, Dict[str, Dict[int, float]]] = {
        "income_statement": {}, "balance_sheet": {}, "cash_flow": {},
    }
    if not us_gaap:
        quality.miss("SEC EDGAR: nessun fatto us-gaap nel deposito.")
        return output

    used: List[str] = []
    for spec in SEC_TAGS:
        for tag in spec["tags"]:
            concept = us_gaap.get(tag)
            if not concept:
                continue
            units = concept.get("units") or {}
            entries = units.get("USD") or next(
                (value for key, value in units.items() if key.startswith("USD")), None
            )
            if not entries:
                continue
            values = _annual_values(entries)
            if not values:
                continue
            if spec["field"] in NEGATED_FIELDS:
                values = {year: -abs(value) for year, value in values.items()}
            output[spec["statement"]][spec["label"]] = values
            used.append(f"{spec['field']}<-{tag}")
            break

    if used:
        quality.note(
            f"SEC EDGAR: {len(used)} voci lette dai depositi XBRL "
            f"({', '.join(sorted(used)[:6])}{', ...' if len(used) > 6 else ''})."
        )
    return output


# ---------------------------------------------------------------------------
# 2. Override manuali
# ---------------------------------------------------------------------------


def load_overrides(
    path: str,
    ticker: str,
    quality: Optional[_DataQuality] = None,
) -> Dict[str, Dict[str, Dict[int, float]]]:
    """Legge le voci inserite a mano per un ticker.

    Formato (JSON, oppure YAML se PyYAML e' installato)::

        {
          "JPM": {
            "balance_sheet": {
              "Net Loan":       {"2024": 1300000000000, "2023": 1250000000000},
              "Total Deposits": {"2024": 2400000000000}
            },
            "income_statement": {
              "Credit Losses Provision": {"2024": 9000000000}
            }
          }
        }

    Le etichette sono quelle di yfinance — le stesse che il modello cerca — quindi si
    possono copiare dal bilancio senza conoscere la struttura interna del codice. Gli
    importi vanno nell'unita' del bilancio (unita', non milioni).

    A differenza di SEC EDGAR, un override **sovrascrive** il dato esistente: se lo si
    scrive a mano e' perche' quello automatico manca o e' sbagliato.
    """
    quality = quality if quality is not None else _DataQuality()
    empty: Dict[str, Dict[str, Dict[int, float]]] = {
        "income_statement": {}, "balance_sheet": {}, "cash_flow": {},
    }
    if not path:
        return empty
    if not os.path.exists(path):
        quality.miss(f"File di override non trovato: {path}")
        return empty

    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if path.lower().endswith((".yaml", ".yml")):
            try:
                import yaml
            except ImportError:
                quality.miss(
                    f"{path} e' YAML ma PyYAML non e' installato: convertire in JSON "
                    "oppure `pip install pyyaml`."
                )
                return empty
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
    except Exception as exc:
        quality.miss(f"File di override illeggibile ({path}): {exc}")
        return empty

    if not isinstance(data, Mapping):
        quality.miss(f"File di override malformato ({path}): serve un dizionario.")
        return empty

    entry = None
    for key, value in data.items():
        if str(key).startswith("_"):
            continue
        if str(key).upper() == ticker.upper():
            entry = value
            break
    if entry is None:
        return empty
    if not isinstance(entry, Mapping):
        quality.miss(f"Override di {ticker.upper()} malformato: serve un dizionario.")
        return empty

    result = dict(empty)
    count = 0
    for statement, rows in entry.items():
        # Le chiavi che iniziano con _ sono commenti: il file e' scritto a mano e deve
        # poter essere annotato senza generare avvisi.
        if str(statement).startswith("_"):
            continue
        if statement not in result:
            quality.note(
                f"Override: prospetto '{statement}' ignorato (validi: "
                f"{', '.join(sorted(empty))})."
            )
            continue
        if not isinstance(rows, Mapping):
            continue
        for label, series in rows.items():
            if not isinstance(series, Mapping):
                quality.note(f"Override '{label}' ignorato: serve {{anno: valore}}.")
                continue
            parsed: Dict[int, float] = {}
            for year, value in series.items():
                try:
                    numeric = _to_float(value)
                    if numeric is not None:
                        parsed[int(year)] = numeric
                except (TypeError, ValueError):
                    quality.note(f"Override '{label}': anno '{year}' non valido.")
            if parsed:
                result[statement][str(label)] = parsed
                count += 1
    if count:
        quality.estimate(
            f"{count} voci di bilancio inserite a mano da {os.path.basename(path)}: "
            "non provengono da una fonte automatica e valgono quanto vale chi le ha "
            "scritte."
        )
    return result


# ---------------------------------------------------------------------------
# 3. Merge nei prospetti
# ---------------------------------------------------------------------------


def _apply_rows(
    frame: Any,
    rows: Mapping[str, Mapping[int, float]],
    *,
    overwrite: bool,
    source: str,
    quality: _DataQuality,
    discrepancy_threshold: float = 0.05,
) -> Tuple[Any, List[str], List[str], int]:
    """Inserisce righe in un prospetto, restituendo (frame, aggiunte, sostituite, celle).

    Il merge lavora **per cella**, non per riga. Una riga che esiste ma copre meno
    esercizi della fonte non e' un conflitto: gli anni che le mancano sono buchi, e
    riempirli e' esattamente il compito di questo modulo. E' anche il modo in cui la
    storia si allunga oltre i 4-5 esercizi che yfinance espone — gli anni in piu'
    diventano colonne nuove.

    Con ``overwrite=False`` si scrive solo dove non c'e' nulla; dove un valore c'e' gia'
    e le due fonti divergono oltre la soglia, la differenza viene **dichiarata** e il
    dato di partenza resta al suo posto.
    """
    if pd is None or not rows:
        return frame, [], [], 0

    if frame is None or not hasattr(frame, "index"):
        frame = pd.DataFrame()
    else:
        frame = frame.copy()

    existing = _row_index(frame)

    def column_for(year: int) -> Any:
        """Colonna dell'esercizio, creata se il prospetto non ce l'ha."""
        for column in frame.columns:
            candidate = getattr(column, "year", None)
            if candidate is None:
                try:
                    candidate = int(str(column)[:4])
                except (TypeError, ValueError):
                    continue
            if int(candidate) == year:
                return column
        column = pd.Timestamp(f"{year}-12-31")
        frame[column] = pd.NA
        return column

    added: List[str] = []
    replaced: List[str] = []
    filled_cells = 0

    for label, series in rows.items():
        # Le colonne vanno create prima della riga: su un prospetto vuoto pandas non
        # accetta di aggiungere una riga a un frame senza colonne definite.
        for year in series:
            column_for(year)

        key = _normalize_label(label)
        if key in existing:
            target = next(
                (name for name in frame.index if _normalize_label(name) == key), label
            )
            is_new = False
        else:
            target = label
            is_new = True
            frame.loc[target] = pd.NA

        touched = 0
        for year, value in sorted(series.items(), reverse=True):
            column = column_for(year)
            current = _to_float(frame.loc[target, column]) if not is_new else None

            if current is None or overwrite:
                frame.loc[target, column] = value
                touched += 1
                continue

            # Il valore c'e' gia' e non lo si tocca: resta da dire se le fonti divergono.
            if value is not None and current != 0:
                if abs(current - value) / max(abs(current), 1.0) > discrepancy_threshold:
                    quality.note(
                        f"{year}: '{label}' differisce fra il dato di partenza "
                        f"({current:,.0f}) e {source} ({value:,.0f}); tenuto il primo."
                    )

        if touched:
            filled_cells += touched
            (added if is_new else replaced).append(label)
        elif is_new:
            # Nessun valore scritto: la riga vuota sarebbe solo rumore.
            frame = frame.drop(index=target)

    if not frame.empty:
        frame = frame[sorted(frame.columns, key=lambda c: str(c), reverse=True)]
    return frame, added, replaced, filled_cells


def enrich_financials(
    financials: Mapping[str, Any],
    ticker: Optional[str] = None,
    *,
    sec: bool = True,
    overrides_path: Optional[str] = None,
    sec_facts: Optional[Mapping[str, Any]] = None,
    quality: Optional[_DataQuality] = None,
    cache_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Arricchisce i prospetti con SEC EDGAR e con le voci inserite a mano.

    Non modifica l'input: restituisce una copia con i tre prospetti integrati e la
    provenienza dichiarata in ``data_quality``.

    Args:
        financials: output di ``fetch_financials``.
        ticker: simbolo; se omesso si usa quello dentro ``financials``.
        sec: se ``False`` salta EDGAR (utile offline o per i titoli non americani).
        overrides_path: file JSON/YAML con le voci inserite a mano.
        sec_facts: companyfacts gia' disponibili — usato dai test, che devono girare
            senza rete.

    Returns:
        Una copia di ``financials``. In caso di rete assente o ticker non americano
        restituisce l'input invariato: l'arricchimento e' additivo e non puo' peggiorare
        il risultato.
    """
    # ``fetch_financials`` mette un _DataQuality in "data_quality", i bilanci sintetici
    # dei test un dizionario: accettare solo il primo evita di scrivere note dentro una
    # struttura che non le sa raccogliere.
    if not isinstance(quality, _DataQuality):
        quality = _DataQuality()
    result: Dict[str, Any] = dict(financials)
    symbol = (ticker or financials.get("ticker") or "").upper()

    layers: List[Tuple[str, Dict[str, Dict[str, Dict[int, float]]], bool]] = []

    if sec or sec_facts is not None:
        facts = sec_facts
        if facts is None and sec:
            facts = fetch_sec_companyfacts(symbol, quality, cache_dir=cache_dir)
        if facts:
            layers.append(("SEC EDGAR", sec_rows(facts, quality), False))

    if overrides_path:
        layers.append(("override manuale", load_overrides(overrides_path, symbol, quality), True))

    if not layers:
        return result

    provenance: Dict[str, List[str]] = {}
    filled: Dict[str, int] = {}
    for source, rows_by_statement, overwrite in layers:
        for statement, rows in rows_by_statement.items():
            if not rows:
                continue
            frame, added, replaced, cells = _apply_rows(
                result.get(statement), rows, overwrite=overwrite,
                source=source, quality=quality,
            )
            result[statement] = frame
            for label in added:
                provenance.setdefault(source, []).append(f"{label} (aggiunta)")
            for label in replaced:
                verbo = "sostituita" if overwrite else "completata"
                provenance.setdefault(source, []).append(f"{label} ({verbo})")
            filled[source] = filled.get(source, 0) + cells

    # Gli anni disponibili si ricalcolano: una fonte con piu' storia li allunga.
    years: List[int] = []
    for statement in ("income_statement", "balance_sheet", "cash_flow"):
        frame = result.get(statement)
        if frame is None or not hasattr(frame, "columns"):
            continue
        for column in frame.columns:
            year = getattr(column, "year", None)
            if year is None:
                try:
                    year = int(str(column)[:4])
                except (TypeError, ValueError):
                    continue
            years.append(int(year))
    if years:
        previous = list(financials.get("years") or [])
        merged = sorted(set(years), reverse=True)
        result["years"] = merged
        if previous and len(merged) > len(previous):
            quality.note(
                f"Storia estesa da {len(previous)} a {len(merged)} esercizi grazie alle "
                "fonti aggiuntive: le metriche di consistenza diventano piu' solide."
            )

    for source, labels in provenance.items():
        quality.note(f"Provenienza — {source}: {', '.join(sorted(labels))}.")
    if not provenance:
        quality.note("Fonti aggiuntive interrogate: nessuna voce nuova da integrare.")

    return result


if __name__ == "__main__":  # pragma: no cover - uso manuale
    import sys

    symbols = [arg for arg in sys.argv[1:] if not arg.startswith("-")] or ["AAPL"]
    for symbol in symbols:
        diagnostics = _DataQuality()
        print(f"\nSEC EDGAR: {symbol.upper()}")
        data = fetch_sec_companyfacts(symbol, diagnostics)
        if data:
            rows = sec_rows(data, diagnostics)
            for statement, content in rows.items():
                if content:
                    print(f"  {statement}:")
                    for label, series in sorted(content.items()):
                        anni = sorted(series, reverse=True)
                        print(f"    {label:32} {len(series):>2} esercizi "
                              f"({anni[-1]}-{anni[0]})")
        for section, entries in diagnostics.as_dict().items():
            for entry in entries:
                print(f"  [{section}] {entry}")
