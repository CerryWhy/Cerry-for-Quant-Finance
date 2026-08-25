"""Valuation engine: valore intrinseco e margine di sicurezza.

Costruito sopra ``quality_score.py``: quello risponde a "e' una buona azienda?",
questo risponde a "a che prezzo vale la pena comprarla?".

Metodi implementati
-------------------
* **DCF sugli Owner Earnings** a due stadi con fade: N anni di crescita esplicita,
  discesa lineare verso la crescita terminale, valore terminale alla Gordon.
  Convenzione mid-year opzionale (standard nei modelli di M&A/PE).
* **Reverse DCF**: quale tasso di crescita e' gia' scontato nel prezzo di mercato?
  E' la domanda piu' utile per un value investor: invece di stimare il futuro,
  misura quanto ottimismo stai comprando.
* **EPV (Earnings Power Value, Greenwald)**: valore della capacita' di reddito
  attuale, crescita zero. E' il pavimento sotto cui il DCF non dovrebbe scendere.
* **Graham Number** e **NCAV/net-net**: riferimenti deep value classici.
* **Multipli storici** (P/E, EV/EBIT, P/OE) confrontati con la mediana del titolo
  stesso, non con il settore: e' il titolo che fa da benchmark a se' stesso.

Output
------
Un intervallo di fair value (min / mediana / max fra i metodi), il margine di
sicurezza rispetto al prezzo corrente, tre scenari (bear/base/bull), una griglia
di sensitivita' WACC x crescita terminale e l'elenco esplicito di ogni ipotesi e
approssimazione usata.

Nessun numero esce da questo modulo senza le ipotesi che lo hanno generato: un
valore intrinseco senza le sue assunzioni e' un'opinione travestita da calcolo.

Uso::

    python valuation.py AAPL
    python valuation.py MSFT --growth 0.08 --wacc 0.09
"""

from __future__ import annotations

import math
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import pandas as pd
except ImportError:  # pragma: no cover - ambiente senza pandas
    pd = None  # type: ignore[assignment]

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None  # type: ignore[assignment]

try:  # import come package (backend.models.valuation)
    from .quality_score import (
        DEFAULT_TAX_RATE,
        _DataQuality,
        _fmt,
        _fmt_big,
        _mean,
        _round,
        _safe_div,
        _to_float,
        calculate_owner_earnings,
        extract_fundamentals,
        fetch_financials,
    )
except ImportError:  # import come script standalone
    from quality_score import (  # type: ignore[no-redef]
        DEFAULT_TAX_RATE,
        _DataQuality,
        _fmt,
        _fmt_big,
        _mean,
        _round,
        _safe_div,
        _to_float,
        calculate_owner_earnings,
        extract_fundamentals,
        fetch_financials,
    )


__all__ = [
    "DEFAULT_ASSUMPTIONS",
    "calculate_valuation",
    "cost_of_capital",
    "dcf_value_per_share",
    "epv_value_per_share",
    "fetch_market_data",
    "format_valuation_report",
    "graham_number",
    "growth_path",
    "historical_cagr",
    "ncav_per_share",
    "normalize_series",
    "reverse_dcf",
    "sensitivity_grid",
]


# ---------------------------------------------------------------------------
# Ipotesi di default
# ---------------------------------------------------------------------------

DEFAULT_ASSUMPTIONS: Dict[str, Any] = {
    # Costo del capitale
    "risk_free_rate": 0.040,       # rendimento del decennale USA (aggiornabile)
    "equity_risk_premium": 0.050,  # premio al rischio azionario (Damodaran ~4.5-5.5%)
    "default_beta": 1.00,
    "min_wacc": 0.070,             # pavimento prudenziale: nessun titolo si sconta al 5%
    "max_wacc": 0.150,
    # Crescita
    "terminal_growth": 0.025,      # ~ inflazione + crescita reale di lungo periodo
    "max_growth": 0.150,           # tetto alla crescita esplicita: prudenza Buffett-style
    "min_growth": -0.050,
    "projection_years": 10,
    "fade_years": 5,               # ultimi anni in cui la crescita scende verso quella terminale
    # Normalizzazione degli utili
    "normalization": "median3",    # last | mean3 | median3 | mean5 | median5
    "mid_year_convention": True,
    # Scenari
    "scenario_growth_delta": 0.04,
    "scenario_wacc_delta": 0.015,
    "scenario_roe_delta": 0.03,       # ampiezza degli scenari sul ROE (finanziari)
    # Rendimento in eccesso a fine periodo per i finanziari: zero significa che la
    # concorrenza erode il vantaggio entro l'orizzonte, e il modello non dipende da
    # ipotesi oltre il decimo anno.
    "terminal_roe_premium": 0.000,
    # Soglia operativa
    "target_margin_of_safety": 0.30,
}

#: Pesi con cui i metodi "going concern" formano il fair value di sintesi.
#: Il DCF guida, l'EPV fa da ancora prudenziale, i multipli storici da controllo di
#: mercato. I pesi vengono rinormalizzati sui soli metodi effettivamente calcolabili.
AGGREGATE_WEIGHTS: Dict[str, float] = {
    "dcf_owner_earnings": 0.60,
    "epv": 0.15,
    "historical_multiples": 0.25,
}

#: Pesi per banche e assicurazioni. Il residual income guida (e' il modello corretto
#: per il capitale di un finanziario), il P/B giustificato fa da controllo rapido sulla
#: redditivita', i multipli storici da riferimento di mercato.
FINANCIAL_AGGREGATE_WEIGHTS: Dict[str, float] = {
    "residual_income": 0.50,
    "justified_price_to_book": 0.30,
    "historical_multiples": 0.20,
}

#: Metodi mostrati come riferimento ma **esclusi** dalla sintesi.
#: Graham Number e NCAV nascono per aziende asset-heavy comprate a sconto sul
#: patrimonio: applicati a un'azienda asset-light (o che ha ricomprato azioni
#: erodendo il patrimonio netto) producono numeri sistematicamente insignificanti.
#: Restano nel report come pavimento di liquidazione, non come stima di valore.
REFERENCE_METHODS = ("graham_number", "ncav")

#: Soglie del giudizio finale sul margine di sicurezza.
VERDICT_THRESHOLDS = (
    (0.30, "Sconto significativo"),
    (0.10, "Moderatamente sottovalutata"),
    (-0.10, "In linea con il valore stimato"),
    (-0.30, "Moderatamente sopravvalutata"),
)


# ---------------------------------------------------------------------------
# Dati di mercato
# ---------------------------------------------------------------------------


def fetch_market_data(ticker: str, quality: Optional[_DataQuality] = None) -> Dict[str, Any]:
    """Scarica prezzo, numero di azioni, beta e capitalizzazione.

    Prova prima ``fast_info`` (veloce e stabile), poi ``info``, poi la serie storica.
    Non solleva eccezioni: i campi non recuperabili restano ``None``.
    """
    quality = quality if quality is not None else _DataQuality()
    data: Dict[str, Any] = {
        "price": None, "shares_outstanding": None, "market_cap": None,
        "beta": None, "currency": None, "company_name": None,
    }
    if yf is None:
        quality.miss("yfinance non installato: dati di mercato non disponibili.")
        return data

    try:
        handle = yf.Ticker(ticker)
    except Exception as exc:  # pragma: no cover - dipende dalla rete
        quality.miss(f"Ticker {ticker} non inizializzabile: {exc}")
        return data

    try:
        fast = handle.fast_info
        data["price"] = _to_float(getattr(fast, "last_price", None))
        data["shares_outstanding"] = _to_float(getattr(fast, "shares", None))
        data["market_cap"] = _to_float(getattr(fast, "market_cap", None))
        data["currency"] = getattr(fast, "currency", None)
    except Exception:
        quality.note("fast_info non disponibile: si passa a info/history.")

    if data["price"] is None or data["shares_outstanding"] is None or data["beta"] is None:
        try:
            info = handle.get_info() if hasattr(handle, "get_info") else getattr(handle, "info", {})
            if isinstance(info, dict):
                data["price"] = data["price"] or _to_float(
                    info.get("currentPrice") or info.get("regularMarketPrice")
                )
                data["shares_outstanding"] = data["shares_outstanding"] or _to_float(
                    info.get("sharesOutstanding")
                )
                data["market_cap"] = data["market_cap"] or _to_float(info.get("marketCap"))
                data["beta"] = _to_float(info.get("beta"))
                data["currency"] = data["currency"] or info.get("financialCurrency")
                data["company_name"] = info.get("longName") or info.get("shortName")
        except Exception:
            quality.note("Endpoint info non raggiungibile.")

    if data["price"] is None:
        try:
            history = handle.history(period="5d")
            if history is not None and not history.empty:
                data["price"] = _to_float(history["Close"].iloc[-1])
        except Exception:
            quality.miss("Prezzo di mercato non recuperabile.")

    if data["market_cap"] is None and data["price"] and data["shares_outstanding"]:
        data["market_cap"] = data["price"] * data["shares_outstanding"]

    for field in ("price", "shares_outstanding"):
        if data[field] is None:
            quality.miss(f"Dato di mercato mancante: {field}.")
    return data


def fetch_price_history(ticker: str, period: str = "10y") -> Optional[Any]:
    """Serie storica dei prezzi rettificati (dividendi inclusi). ``None`` se non disponibile."""
    if yf is None:
        return None
    try:
        history = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if history is None or history.empty:
            return None
        return history["Close"]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Costo del capitale
# ---------------------------------------------------------------------------


def cost_of_capital(
    *,
    beta: Optional[float],
    market_cap: Optional[float],
    total_debt: Optional[float],
    interest_expense: Optional[float],
    tax_rate: Optional[float],
    assumptions: Mapping[str, Any],
    quality: Optional[_DataQuality] = None,
    override: Optional[float] = None,
) -> Dict[str, Any]:
    """WACC = E/V * Ke + D/V * Kd * (1 - t), con Ke da CAPM.

    ``Ke = risk free + beta * equity risk premium``;
    ``Kd = oneri finanziari / debito totale`` (costo effettivo del debito a bilancio).
    Il risultato viene limitato all'intervallo ``[min_wacc, max_wacc]``: un WACC
    fuori da quella fascia e' quasi sempre il sintomo di un input sporco, non di
    un'informazione vera.
    """
    quality = quality if quality is not None else _DataQuality()
    risk_free = float(assumptions["risk_free_rate"])
    erp = float(assumptions["equity_risk_premium"])
    tax = tax_rate if tax_rate is not None else DEFAULT_TAX_RATE

    if beta is None:
        beta = float(assumptions["default_beta"])
        quality.estimate(f"Beta non disponibile: usato il default {beta:.2f}.")
    beta = max(0.3, min(2.5, beta))

    cost_equity = risk_free + beta * erp

    cost_debt = _safe_div(abs(interest_expense) if interest_expense else None, total_debt)
    if cost_debt is None or not (0.001 <= cost_debt <= 0.25):
        fallback = risk_free + 0.015
        if cost_debt is not None:
            quality.estimate(
                f"Costo del debito implausibile ({cost_debt:.1%}): sostituito con "
                f"risk free + 150bp = {fallback:.1%}."
            )
        else:
            quality.estimate(f"Costo del debito non calcolabile: usato {fallback:.1%}.")
        cost_debt = fallback

    equity_value = market_cap
    debt_value = total_debt or 0.0
    if equity_value is None or equity_value <= 0:
        weight_equity, weight_debt = 1.0, 0.0
        quality.estimate("Capitalizzazione non disponibile: WACC assimilato al costo dell'equity.")
    else:
        total_value = equity_value + debt_value
        weight_equity = equity_value / total_value
        weight_debt = debt_value / total_value

    wacc = weight_equity * cost_equity + weight_debt * cost_debt * (1.0 - tax)

    raw_wacc = wacc
    wacc = max(float(assumptions["min_wacc"]), min(float(assumptions["max_wacc"]), wacc))
    if abs(wacc - raw_wacc) > 1e-9:
        quality.note(f"WACC calcolato {raw_wacc:.2%} riportato nel range prudenziale: {wacc:.2%}.")

    if override is not None:
        quality.note(f"WACC forzato dall'utente a {override:.2%} (calcolato: {wacc:.2%}).")
        wacc = float(override)

    return {
        "wacc": wacc,
        "cost_of_equity": cost_equity,
        "cost_of_debt": cost_debt,
        "after_tax_cost_of_debt": cost_debt * (1.0 - tax),
        "beta": beta,
        "risk_free_rate": risk_free,
        "equity_risk_premium": erp,
        "tax_rate": tax,
        "weight_equity": weight_equity,
        "weight_debt": weight_debt,
    }


# ---------------------------------------------------------------------------
# Normalizzazione e crescita
# ---------------------------------------------------------------------------


def normalize_series(
    series: Mapping[int, Optional[float]],
    method: str = "median3",
    quality: Optional[_DataQuality] = None,
    label: str = "serie",
) -> Optional[float]:
    """Valore normalizzato di una serie annuale (ultimo, media o mediana su N anni).

    La normalizzazione serve a non ancorare una valutazione decennale all'ultimo
    esercizio, che puo' essere un picco o una valle del ciclo.
    """
    quality = quality if quality is not None else _DataQuality()
    values = [series[year] for year in sorted(series, reverse=True) if series.get(year) is not None]
    if not values:
        quality.miss(f"{label}: nessun valore disponibile per la normalizzazione.")
        return None

    method = (method or "median3").lower()
    if method == "last":
        return values[0]

    window = 5 if method.endswith("5") else 3
    sample = values[:window]
    if len(sample) < window:
        quality.note(
            f"{label}: normalizzazione '{method}' richiede {window} esercizi, "
            f"disponibili {len(sample)}."
        )
    if method.startswith("median"):
        ordered = sorted(sample)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0
    return sum(sample) / len(sample)


def historical_cagr(series: Mapping[int, Optional[float]]) -> Optional[float]:
    """CAGR fra il primo e l'ultimo valore disponibile della serie.

    Restituisce ``None`` se gli estremi non sono entrambi positivi: un CAGR
    calcolato su una base negativa non ha significato economico.
    """
    years = [year for year in sorted(series) if series.get(year) is not None]
    if len(years) < 2:
        return None
    first, last = series[years[0]], series[years[-1]]
    periods = years[-1] - years[0]
    if first is None or last is None or first <= 0 or last <= 0 or periods <= 0:
        return None
    return (last / first) ** (1.0 / periods) - 1.0


def growth_path(
    initial_growth: float,
    terminal_growth: float,
    years: int,
    fade_years: int,
) -> List[float]:
    """Sentiero di crescita: ``years - fade_years`` anni stabili, poi fade lineare.

    Il fade evita il salto artificiale fra l'ultimo anno di crescita esplicita e la
    crescita perpetua, che e' uno dei modi piu' comuni per gonfiare un DCF.
    """
    years = max(1, int(years))
    fade_years = max(0, min(int(fade_years), years))
    stable_years = years - fade_years
    path = [initial_growth] * stable_years
    for step in range(1, fade_years + 1):
        path.append(initial_growth + (terminal_growth - initial_growth) * step / fade_years)
    return path


# ---------------------------------------------------------------------------
# Metodi di valutazione
# ---------------------------------------------------------------------------


def dcf_value_per_share(
    base_cash_flow: Optional[float],
    *,
    discount_rate: float,
    initial_growth: float,
    terminal_growth: float,
    net_debt: Optional[float],
    shares: Optional[float],
    projection_years: int = 10,
    fade_years: int = 5,
    mid_year: bool = True,
) -> Dict[str, Any]:
    """DCF a due stadi con fade. Restituisce valore per azione e dettaglio dei flussi."""
    result: Dict[str, Any] = {
        "value_per_share": None, "enterprise_value": None, "equity_value": None,
        "pv_explicit": None, "pv_terminal": None, "terminal_weight": None,
        "cash_flows": [], "error": None,
    }
    if base_cash_flow is None or shares is None or shares <= 0:
        result["error"] = "Flusso base o numero di azioni non disponibile."
        return result
    if base_cash_flow <= 0:
        result["error"] = "Flusso di cassa base non positivo: DCF non significativo."
        return result
    if discount_rate <= terminal_growth:
        result["error"] = (
            f"Tasso di sconto ({discount_rate:.2%}) non superiore alla crescita terminale "
            f"({terminal_growth:.2%}): il valore terminale divergerebbe."
        )
        return result

    path = growth_path(initial_growth, terminal_growth, projection_years, fade_years)
    cash_flow = float(base_cash_flow)
    pv_explicit = 0.0
    flows: List[Dict[str, float]] = []
    for year, growth in enumerate(path, start=1):
        cash_flow *= (1.0 + growth)
        exponent = year - 0.5 if mid_year else year
        present_value = cash_flow / ((1.0 + discount_rate) ** exponent)
        pv_explicit += present_value
        flows.append({
            "year": year, "growth": growth,
            "cash_flow": cash_flow, "present_value": present_value,
        })

    terminal_value = cash_flow * (1.0 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1.0 + discount_rate) ** len(path))

    enterprise_value = pv_explicit + pv_terminal
    equity_value = enterprise_value - (net_debt or 0.0)

    result.update({
        "value_per_share": equity_value / shares,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "pv_explicit": pv_explicit,
        "pv_terminal": pv_terminal,
        "terminal_value": terminal_value,
        "terminal_weight": pv_terminal / enterprise_value if enterprise_value else None,
        "cash_flows": flows,
    })
    return result


def reverse_dcf(
    price: Optional[float],
    base_cash_flow: Optional[float],
    *,
    discount_rate: float,
    terminal_growth: float,
    net_debt: Optional[float],
    shares: Optional[float],
    projection_years: int = 10,
    fade_years: int = 5,
    mid_year: bool = True,
    bounds: Tuple[float, float] = (-0.30, 0.50),
    tolerance: float = 1e-4,
) -> Dict[str, Any]:
    """Crescita implicita nel prezzo: quale ``g`` rende il DCF uguale alla quotazione?

    Il valore per azione e' monotono crescente in ``g``, quindi la bisezione converge
    sempre. Se il prezzo cade fuori dall'intervallo raggiungibile, viene restituito
    l'estremo con la segnalazione relativa.
    """
    result: Dict[str, Any] = {"implied_growth": None, "at_bound": None, "error": None}
    if price is None or price <= 0:
        result["error"] = "Prezzo di mercato non disponibile."
        return result

    def value_at(growth: float) -> Optional[float]:
        return dcf_value_per_share(
            base_cash_flow, discount_rate=discount_rate, initial_growth=growth,
            terminal_growth=terminal_growth, net_debt=net_debt, shares=shares,
            projection_years=projection_years, fade_years=fade_years, mid_year=mid_year,
        )["value_per_share"]

    low, high = bounds
    value_low, value_high = value_at(low), value_at(high)
    if value_low is None or value_high is None:
        result["error"] = "DCF non calcolabile con questi input."
        return result
    if price <= value_low:
        result.update({"implied_growth": low, "at_bound": "min"})
        return result
    if price >= value_high:
        result.update({"implied_growth": high, "at_bound": "max"})
        return result

    for _ in range(200):
        middle = (low + high) / 2.0
        value_middle = value_at(middle)
        if value_middle is None:
            break
        if abs(value_middle - price) < tolerance * max(1.0, price):
            result["implied_growth"] = middle
            return result
        if value_middle < price:
            low = middle
        else:
            high = middle
    result["implied_growth"] = (low + high) / 2.0
    return result


def epv_value_per_share(
    normalized_ebit: Optional[float],
    *,
    tax_rate: float,
    discount_rate: float,
    net_debt: Optional[float],
    shares: Optional[float],
) -> Dict[str, Any]:
    """EPV di Greenwald: valore della capacita' di reddito attuale, crescita zero.

    ``EPV = EBIT normalizzato * (1 - aliquota) / WACC``, poi si sottrae il debito netto.
    Se il prezzo di mercato e' sotto l'EPV, il mercato sta pagando zero (o meno) per
    la crescita futura.
    """
    result: Dict[str, Any] = {"value_per_share": None, "enterprise_value": None, "error": None}
    if normalized_ebit is None or shares is None or shares <= 0:
        result["error"] = "EBIT normalizzato o numero di azioni non disponibile."
        return result
    if discount_rate <= 0:
        result["error"] = "Tasso di sconto non valido."
        return result
    nopat = normalized_ebit * (1.0 - tax_rate)
    if nopat <= 0:
        result["error"] = "NOPAT normalizzato non positivo."
        return result
    enterprise_value = nopat / discount_rate
    equity_value = enterprise_value - (net_debt or 0.0)
    result.update({
        "value_per_share": equity_value / shares,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "nopat": nopat,
    })
    return result


def residual_income_value_per_share(
    book_value_per_share: Optional[float],
    *,
    return_on_equity: Optional[float],
    cost_of_equity: float,
    growth: float,
    projection_years: int = 10,
    fade_years: int = 5,
    terminal_roe: Optional[float] = None,
) -> Dict[str, Any]:
    """Modello a rendimenti in eccesso (residual income) — la valutazione dei finanziari.

    Per una banca il DCF non e' applicabile: non esiste un flusso di cassa operativo
    separabile da quello di finanziamento, perche' raccolta e impieghi *sono* l'attivita'.
    Si valuta allora il capitale::

        Valore = Patrimonio contabile
               + valore attuale di [(ROE - Ke) x Patrimonio] per ogni anno

    L'intuizione: una banca vale il suo patrimonio, **piu'** quanto riesce a rendere
    *sopra* il costo del capitale. Se ROE = Ke vale esattamente il book value.

    Funziona sui finanziari e non sugli industriali perche' il patrimonio contabile di
    una banca e' vicino al valore di mercato dei suoi attivi (crediti e titoli, in buona
    parte valutati a mercato), mentre per un industriale il valore sta in marchi e
    posizione competitiva, che in bilancio non compaiono.

    Args:
        book_value_per_share: patrimonio (o patrimonio tangibile) per azione.
        return_on_equity: ROE/ROTCE normalizzato, in forma decimale (``0.14`` = 14%).
        cost_of_equity: Ke da CAPM. **Mai il WACC**: per un finanziario il debito e'
            materia prima, non finanziamento.
        growth: crescita annua del patrimonio.
        terminal_roe: ROE a fine periodo. Il default e' ``cost_of_equity``, cioe' i
            rendimenti in eccesso si azzerano: in quel caso non c'e' valore terminale
            e la stima non dipende da ipotesi oltre l'orizzonte esplicito.
    """
    result: Dict[str, Any] = {
        "value_per_share": None, "book_value": None, "pv_excess_returns": None,
        "pv_terminal": None, "excess_returns": [], "error": None,
    }
    if book_value_per_share is None or return_on_equity is None:
        result["error"] = "Patrimonio per azione o ROE non disponibile."
        return result
    if book_value_per_share <= 0:
        result["error"] = "Patrimonio contabile non positivo: modello non applicabile."
        return result
    if cost_of_equity <= growth:
        result["error"] = (
            f"Costo dell'equity ({cost_of_equity:.2%}) non superiore alla crescita "
            f"({growth:.2%}): il valore divergerebbe."
        )
        return result

    terminal = cost_of_equity if terminal_roe is None else terminal_roe
    path = growth_path(return_on_equity, terminal, projection_years, fade_years)

    book = float(book_value_per_share)
    present_value = 0.0
    flows: List[Dict[str, float]] = []
    for year, roe in enumerate(path, start=1):
        # Il rendimento in eccesso si calcola sul patrimonio di **inizio** anno.
        excess = (roe - cost_of_equity) * book
        discounted = excess / ((1.0 + cost_of_equity) ** year)
        present_value += discounted
        flows.append({
            "year": year, "roe": roe, "book_value": book,
            "excess_return": excess, "present_value": discounted,
        })
        book *= (1.0 + growth)

    pv_terminal = 0.0
    if terminal > cost_of_equity:
        terminal_excess = (terminal - cost_of_equity) * book
        pv_terminal = (terminal_excess / (cost_of_equity - growth)) / (
            (1.0 + cost_of_equity) ** len(path)
        )

    result.update({
        "value_per_share": book_value_per_share + present_value + pv_terminal,
        "book_value": book_value_per_share,
        "pv_excess_returns": present_value,
        "pv_terminal": pv_terminal,
        "excess_returns": flows,
    })
    return result


def justified_price_to_book(
    book_value_per_share: Optional[float],
    *,
    return_on_equity: Optional[float],
    cost_of_equity: float,
    growth: float,
) -> Dict[str, Any]:
    """P/B giustificato dalla redditivita': ``(ROE - g) / (Ke - g)``.

    Deriva dalla formula di Gordon applicata al patrimonio. Dice a quale multiplo del
    patrimonio *dovrebbe* trattare un finanziario, dato quanto rende. Una banca che
    rende il 15% con Ke 10% e crescita 3% vale ``(0.15-0.03)/(0.10-0.03)`` = 1.7 volte
    il patrimonio tangibile; se tratta a 1.0x e' a sconto.

    E' il controllo di realta' piu' rapido su un finanziario, e il motivo per cui i
    multipli di patrimonio hanno senso qui e non su un'azienda asset-light.
    """
    result: Dict[str, Any] = {"value_per_share": None, "justified_multiple": None, "error": None}
    if book_value_per_share is None or return_on_equity is None:
        result["error"] = "Patrimonio per azione o ROE non disponibile."
        return result
    if book_value_per_share <= 0:
        result["error"] = "Patrimonio contabile non positivo."
        return result
    if cost_of_equity <= growth:
        result["error"] = "Costo dell'equity non superiore alla crescita."
        return result
    if return_on_equity <= growth:
        result["error"] = (
            f"ROE ({return_on_equity:.2%}) non superiore alla crescita ({growth:.2%}): "
            "il multiplo giustificato sarebbe nullo o negativo."
        )
        return result

    multiple = (return_on_equity - growth) / (cost_of_equity - growth)
    result.update({
        "justified_multiple": multiple,
        "value_per_share": multiple * book_value_per_share,
    })
    return result


def reverse_residual_income(
    price: Optional[float],
    book_value_per_share: Optional[float],
    *,
    cost_of_equity: float,
    growth: float,
    projection_years: int = 10,
    fade_years: int = 5,
    bounds: Tuple[float, float] = (0.0, 0.40),
    tolerance: float = 1e-4,
) -> Dict[str, Any]:
    """ROE implicito nel prezzo: quale redditivita' sta gia' scontando il mercato?

    E' l'equivalente del reverse DCF per i finanziari, e ha lo stesso pregio: non
    richiede di stimare il futuro, misura le aspettative gia' incorporate nel prezzo.
    Il confronto con il ROE storico dice quanto ottimismo (o pessimismo) stai comprando.
    """
    result: Dict[str, Any] = {"implied_roe": None, "at_bound": None, "error": None}
    if price is None or price <= 0:
        result["error"] = "Prezzo di mercato non disponibile."
        return result
    if book_value_per_share is None or book_value_per_share <= 0:
        result["error"] = "Patrimonio per azione non disponibile o non positivo."
        return result

    def value_at(roe: float) -> Optional[float]:
        return residual_income_value_per_share(
            book_value_per_share, return_on_equity=roe, cost_of_equity=cost_of_equity,
            growth=growth, projection_years=projection_years, fade_years=fade_years,
        )["value_per_share"]

    low, high = bounds
    value_low, value_high = value_at(low), value_at(high)
    if value_low is None or value_high is None:
        result["error"] = "Modello non calcolabile con questi input."
        return result
    if price <= value_low:
        result.update({"implied_roe": low, "at_bound": "min"})
        return result
    if price >= value_high:
        result.update({"implied_roe": high, "at_bound": "max"})
        return result

    for _ in range(200):
        middle = (low + high) / 2.0
        value_middle = value_at(middle)
        if value_middle is None:
            break
        if abs(value_middle - price) < tolerance * max(1.0, price):
            result["implied_roe"] = middle
            return result
        if value_middle < price:
            low = middle
        else:
            high = middle
    result["implied_roe"] = (low + high) / 2.0
    return result


def graham_number(eps: Optional[float], book_value_per_share: Optional[float]) -> Optional[float]:
    """Graham Number = sqrt(22.5 * EPS * valore contabile per azione).

    Il 22.5 e' il prodotto dei due tetti di Graham: P/E 15 e P/B 1.5. Ha senso solo
    con utili e patrimonio entrambi positivi.
    """
    if eps is None or book_value_per_share is None:
        return None
    if eps <= 0 or book_value_per_share <= 0:
        return None
    return math.sqrt(22.5 * eps * book_value_per_share)


def ncav_per_share(
    current_assets: Optional[float],
    total_liabilities: Optional[float],
    shares: Optional[float],
) -> Optional[float]:
    """Net Current Asset Value per azione (il "net-net" di Graham).

    Valore di liquidazione approssimato: attivo corrente meno *tutte* le passivita'.
    Raramente positivo in modo interessante, ma quando lo e' segnala un pavimento duro.
    """
    if current_assets is None or total_liabilities is None or not shares or shares <= 0:
        return None
    return (current_assets - total_liabilities) / shares


def historical_multiples_valuation(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    price_history: Any,
    *,
    shares: Optional[float],
    current_metrics: Mapping[str, Optional[float]],
    quality: Optional[_DataQuality] = None,
) -> Dict[str, Any]:
    """Valuta il titolo contro la mediana dei propri multipli storici.

    Per ogni esercizio prende il prezzo alla data di chiusura del bilancio e calcola
    P/E e P/Owner Earnings; la mediana storica moltiplicata per la metrica corrente
    da' il valore implicito. Il confronto e' con la storia del titolo stesso, non con
    un settore: e' il modo piu' robusto di usare i multipli senza fare peer picking.
    """
    quality = quality if quality is not None else _DataQuality()
    result: Dict[str, Any] = {"value_per_share": None, "multiples": {}, "error": None}
    if price_history is None or pd is None or not shares or shares <= 0:
        result["error"] = "Storico prezzi o numero di azioni non disponibile."
        return result

    try:
        closes = price_history.dropna()
        if closes.empty:
            result["error"] = "Storico prezzi vuoto."
            return result
        index = closes.index
        try:  # le serie di yfinance sono tz-aware: uniformiamo per il confronto
            index = index.tz_localize(None)
            closes = pd.Series(closes.values, index=index)
        except (TypeError, AttributeError):
            pass

        pe_ratios: List[float] = []
        poe_ratios: List[float] = []
        for year, row in fundamentals.items():
            try:
                target = pd.Timestamp(year=int(year), month=12, day=31)
                position = closes.index.get_indexer([target], method="nearest")[0]
                if position < 0:
                    continue
                price_at_year = _to_float(closes.iloc[position])
            except Exception:
                continue
            if price_at_year is None:
                continue
            eps = _safe_div(row.get("net_income"), shares)
            owner_earnings_ps = _safe_div(row.get("owner_earnings"), shares)
            if eps and eps > 0:
                pe_ratios.append(price_at_year / eps)
            if owner_earnings_ps and owner_earnings_ps > 0:
                poe_ratios.append(price_at_year / owner_earnings_ps)

        def median(values: Sequence[float]) -> Optional[float]:
            if not values:
                return None
            ordered = sorted(values)
            middle = len(ordered) // 2
            if len(ordered) % 2:
                return ordered[middle]
            return (ordered[middle - 1] + ordered[middle]) / 2.0

        median_pe = median(pe_ratios)
        median_poe = median(poe_ratios)
        result["multiples"] = {
            "median_pe": median_pe, "median_price_to_owner_earnings": median_poe,
            "observations_pe": len(pe_ratios), "observations_poe": len(poe_ratios),
        }

        candidates: List[float] = []
        current_eps = current_metrics.get("eps")
        current_oe_ps = current_metrics.get("owner_earnings_per_share")
        if median_pe and current_eps and current_eps > 0:
            candidates.append(median_pe * current_eps)
        if median_poe and current_oe_ps and current_oe_ps > 0:
            candidates.append(median_poe * current_oe_ps)
        if candidates:
            result["value_per_share"] = sum(candidates) / len(candidates)
        else:
            result["error"] = "Multipli storici non calcolabili (utili o OE non positivi)."
    except Exception as exc:  # pragma: no cover - difensivo
        result["error"] = f"Errore nel calcolo dei multipli storici: {exc}"
        quality.note(result["error"])
    return result


# ---------------------------------------------------------------------------
# Sensitivita'
# ---------------------------------------------------------------------------


def sensitivity_grid(
    *,
    base_cash_flow: Optional[float],
    wacc_values: Sequence[float],
    growth_values: Sequence[float],
    initial_growth: float,
    net_debt: Optional[float],
    shares: Optional[float],
    projection_years: int,
    fade_years: int,
    mid_year: bool,
    price: Optional[float] = None,
) -> Dict[str, Any]:
    """Griglia valore-per-azione al variare di WACC (x) e crescita terminale (y).

    E' la tabella di sensitivita' classica dei modelli di investimento, e la base
    della superficie 3D in ``visualize.py``. Con ``price`` valorizzato viene
    calcolata anche la griglia dell'upside percentuale rispetto al prezzo corrente.
    """
    values: List[List[Optional[float]]] = []
    upside: List[List[Optional[float]]] = []
    for terminal in growth_values:
        row_values: List[Optional[float]] = []
        row_upside: List[Optional[float]] = []
        for wacc in wacc_values:
            outcome = dcf_value_per_share(
                base_cash_flow, discount_rate=wacc, initial_growth=initial_growth,
                terminal_growth=terminal, net_debt=net_debt, shares=shares,
                projection_years=projection_years, fade_years=fade_years, mid_year=mid_year,
            )
            value = outcome["value_per_share"]
            row_values.append(value)
            row_upside.append(
                (value / price - 1.0) * 100.0 if (value is not None and price) else None
            )
        values.append(row_values)
        upside.append(row_upside)
    # Le etichette restano in inglese: sono chiavi stabili consumate da visualize.py,
    # che le traduce nella lingua scelta al momento di disegnare.
    return {
        "x_label": "WACC", "x_values": [float(v) for v in wacc_values],
        "y_label": "Terminal growth", "y_values": [float(v) for v in growth_values],
        "z_label": "Value per share", "values": values,
        "upside_pct": upside,
    }


# ---------------------------------------------------------------------------
# Funzione principale
# ---------------------------------------------------------------------------


def _financial_valuation(
    *,
    result: Dict[str, Any],
    financials: Mapping[str, Any],
    fundamentals: Dict[int, Dict[str, Optional[float]]],
    sector: str,
    capital: Mapping[str, Any],
    price: Optional[float],
    shares: Optional[float],
    config: Mapping[str, Any],
    quality: _DataQuality,
    sectors_module: Any,
    price_history: Optional[Any] = None,
) -> Dict[str, Any]:
    """Valutazione di banche e assicurazioni: residual income, P/B giustificato, multipli.

    Tre differenze sostanziali rispetto al percorso industriale:

    1. si sconta al **costo dell'equity**, mai al WACC — per un finanziario il debito e'
       materia prima, non finanziamento, e un WACC sarebbe un errore concettuale;
    2. la base non e' un flusso di cassa ma il **patrimonio contabile**, che per una
       banca e' vicino al valore di mercato degli attivi;
    3. la crescita che conta e' quella del **patrimonio per azione**, non dei ricavi.
    """
    fundamentals = sectors_module.extract_sector_fundamentals(
        financials, sector, fundamentals, quality
    )
    metrics = sectors_module.build_metrics(fundamentals, sector, quality)
    years_desc = sorted(fundamentals, reverse=True)

    cost_of_equity = float(capital["cost_of_equity"])
    normalization = str(config["normalization"])

    # Per una banca il ROTCE e' il rendimento sul capitale che assorbe davvero le
    # perdite; il ROE serve da riserva quando l'avviamento non e' separabile.
    roe_percent = normalize_series(metrics.get("rotce", {}), normalization, quality, "ROTCE")
    equity_basis = "tangible_book_per_share"
    if roe_percent is None:
        roe_percent = normalize_series(metrics.get("roe", {}), normalization, quality, "ROE")
        equity_basis = "book_value_per_share"
        quality.estimate("ROTCE non calcolabile: usato il ROE sul patrimonio contabile.")

    book_series = metrics.get(equity_basis) or metrics.get("book_value_per_share") or {}
    if not book_series and shares:
        book_series = {
            year: _safe_div(fundamentals[year].get("equity"), shares) for year in years_desc
        }
    book_value_per_share = normalize_series(book_series, "last", quality, "Patrimonio per azione")

    growth_book = historical_cagr(book_series)
    growth_used = growth_book if growth_book is not None else float(config["terminal_growth"])
    if growth_book is None:
        quality.estimate(
            f"Crescita del patrimonio per azione non calcolabile: usata quella terminale "
            f"({growth_used:.2%})."
        )
    raw_growth = growth_used
    ceiling = min(float(config["max_growth"]), cost_of_equity - 0.02)
    growth_used = max(float(config["min_growth"]), min(ceiling, growth_used))
    if abs(growth_used - raw_growth) > 1e-9:
        quality.note(
            f"Crescita del patrimonio {raw_growth:.2%} riportata nel range prudenziale "
            f"(massimo {ceiling:.2%}, sotto il costo dell'equity): {growth_used:.2%}."
        )

    return_on_equity = (roe_percent / 100.0) if roe_percent is not None else None
    latest = fundamentals[years_desc[0]] if years_desc else {}
    eps = _safe_div(latest.get("net_income"), shares)

    result["inputs"] = {
        "years_analyzed": years_desc,
        "normalization": normalization,
        "return_on_equity": _round(return_on_equity, 4),
        "equity_basis": equity_basis,
        "book_value_per_share": _round(book_value_per_share, 4),
        "growth_book_value_cagr": _round(growth_book, 4),
        "growth_used": _round(growth_used, 4),
        "shares_outstanding": _round(shares, 0) if shares else None,
        "eps": _round(eps, 4),
        "equity_to_assets_avg": _round(_mean((metrics.get("equity_to_assets") or {}).values()), 3),
    }

    projection_years = int(config["projection_years"])
    fade_years = int(config["fade_years"])
    terminal_roe = cost_of_equity + float(config.get("terminal_roe_premium", 0.0))

    residual = residual_income_value_per_share(
        book_value_per_share, return_on_equity=return_on_equity,
        cost_of_equity=cost_of_equity, growth=growth_used,
        projection_years=projection_years, fade_years=fade_years, terminal_roe=terminal_roe,
    )
    if residual.get("error"):
        quality.miss(f"Residual income: {residual['error']}")

    justified = justified_price_to_book(
        book_value_per_share, return_on_equity=return_on_equity,
        cost_of_equity=cost_of_equity, growth=growth_used,
    )
    if justified.get("error"):
        quality.miss(f"P/B giustificato: {justified['error']}")

    multiples = historical_multiples_valuation(
        fundamentals, price_history, shares=shares,
        current_metrics={"eps": eps, "owner_earnings_per_share": None},
        quality=quality,
    )

    graham = graham_number(eps, book_value_per_share)

    result["methods"] = {
        "residual_income": {
            "label": "Residual income (rendimenti in eccesso)",
            "value_per_share": _round(residual.get("value_per_share"), 2),
            "book_value": _round(residual.get("book_value"), 2),
            "pv_excess_returns": _round(residual.get("pv_excess_returns"), 2),
            "error": residual.get("error"),
        },
        "justified_price_to_book": {
            "label": "P/B giustificato dalla redditivita'",
            "value_per_share": _round(justified.get("value_per_share"), 2),
            "justified_multiple": _round(justified.get("justified_multiple"), 3),
            "error": justified.get("error"),
        },
        "historical_multiples": {
            "label": "Multipli storici del titolo",
            "value_per_share": _round(multiples.get("value_per_share"), 2),
            "detail": multiples.get("multiples"),
            "error": multiples.get("error"),
        },
        "graham_number": {
            "label": "Graham Number",
            "value_per_share": _round(graham, 2),
            "error": None if graham is not None else "Utili o patrimonio non positivi.",
        },
    }

    # --- Sintesi --------------------------------------------------------------
    contributions: Dict[str, Dict[str, float]] = {}
    for name, weight in FINANCIAL_AGGREGATE_WEIGHTS.items():
        value = result["methods"].get(name, {}).get("value_per_share")
        if value is not None and value > 0:
            contributions[name] = {"value": value, "weight": weight}
    result["methods"]["graham_number"]["aggregated"] = False
    for name in FINANCIAL_AGGREGATE_WEIGHTS:
        result["methods"][name]["aggregated"] = name in contributions
        result["methods"][name]["weight"] = 0.0
    for name in (set(FINANCIAL_AGGREGATE_WEIGHTS) - set(contributions)):
        quality.note(
            f"Metodo '{name}' escluso dalla sintesi (non calcolabile): peso ridistribuito."
        )

    if contributions:
        total_weight = sum(item["weight"] for item in contributions.values())
        fair_value = sum(
            item["value"] * item["weight"] for item in contributions.values()
        ) / total_weight
        for name, item in contributions.items():
            result["methods"][name]["weight"] = _round(item["weight"] / total_weight, 4)
        spread = [item["value"] for item in contributions.values()]
        # I metodi incorporano ipotesi opposte sulla durata del vantaggio competitivo
        # (il P/B giustificato lo assume perpetuo, il residual income lo azzera in N
        # anni): una forbice ampia e' informazione, non rumore, e va dichiarata.
        if min(spread) > 0 and max(spread) / min(spread) > 1.8:
            quality.note(
                f"I metodi divergono molto ({_round(min(spread), 2)} - {_round(max(spread), 2)}): "
                "il fair value puntuale e' poco significativo, guardare l'intervallo."
            )
        result["fair_value"] = {
            "point": _round(fair_value, 2),
            "low": _round(min(spread), 2),
            "high": _round(max(spread), 2),
            "methods_used": len(contributions),
            "weights": {name: _round(item["weight"] / total_weight, 3)
                        for name, item in contributions.items()},
        }
        if price and price > 0:
            result["margin_of_safety"] = _round((fair_value - price) / fair_value, 4)
            result["upside_pct"] = _round((fair_value / price - 1.0) * 100.0, 2)
            result["verdict"] = _verdict(result["margin_of_safety"])
            target = float(config["target_margin_of_safety"])
            result["buy_below"] = _round(fair_value * (1.0 - target), 2)
            if book_value_per_share:
                result["price_to_book"] = _round(price / book_value_per_share, 3)
        else:
            quality.miss("Prezzo non disponibile: margine di sicurezza non calcolabile.")
    else:
        result["error"] = "Nessun metodo di valutazione ha prodotto un valore positivo."

    # --- Scenari --------------------------------------------------------------
    roe_delta = float(config.get("scenario_roe_delta", 0.03))
    wacc_delta = float(config["scenario_wacc_delta"])
    if return_on_equity is not None:
        scenarios = {
            "bear": (return_on_equity - roe_delta, cost_of_equity + wacc_delta),
            "base": (return_on_equity, cost_of_equity),
            "bull": (return_on_equity + roe_delta, max(0.02, cost_of_equity - wacc_delta)),
        }
        for name, (scenario_roe, scenario_ke) in scenarios.items():
            outcome = residual_income_value_per_share(
                book_value_per_share, return_on_equity=scenario_roe,
                cost_of_equity=scenario_ke,
                growth=min(growth_used, scenario_ke - 0.02),
                projection_years=projection_years, fade_years=fade_years,
                terminal_roe=scenario_ke + float(config.get("terminal_roe_premium", 0.0)),
            )
            value = outcome.get("value_per_share")
            result["scenarios"][name] = {
                "return_on_equity": _round(scenario_roe, 4),
                "cost_of_equity": _round(scenario_ke, 4),
                "value_per_share": _round(value, 2),
                "upside_pct": _round((value / price - 1.0) * 100.0, 2) if (value and price) else None,
                "error": outcome.get("error"),
            }

    # --- ROE implicito nel prezzo --------------------------------------------
    reverse = reverse_residual_income(
        price, book_value_per_share, cost_of_equity=cost_of_equity, growth=growth_used,
        projection_years=projection_years, fade_years=fade_years,
    )
    result["reverse_dcf"] = {
        "model": "residual_income",
        "implied_roe": _round(reverse.get("implied_roe"), 4),
        "historical_roe": _round(return_on_equity, 4),
        "at_bound": reverse.get("at_bound"),
        "error": reverse.get("error"),
    }
    implied = reverse.get("implied_roe")
    if implied is not None and return_on_equity is not None:
        gap = implied - return_on_equity
        result["reverse_dcf"]["gap_vs_historical"] = _round(gap, 4)
        result["reverse_dcf"]["reading"] = (
            "Il mercato sconta una redditivita' superiore a quella storica" if gap > 0.005
            else "Il mercato sconta una redditivita' inferiore a quella storica" if gap < -0.005
            else "Il mercato sconta all'incirca la redditivita' storica"
        )

    # --- Sensitivita': costo dell'equity x crescita del patrimonio -----------
    if book_value_per_share and return_on_equity is not None:
        ke_axis = [round(cost_of_equity + step * 0.01, 4) for step in (-2, -1, 0, 1, 2)]
        ke_axis = [ke for ke in ke_axis if ke > 0.03]
        growth_axis = [round(growth_used + step * 0.01, 4) for step in (-2, -1, 0, 1, 2)]
        values: List[List[Optional[float]]] = []
        upside: List[List[Optional[float]]] = []
        for grid_growth in growth_axis:
            row_values: List[Optional[float]] = []
            row_upside: List[Optional[float]] = []
            for grid_ke in ke_axis:
                outcome = residual_income_value_per_share(
                    book_value_per_share, return_on_equity=return_on_equity,
                    cost_of_equity=grid_ke, growth=grid_growth,
                    projection_years=projection_years, fade_years=fade_years,
                    terminal_roe=grid_ke,
                )
                value = outcome.get("value_per_share")
                row_values.append(_round(value, 2))
                row_upside.append(
                    _round((value / price - 1.0) * 100.0, 2) if (value and price) else None
                )
            values.append(row_values)
            upside.append(row_upside)
        result["sensitivity"] = {
            "x_label": "Cost of equity", "x_values": ke_axis,
            "y_label": "Book value growth", "y_values": growth_axis,
            "z_label": "Value per share", "values": values, "upside_pct": upside,
        }

    quality.note(
        "Profilo finanziario: DCF, EPV e NCAV non sono applicabili e non vengono "
        "calcolati. Lo sconto avviene al costo dell'equity, non al WACC."
    )
    result["data_quality"] = quality.as_dict()
    return result


def _verdict(margin_of_safety: Optional[float]) -> str:
    if margin_of_safety is None:
        return "Non valutabile"
    for threshold, label in VERDICT_THRESHOLDS:
        if margin_of_safety >= threshold:
            return label
    return "Sopravvalutata"


def calculate_valuation(
    ticker: str,
    *,
    assumptions: Optional[Mapping[str, Any]] = None,
    financials: Optional[Mapping[str, Any]] = None,
    market_data: Optional[Mapping[str, Any]] = None,
    price_history: Optional[Any] = None,
    growth_override: Optional[float] = None,
    wacc_override: Optional[float] = None,
    years: int = 10,
    sector: Optional[str] = None,
) -> Dict[str, Any]:
    """Valutazione completa di un titolo: fair value, margine di sicurezza, scenari.

    Args:
        ticker: simbolo di borsa.
        assumptions: override di :data:`DEFAULT_ASSUMPTIONS`.
        financials: bilanci gia' scaricati (output di ``fetch_financials``), per test
            offline o per riutilizzare un download.
        market_data: dati di mercato gia' pronti (``price``, ``shares_outstanding``, ...).
        price_history: serie storica dei prezzi, per la valutazione a multipli storici.
        growth_override: crescita esplicita imposta dall'utente (annua, es. ``0.08``).
        wacc_override: tasso di sconto imposto dall'utente.
        sector: ``"industrial"``, ``"bank"`` o ``"insurance"``; se omesso viene
            riconosciuto dal bilancio. Sui finanziari il DCF viene sostituito dal
            residual income e lo sconto avviene al costo dell'equity.
        years: esercizi di bilancio da considerare.

    Returns:
        Dizionario con ``fair_value``, ``margin_of_safety``, ``verdict``, ``methods``,
        ``scenarios``, ``reverse_dcf``, ``sensitivity``, ``cost_of_capital``,
        ``inputs``, ``assumptions`` e ``data_quality``. Non solleva eccezioni.
    """
    quality = _DataQuality()
    config: Dict[str, Any] = dict(DEFAULT_ASSUMPTIONS)
    if assumptions:
        for key, value in assumptions.items():
            if key in config and value is not None:
                config[key] = value
            elif key not in config:
                quality.note(f"Ipotesi '{key}' sconosciuta: ignorata.")

    result: Dict[str, Any] = {
        "ticker": ticker.upper(), "company_name": None, "currency": None,
        "sector": None, "sector_label": None,
        "price": None, "fair_value": {}, "margin_of_safety": None, "verdict": "Non valutabile",
        "methods": {}, "scenarios": {}, "reverse_dcf": {}, "sensitivity": {},
        "cost_of_capital": {}, "inputs": {}, "assumptions": config,
        "data_quality": {}, "error": None,
    }

    # --- Bilanci --------------------------------------------------------------
    # Se i bilanci arrivano dal chiamante siamo in modalita' offline/test: in quel
    # caso non si tenta nessun download accessorio.
    live_mode = financials is None
    if financials is None:
        financials = fetch_financials(ticker, years=years)
    fetch_notes = financials.get("data_quality")
    if isinstance(fetch_notes, Mapping):
        for message in fetch_notes.get("notes", []):
            quality.note(message)
        for message in fetch_notes.get("missing", []):
            quality.miss(message)

    try:
        from . import sectors
    except ImportError:
        import sectors  # type: ignore[no-redef]
    detected = sector if sector in sectors.PROFILES else sectors.detect_sector(financials, quality)
    is_financial = detected in (sectors.BANK, sectors.INSURANCE)

    try:
        fundamentals = extract_fundamentals(financials, quality, sector=detected)
    except Exception as exc:  # pragma: no cover - difensivo
        result["error"] = f"Normalizzazione dei bilanci fallita: {exc}"
        result["data_quality"] = quality.as_dict()
        return result

    if not fundamentals:
        result["error"] = f"Nessun dato di bilancio utilizzabile per {ticker.upper()}."
        result["data_quality"] = quality.as_dict()
        return result

    if not is_financial:
        owner_earnings = calculate_owner_earnings(fundamentals, quality)
        for year, value in owner_earnings.items():
            fundamentals[year]["owner_earnings"] = value

    years_desc = sorted(fundamentals, reverse=True)
    latest = fundamentals[years_desc[0]]

    # --- Dati di mercato ------------------------------------------------------
    if market_data is None:
        market_data = fetch_market_data(ticker, quality)
    price = _to_float(market_data.get("price"))
    shares = _to_float(market_data.get("shares_outstanding")) or latest.get("shares_outstanding")
    if shares is None:
        quality.miss("Numero di azioni non disponibile: valori per azione non calcolabili.")
    elif market_data.get("shares_outstanding") is None:
        quality.estimate("Numero di azioni preso dall'ultimo stato patrimoniale disponibile.")

    result["company_name"] = market_data.get("company_name") or financials.get("company_name")
    result["currency"] = market_data.get("currency") or financials.get("currency")
    result["price"] = price

    market_cap = _to_float(market_data.get("market_cap"))
    if market_cap is None and price and shares:
        market_cap = price * shares

    net_debt = None
    if latest.get("total_debt") is not None:
        net_debt = latest["total_debt"] - (latest.get("cash") or 0.0)
        if latest.get("cash") is None:
            quality.estimate("Debito netto calcolato senza dedurre la cassa (dato mancante).")
    else:
        quality.miss("Debito totale non disponibile: debito netto assunto pari a zero.")
        net_debt = 0.0

    # --- Costo del capitale ---------------------------------------------------
    capital = cost_of_capital(
        beta=_to_float(market_data.get("beta")),
        market_cap=market_cap,
        total_debt=latest.get("total_debt"),
        interest_expense=latest.get("interest_expense"),
        tax_rate=latest.get("tax_rate"),
        assumptions=config,
        quality=quality,
        override=wacc_override,
    )
    result["cost_of_capital"] = {key: _round(value, 5) if isinstance(value, float) else value
                                for key, value in capital.items()}
    wacc = capital["wacc"]
    tax_rate = capital["tax_rate"]

    # --- Bivio per settore ----------------------------------------------------
    # Su una banca il DCF non e' impreciso: risponde a una domanda che per quel tipo di
    # azienda non esiste. Da qui in poi i due percorsi sono metodi diversi, non soglie
    # diverse.
    result["sector"] = detected
    result["sector_label"] = sectors.PROFILES[detected]["label"]

    if detected in (sectors.BANK, sectors.INSURANCE):
        return _financial_valuation(
            result=result, financials=financials, fundamentals=fundamentals,
            sector=detected, capital=capital, price=price, shares=shares,
            config=config, quality=quality, sectors_module=sectors,
            price_history=(
                price_history if price_history is not None
                else (fetch_price_history(ticker) if (live_mode and yf is not None) else None)
            ),
        )

    # --- Input normalizzati ---------------------------------------------------
    normalization = str(config["normalization"])
    base_owner_earnings = normalize_series(
        owner_earnings, normalization, quality, "Owner Earnings"
    )
    normalized_ebit = normalize_series(
        {year: fundamentals[year].get("ebit") for year in years_desc},
        normalization, quality, "EBIT",
    )
    growth_oe = historical_cagr(owner_earnings)
    growth_revenue = historical_cagr({year: fundamentals[year].get("revenue") for year in years_desc})
    growth_net_income = historical_cagr(
        {year: fundamentals[year].get("net_income") for year in years_desc}
    )

    if growth_override is not None:
        growth_used = float(growth_override)
        quality.note(f"Crescita esplicita imposta dall'utente: {growth_used:.2%}.")
    else:
        candidates = [g for g in (growth_oe, growth_revenue, growth_net_income) if g is not None]
        if candidates:
            # Si prende la piu' prudente fra le crescite storiche disponibili, poi si
            # applica il tetto: proiettare il passato migliore e' l'errore piu' costoso
            # in un DCF.
            growth_used = min(candidates)
            quality.estimate(
                f"Crescita esplicita = minimo fra i CAGR storici disponibili ({growth_used:.2%})."
            )
        else:
            growth_used = float(config["terminal_growth"])
            quality.estimate(
                f"Nessun CAGR storico calcolabile: crescita esplicita posta pari a quella "
                f"terminale ({growth_used:.2%})."
            )
    raw_growth = growth_used
    growth_used = max(float(config["min_growth"]), min(float(config["max_growth"]), growth_used))
    if abs(growth_used - raw_growth) > 1e-9:
        quality.note(
            f"Crescita storica {raw_growth:.2%} riportata nel range prudenziale: {growth_used:.2%}."
        )

    eps = _safe_div(latest.get("net_income"), shares)
    book_value_per_share = _safe_div(latest.get("equity"), shares)
    owner_earnings_per_share = _safe_div(base_owner_earnings, shares)

    result["inputs"] = {
        "years_analyzed": years_desc,
        "base_owner_earnings": _round(base_owner_earnings, 2),
        "normalization": normalization,
        "normalized_ebit": _round(normalized_ebit, 2),
        "growth_owner_earnings_cagr": _round(growth_oe, 4),
        "growth_revenue_cagr": _round(growth_revenue, 4),
        "growth_net_income_cagr": _round(growth_net_income, 4),
        "growth_used": _round(growth_used, 4),
        "shares_outstanding": _round(shares, 0) if shares else None,
        "market_cap": _round(market_cap, 0) if market_cap else None,
        "net_debt": _round(net_debt, 0) if net_debt is not None else None,
        "eps": _round(eps, 4),
        "book_value_per_share": _round(book_value_per_share, 4),
        "owner_earnings_per_share": _round(owner_earnings_per_share, 4),
    }

    # --- Metodi ---------------------------------------------------------------
    projection_years = int(config["projection_years"])
    fade_years = int(config["fade_years"])
    terminal_growth = float(config["terminal_growth"])
    mid_year = bool(config["mid_year_convention"])

    dcf = dcf_value_per_share(
        base_owner_earnings, discount_rate=wacc, initial_growth=growth_used,
        terminal_growth=terminal_growth, net_debt=net_debt, shares=shares,
        projection_years=projection_years, fade_years=fade_years, mid_year=mid_year,
    )
    if dcf.get("error"):
        quality.miss(f"DCF: {dcf['error']}")
    elif dcf.get("terminal_weight") and dcf["terminal_weight"] > 0.75:
        quality.note(
            f"Il valore terminale pesa {dcf['terminal_weight']:.0%} del totale: "
            "la valutazione dipende soprattutto da ipotesi oltre l'orizzonte esplicito."
        )

    epv = epv_value_per_share(
        normalized_ebit, tax_rate=tax_rate, discount_rate=wacc,
        net_debt=net_debt, shares=shares,
    )
    if epv.get("error"):
        quality.miss(f"EPV: {epv['error']}")

    graham = graham_number(eps, book_value_per_share)
    ncav = ncav_per_share(latest.get("current_assets"), latest.get("total_liabilities"), shares)

    if price_history is None and live_mode and yf is not None:
        price_history = fetch_price_history(ticker)
    multiples = historical_multiples_valuation(
        fundamentals, price_history, shares=shares,
        current_metrics={"eps": eps, "owner_earnings_per_share": owner_earnings_per_share},
        quality=quality,
    )

    result["methods"] = {
        "dcf_owner_earnings": {
            "label": "DCF Owner Earnings",
            "value_per_share": _round(dcf.get("value_per_share"), 2),
            "enterprise_value": _round(dcf.get("enterprise_value"), 0),
            "terminal_weight": _round(dcf.get("terminal_weight"), 4),
            "error": dcf.get("error"),
        },
        "epv": {
            "label": "EPV (crescita zero)",
            "value_per_share": _round(epv.get("value_per_share"), 2),
            "enterprise_value": _round(epv.get("enterprise_value"), 0),
            "error": epv.get("error"),
        },
        "graham_number": {
            "label": "Graham Number",
            "value_per_share": _round(graham, 2),
            "error": None if graham is not None else "Utili o patrimonio non positivi.",
        },
        "ncav": {
            "label": "NCAV (net-net)",
            "value_per_share": _round(ncav, 2),
            "error": None if ncav is not None else "Attivo corrente o passivita' non disponibili.",
        },
        "historical_multiples": {
            "label": "Multipli storici del titolo",
            "value_per_share": _round(multiples.get("value_per_share"), 2),
            "detail": multiples.get("multiples"),
            "error": multiples.get("error"),
        },
    }

    # --- Fair value e margine di sicurezza -----------------------------------
    # Media pesata dei soli metodi "going concern" (vedi AGGREGATE_WEIGHTS): una
    # mediana secca fra metodi di natura diversa lascerebbe che un Graham Number
    # privo di significato per un'azienda asset-light decida il fair value.
    contributions: Dict[str, Dict[str, float]] = {}
    for name, weight in AGGREGATE_WEIGHTS.items():
        value = result["methods"].get(name, {}).get("value_per_share")
        if value is not None and value > 0:
            contributions[name] = {"value": value, "weight": weight}
    for name in REFERENCE_METHODS:
        result["methods"][name]["aggregated"] = False
    for name in AGGREGATE_WEIGHTS:
        result["methods"][name]["aggregated"] = name in contributions
        result["methods"][name]["weight"] = (
            AGGREGATE_WEIGHTS[name] if name in contributions else 0.0
        )

    skipped = [name for name in AGGREGATE_WEIGHTS if name not in contributions]
    for name in skipped:
        quality.note(
            f"Metodo '{name}' escluso dalla sintesi (non calcolabile): "
            "peso ridistribuito sugli altri."
        )

    if contributions:
        total_weight = sum(item["weight"] for item in contributions.values())
        fair_value = sum(
            item["value"] * item["weight"] for item in contributions.values()
        ) / total_weight
        for name, item in contributions.items():
            result["methods"][name]["weight"] = _round(item["weight"] / total_weight, 4)

        # L'intervallo comprende anche gli scenari, che sono il modo piu' onesto di
        # rappresentare l'incertezza: un fair value puntuale e' sempre falsa precisione.
        spread = [item["value"] for item in contributions.values()]
        # I metodi incorporano ipotesi opposte sulla durata del vantaggio competitivo
        # (il P/B giustificato lo assume perpetuo, il residual income lo azzera in N
        # anni): una forbice ampia e' informazione, non rumore, e va dichiarata.
        if min(spread) > 0 and max(spread) / min(spread) > 1.8:
            quality.note(
                f"I metodi divergono molto ({_round(min(spread), 2)} - {_round(max(spread), 2)}): "
                "il fair value puntuale e' poco significativo, guardare l'intervallo."
            )
        result["fair_value"] = {
            "point": _round(fair_value, 2),
            "low": _round(min(spread), 2),
            "high": _round(max(spread), 2),
            "methods_used": len(contributions),
            "weights": {name: _round(item["weight"] / total_weight, 3)
                        for name, item in contributions.items()},
        }
        if price and price > 0:
            result["margin_of_safety"] = _round((fair_value - price) / fair_value, 4)
            result["upside_pct"] = _round((fair_value / price - 1.0) * 100.0, 2)
            result["verdict"] = _verdict(result["margin_of_safety"])
            target = float(config["target_margin_of_safety"])
            result["buy_below"] = _round(fair_value * (1.0 - target), 2)
        else:
            quality.miss("Prezzo non disponibile: margine di sicurezza non calcolabile.")
    else:
        result["error"] = "Nessun metodo di valutazione ha prodotto un valore positivo."

    # --- Scenari --------------------------------------------------------------
    growth_delta = float(config["scenario_growth_delta"])
    wacc_delta = float(config["scenario_wacc_delta"])
    scenarios = {
        "bear": (growth_used - growth_delta, wacc + wacc_delta),
        "base": (growth_used, wacc),
        "bull": (growth_used + growth_delta, max(0.01, wacc - wacc_delta)),
    }
    for name, (scenario_growth, scenario_wacc) in scenarios.items():
        outcome = dcf_value_per_share(
            base_owner_earnings, discount_rate=scenario_wacc, initial_growth=scenario_growth,
            terminal_growth=terminal_growth, net_debt=net_debt, shares=shares,
            projection_years=projection_years, fade_years=fade_years, mid_year=mid_year,
        )
        value = outcome.get("value_per_share")
        result["scenarios"][name] = {
            "growth": _round(scenario_growth, 4),
            "wacc": _round(scenario_wacc, 4),
            "value_per_share": _round(value, 2),
            "upside_pct": _round((value / price - 1.0) * 100.0, 2) if (value and price) else None,
            "error": outcome.get("error"),
        }

    # --- Reverse DCF ----------------------------------------------------------
    reverse = reverse_dcf(
        price, base_owner_earnings, discount_rate=wacc, terminal_growth=terminal_growth,
        net_debt=net_debt, shares=shares, projection_years=projection_years,
        fade_years=fade_years, mid_year=mid_year,
    )
    result["reverse_dcf"] = {
        "implied_growth": _round(reverse.get("implied_growth"), 4),
        "historical_growth": _round(growth_oe, 4),
        "at_bound": reverse.get("at_bound"),
        "error": reverse.get("error"),
    }
    implied = reverse.get("implied_growth")
    if implied is not None and growth_oe is not None:
        gap = implied - growth_oe
        result["reverse_dcf"]["gap_vs_historical"] = _round(gap, 4)
        result["reverse_dcf"]["reading"] = (
            "Il mercato sconta una crescita superiore a quella storica"
            if gap > 0.01 else
            "Il mercato sconta una crescita inferiore a quella storica"
            if gap < -0.01 else
            "Il mercato sconta all'incirca la crescita storica"
        )

    # --- Sensitivita' ---------------------------------------------------------
    if base_owner_earnings and shares:
        wacc_axis = [round(wacc + step * 0.01, 4) for step in (-2, -1, 0, 1, 2)]
        wacc_axis = [w for w in wacc_axis if w > terminal_growth + 0.005]
        growth_axis = [round(terminal_growth + step * 0.005, 4) for step in (-2, -1, 0, 1, 2)]
        result["sensitivity"] = sensitivity_grid(
            base_cash_flow=base_owner_earnings, wacc_values=wacc_axis,
            growth_values=growth_axis, initial_growth=growth_used, net_debt=net_debt,
            shares=shares, projection_years=projection_years, fade_years=fade_years,
            mid_year=mid_year, price=price,
        )

    result["data_quality"] = quality.as_dict()
    return result


# ---------------------------------------------------------------------------
# Report leggibile
# ---------------------------------------------------------------------------


def format_valuation_report(result: Mapping[str, Any], max_notes: int = 10) -> str:
    """Rende leggibile l'output di :func:`calculate_valuation`."""
    try:
        from .quality_score import _group_messages
    except ImportError:
        from quality_score import _group_messages  # type: ignore[no-redef]

    width = 92
    lines: List[str] = []
    name = result.get("company_name") or result.get("ticker")
    currency = result.get("currency") or ""
    lines.append("=" * width)
    lines.append(f" VALUTAZIONE - {name} ({result.get('ticker')})")
    lines.append("=" * width)

    if result.get("error"):
        lines.append(f" ERRORE: {result['error']}")

    price = result.get("price")
    fair = result.get("fair_value") or {}
    lines.append(f" Prezzo di mercato       : {_fmt(price, 2)} {currency}")
    lines.append(
        f" Fair value stimato      : {_fmt(fair.get('point'), 2)} {currency}"
        f"   (range {_fmt(fair.get('low'), 2)} - {_fmt(fair.get('high'), 2)}"
        f", media pesata di {fair.get('methods_used', 0)} metodi)"
    )
    margin = result.get("margin_of_safety")
    lines.append(
        f" Margine di sicurezza    : {_fmt(margin * 100 if margin is not None else None, 1)}%"
        f"   ->  {result.get('verdict')}"
    )
    if result.get("buy_below") is not None:
        target = result["assumptions"]["target_margin_of_safety"]
        lines.append(
            f" Prezzo d'acquisto       : sotto {_fmt(result['buy_below'], 2)} {currency}"
            f"   (per un margine del {target:.0%})"
        )
    lines.append("")

    capital = result.get("cost_of_capital") or {}
    inputs = result.get("inputs") or {}
    assumptions = result.get("assumptions") or {}
    lines.append("-" * width)
    lines.append(" IPOTESI")
    lines.append("-" * width)
    is_financial = result.get("sector") in ("bank", "insurance")
    if result.get("sector_label"):
        lines.append(f" Profilo di valutazione: {result['sector_label']}")

    if is_financial:
        # Per un finanziario il debito e' materia prima: si sconta al costo dell'equity,
        # e la base non e' un flusso di cassa ma il patrimonio.
        lines.append(
            f" Costo dell'equity {_fmt((capital.get('cost_of_equity') or 0) * 100, 2)}%"
            f"  =  risk free {_fmt((capital.get('risk_free_rate') or 0) * 100, 2)}%"
            f"  +  beta {_fmt(capital.get('beta'), 2)}"
            f" x premio {_fmt((capital.get('equity_risk_premium') or 0) * 100, 2)}%"
        )
        basis = "tangibile" if inputs.get("equity_basis") == "tangible_book_per_share" else "contabile"
        lines.append(
            f" Patrimonio {basis} per azione: {_fmt(inputs.get('book_value_per_share'), 2)} {currency}"
            f" | ROE normalizzato ({inputs.get('normalization')}):"
            f" {_fmt((inputs.get('return_on_equity') or 0) * 100, 2)}%"
        )
        lines.append(
            f" Crescita del patrimonio: {_fmt((inputs.get('growth_used') or 0) * 100, 2)}%"
            f" (storica {_fmt((inputs.get('growth_book_value_cagr') or 0) * 100, 2)}%)"
            f" per {assumptions.get('projection_years')} anni,"
            f" fade negli ultimi {assumptions.get('fade_years')}"
        )
        lines.append(
            " Il rendimento in eccesso si azzera a fine periodo: nessun valore terminale."
            if not assumptions.get("terminal_roe_premium")
            else f" Premio di ROE terminale: {assumptions['terminal_roe_premium']:.2%}"
        )
        if result.get("price_to_book") is not None:
            lines.append(f" Prezzo / patrimonio {basis}: {_fmt(result['price_to_book'], 2)}x")
    else:
        lines.append(
            f" WACC {_fmt((capital.get('wacc') or 0) * 100, 2)}%"
            f"  =  Ke {_fmt((capital.get('cost_of_equity') or 0) * 100, 2)}%"
            f" x {_fmt((capital.get('weight_equity') or 0) * 100, 1)}%"
            f"  +  Kd netto {_fmt((capital.get('after_tax_cost_of_debt') or 0) * 100, 2)}%"
            f" x {_fmt((capital.get('weight_debt') or 0) * 100, 1)}%"
        )
        lines.append(
            f" Beta {_fmt(capital.get('beta'), 2)}"
            f" | risk free {_fmt((capital.get('risk_free_rate') or 0) * 100, 2)}%"
            f" | premio al rischio {_fmt((capital.get('equity_risk_premium') or 0) * 100, 2)}%"
            f" | aliquota {_fmt((capital.get('tax_rate') or 0) * 100, 1)}%"
        )
        lines.append(
            f" Crescita esplicita {_fmt((inputs.get('growth_used') or 0) * 100, 2)}%"
            f" per {assumptions.get('projection_years')} anni"
            f" (fade negli ultimi {assumptions.get('fade_years')})"
            f" -> terminale {_fmt((assumptions.get('terminal_growth') or 0) * 100, 2)}%"
        )
        lines.append(
            f" CAGR storici: Owner Earnings {_fmt((inputs.get('growth_owner_earnings_cagr') or 0) * 100, 1)}%"
            f" | ricavi {_fmt((inputs.get('growth_revenue_cagr') or 0) * 100, 1)}%"
            f" | utile netto {_fmt((inputs.get('growth_net_income_cagr') or 0) * 100, 1)}%"
        )
        lines.append(
            f" Owner Earnings base ({inputs.get('normalization')}): {_fmt_big(inputs.get('base_owner_earnings'))}"
            f" | debito netto {_fmt_big(inputs.get('net_debt'))}"
            f" | azioni {_fmt_big(inputs.get('shares_outstanding'))}"
        )
    lines.append("")

    lines.append("-" * width)
    lines.append(" METODI DI VALUTAZIONE")
    lines.append("-" * width)
    for key, method in (result.get("methods") or {}).items():
        value = method.get("value_per_share")
        if key in REFERENCE_METHODS:
            tag = "riferimento"
        elif method.get("aggregated"):
            tag = f"peso {method.get('weight', 0) * 100:.0f}%"
        else:
            tag = "escluso"
        if value is None:
            lines.append(f" {method['label']:<44}{'n/d':>10}       {method.get('error', '')}")
            continue
        delta = f"{(value / price - 1.0) * 100:+.1f}%" if price else "n/d"
        lines.append(
            f" {method['label']:<44}{_fmt(value, 2):>10} {currency:<4}"
            f" vs prezzo: {delta:>8}   [{tag}]"
        )
    lines.append("")

    scenarios = result.get("scenarios") or {}
    if scenarios:
        lines.append("-" * width)
        lines.append(" SCENARI (residual income)" if is_financial else " SCENARI (DCF)")
        lines.append("-" * width)
        first_label = "ROE" if is_financial else "crescita"
        second_label = "Ke" if is_financial else "WACC"
        lines.append(
            f" {'Scenario':<10}{first_label:>12}{second_label:>10}{'valore':>12}{'upside':>12}"
        )
        for name_scenario, scenario in scenarios.items():
            first = scenario.get("return_on_equity") if is_financial else scenario.get("growth")
            second = scenario.get("cost_of_equity") if is_financial else scenario.get("wacc")
            lines.append(
                f" {name_scenario:<10}"
                f"{_fmt((first or 0) * 100, 2) + '%':>12}"
                f"{_fmt((second or 0) * 100, 2) + '%':>10}"
                f"{_fmt(scenario.get('value_per_share'), 2):>12}"
                f"{(_fmt(scenario.get('upside_pct'), 1) + '%'):>12}"
            )
        lines.append("")

    reverse = result.get("reverse_dcf") or {}
    if is_financial and reverse.get("implied_roe") is not None:
        lines.append("-" * width)
        lines.append(" ROE IMPLICITO - cosa sta scontando il mercato")
        lines.append("-" * width)
        lines.append(
            f" Redditivita' implicita nel prezzo : {_fmt(reverse['implied_roe'] * 100, 2)}%"
            + ("  (estremo della ricerca)" if reverse.get("at_bound") else "")
        )
        if reverse.get("historical_roe") is not None:
            lines.append(
                f" Redditivita' normalizzata        : {_fmt(reverse['historical_roe'] * 100, 2)}%"
            )
        if reverse.get("reading"):
            lines.append(f" Lettura                          : {reverse['reading']}")
        lines.append("")
    elif reverse.get("implied_growth") is not None:
        lines.append("-" * width)
        lines.append(" REVERSE DCF - cosa sta scontando il mercato")
        lines.append("-" * width)
        lines.append(
            f" Crescita implicita nel prezzo : {_fmt(reverse['implied_growth'] * 100, 2)}% annuo"
            f" per {assumptions.get('projection_years')} anni"
        )
        if reverse.get("historical_growth") is not None:
            lines.append(
                f" Crescita storica Owner Earn.  : {_fmt(reverse['historical_growth'] * 100, 2)}%"
            )
        if reverse.get("reading"):
            lines.append(f" Lettura                       : {reverse['reading']}")
        lines.append("")

    sensitivity = result.get("sensitivity") or {}
    if sensitivity.get("values"):
        lines.append("-" * width)
        lines.append(
            " SENSITIVITA' - valore per azione (righe: "
            + ("crescita del patrimonio, colonne: costo dell'equity)" if is_financial
               else "crescita terminale, colonne: WACC)")
        )
        lines.append("-" * width)
        corner = "g / Ke" if is_financial else "g / WACC"
        header = f" {corner:<12}" + "".join(
            f"{value * 100:>11.2f}%" for value in sensitivity["x_values"]
        )
        lines.append(header)
        for row_index, growth in enumerate(sensitivity["y_values"]):
            cells = "".join(
                f"{_fmt(cell, 2):>12}" for cell in sensitivity["values"][row_index]
            )
            lines.append(f" {growth * 100:>10.2f}%  " + cells)
        lines.append("")

    data_quality = result.get("data_quality") or {}
    lines.append("-" * width)
    lines.append(" QUALITA' DEL DATO E APPROSSIMAZIONI")
    lines.append("-" * width)
    for section, title in (
        ("estimated", "Stime e approssimazioni"),
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
# CLI
# ---------------------------------------------------------------------------


def _parse_cli(argv: Sequence[str]) -> Tuple[List[str], Dict[str, Any]]:
    tickers: List[str] = []
    overrides: Dict[str, Any] = {}
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--growth" and index + 1 < len(argv):
            overrides["growth_override"] = float(argv[index + 1])
            index += 2
        elif argument == "--wacc" and index + 1 < len(argv):
            overrides["wacc_override"] = float(argv[index + 1])
            index += 2
        elif argument == "--terminal-growth" and index + 1 < len(argv):
            overrides.setdefault("assumptions", {})["terminal_growth"] = float(argv[index + 1])
            index += 2
        elif not argument.startswith("-"):
            tickers.append(argument)
            index += 1
        else:
            index += 1
    return tickers or ["AAPL"], overrides


if __name__ == "__main__":
    symbols, cli_overrides = _parse_cli(sys.argv[1:])
    for symbol in symbols:
        print(f"\nValutazione di {symbol.upper()} ...\n")
        print(format_valuation_report(calculate_valuation(symbol, **cli_overrides)))
