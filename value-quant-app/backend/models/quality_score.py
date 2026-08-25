"""Quality Score: analisi della qualita' di un'azienda a partire dai fondamentali.

Il modulo scarica i bilanci annuali (conto economico, stato patrimoniale, rendiconto
finanziario) tramite ``yfinance`` e calcola, anno per anno:

* redditivita': ROIC, ROE, ROA, margine operativo, margine netto, Owner Earnings;
* solidita' patrimoniale: Debt/Equity, Debt/EBITDA, Interest Coverage, Current Ratio;
* consistenza: deviazione standard, coefficiente di variazione e percentuale di anni
  in crescita per le metriche chiave.

Il tutto viene aggregato in un punteggio 0-100 con pesi personalizzabili
(default: 40% profittabilita', 30% consistenza, 30% solidita' di bilancio).

Convenzioni usate nell'output:
* ROIC / ROE / ROA / margini sono espressi in **punti percentuali** (es. 28.5 = 28.5%);
* i multipli (Debt/Equity, Debt/EBITDA, Interest Coverage, Current Ratio) sono numeri puri;
* Owner Earnings e le voci di bilancio sono nella valuta di reporting dell'emittente.

Uso da riga di comando::

    python quality_score.py            # analizza AAPL
    python quality_score.py MSFT KO    # analizza piu' ticker

Nota sui dati: yfinance espone tipicamente 4-5 esercizi annuali per ticker, non 10.
Il modulo richiede fino a ``years`` esercizi e lavora con quelli effettivamente
disponibili, segnalando nella sezione ``data_quality`` quanti anni ha usato.
"""

from __future__ import annotations

import math
import re
import statistics
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

try:  # pandas e' una dipendenza di yfinance, ma il modulo resta importabile senza
    import pandas as pd
except ImportError:  # pragma: no cover - ambiente senza pandas
    pd = None  # type: ignore[assignment]

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - il layer di calcolo resta utilizzabile
    yf = None  # type: ignore[assignment]


__all__ = [
    "fetch_financials",
    "extract_fundamentals",
    "calculate_roic",
    "calculate_roe",
    "calculate_roa",
    "calculate_margins",
    "calculate_owner_earnings",
    "estimate_maintenance_capex",
    "calculate_return_on_tangible_capital",
    "calculate_balance_sheet_ratios",
    "calculate_consistency",
    "calculate_quality_score",
    "format_report",
    "DEFAULT_WEIGHTS",
]


# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

DEFAULT_YEARS = 10

DEFAULT_WEIGHTS: Dict[str, float] = {
    "profitability": 0.40,
    "consistency": 0.30,
    "balance_sheet": 0.30,
}

#: Aliquota fiscale di fallback quando non e' ricostruibile dal conto economico.
DEFAULT_TAX_RATE = 0.25

#: Valore convenzionale usato per l'Interest Coverage quando non ci sono oneri finanziari.
NO_DEBT_COVERAGE = 999.0

# Etichette alternative usate da yfinance per la stessa voce di bilancio.
# L'ordine conta: la prima voce disponibile vince.
INCOME_ALIASES: Dict[str, Sequence[str]] = {
    "revenue": ("Total Revenue", "Operating Revenue", "Revenue"),
    "gross_profit": ("Gross Profit",),
    "operating_income": ("Operating Income", "Total Operating Income As Reported"),
    "ebit": ("EBIT",),
    "ebitda": ("EBITDA", "Normalized EBITDA"),
    "pretax_income": ("Pretax Income", "Income Before Tax"),
    "tax_provision": ("Tax Provision", "Income Tax Expense"),
    "net_income": (
        "Net Income",
        "Net Income Common Stockholders",
        "Net Income From Continuing Operation Net Minority Interest",
    ),
    "interest_expense": (
        "Interest Expense",
        "Interest Expense Non Operating",
        "Net Interest Income",
    ),
    "depreciation_income": ("Reconciled Depreciation",),
}

BALANCE_ALIASES: Dict[str, Sequence[str]] = {
    "total_assets": ("Total Assets",),
    "total_liabilities": (
        "Total Liabilities Net Minority Interest",
        "Total Liabilities",
    ),
    "equity": (
        "Stockholders Equity",
        "Common Stock Equity",
        "Total Equity Gross Minority Interest",
    ),
    "total_debt": ("Total Debt",),
    "long_term_debt": ("Long Term Debt", "Long Term Debt And Capital Lease Obligation"),
    "current_debt": ("Current Debt", "Current Debt And Capital Lease Obligation"),
    "cash": (
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash Financial",
    ),
    "current_assets": ("Current Assets", "Total Current Assets"),
    "current_liabilities": ("Current Liabilities", "Total Current Liabilities"),
    "invested_capital": ("Invested Capital",),
    "shares_outstanding": ("Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"),
    "tangible_book_value": ("Tangible Book Value", "Net Tangible Assets"),
    # Servono per il CapEx di mantenimento (rapporto immobilizzazioni/ricavi) e per il
    # rendimento sul capitale **tangibile**, che e' il metro di Buffett.
    "net_ppe": (
        "Net PPE", "Net Property Plant And Equipment",
        "Property Plant And Equipment Net", "Gross PPE",
    ),
    "goodwill": ("Goodwill",),
    "intangibles": (
        "Other Intangible Assets", "Intangible Assets",
        "Goodwill And Other Intangible Assets",
    ),
}

CASHFLOW_ALIASES: Dict[str, Sequence[str]] = {
    "d_and_a": (
        "Depreciation And Amortization",
        "Depreciation Amortization Depletion",
        "Depreciation",
    ),
    "capex": ("Capital Expenditure", "Purchase Of PPE"),
    "change_in_working_capital": ("Change In Working Capital",),
    "free_cash_flow": ("Free Cash Flow",),
    "operating_cash_flow": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
}


# ---------------------------------------------------------------------------
# Utility numeriche e di parsing
# ---------------------------------------------------------------------------


def _normalize_label(label: Any) -> str:
    """Normalizza l'etichetta di una riga di bilancio per il matching."""
    return re.sub(r"[^a-z0-9]", "", str(label).lower())


def _to_float(value: Any) -> Optional[float]:
    """Converte un valore in float, restituendo ``None`` per NaN/None/non numerici."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _safe_div(
    numerator: Optional[float],
    denominator: Optional[float],
    *,
    scale: float = 1.0,
) -> Optional[float]:
    """Divisione protetta: ``None`` se un termine manca o il denominatore e' nullo."""
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return (numerator / denominator) * scale


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def _column_year(column: Any) -> Optional[int]:
    """Estrae l'anno dalla colonna di un DataFrame di yfinance (Timestamp o stringa)."""
    year = getattr(column, "year", None)
    if isinstance(year, int):
        return year
    match = re.search(r"(19|20)\d{2}", str(column))
    return int(match.group(0)) if match else None


def _row_index(frame: Any) -> Dict[str, Any]:
    """Mappa etichetta-normalizzata -> riga del DataFrame (prima occorrenza vince)."""
    index: Dict[str, Any] = {}
    if frame is None or pd is None or not hasattr(frame, "index"):
        return index
    try:
        if frame.empty:
            return index
    except Exception:  # pragma: no cover - oggetti non-DataFrame
        return index
    for label in frame.index:
        key = _normalize_label(label)
        if key not in index:
            try:
                index[key] = frame.loc[label]
            except Exception:  # pragma: no cover - indici duplicati/anomali
                continue
    return index


def _series_by_year(
    rows: Mapping[str, Any],
    aliases: Sequence[str],
) -> Tuple[Dict[int, float], Optional[str]]:
    """Restituisce ``{anno: valore}`` per la prima etichetta disponibile fra gli alias."""
    for alias in aliases:
        row = rows.get(_normalize_label(alias))
        if row is None:
            continue
        values: Dict[int, float] = {}
        try:
            items = row.items()
        except AttributeError:  # pragma: no cover - riga non iterabile
            continue
        for column, raw in items:
            year = _column_year(column)
            value = _to_float(raw)
            if year is None or value is None:
                continue
            values.setdefault(year, value)
        if values:
            return values, alias
    return {}, None


class _DataQuality:
    """Raccoglie note, stime e dati mancanti incontrati durante l'elaborazione."""

    def __init__(self) -> None:
        self.notes: List[str] = []
        self.estimated: List[str] = []
        self.missing: List[str] = []

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    def estimate(self, message: str) -> None:
        if message not in self.estimated:
            self.estimated.append(message)

    def miss(self, message: str) -> None:
        if message not in self.missing:
            self.missing.append(message)

    def as_dict(self) -> Dict[str, List[str]]:
        return {
            "notes": list(self.notes),
            "estimated": list(self.estimated),
            "missing": list(self.missing),
        }


# ---------------------------------------------------------------------------
# 1. Download dei fondamentali
# ---------------------------------------------------------------------------


def fetch_financials(ticker: str, years: int = DEFAULT_YEARS) -> Dict[str, Any]:
    """Scarica i bilanci annuali di ``ticker`` (fino a ``years`` esercizi).

    Args:
        ticker: simbolo di borsa (es. ``"AAPL"``).
        years: numero massimo di esercizi da tenere, dal piu' recente.

    Returns:
        Dizionario con i tre prospetti (``income_statement``, ``balance_sheet``,
        ``cash_flow``) come DataFrame, la lista degli anni disponibili, il nome
        della societa' e una sezione ``data_quality`` con note su cio' che manca.
        Non solleva eccezioni: in caso di errore i prospetti sono vuoti e il
        problema e' descritto in ``data_quality``.
    """
    quality = _DataQuality()
    result: Dict[str, Any] = {
        "ticker": ticker.upper(),
        "company_name": None,
        "currency": None,
        "income_statement": None,
        "balance_sheet": None,
        "cash_flow": None,
        "years": [],
        "data_quality": quality,
    }

    if yf is None:
        quality.miss("yfinance non installato: eseguire `pip install yfinance`.")
        return result

    try:
        handle = yf.Ticker(ticker)
    except Exception as exc:  # pragma: no cover - dipende dalla rete
        quality.miss(f"Impossibile inizializzare il ticker {ticker}: {exc}")
        return result

    statements = {
        "income_statement": ("conto economico", ("income_stmt", "financials")),
        "balance_sheet": ("stato patrimoniale", ("balance_sheet", "balancesheet")),
        "cash_flow": ("rendiconto finanziario", ("cashflow", "cash_flow")),
    }

    for key, (label, attributes) in statements.items():
        frame = None
        for attribute in attributes:
            try:
                candidate = getattr(handle, attribute, None)
            except Exception as exc:  # pragma: no cover - errori di rete/parsing
                quality.miss(f"Errore nel download del {label}: {exc}")
                continue
            if candidate is None or pd is None:
                continue
            try:
                if not candidate.empty:
                    frame = candidate
                    break
            except Exception:  # pragma: no cover - oggetto inatteso
                continue
        if frame is None:
            quality.miss(f"{label.capitalize()} non disponibile per {ticker}.")
        result[key] = frame

    # Metadati (best effort: l'endpoint info e' spesso instabile).
    try:
        info = handle.get_info() if hasattr(handle, "get_info") else getattr(handle, "info", {})
        if isinstance(info, dict):
            result["company_name"] = info.get("longName") or info.get("shortName")
            result["currency"] = info.get("financialCurrency") or info.get("currency")
    except Exception:
        quality.note("Metadati anagrafici (nome/valuta) non recuperati.")

    available_years = sorted(
        {
            year
            for key in ("income_statement", "balance_sheet", "cash_flow")
            for year in (
                _column_year(column)
                for column in getattr(result[key], "columns", [])
            )
            if year is not None
        },
        reverse=True,
    )
    result["years"] = available_years[:years]

    if not result["years"]:
        quality.miss(f"Nessun esercizio annuale disponibile per {ticker}.")
    elif len(result["years"]) < years:
        quality.note(
            f"Richiesti {years} esercizi, disponibili {len(result['years'])} "
            f"({result['years'][-1]}-{result['years'][0]}): yfinance espone uno storico limitato."
        )

    result["data_quality"] = quality.as_dict()
    return result


# ---------------------------------------------------------------------------
# 2. Normalizzazione delle voci di bilancio
# ---------------------------------------------------------------------------


def extract_fundamentals(
    financials: Mapping[str, Any],
    quality: Optional[_DataQuality] = None,
    sector: Optional[str] = None,
) -> Dict[int, Dict[str, Optional[float]]]:
    """Normalizza i tre prospetti in un dizionario ``{anno: {voce: valore}}``.

    Applica i fallback tipici (EBIT da utile ante imposte + oneri finanziari,
    EBITDA da EBIT + D&A, debito totale da debito a breve + lungo termine, ...)
    e annota in ``quality`` ogni approssimazione effettuata.

    Con ``sector`` bancario o assicurativo le derivazioni tipiche degli industriali
    (EBIT, EBITDA, aliquota effettiva, capitale investito) vengono **saltate**: su un
    finanziario non hanno significato, e calcolarle riempirebbe ``data_quality`` di
    approssimazioni su grandezze che nessuno usera'.
    """
    quality = quality if quality is not None else _DataQuality()
    operating = sector not in ("bank", "insurance")

    income_rows = _row_index(financials.get("income_statement"))
    balance_rows = _row_index(financials.get("balance_sheet"))
    cash_rows = _row_index(financials.get("cash_flow"))

    raw: Dict[str, Dict[int, float]] = {}
    for field, aliases in INCOME_ALIASES.items():
        raw[field], _ = _series_by_year(income_rows, aliases)
    for field, aliases in BALANCE_ALIASES.items():
        raw[field], _ = _series_by_year(balance_rows, aliases)
    for field, aliases in CASHFLOW_ALIASES.items():
        raw[field], _ = _series_by_year(cash_rows, aliases)

    years = list(financials.get("years") or sorted(
        {year for series in raw.values() for year in series}, reverse=True
    ))

    fundamentals: Dict[int, Dict[str, Optional[float]]] = {}
    for year in years:
        row: Dict[str, Optional[float]] = {
            field: raw.get(field, {}).get(year) for field in raw
        }

        # --- Conto economico -------------------------------------------------
        if operating and row["ebit"] is None and row["operating_income"] is not None:
            row["ebit"] = row["operating_income"]
            quality.estimate(f"{year}: EBIT approssimato con il reddito operativo.")
        if operating and row["ebit"] is None and row["pretax_income"] is not None:
            interest = row["interest_expense"] or 0.0
            row["ebit"] = row["pretax_income"] + abs(interest)
            quality.estimate(
                f"{year}: EBIT stimato come utile ante imposte + oneri finanziari."
            )

        if operating and row["operating_income"] is None and row["ebit"] is not None:
            row["operating_income"] = row["ebit"]
            quality.estimate(f"{year}: reddito operativo approssimato con l'EBIT.")

        # D&A: preferenza al rendiconto finanziario, poi alla voce riconciliata.
        if operating and row["d_and_a"] is None and row["depreciation_income"] is not None:
            row["d_and_a"] = row["depreciation_income"]
            quality.estimate(
                f"{year}: D&A presa dalla voce 'Reconciled Depreciation' del conto economico."
            )

        if operating and row["ebitda"] is None and row["ebit"] is not None and row["d_and_a"] is not None:
            row["ebitda"] = row["ebit"] + abs(row["d_and_a"])
            quality.estimate(f"{year}: EBITDA stimato come EBIT + D&A.")

        # Aliquota fiscale effettiva, con clamp per evitare valori anomali.
        tax_rate = _safe_div(row["tax_provision"], row["pretax_income"])
        if tax_rate is None or not (0.0 <= tax_rate <= 0.60):
            if not operating:
                pass
            elif tax_rate is not None:
                quality.estimate(
                    f"{year}: aliquota effettiva anomala ({tax_rate:.1%}), "
                    f"sostituita con il default {DEFAULT_TAX_RATE:.0%}."
                )
            else:
                quality.estimate(
                    f"{year}: aliquota effettiva non calcolabile, usato il default "
                    f"{DEFAULT_TAX_RATE:.0%}."
                )
            tax_rate = DEFAULT_TAX_RATE
        row["tax_rate"] = tax_rate

        # --- Stato patrimoniale ---------------------------------------------
        if row["total_debt"] is None:
            pieces = [row["long_term_debt"], row["current_debt"]]
            if any(p is not None for p in pieces):
                row["total_debt"] = sum(p for p in pieces if p is not None)
                quality.estimate(
                    f"{year}: debito totale ricostruito come debito a breve + lungo termine."
                )
        if operating and row["total_debt"] is None:
            quality.miss(f"{year}: debito totale non disponibile.")

        if row["equity"] is None and row["total_assets"] is not None and row["total_liabilities"] is not None:
            row["equity"] = row["total_assets"] - row["total_liabilities"]
            quality.estimate(f"{year}: patrimonio netto stimato come attivo - passivo.")

        if operating and row["cash"] is None:
            quality.miss(f"{year}: cassa e equivalenti non disponibili.")

        # --- Capitale investito ---------------------------------------------
        # Non ha senso su un finanziario: il "capitale investito" di una banca sono i
        # depositi della clientela, che non sono capitale dell'azionista.
        debt = row["total_debt"] if operating else None
        equity = row["equity"]
        cash = row["cash"]
        if debt is not None and equity is not None:
            invested = debt + equity - (cash or 0.0)
            if cash is None:
                quality.estimate(
                    f"{year}: capitale investito calcolato senza dedurre la cassa (dato mancante)."
                )
            row["invested_capital_calc"] = invested if invested > 0 else None
            if invested <= 0:
                quality.note(
                    f"{year}: capitale investito non positivo ({invested:,.0f}); ROIC non calcolato."
                )
        else:
            row["invested_capital_calc"] = None

        # --- Rendiconto finanziario -----------------------------------------
        if row["capex"] is not None:
            row["capex"] = abs(row["capex"])
        elif operating:
            quality.miss(f"{year}: CapEx non disponibile.")

        fundamentals[year] = row

    return fundamentals


# ---------------------------------------------------------------------------
# 3. Metriche anno per anno
# ---------------------------------------------------------------------------


def calculate_roic(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
) -> Dict[int, Optional[float]]:
    """ROIC (%) = NOPAT / Capitale Investito.

    NOPAT = EBIT * (1 - aliquota effettiva);
    Capitale Investito = Debito Totale + Patrimonio Netto - Cassa.
    """
    quality = quality if quality is not None else _DataQuality()
    out: Dict[int, Optional[float]] = {}
    for year, row in fundamentals.items():
        try:
            ebit = row.get("ebit")
            invested = row.get("invested_capital_calc")
            if ebit is None or invested is None:
                out[year] = None
                continue
            nopat = ebit * (1.0 - (row.get("tax_rate") or DEFAULT_TAX_RATE))
            out[year] = _safe_div(nopat, invested, scale=100.0)
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: ROIC non calcolabile ({exc}).")
            out[year] = None
    return out


def calculate_roe(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
) -> Dict[int, Optional[float]]:
    """ROE (%) = Utile Netto / Patrimonio Netto."""
    quality = quality if quality is not None else _DataQuality()
    out: Dict[int, Optional[float]] = {}
    for year, row in fundamentals.items():
        try:
            out[year] = _safe_div(row.get("net_income"), row.get("equity"), scale=100.0)
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: ROE non calcolabile ({exc}).")
            out[year] = None
    return out


def calculate_roa(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
) -> Dict[int, Optional[float]]:
    """ROA (%) = Utile Netto / Totale Attivo."""
    quality = quality if quality is not None else _DataQuality()
    out: Dict[int, Optional[float]] = {}
    for year, row in fundamentals.items():
        try:
            out[year] = _safe_div(row.get("net_income"), row.get("total_assets"), scale=100.0)
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: ROA non calcolabile ({exc}).")
            out[year] = None
    return out


def calculate_margins(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
) -> Dict[str, Dict[int, Optional[float]]]:
    """Margine operativo e margine netto (%), anno per anno."""
    quality = quality if quality is not None else _DataQuality()
    operating: Dict[int, Optional[float]] = {}
    net: Dict[int, Optional[float]] = {}
    gross: Dict[int, Optional[float]] = {}
    for year, row in fundamentals.items():
        revenue = row.get("revenue")
        if revenue is None:
            quality.miss(f"{year}: ricavi non disponibili, margini non calcolabili.")
        try:
            operating[year] = _safe_div(row.get("operating_income"), revenue, scale=100.0)
            net[year] = _safe_div(row.get("net_income"), revenue, scale=100.0)
            gross[year] = _safe_div(row.get("gross_profit"), revenue, scale=100.0)
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: margini non calcolabili ({exc}).")
            operating[year] = net[year] = gross[year] = None
    return {"operating_margin": operating, "net_margin": net, "gross_margin": gross}


def estimate_maintenance_capex(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
) -> Dict[int, Optional[float]]:
    """Stima il CapEx **di mantenimento** separandolo da quello di crescita.

    Buffett definisce gli Owner Earnings sottraendo "la media annua degli investimenti
    capitalizzati che l'azienda richiede per mantenere pienamente la propria posizione
    competitiva e il proprio volume". Quella cifra non compare in nessun bilancio:
    va stimata.

    Il metodo usato e' quello di **Bruce Greenwald** (Columbia), lo standard nella
    letteratura value::

        immobilizzazioni/ricavi = media storica di (immobilizzazioni nette / ricavi)
        CapEx di crescita       = immobilizzazioni/ricavi x incremento dei ricavi
        CapEx di mantenimento   = CapEx totale - CapEx di crescita

    L'intuizione: se servono 40 centesimi di impianti per ogni euro di ricavo, allora
    crescere di 100 di ricavi richiede 40 di investimento *aggiuntivo*; il resto del
    CapEx serviva a stare fermi. Quando i ricavi calano il CapEx di crescita e' zero e
    tutto il CapEx e' di mantenimento.

    Ripieghi, in ordine: se mancano le immobilizzazioni si usa il **D&A** (gli
    ammortamenti approssimano il logorio da rimpiazzare); se manca anche quello si
    torna al CapEx totale, che e' la stima piu' prudente possibile.
    """
    quality = quality if quality is not None else _DataQuality()
    years_desc = sorted(fundamentals, reverse=True)

    ratios = [
        _safe_div(fundamentals[year].get("net_ppe"), fundamentals[year].get("revenue"))
        for year in years_desc
    ]
    ppe_to_sales = _mean(ratios)

    output: Dict[int, Optional[float]] = {}
    for year in years_desc:
        row = fundamentals[year]
        capex = row.get("capex")
        if capex is None:
            output[year] = None
            continue
        capex = abs(capex)

        previous = fundamentals.get(year - 1)
        revenue = row.get("revenue")
        previous_revenue = previous.get("revenue") if previous else None

        if ppe_to_sales is not None and revenue is not None and previous_revenue is not None:
            # Solo la crescita dei ricavi assorbe investimento aggiuntivo: se calano,
            # il CapEx di crescita e' zero e tutto il CapEx serve a mantenere.
            growth_capex = ppe_to_sales * max(0.0, revenue - previous_revenue)
            output[year] = max(0.0, capex - growth_capex)
        elif row.get("d_and_a") is not None:
            output[year] = min(capex, abs(row["d_and_a"]))
            quality.estimate(
                f"{year}: CapEx di mantenimento stimato con gli ammortamenti "
                "(immobilizzazioni o ricavi dell'anno precedente non disponibili)."
            )
        else:
            output[year] = capex
            quality.estimate(
                f"{year}: CapEx di mantenimento non separabile: usato il CapEx totale."
            )

    if ppe_to_sales is not None:
        quality.note(
            f"CapEx di mantenimento stimato col metodo Greenwald, rapporto "
            f"immobilizzazioni/ricavi {ppe_to_sales:.2f}."
        )
    return output


def calculate_owner_earnings(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
    maintenance_capex: Optional[Mapping[int, Optional[float]]] = None,
) -> Dict[int, Optional[float]]:
    """Owner Earnings alla Buffett, anno per anno.

    Formula (lettera agli azionisti 1986, appendice)::

        Owner Earnings = Utile Netto
                       + Ammortamenti e altre poste non monetarie
                       - CapEx di mantenimento
                       -/+ Variazione del capitale circolante

    Args:
        maintenance_capex: serie del CapEx di mantenimento. Se omessa viene usato il
            **CapEx totale**, che e' una stima prudenziale (sottrae anche gli
            investimenti di crescita, quindi comprime gli Owner Earnings di un'azienda
            in espansione). Per la stima fedele si passa l'output di
            :func:`estimate_maintenance_capex`.

    **Sul segno della variazione di circolante**: yfinance riporta la voce
    "Change In Working Capital" gia' come *impatto sulla cassa* (positiva = circolante
    liberato), quindi si somma. Se manca, viene stimata dalla differenza anno su anno di
    (attivo corrente - passivo corrente), col segno invertito perche' un aumento del
    circolante assorbe cassa.
    """
    quality = quality if quality is not None else _DataQuality()
    out: Dict[int, Optional[float]] = {}
    years_desc = sorted(fundamentals, reverse=True)

    for year in years_desc:
        row = fundamentals[year]
        try:
            net_income = row.get("net_income")
            d_and_a = row.get("d_and_a")
            if maintenance_capex is not None:
                capex = maintenance_capex.get(year)
            else:
                capex = row.get("capex")
            if net_income is None:
                quality.miss(f"{year}: utile netto mancante, Owner Earnings non calcolabile.")
                out[year] = None
                continue

            if d_and_a is None:
                d_and_a = 0.0
                quality.estimate(f"{year}: D&A non disponibile, considerata pari a zero.")
            else:
                d_and_a = abs(d_and_a)

            if capex is None:
                capex = 0.0
                quality.estimate(f"{year}: CapEx non disponibile, considerato pari a zero.")
            else:
                capex = abs(capex)
                if maintenance_capex is None:
                    quality.estimate(
                        "CapEx di mantenimento non stimato: usato il CapEx totale come "
                        "proxy (Owner Earnings prudenziali)."
                    )

            delta_wc_cash_impact = row.get("change_in_working_capital")
            if delta_wc_cash_impact is None:
                previous = fundamentals.get(year - 1)
                current_wc = _working_capital(row)
                previous_wc = _working_capital(previous) if previous else None
                if current_wc is not None and previous_wc is not None:
                    delta_wc_cash_impact = -(current_wc - previous_wc)
                    quality.estimate(
                        f"{year}: variazione del capitale circolante stimata dallo stato "
                        "patrimoniale (attivo corrente - passivo corrente)."
                    )
                else:
                    delta_wc_cash_impact = 0.0
                    quality.estimate(
                        f"{year}: variazione del capitale circolante non disponibile, "
                        "considerata pari a zero."
                    )

            out[year] = net_income + d_and_a - capex + delta_wc_cash_impact
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: Owner Earnings non calcolabile ({exc}).")
            out[year] = None
    return out


def calculate_return_on_tangible_capital(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
    pretax: bool = True,
) -> Dict[int, Optional[float]]:
    """Rendimento sul capitale **tangibile senza leva** — il metro di Buffett.

    Nella lettera del 2007 Buffett misura le aziende sul "return on unleveraged net
    tangible assets", e cita See's Candies oltre il **200% ante imposte**. La differenza
    rispetto al ROIC ordinario e' che il denominatore **esclude avviamento e
    immateriali**::

        Capitale tangibile = Debito Totale + Patrimonio Netto - Cassa
                             - Avviamento - Immateriali
        Rendimento = EBIT / Capitale tangibile          (ante imposte)

    Perche' toglie l'avviamento: quello e' il prezzo pagato in passato per delle
    acquisizioni, non il capitale che il business richiede *oggi* per funzionare. Un
    ROIC che lo include dice quanto e' stato bravo chi ha comprato; questo dice quanto
    e' buono il business in se'. Su un'azienda cresciuta per acquisizioni i due numeri
    divergono moltissimo.

    Args:
        pretax: se ``True`` usa l'EBIT (come Buffett), altrimenti il NOPAT.
    """
    quality = quality if quality is not None else _DataQuality()
    out: Dict[int, Optional[float]] = {}
    for year, row in fundamentals.items():
        try:
            ebit = row.get("ebit")
            invested = row.get("invested_capital_calc")
            if ebit is None or invested is None:
                out[year] = None
                continue
            goodwill = row.get("goodwill") or 0.0
            intangibles = row.get("intangibles") or 0.0
            if row.get("goodwill") is None and row.get("intangibles") is None:
                quality.estimate(
                    f"{year}: avviamento e immateriali non disponibili, capitale "
                    "tangibile assunto pari al capitale investito."
                )
            tangible = invested - goodwill - intangibles
            if tangible <= 0:
                quality.note(
                    f"{year}: capitale tangibile non positivo, rendimento non calcolato."
                )
                out[year] = None
                continue
            earnings = ebit if pretax else ebit * (1.0 - (row.get("tax_rate") or DEFAULT_TAX_RATE))
            out[year] = earnings / tangible * 100.0
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: rendimento sul capitale tangibile non calcolabile ({exc}).")
            out[year] = None
    return out


def _working_capital(row: Optional[Mapping[str, Optional[float]]]) -> Optional[float]:
    if not row:
        return None
    current_assets = row.get("current_assets")
    current_liabilities = row.get("current_liabilities")
    if current_assets is None or current_liabilities is None:
        return None
    return current_assets - current_liabilities


def calculate_balance_sheet_ratios(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
) -> Dict[str, Dict[int, Optional[float]]]:
    """Debt/Equity, Debt/EBITDA, Interest Coverage e Current Ratio, anno per anno."""
    quality = quality if quality is not None else _DataQuality()
    debt_equity: Dict[int, Optional[float]] = {}
    debt_ebitda: Dict[int, Optional[float]] = {}
    interest_coverage: Dict[int, Optional[float]] = {}
    current_ratio: Dict[int, Optional[float]] = {}

    for year, row in fundamentals.items():
        debt = row.get("total_debt")
        ebitda = row.get("ebitda")
        ebit = row.get("ebit")
        interest = row.get("interest_expense")

        try:
            debt_equity[year] = _safe_div(debt, row.get("equity"))
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: Debt/Equity non calcolabile ({exc}).")
            debt_equity[year] = None

        try:
            if debt == 0:
                debt_ebitda[year] = 0.0
            else:
                debt_ebitda[year] = _safe_div(debt, ebitda)
                if ebitda is not None and ebitda <= 0 and debt_ebitda[year] is not None:
                    quality.note(f"{year}: EBITDA negativo, Debt/EBITDA poco significativo.")
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: Debt/EBITDA non calcolabile ({exc}).")
            debt_ebitda[year] = None

        try:
            if interest is None or abs(interest) < 1e-9:
                if ebit is not None and debt is not None and debt == 0:
                    interest_coverage[year] = NO_DEBT_COVERAGE
                    quality.note(
                        f"{year}: oneri finanziari nulli o assenti, Interest Coverage "
                        "convenzionalmente massimo."
                    )
                else:
                    interest_coverage[year] = None
                    quality.miss(f"{year}: oneri finanziari non disponibili.")
            else:
                interest_coverage[year] = _safe_div(ebit, abs(interest))
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: Interest Coverage non calcolabile ({exc}).")
            interest_coverage[year] = None

        try:
            current_ratio[year] = _safe_div(
                row.get("current_assets"), row.get("current_liabilities")
            )
            if current_ratio[year] is None:
                quality.miss(f"{year}: attivo/passivo corrente non disponibili (Current Ratio).")
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"{year}: Current Ratio non calcolabile ({exc}).")
            current_ratio[year] = None

    return {
        "debt_to_equity": debt_equity,
        "debt_to_ebitda": debt_ebitda,
        "interest_coverage": interest_coverage,
        "current_ratio": current_ratio,
    }


# ---------------------------------------------------------------------------
# 4. Consistenza di una serie storica
# ---------------------------------------------------------------------------


def calculate_consistency(
    serie_storica: Union[Sequence[Optional[float]], Mapping[int, Optional[float]]],
) -> Dict[str, Optional[float]]:
    """Statistiche di consistenza di una serie annuale di una metrica.

    Args:
        serie_storica: sequenza in ordine **cronologico crescente** (dal piu' vecchio
            al piu' recente) oppure dizionario ``{anno: valore}`` (riordinato
            automaticamente). I valori ``None``/NaN vengono ignorati.

    Returns:
        Dizionario con:
        ``n`` (osservazioni valide), ``mean``, ``std_dev`` (deviazione standard
        campionaria), ``coefficient_of_variation`` (std / |media|),
        ``growth_years`` e ``comparisons`` (numero di confronti anno su anno),
        ``growth_years_pct`` (% di anni in crescita), ``positive_years_pct``,
        ``min``, ``max`` e ``last``.
    """
    if isinstance(serie_storica, Mapping):
        ordered = [serie_storica[key] for key in sorted(serie_storica)]
    else:
        ordered = list(serie_storica)

    values = [v for v in (_to_float(x) for x in ordered) if v is not None]
    n = len(values)

    result: Dict[str, Optional[float]] = {
        "n": float(n),
        "mean": None,
        "std_dev": None,
        "coefficient_of_variation": None,
        "growth_years": None,
        "comparisons": None,
        "growth_years_pct": None,
        "positive_years_pct": None,
        "min": None,
        "max": None,
        "last": None,
    }
    if n == 0:
        return result

    mean = sum(values) / n
    result["mean"] = mean
    result["min"] = min(values)
    result["max"] = max(values)
    result["last"] = values[-1]
    result["positive_years_pct"] = 100.0 * sum(1 for v in values if v > 0) / n

    if n >= 2:
        std_dev = statistics.stdev(values)
        result["std_dev"] = std_dev
        if abs(mean) > 1e-12:
            result["coefficient_of_variation"] = std_dev / abs(mean)
        comparisons = n - 1
        growth_years = sum(
            1 for previous, current in zip(values, values[1:]) if current > previous
        )
        result["comparisons"] = float(comparisons)
        result["growth_years"] = float(growth_years)
        result["growth_years_pct"] = 100.0 * growth_years / comparisons

    return result


# ---------------------------------------------------------------------------
# 5. Scoring
# ---------------------------------------------------------------------------


def _score_linear(
    value: Optional[float],
    low: float,
    high: float,
) -> Optional[float]:
    """Mappa ``value`` su 0-100 in modo lineare fra ``low`` (0) e ``high`` (100).

    Se ``low > high`` la scala e' invertita (valori bassi = punteggio alto).
    """
    if value is None:
        return None
    if high == low:
        return None
    score = 100.0 * (value - low) / (high - low)
    return max(0.0, min(100.0, score))


# Il motore di punteggio (soglie, pesi, ridistribuzione) vive in ``sectors.py``:
# e' lo stesso per industriali, banche e assicurazioni, cambia solo il profilo.


def _industrial_metrics(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    years: Sequence[int],
    quality: _DataQuality,
) -> Dict[str, Dict[int, Optional[float]]]:
    """Serie annuali delle metriche di un'azienda operativa.

    L'equivalente per banche e assicurazioni e' ``sectors.build_metrics``.
    """
    roic = calculate_roic(fundamentals, quality)
    roe = calculate_roe(fundamentals, quality)
    roa = calculate_roa(fundamentals, quality)
    margins = calculate_margins(fundamentals, quality)
    maintenance_capex = estimate_maintenance_capex(fundamentals, quality)
    owner_earnings = calculate_owner_earnings(
        fundamentals, quality, maintenance_capex=maintenance_capex
    )
    tangible_return = calculate_return_on_tangible_capital(fundamentals, quality)
    ratios = calculate_balance_sheet_ratios(fundamentals, quality)

    owner_earnings_margin = {
        year: _safe_div(owner_earnings.get(year), fundamentals[year].get("revenue"), scale=100.0)
        for year in years
    }

    return {
        "revenue": {year: fundamentals[year].get("revenue") for year in years},
        "net_income": {year: fundamentals[year].get("net_income") for year in years},
        "ebit": {year: fundamentals[year].get("ebit") for year in years},
        "ebitda": {year: fundamentals[year].get("ebitda") for year in years},
        "invested_capital": {
            year: fundamentals[year].get("invested_capital_calc") for year in years
        },
        "roic": roic,
        "roe": roe,
        "roa": roa,
        "operating_margin": margins["operating_margin"],
        "net_margin": margins["net_margin"],
        "gross_margin": margins["gross_margin"],
        "owner_earnings": owner_earnings,
        "owner_earnings_margin": owner_earnings_margin,
        "maintenance_capex": {year: maintenance_capex.get(year) for year in years},
        "capex": {year: fundamentals[year].get("capex") for year in years},
        "return_on_tangible_capital": tangible_return,
        # Anni di Owner Earnings necessari a estinguere il debito: la domanda che conta,
        # e insensibile ai trucchi contabili che rendono l'EBITDA poco affidabile.
        "debt_to_owner_earnings": {
            year: (
                0.0 if (fundamentals[year].get("total_debt") == 0)
                else _safe_div(
                    fundamentals[year].get("total_debt"),
                    owner_earnings.get(year) if (owner_earnings.get(year) or 0) > 0 else None,
                )
            )
            for year in years
        },
        "debt_to_equity": ratios["debt_to_equity"],
        "debt_to_ebitda": ratios["debt_to_ebitda"],
        "interest_coverage": ratios["interest_coverage"],
        "current_ratio": ratios["current_ratio"],
        "effective_tax_rate": {
            year: (fundamentals[year].get("tax_rate") or 0.0) * 100.0 for year in years
        },
    }


def _rating(score: Optional[float]) -> str:
    if score is None:
        return "Non valutabile"
    if score >= 80:
        return "Eccellente"
    if score >= 65:
        return "Buona"
    if score >= 50:
        return "Discreta"
    if score >= 35:
        return "Debole"
    return "Scarsa"


def _normalize_weights(
    weights: Optional[Mapping[str, float]],
    quality: _DataQuality,
) -> Dict[str, float]:
    if not weights:
        return dict(DEFAULT_WEIGHTS)
    merged = dict(DEFAULT_WEIGHTS)
    for key, value in weights.items():
        if key not in DEFAULT_WEIGHTS:
            quality.note(f"Peso '{key}' ignorato: categorie valide {sorted(DEFAULT_WEIGHTS)}.")
            continue
        numeric = _to_float(value)
        if numeric is None or numeric < 0:
            quality.note(f"Peso '{key}' non valido ({value!r}): usato il default.")
            continue
        merged[key] = numeric
    total = sum(merged.values())
    if total <= 0:
        quality.note("Somma dei pesi nulla: ripristinati i pesi di default.")
        return dict(DEFAULT_WEIGHTS)
    if abs(total - 1.0) > 1e-9:
        quality.note(f"Pesi normalizzati: la somma fornita era {total:.3f}.")
        merged = {key: value / total for key, value in merged.items()}
    return merged


# ---------------------------------------------------------------------------
# 6. Funzione principale
# ---------------------------------------------------------------------------


def calculate_quality_score(
    ticker: str,
    weights: Optional[Mapping[str, float]] = None,
    years: int = DEFAULT_YEARS,
    financials: Optional[Mapping[str, Any]] = None,
    sector: Optional[str] = None,
    mode: str = "standard",
) -> Dict[str, Any]:
    """Calcola il Quality Score (0-100) di un'azienda.

    Args:
        ticker: simbolo di borsa (es. ``"AAPL"``).
        weights: pesi delle tre categorie (``profitability``, ``consistency``,
            ``balance_sheet``). Vengono normalizzati a somma 1.
            Default: 40% / 30% / 30%.
        years: numero massimo di esercizi da analizzare.
        financials: bilanci gia' scaricati (output di :func:`fetch_financials`).
            Utile per test offline o per evitare download ripetuti.
        sector: ``"industrial"``, ``"bank"`` o ``"insurance"``. Se omesso viene
            riconosciuto dalla struttura del bilancio (presenza di depositi, di premi
            assicurativi). Il settore determina **quali** metriche vengono calcolate e
            con quali soglie: applicare il metro industriale a una banca produce numeri
            plausibili e privi di significato.
        mode: ``"standard"`` oppure ``"buffett"``. In modalita' buffett le aziende
            operative vengono misurate sui criteri pubblicati negli annual report di
            Berkshire: rendimento ante imposte sul capitale **tangibile** al posto del
            ROIC, Debito / Owner Earnings al posto del Debt/EBITDA (che Buffett rifiuta
            apertamente), e soglie piu' severe su debito e continuita' degli utili.

    Returns:
        Dizionario con ``quality_score``, ``rating``, ``category_scores`` (punteggio
        e componenti per categoria), ``metrics`` (tutte le metriche anno per anno),
        ``consistency``, ``averages`` e ``data_quality`` (dati stimati/mancanti e note).
        Non solleva eccezioni: in caso di dati insufficienti ``quality_score`` e' ``None``
        e il motivo e' descritto in ``data_quality`` ed ``error``.
    """
    quality = _DataQuality()
    weights_used = _normalize_weights(weights, quality)

    result: Dict[str, Any] = {
        "ticker": ticker.upper(),
        "company_name": None,
        "currency": None,
        "sector": None,
        "sector_label": None,
        "mode": "standard",
        "profile": None,
        "years_analyzed": [],
        "quality_score": None,
        "rating": "Non valutabile",
        "weights": weights_used,
        "category_scores": {},
        "metrics": {},
        "consistency": {},
        "averages": {},
        "data_quality": {},
        "error": None,
    }

    try:
        if financials is None:
            financials = fetch_financials(ticker, years=years)
    except Exception as exc:  # pragma: no cover - difensivo, fetch non rilancia
        result["error"] = f"Download dei bilanci fallito: {exc}"
        result["data_quality"] = quality.as_dict()
        return result

    fetch_quality = financials.get("data_quality")
    if isinstance(fetch_quality, _DataQuality):
        for message in fetch_quality.notes:
            quality.note(message)
        for message in fetch_quality.estimated:
            quality.estimate(message)
        for message in fetch_quality.missing:
            quality.miss(message)
    elif isinstance(fetch_quality, Mapping):
        for message in fetch_quality.get("notes", []):
            quality.note(message)
        for message in fetch_quality.get("estimated", []):
            quality.estimate(message)
        for message in fetch_quality.get("missing", []):
            quality.miss(message)

    result["company_name"] = financials.get("company_name")
    result["currency"] = financials.get("currency")

    # Import ritardato: sectors importa da questo modulo, a livello globale sarebbe un ciclo.
    try:
        from . import sectors
    except ImportError:
        import sectors  # type: ignore[no-redef]

    if sector is None:
        sector = sectors.detect_sector(financials, quality)
    elif sector not in sectors.PROFILES:
        quality.note(f"Settore '{sector}' sconosciuto: uso il profilo industriale.")
        sector = sectors.INDUSTRIAL

    try:
        fundamentals = extract_fundamentals(financials, quality, sector=sector)
    except Exception as exc:  # pragma: no cover - difensivo
        result["error"] = f"Normalizzazione dei bilanci fallita: {exc}"
        result["data_quality"] = quality.as_dict()
        return result

    if not fundamentals:
        result["error"] = f"Nessun dato di bilancio utilizzabile per {ticker.upper()}."
        result["data_quality"] = quality.as_dict()
        return result

    years_desc = sorted(fundamentals, reverse=True)[:years]
    fundamentals = {year: fundamentals[year] for year in years_desc}
    result["years_analyzed"] = years_desc

    # --- Settore e modalita' ---------------------------------------------------
    profile, profile_key = sectors.resolve_profile(sector, mode)
    if str(mode).lower() == sectors.BUFFETT and profile_key != sectors.BUFFETT:
        quality.note(
            "Modalita' buffett ignorata: i criteri pubblicati di Berkshire riguardano "
            "aziende operative, non banche o assicurazioni."
        )
    result["sector"] = sector
    result["mode"] = profile_key if profile_key == sectors.BUFFETT else "standard"
    result["profile"] = profile_key
    result["sector_label"] = profile["label"]
    if sector != sectors.INDUSTRIAL:
        fundamentals = sectors.extract_sector_fundamentals(
            financials, sector, fundamentals, quality
        )

    # --- Metriche anno per anno ---------------------------------------------
    if sector != sectors.INDUSTRIAL:
        metrics: Dict[str, Dict[int, Optional[float]]] = sectors.build_metrics(
            fundamentals, sector, quality
        )
    else:
        metrics = _industrial_metrics(fundamentals, years_desc, quality)

    result["metrics"] = {
        name: {year: _round(series.get(year), 4) for year in years_desc}
        for name, series in metrics.items()
    }

    # --- Consistenza ---------------------------------------------------------
    consistency = {
        name: calculate_consistency(metrics[name])
        for name in sectors.CONSISTENCY_TARGETS[profile_key] if name in metrics
    }
    result["consistency"] = {
        name: {key: _round(value, 4) for key, value in stats.items()}
        for name, stats in consistency.items()
    }

    # --- Medie ---------------------------------------------------------------
    averages = {
        name: _mean(metrics[name].values())
        for name in sectors.AVERAGE_TARGETS[profile_key] if name in metrics
    }
    result["averages"] = {name: _round(value, 4) for name, value in averages.items()}

    # --- Punteggi ------------------------------------------------------------
    categories = sectors.score_categories(profile, averages, consistency, quality)
    for name, category in categories.items():
        category["weight"] = weights_used[name]
    result["category_scores"] = categories

    available = {
        name: category for name, category in categories.items() if category["score"] is not None
    }
    missing_categories = [name for name in categories if name not in available]
    for name in missing_categories:
        quality.miss(f"Categoria '{name}' non valutabile: peso ridistribuito sulle altre.")

    total_weight = sum(category["weight"] for category in available.values())
    if total_weight > 0:
        final = sum(
            category["score"] * category["weight"] for category in available.values()
        ) / total_weight
        result["quality_score"] = round(final, 1)
        result["rating"] = _rating(final)
    else:
        result["error"] = "Dati insufficienti per calcolare un punteggio."

    if len(years_desc) < 3:
        quality.note(
            f"Solo {len(years_desc)} esercizio/i disponibili: le metriche di consistenza "
            "sono poco significative."
        )

    result["data_quality"] = {
        **quality.as_dict(),
        "years_available": len(years_desc),
        "years_requested": years,
    }
    return result


# ---------------------------------------------------------------------------
# 7. Formattazione leggibile
# ---------------------------------------------------------------------------


def _fmt(value: Optional[float], digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "n/d"
    return f"{value:,.{digits}f}{suffix}"


def _fmt_big(value: Optional[float]) -> str:
    """Formatta un importo in milioni/miliardi."""
    if value is None:
        return "n/d"
    for divisor, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= divisor:
            return f"{value / divisor:,.2f}{unit}"
    return f"{value:,.0f}"


def _group_messages(entries: Sequence[str]) -> List[str]:
    """Compatta i messaggi ripetuti anno per anno in un'unica riga con l'elenco degli anni."""
    grouped: Dict[str, List[str]] = {}
    for entry in entries:
        match = re.match(r"^((?:19|20)\d{2}):\s*(.+)$", entry)
        if match:
            grouped.setdefault(match.group(2), []).append(match.group(1))
        else:
            grouped.setdefault(entry, [])
    output: List[str] = []
    for message, years in grouped.items():
        if not years:
            output.append(message)
        elif len(years) == 1:
            output.append(f"{message} ({years[0]})")
        else:
            ordered = sorted(years)
            output.append(f"{message} ({ordered[0]}-{ordered[-1]}, {len(ordered)} esercizi)")
    return output


def format_report(result: Mapping[str, Any], max_notes: int = 12) -> str:
    """Rende leggibile l'output di :func:`calculate_quality_score`."""
    lines: List[str] = []
    width = 92
    name = result.get("company_name") or result.get("ticker")
    lines.append("=" * width)
    lines.append(f" QUALITY SCORE - {name} ({result.get('ticker')})")
    lines.append("=" * width)

    if result.get("error"):
        lines.append(f" ERRORE: {result['error']}")

    score = result.get("quality_score")
    lines.append(
        f" Punteggio finale: {_fmt(score, 1)} / 100   ->  {result.get('rating')}"
    )
    years = result.get("years_analyzed") or []
    if years:
        lines.append(f" Esercizi analizzati: {len(years)} ({min(years)}-{max(years)})")
    if result.get("sector_label"):
        lines.append(f" Profilo di analisi: {result['sector_label']}")
    if result.get("currency"):
        lines.append(f" Valuta di bilancio: {result['currency']}")
    lines.append("")

    # --- Categorie -----------------------------------------------------------
    lines.append("-" * width)
    lines.append(" PUNTEGGI PER CATEGORIA")
    lines.append("-" * width)
    for key, category in (result.get("category_scores") or {}).items():
        lines.append(
            f" {category.get('label', key):<30} peso {category['weight'] * 100:>5.1f}%   "
            f"punteggio {_fmt(category['score'], 1):>6} / 100"
        )
        for component in category.get("components", {}).values():
            lines.append(
                f"    - {component['label']:<44} valore {_fmt(component['value'], 2):>10}"
                f"   score {_fmt(component['score'], 1):>6}   [{component['scale']}]"
            )
        lines.append("")

    # --- Tabella anno per anno ----------------------------------------------
    metrics = result.get("metrics") or {}
    if years:
        try:
            from . import sectors
        except ImportError:
            import sectors  # type: ignore[no-redef]
        table_rows = sectors.TABLE_ROWS.get(
            result.get("profile") or result.get("sector") or sectors.INDUSTRIAL,
            sectors.TABLE_ROWS[sectors.INDUSTRIAL],
        )
        lines.append("-" * width)
        lines.append(" METRICHE ANNO PER ANNO (dal piu' recente)")
        lines.append("-" * width)
        header = f" {'Metrica':<24}" + "".join(f"{year:>11}" for year in years)
        lines.append(header)
        lines.append(" " + "-" * (len(header) - 1))
        for label, key, kind in table_rows:
            series = metrics.get(key, {})
            cells = []
            for year in years:
                value = series.get(year)
                if kind == "big":
                    cells.append(f"{_fmt_big(value):>11}")
                elif kind == "pct":
                    cells.append(f"{_fmt(value, 1):>11}")
                else:
                    cells.append(f"{_fmt(value, 2):>11}")
            lines.append(f" {label:<24}" + "".join(cells))
        lines.append("")

    # --- Consistenza ---------------------------------------------------------
    consistency = result.get("consistency") or {}
    if consistency:
        lines.append("-" * width)
        lines.append(" CONSISTENZA (dev. std, coeff. di variazione, anni in crescita)")
        lines.append("-" * width)
        lines.append(
            f" {'Metrica':<24}{'n':>4}{'media':>12}{'dev.std':>12}{'CV':>10}{'crescita %':>13}"
        )
        for key, stats in consistency.items():
            compact = any(
                stats.get(field) is not None and abs(stats[field]) >= 1e6
                for field in ("mean", "std_dev")
            )
            render = _fmt_big if compact else (lambda value: _fmt(value, 2))
            lines.append(
                f" {key:<24}{int(stats.get('n') or 0):>4}"
                f"{render(stats.get('mean')):>12}"
                f"{render(stats.get('std_dev')):>12}"
                f"{_fmt(stats.get('coefficient_of_variation'), 2):>10}"
                f"{_fmt(stats.get('growth_years_pct'), 1):>13}"
            )
        lines.append("")

    # --- Qualita' del dato ---------------------------------------------------
    data_quality = result.get("data_quality") or {}
    lines.append("-" * width)
    lines.append(" QUALITA' DEL DATO")
    lines.append("-" * width)
    lines.append(
        f" Esercizi disponibili: {data_quality.get('years_available', 0)} "
        f"su {data_quality.get('years_requested', DEFAULT_YEARS)} richiesti"
    )
    for section, title in (
        ("estimated", "Dati stimati / approssimati"),
        ("missing", "Dati mancanti"),
        ("notes", "Note"),
    ):
        entries = _group_messages(data_quality.get(section) or [])
        if not entries:
            continue
        lines.append(f" {title} ({len(entries)}):")
        for entry in entries[:max_notes]:
            lines.append(f"    * {entry}")
        if len(entries) > max_notes:
            lines.append(f"    * ... e altre {len(entries) - max_notes} voci")
    lines.append("=" * width)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. Test da riga di comando
# ---------------------------------------------------------------------------


def _self_test() -> None:
    """Verifica offline della logica di calcolo con una serie sintetica."""
    print("Self-test di calculate_consistency():")
    serie = [10.0, 11.0, 10.5, 12.0, 13.5]
    stats = calculate_consistency(serie)
    print(f"  serie                    : {serie}")
    print(f"  media                    : {stats['mean']:.3f}")
    print(f"  deviazione standard      : {stats['std_dev']:.3f}")
    print(f"  coefficiente variazione  : {stats['coefficient_of_variation']:.3f}")
    print(
        f"  anni in crescita         : {int(stats['growth_years'])}/"
        f"{int(stats['comparisons'])} = {stats['growth_years_pct']:.1f}%"
    )
    print()


if __name__ == "__main__":
    tickers = [arg for arg in sys.argv[1:] if not arg.startswith("-")] or ["AAPL"]

    if "--self-test" in sys.argv:
        _self_test()

    for symbol in tickers:
        print(f"\nScarico i bilanci di {symbol.upper()} ...\n")
        report = calculate_quality_score(symbol)
        print(format_report(report))
