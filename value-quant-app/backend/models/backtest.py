"""Backtest di una strategia quality + value, costruito per non ingannarsi da solo.

La strategia: a ogni ribilanciamento si classificano i titoli dell'universo su due
assi — **qualita'** (il Quality Score di ``quality_score.py``) e **prezzo**
(earnings yield EBIT/EV, il lato "value" della magic formula di Greenblatt) — si
comprano i primi ``top_n`` e si tengono fino al ribilanciamento successivo.

Perche' point-in-time
---------------------
Il modo piu' facile di produrre un backtest bellissimo e falso e' usare oggi dati
che nel passato non erano ancora pubblici. Qui ogni decisione presa alla data ``D``
usa solo esercizi il cui bilancio era gia' depositato: il filtro e'
``fine esercizio + reporting_lag_days <= D`` (default 90 giorni, la finestra tipica
di deposito per un emittente USA). Il prezzo usato per il ranking e' quello di ``D``,
che a quella data era ovviamente noto.

Cosa resta comunque distorto
----------------------------
Nessun backtest costruito su dati gratuiti e' pulito, e fingere il contrario e'
peggio che non farlo. I limiti sono elencati in ``result["caveats"]`` a ogni
esecuzione. I principali:

* **Survivorship bias**: l'universo e' fatto di societa' che esistono *oggi*. Chi e'
  fallito o e' stato delistato non c'e', e questo gonfia strutturalmente i rendimenti.
* **Restatement**: yfinance espone i bilanci nella versione *attuale*, non in quella
  originariamente depositata. Il lag di reporting non protegge dalle revisioni.
* **Storico corto**: yfinance da' 4-5 esercizi, quindi pochi ribilanciamenti. Su
  cosi' pochi periodi la differenza con il benchmark e' rumore, non evidenza.
* **Multiple testing**: :func:`sweep_parameters` esplora una griglia di parametri.
  Scegliere la cella migliore *dopo* aver visto i risultati e' overfitting: la
  griglia serve a vedere se la strategia e' robusta in una regione, non a trovare
  il massimo.

Uso::

    python backtest.py                      # universo di default, dati sintetici se manca la rete
    python backtest.py AAPL MSFT KO PG JNJ
"""

from __future__ import annotations

import math
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None  # type: ignore[assignment]

try:
    from .quality_score import (
        _DataQuality,
        _round,
        _safe_div,
        _to_float,
        calculate_quality_score,
        extract_fundamentals,
        fetch_financials,
    )
except ImportError:
    from quality_score import (  # type: ignore[no-redef]
        _DataQuality,
        _round,
        _safe_div,
        _to_float,
        calculate_quality_score,
        extract_fundamentals,
        fetch_financials,
    )


__all__ = [
    "DEFAULT_STRATEGY",
    "TRADING_DAYS",
    "build_signal_panel",
    "fetch_universe_data",
    "performance_metrics",
    "rebalance_dates",
    "run_backtest",
    "slice_financials",
    "sweep_parameters",
    "format_backtest_report",
]


TRADING_DAYS = 252

DEFAULT_STRATEGY: Dict[str, Any] = {
    "top_n": 10,
    "rebalance": "annual",          # annual | quarterly
    "reporting_lag_days": 90,
    # Costo applicato alla somma dei |delta peso|: con questa convenzione una
    # rotazione completa del portafoglio vale 200% di turnover e paga 2 x il costo.
    "transaction_cost_bps": 10.0,
    "quality_weight": 0.50,
    "value_weight": 0.50,
    "min_quality_score": None,      # filtro opzionale sul punteggio di qualita'
    "weighting": "equal",           # equal | score
    "risk_free_rate": 0.02,         # annuo, per Sharpe/Sortino
    "initial_capital": 100.0,
}


# ---------------------------------------------------------------------------
# Download dei dati
# ---------------------------------------------------------------------------


def fetch_universe_data(
    tickers: Sequence[str],
    *,
    years: int = 10,
    benchmark: Optional[str] = "SPY",
    quality: Optional[_DataQuality] = None,
) -> Dict[str, Any]:
    """Scarica prezzi rettificati e bilanci per l'intero universo.

    Returns:
        ``{"prices": DataFrame, "benchmark": Series|None, "financials": {ticker: dict},
        "data_quality": {...}}``. I ticker senza dati vengono esclusi e segnalati.
    """
    quality = quality if quality is not None else _DataQuality()
    result: Dict[str, Any] = {
        "prices": None, "benchmark": None, "financials": {},
        "data_quality": quality.as_dict(),
    }
    if yf is None or pd is None:
        quality.miss("yfinance/pandas non disponibili: impossibile scaricare l'universo.")
        result["data_quality"] = quality.as_dict()
        return result

    symbols = [ticker.upper() for ticker in tickers]
    download_list = symbols + ([benchmark.upper()] if benchmark else [])

    prices = None
    try:
        raw = yf.download(
            download_list, period=f"{years}y", auto_adjust=True,
            progress=False, group_by="column",
        )
        if raw is not None and not raw.empty:
            close = raw["Close"] if "Close" in raw else raw
            prices = close.dropna(how="all")
    except Exception as exc:  # pragma: no cover - dipende dalla rete
        quality.miss(f"Download dei prezzi fallito: {exc}")

    if prices is not None:
        if benchmark and benchmark.upper() in prices.columns:
            result["benchmark"] = prices[benchmark.upper()]
            prices = prices.drop(columns=[benchmark.upper()])
        result["prices"] = prices
        missing_prices = [s for s in symbols if s not in prices.columns]
        for symbol in missing_prices:
            quality.miss(f"{symbol}: serie storica dei prezzi non disponibile.")
    else:
        quality.miss("Nessun prezzo scaricato.")

    for symbol in symbols:
        try:
            financials = fetch_financials(symbol, years=years)
            if financials.get("years"):
                result["financials"][symbol] = financials
            else:
                quality.miss(f"{symbol}: bilanci non disponibili, escluso dall'universo.")
        except Exception as exc:  # pragma: no cover - difensivo
            quality.miss(f"{symbol}: errore nel download dei bilanci ({exc}).")

    result["data_quality"] = quality.as_dict()
    return result


# ---------------------------------------------------------------------------
# Point-in-time
# ---------------------------------------------------------------------------


def slice_financials(financials: Mapping[str, Any], cutoff: Any) -> Dict[str, Any]:
    """Ritaglia i bilanci ai soli esercizi chiusi entro ``cutoff``.

    E' il cuore della difesa contro il look-ahead bias: le colonne dei tre prospetti
    con data di chiusura successiva a ``cutoff`` vengono rimosse, cosi' il resto del
    modello non puo' nemmeno accidentalmente vedere il futuro.
    """
    if pd is None:
        return dict(financials)
    cutoff_ts = pd.Timestamp(cutoff)
    try:
        cutoff_ts = cutoff_ts.tz_localize(None)
    except (TypeError, ValueError):
        pass

    sliced: Dict[str, Any] = dict(financials)
    years: List[int] = []
    for key in ("income_statement", "balance_sheet", "cash_flow"):
        frame = financials.get(key)
        if frame is None or not hasattr(frame, "columns"):
            continue
        keep = []
        for column in frame.columns:
            try:
                stamp = pd.Timestamp(column)
                try:
                    stamp = stamp.tz_localize(None)
                except (TypeError, ValueError):
                    pass
            except Exception:
                continue
            if stamp <= cutoff_ts:
                keep.append(column)
                years.append(stamp.year)
        sliced[key] = frame[keep] if keep else frame.iloc[:, :0]
    sliced["years"] = sorted(set(years), reverse=True)
    return sliced


def rebalance_dates(
    index: Any,
    frequency: str = "annual",
    *,
    start: Any = None,
) -> List[Any]:
    """Prima data di contrattazione di ogni anno (o trimestre) presente nell'indice."""
    if pd is None or index is None or len(index) == 0:
        return []
    series = pd.Series(range(len(index)), index=index)
    if start is not None:
        series = series[series.index >= pd.Timestamp(start)]
    if series.empty:
        return []
    rule = "YS" if str(frequency).lower().startswith("a") else "QS"
    try:
        grouped = series.groupby(pd.Grouper(freq=rule)).first().dropna()
    except Exception:  # pragma: no cover - indici non temporali
        return [series.index[0]]
    return [series.index[int(position)] for position in grouped.tolist()]


def build_signal_panel(
    financials_by_ticker: Mapping[str, Mapping[str, Any]],
    prices: Any,
    dates: Sequence[Any],
    *,
    reporting_lag_days: int = 90,
    quality: Optional[_DataQuality] = None,
) -> Dict[Any, Dict[str, Dict[str, Optional[float]]]]:
    """Calcola, per ogni data di ribilanciamento, i segnali grezzi di ogni titolo.

    I segnali non dipendono dai parametri di portafoglio (``top_n``, pesi, ...), per
    cui il pannello si calcola una volta sola e viene riutilizzato da ogni backtest
    e da ogni cella dello sweep.

    Returns:
        ``{data: {ticker: {"quality_score", "earnings_yield", "roic", "price",
        "market_cap", "fiscal_year"}}}``
    """
    quality = quality if quality is not None else _DataQuality()
    panel: Dict[Any, Dict[str, Dict[str, Optional[float]]]] = {}
    if pd is None or prices is None:
        return panel

    for date in dates:
        snapshot: Dict[str, Dict[str, Optional[float]]] = {}
        cutoff = pd.Timestamp(date) - pd.Timedelta(days=int(reporting_lag_days))
        for ticker, financials in financials_by_ticker.items():
            if ticker not in getattr(prices, "columns", []):
                continue
            try:
                price = _to_float(prices[ticker].asof(date))
            except Exception:
                price = None
            if price is None or price <= 0:
                continue

            sliced = slice_financials(financials, cutoff)
            if not sliced.get("years"):
                continue

            try:
                score_result = calculate_quality_score(ticker, financials=sliced)
                fundamentals = extract_fundamentals(sliced, _DataQuality())
            except Exception as exc:  # pragma: no cover - difensivo
                quality.note(f"{ticker} @ {date}: segnali non calcolabili ({exc}).")
                continue
            if not fundamentals:
                continue

            fiscal_year = max(fundamentals)
            latest = fundamentals[fiscal_year]
            shares = latest.get("shares_outstanding")
            market_cap = price * shares if shares else None
            net_debt = None
            if latest.get("total_debt") is not None:
                net_debt = latest["total_debt"] - (latest.get("cash") or 0.0)
            enterprise_value = (
                market_cap + net_debt if (market_cap is not None and net_debt is not None) else None
            )

            snapshot[ticker] = {
                "quality_score": score_result.get("quality_score"),
                "earnings_yield": _safe_div(latest.get("ebit"), enterprise_value),
                "roic": (score_result.get("averages") or {}).get("roic"),
                "price": price,
                "market_cap": market_cap,
                "enterprise_value": enterprise_value,
                "fiscal_year": float(fiscal_year),
            }
        if snapshot:
            panel[date] = snapshot
        else:
            quality.note(f"{date}: nessun titolo con segnali completi, ribilanciamento saltato.")
    return panel


# ---------------------------------------------------------------------------
# Ranking e costruzione del portafoglio
# ---------------------------------------------------------------------------


def _zscores(values: Mapping[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """Z-score cross-section, robusto a serie costanti o quasi vuote."""
    clean = {key: value for key, value in values.items() if value is not None}
    if len(clean) < 2:
        return {key: 0.0 for key in clean}
    mean = sum(clean.values()) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean.values()) / (len(clean) - 1)
    std = math.sqrt(variance)
    if std < 1e-12:
        return {key: 0.0 for key in clean}
    return {key: (value - mean) / std for key, value in clean.items()}


def rank_snapshot(
    snapshot: Mapping[str, Mapping[str, Optional[float]]],
    *,
    quality_weight: float,
    value_weight: float,
    min_quality_score: Optional[float] = None,
) -> List[Tuple[str, float, Dict[str, Optional[float]]]]:
    """Ordina i titoli di una data per punteggio composito (qualita' + valore).

    Il composito e' la somma pesata degli z-score cross-section delle due dimensioni:
    normalizzare *dentro* la data evita che l'ampiezza assoluta dei punteggi (che
    cambia nel tempo) influenzi la selezione.
    """
    eligible = dict(snapshot)
    if min_quality_score is not None:
        eligible = {
            ticker: data for ticker, data in eligible.items()
            if data.get("quality_score") is not None
            and data["quality_score"] >= min_quality_score
        }

    quality_z = _zscores({t: d.get("quality_score") for t, d in eligible.items()})
    value_z = _zscores({t: d.get("earnings_yield") for t, d in eligible.items()})

    ranked: List[Tuple[str, float, Dict[str, Optional[float]]]] = []
    for ticker, data in eligible.items():
        if ticker not in quality_z and ticker not in value_z:
            continue
        composite = (
            quality_weight * quality_z.get(ticker, 0.0)
            + value_weight * value_z.get(ticker, 0.0)
        )
        detail = dict(data)
        detail["quality_z"] = quality_z.get(ticker)
        detail["value_z"] = value_z.get(ticker)
        detail["composite"] = composite
        ranked.append((ticker, composite, detail))

    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _target_weights(
    selection: Sequence[Tuple[str, float, Mapping[str, Optional[float]]]],
    weighting: str,
) -> Dict[str, float]:
    if not selection:
        return {}
    if str(weighting).lower() == "score":
        # Pesi proporzionali al composito traslato in positivo: mantiene l'ordinamento
        # senza permettere pesi negativi (la strategia e' long-only).
        floor = min(item[1] for item in selection)
        shifted = {ticker: (score - floor) + 0.1 for ticker, score, _ in selection}
        total = sum(shifted.values())
        return {ticker: value / total for ticker, value in shifted.items()}
    equal = 1.0 / len(selection)
    return {ticker: equal for ticker, _, _ in selection}


# ---------------------------------------------------------------------------
# Metriche di performance
# ---------------------------------------------------------------------------


def performance_metrics(
    returns: Any,
    *,
    benchmark_returns: Any = None,
    risk_free_rate: float = 0.02,
    periods_per_year: int = TRADING_DAYS,
) -> Dict[str, Optional[float]]:
    """Metriche standard di un tear sheet, calcolate su rendimenti periodali semplici."""
    empty: Dict[str, Optional[float]] = {
        "total_return": None, "cagr": None, "volatility": None, "sharpe": None,
        "sortino": None, "max_drawdown": None, "calmar": None, "best_period": None,
        "worst_period": None, "positive_periods_pct": None, "beta": None, "alpha": None,
        "tracking_error": None, "information_ratio": None, "correlation": None,
        "periods": None, "years": None,
    }
    if pd is None or returns is None:
        return empty
    series = pd.Series(returns).dropna()
    if series.empty:
        return empty

    periods = len(series)
    years = periods / periods_per_year
    growth = float((1.0 + series).prod())
    total_return = growth - 1.0
    cagr = growth ** (1.0 / years) - 1.0 if years > 0 and growth > 0 else None

    volatility = float(series.std(ddof=1)) * math.sqrt(periods_per_year) if periods > 1 else None
    period_rf = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = series - period_rf
    sharpe = None
    if periods > 1 and float(excess.std(ddof=1)) > 1e-12:
        sharpe = float(excess.mean() / excess.std(ddof=1)) * math.sqrt(periods_per_year)

    downside = excess[excess < 0]
    sortino = None
    if len(downside) > 1:
        downside_deviation = float(downside.std(ddof=1))
        if downside_deviation > 1e-12:
            sortino = float(excess.mean() / downside_deviation) * math.sqrt(periods_per_year)

    equity = (1.0 + series).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(drawdown.min())

    metrics: Dict[str, Optional[float]] = {
        "total_return": total_return,
        "cagr": cagr,
        "volatility": volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": (cagr / abs(max_drawdown)) if (cagr and max_drawdown) else None,
        "best_period": float(series.max()),
        "worst_period": float(series.min()),
        "positive_periods_pct": 100.0 * float((series > 0).sum()) / periods,
        "periods": float(periods),
        "years": years,
        "beta": None, "alpha": None, "tracking_error": None,
        "information_ratio": None, "correlation": None,
    }

    if benchmark_returns is not None:
        benchmark = pd.Series(benchmark_returns).reindex(series.index).dropna()
        aligned = series.reindex(benchmark.index).dropna()
        benchmark = benchmark.reindex(aligned.index)
        if len(aligned) > 2:
            variance = float(benchmark.var(ddof=1))
            if variance > 1e-18:
                beta = float(aligned.cov(benchmark)) / variance
                metrics["beta"] = beta
                benchmark_growth = float((1.0 + benchmark).prod())
                benchmark_years = len(benchmark) / periods_per_year
                benchmark_cagr = (
                    benchmark_growth ** (1.0 / benchmark_years) - 1.0
                    if benchmark_years > 0 and benchmark_growth > 0 else None
                )
                if cagr is not None and benchmark_cagr is not None:
                    # Alpha di Jensen su base annua.
                    metrics["alpha"] = cagr - (
                        risk_free_rate + beta * (benchmark_cagr - risk_free_rate)
                    )
                metrics["benchmark_cagr"] = benchmark_cagr
            difference = aligned - benchmark
            tracking_error = float(difference.std(ddof=1)) * math.sqrt(periods_per_year)
            metrics["tracking_error"] = tracking_error
            if tracking_error > 1e-12:
                metrics["information_ratio"] = (
                    float(difference.mean()) * periods_per_year / tracking_error
                )
            metrics["correlation"] = float(aligned.corr(benchmark))
    return metrics


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


def run_backtest(
    *,
    prices: Any,
    financials_by_ticker: Optional[Mapping[str, Mapping[str, Any]]] = None,
    panel: Optional[Mapping[Any, Mapping[str, Mapping[str, Optional[float]]]]] = None,
    benchmark: Any = None,
    strategy: Optional[Mapping[str, Any]] = None,
    start: Any = None,
) -> Dict[str, Any]:
    """Esegue il backtest della strategia quality + value.

    Args:
        prices: DataFrame di prezzi rettificati (righe = date, colonne = ticker).
        financials_by_ticker: bilanci per ticker; ignorato se ``panel`` e' fornito.
        panel: pannello di segnali gia' calcolato da :func:`build_signal_panel`
            (riutilizzabile fra piu' esecuzioni: e' la parte costosa).
        benchmark: Series di prezzi del benchmark, allineabile all'indice dei prezzi.
        strategy: override di :data:`DEFAULT_STRATEGY`.
        start: data di inizio del backtest.

    Returns:
        Dizionario con ``equity_curve``, ``drawdown``, ``returns``, ``metrics``,
        ``benchmark_metrics``, ``holdings`` per periodo, ``caveats`` e ``data_quality``.
    """
    quality = _DataQuality()
    config = dict(DEFAULT_STRATEGY)
    if strategy:
        for key, value in strategy.items():
            if key in config and value is not None:
                config[key] = value
            elif key not in config:
                quality.note(f"Parametro di strategia '{key}' sconosciuto: ignorato.")

    result: Dict[str, Any] = {
        "strategy": config, "equity_curve": None, "benchmark_curve": None,
        "drawdown": None, "returns": None, "metrics": {}, "benchmark_metrics": {},
        "holdings": [], "caveats": [], "data_quality": {}, "error": None,
    }

    if pd is None or prices is None or getattr(prices, "empty", True):
        result["error"] = "Serie storiche dei prezzi non disponibili."
        result["data_quality"] = quality.as_dict()
        return result

    prices = prices.sort_index().dropna(how="all")
    if start is not None:
        prices = prices.loc[prices.index >= pd.Timestamp(start)]

    dates = rebalance_dates(prices.index, config["rebalance"])
    if len(dates) < 2:
        result["error"] = (
            "Servono almeno due date di ribilanciamento: lo storico dei prezzi e' troppo corto."
        )
        result["data_quality"] = quality.as_dict()
        return result

    if panel is None:
        if not financials_by_ticker:
            result["error"] = "Servono i bilanci (o un pannello di segnali gia' calcolato)."
            result["data_quality"] = quality.as_dict()
            return result
        panel = build_signal_panel(
            financials_by_ticker, prices, dates,
            reporting_lag_days=int(config["reporting_lag_days"]), quality=quality,
        )
    if not panel:
        result["error"] = "Nessun segnale point-in-time disponibile nel periodo richiesto."
        result["data_quality"] = quality.as_dict()
        return result

    cost_rate = float(config["transaction_cost_bps"]) / 10000.0
    capital = float(config["initial_capital"])
    equity_parts: List[Any] = []
    previous_weights: Dict[str, float] = {}
    holdings_log: List[Dict[str, Any]] = []

    usable_dates = [date for date in dates if date in panel]
    for position, date in enumerate(usable_dates):
        end = usable_dates[position + 1] if position + 1 < len(usable_dates) else prices.index[-1]
        window = prices.loc[date:end]
        if len(window) < 2:
            continue

        ranked = rank_snapshot(
            panel[date],
            quality_weight=float(config["quality_weight"]),
            value_weight=float(config["value_weight"]),
            min_quality_score=config["min_quality_score"],
        )
        # Si tengono solo i titoli con prezzo valido per tutta la finestra: un titolo
        # con buchi nella serie falserebbe il rendimento di periodo.
        ranked = [
            item for item in ranked
            if item[0] in window.columns and window[item[0]].notna().iloc[0]
        ]
        selection = ranked[: int(config["top_n"])]
        if not selection:
            quality.note(f"{date}: nessun titolo selezionabile, periodo saltato.")
            continue

        weights = _target_weights(selection, str(config["weighting"]))

        # Il confronto e' con i pesi *derivati* dal periodo precedente, non con i pesi
        # obiettivo di allora: fra un ribilanciamento e l'altro i prezzi li hanno gia'
        # spostati, e ribilanciare costa solo la differenza residua.
        turnover = sum(
            abs(weights.get(ticker, 0.0) - previous_weights.get(ticker, 0.0))
            for ticker in set(weights) | set(previous_weights)
        )
        capital *= (1.0 - turnover * cost_rate)

        tickers = list(weights)
        sub = window[tickers].ffill()
        normalized = sub.divide(sub.iloc[0], axis=1)
        weighted = normalized.multiply(pd.Series(weights), axis=1).sum(axis=1)
        segment = weighted * capital

        # Il primo punto di ogni segmento coincide con l'ultimo del precedente.
        equity_parts.append(segment.iloc[1:] if equity_parts else segment)
        capital = float(segment.iloc[-1])

        holdings_log.append({
            "date": str(pd.Timestamp(date).date()),
            "turnover": _round(turnover, 4),
            "cost_pct": _round(turnover * cost_rate * 100.0, 4),
            "positions": [
                {
                    "ticker": ticker,
                    "weight": _round(weights[ticker], 4),
                    "composite": _round(detail.get("composite"), 3),
                    "quality_score": _round(detail.get("quality_score"), 1),
                    "earnings_yield_pct": _round(
                        (detail.get("earnings_yield") or 0) * 100.0, 2
                    ) if detail.get("earnings_yield") is not None else None,
                    "fiscal_year": int(detail["fiscal_year"]) if detail.get("fiscal_year") else None,
                }
                for ticker, _, detail in selection
            ],
        })

        # Pesi a fine periodo, dopo la deriva dei prezzi: sono il punto di partenza
        # del prossimo ribilanciamento.
        final_values = normalized.iloc[-1] * pd.Series(weights)
        total_value = float(final_values.sum())
        previous_weights = (
            {ticker: float(value) / total_value for ticker, value in final_values.items()}
            if total_value > 0 else dict(weights)
        )

    if not equity_parts:
        result["error"] = "Nessun periodo investito: verificare universo, date e filtri."
        result["data_quality"] = quality.as_dict()
        return result

    equity = pd.concat(equity_parts).sort_index()
    equity = equity[~equity.index.duplicated(keep="first")]
    returns = equity.pct_change().dropna()

    benchmark_returns = None
    benchmark_curve = None
    if benchmark is not None:
        try:
            aligned = pd.Series(benchmark).reindex(equity.index).ffill().dropna()
            if not aligned.empty:
                benchmark_curve = aligned / aligned.iloc[0] * float(config["initial_capital"])
                benchmark_returns = benchmark_curve.pct_change().dropna()
        except Exception as exc:  # pragma: no cover - difensivo
            quality.note(f"Benchmark non allineabile: {exc}")

    result["equity_curve"] = equity
    result["benchmark_curve"] = benchmark_curve
    result["returns"] = returns
    result["drawdown"] = equity / equity.cummax() - 1.0
    result["metrics"] = performance_metrics(
        returns, benchmark_returns=benchmark_returns,
        risk_free_rate=float(config["risk_free_rate"]),
    )
    if benchmark_returns is not None:
        result["benchmark_metrics"] = performance_metrics(
            benchmark_returns, risk_free_rate=float(config["risk_free_rate"])
        )
    result["holdings"] = holdings_log
    result["periods"] = len(holdings_log)
    result["turnover_mean"] = _round(
        sum(entry["turnover"] for entry in holdings_log) / len(holdings_log), 4
    )

    result["caveats"] = [
        "Survivorship bias: l'universo contiene solo societa' esistenti oggi; "
        "delistate e fallite sono assenti e i rendimenti ne risultano gonfiati.",
        f"Look-ahead mitigato con un lag di reporting di {config['reporting_lag_days']} giorni, "
        "ma i bilanci di yfinance sono nella versione rivista, non in quella depositata.",
        f"Solo {len(holdings_log)} periodi di detenzione: campione troppo piccolo per "
        "trarre conclusioni statistiche.",
        f"Costi: {config['transaction_cost_bps']:.0f} bp per unita' di peso scambiata; "
        "esclusi tasse, slippage variabile e impatto di mercato.",
        "Nessun aggiustamento per delisting, fusioni o sospensioni durante il periodo.",
    ]
    result["data_quality"] = quality.as_dict()
    return result


# ---------------------------------------------------------------------------
# Sweep dei parametri
# ---------------------------------------------------------------------------


def sweep_parameters(
    *,
    prices: Any,
    panel: Mapping[Any, Mapping[str, Mapping[str, Optional[float]]]],
    x_param: str,
    x_values: Sequence[Any],
    y_param: str,
    y_values: Sequence[Any],
    metric: str = "sharpe",
    benchmark: Any = None,
    strategy: Optional[Mapping[str, Any]] = None,
    start: Any = None,
) -> Dict[str, Any]:
    """Esegue il backtest su una griglia di due parametri.

    Serve a rispondere a "la strategia funziona in una *regione* di parametri o solo
    in un punto?". Una superficie con un picco isolato e' quasi sempre overfitting;
    una superficie con un altopiano ampio e' un risultato piu' credibile.

    Non usare questa funzione per scegliere i parametri migliori e poi presentarne il
    risultato come performance attesa: quella cifra e' contaminata dal multiple testing.
    """
    grid: List[List[Optional[float]]] = []
    for y_value in y_values:
        row: List[Optional[float]] = []
        for x_value in x_values:
            config = dict(strategy or {})
            config[x_param] = x_value
            config[y_param] = y_value
            outcome = run_backtest(
                prices=prices, panel=panel, benchmark=benchmark,
                strategy=config, start=start,
            )
            value = (outcome.get("metrics") or {}).get(metric)
            row.append(_round(value, 4) if value is not None else None)
        grid.append(row)

    flat = [value for row in grid for value in row if value is not None]
    best = None
    if flat:
        best_value = max(flat)
        for row_index, row in enumerate(grid):
            for column_index, value in enumerate(row):
                if value == best_value:
                    best = {
                        x_param: x_values[column_index],
                        y_param: y_values[row_index],
                        metric: value,
                    }
                    break
            if best:
                break

    return {
        "x_label": x_param, "x_values": list(x_values),
        "y_label": y_param, "y_values": list(y_values),
        "z_label": metric, "values": grid,
        "best": best,
        "mean": _round(sum(flat) / len(flat), 4) if flat else None,
        "dispersion": _round(max(flat) - min(flat), 4) if flat else None,
        "warning": (
            "La cella migliore non e' una stima di performance attesa: scegliere i "
            "parametri dopo aver visto i risultati e' overfitting. Guardare l'ampiezza "
            "dell'altopiano, non il picco."
        ),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _pct(value: Optional[float], digits: int = 2) -> str:
    return "n/d" if value is None else f"{value * 100:,.{digits}f}%"


def _num(value: Optional[float], digits: int = 2) -> str:
    return "n/d" if value is None else f"{value:,.{digits}f}"


def format_backtest_report(result: Mapping[str, Any], max_periods: int = 6) -> str:
    """Rende leggibile l'output di :func:`run_backtest`."""
    width = 92
    lines: List[str] = ["=" * width, " BACKTEST - strategia quality + value", "=" * width]

    if result.get("error"):
        lines.append(f" ERRORE: {result['error']}")
        lines.append("=" * width)
        return "\n".join(lines)

    config = result.get("strategy") or {}
    lines.append(
        f" Top {config.get('top_n')} titoli | ribilanciamento {config.get('rebalance')}"
        f" | pesi {config.get('weighting')}"
        f" | qualita' {config.get('quality_weight'):.0%} / valore {config.get('value_weight'):.0%}"
    )
    lines.append(
        f" Lag di reporting {config.get('reporting_lag_days')} giorni"
        f" | costi {config.get('transaction_cost_bps'):.0f} bp"
        f" | turnover medio {_pct(result.get('turnover_mean'), 1)}"
        " (somma dei |delta peso|: rotazione totale = 200%)"
    )
    lines.append("")

    metrics = result.get("metrics") or {}
    benchmark_metrics = result.get("benchmark_metrics") or {}
    lines.append("-" * width)
    lines.append(" PERFORMANCE")
    lines.append("-" * width)
    lines.append(f" {'Metrica':<28}{'Strategia':>16}{'Benchmark':>16}")
    rows = (
        ("Rendimento totale", "total_return", _pct),
        ("CAGR", "cagr", _pct),
        ("Volatilita' annua", "volatility", _pct),
        ("Sharpe", "sharpe", _num),
        ("Sortino", "sortino", _num),
        ("Max drawdown", "max_drawdown", _pct),
        ("Calmar", "calmar", _num),
        ("Giorni positivi", "positive_periods_pct", lambda v: "n/d" if v is None else f"{v:,.1f}%"),
    )
    for label, key, formatter in rows:
        lines.append(
            f" {label:<28}{formatter(metrics.get(key)):>16}"
            f"{formatter(benchmark_metrics.get(key)):>16}"
        )
    lines.append("")
    lines.append(f" {'Beta vs benchmark':<28}{_num(metrics.get('beta')):>16}")
    lines.append(f" {'Alpha di Jensen (annuo)':<28}{_pct(metrics.get('alpha')):>16}")
    lines.append(f" {'Tracking error':<28}{_pct(metrics.get('tracking_error')):>16}")
    lines.append(f" {'Information ratio':<28}{_num(metrics.get('information_ratio')):>16}")
    lines.append(f" {'Anni simulati':<28}{_num(metrics.get('years'), 1):>16}")
    lines.append("")

    holdings = result.get("holdings") or []
    if holdings:
        lines.append("-" * width)
        lines.append(" PORTAFOGLI PER RIBILANCIAMENTO")
        lines.append("-" * width)
        for entry in holdings[:max_periods]:
            names = ", ".join(
                f"{position['ticker']}({position['weight'] * 100:.0f}%)"
                for position in entry["positions"]
            )
            lines.append(f" {entry['date']}  turnover {entry['turnover'] * 100:>5.1f}%  {names}")
        if len(holdings) > max_periods:
            lines.append(f" ... e altri {len(holdings) - max_periods} ribilanciamenti")
        lines.append("")

    lines.append("-" * width)
    lines.append(" LIMITI DI QUESTO BACKTEST (leggerli prima di credere ai numeri)")
    lines.append("-" * width)
    for caveat in result.get("caveats") or []:
        lines.append(f"  * {caveat}")
    lines.append("=" * width)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "JNJ", "PG", "KO", "PEP", "V", "MA", "UNH",
    "HD", "MCD", "CSCO", "ADBE", "NKE", "TXN", "HON", "LOW", "SBUX", "CL",
]


if __name__ == "__main__":
    universe = [arg.upper() for arg in sys.argv[1:] if not arg.startswith("-")] or DEFAULT_UNIVERSE
    print(f"Scarico prezzi e bilanci per {len(universe)} titoli ...\n")
    data = fetch_universe_data(universe, years=10, benchmark="SPY")

    if data["prices"] is None or not data["financials"]:
        print("Dati non disponibili (rete assente o ticker non validi).")
        for message in data["data_quality"]["missing"][:5]:
            print(f"  * {message}")
        sys.exit(1)

    outcome = run_backtest(
        prices=data["prices"],
        financials_by_ticker=data["financials"],
        benchmark=data["benchmark"],
    )
    print(format_backtest_report(outcome))
