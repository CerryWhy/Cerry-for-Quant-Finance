"""Test di integrazione: qualita' -> valutazione -> grafici, con dati sintetici.

Serve a verificare che i moduli parlino davvero la stessa lingua: i grafici vengono
costruiti sui dizionari **reali** prodotti da ``calculate_quality_score`` e
``calculate_valuation``, non su strutture scritte a mano per l'occasione. E' il test
che intercetta i disallineamenti fra un modulo e l'altro.

Esecuzione::

    python tests/test_pipeline.py
    pytest tests/test_pipeline.py
"""

from __future__ import annotations

import os
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "backend"))
sys.path.insert(0, os.path.join(BASE, ".."))
sys.path.insert(0, BASE)

import synthetic  # noqa: E402
from models.backtest import build_signal_panel, rebalance_dates, run_backtest  # noqa: E402
from models.quality_score import calculate_quality_score  # noqa: E402
from models.valuation import calculate_valuation  # noqa: E402

import run_analysis  # noqa: E402

try:
    from models import visualize
except ImportError:  # pragma: no cover
    visualize = None


def _fake_fetchers(monkeypatched: dict):
    """Sostituisce i download con i dati sintetici, senza toccare la rete."""
    universe = synthetic.make_universe()
    universe["ALFA"] = synthetic.ALFA

    def fetch_financials(ticker, years=10):
        return universe.get(ticker.upper(), synthetic.ALFA)

    def fetch_market_data(ticker, quality=None):
        data = dict(synthetic.ALFA_MARKET)
        data["company_name"] = f"Societa' {ticker.upper()}"
        # Prezzi diversi per ticker, cosi' i margini di sicurezza non sono identici.
        data["price"] = 120.0 + 25.0 * (sum(map(ord, ticker)) % 5)
        data["shares_outstanding"] = 1e9 if ticker.upper() != "ALFA" else 15.5e9
        data["market_cap"] = data["price"] * data["shares_outstanding"]
        return data

    monkeypatched["fetch_financials"] = fetch_financials
    monkeypatched["fetch_market_data"] = fetch_market_data
    return universe


def test_analyze_ticker_produce_strutture_coerenti():
    patched: dict = {}
    _fake_fetchers(patched)
    original = (run_analysis.fetch_financials, run_analysis.fetch_market_data)
    run_analysis.fetch_financials = patched["fetch_financials"]
    run_analysis.fetch_market_data = patched["fetch_market_data"]
    try:
        analysis = run_analysis.analyze_ticker("ALFA")
    finally:
        run_analysis.fetch_financials, run_analysis.fetch_market_data = original

    quality, valuation = analysis["quality"], analysis["valuation"]
    assert quality["quality_score"] is not None
    assert 0 <= quality["quality_score"] <= 100
    assert valuation["fair_value"].get("point") is not None
    assert valuation["margin_of_safety"] is not None

    row = run_analysis._universe_row(analysis)
    assert row["ticker"] == "ALFA"
    assert row["quality_score"] == quality["quality_score"]
    assert row["fair_value"] == valuation["fair_value"]["point"]


def test_i_grafici_leggono_i_dizionari_reali():
    """I grafici devono funzionare sull'output vero dei moduli, non su dati finti."""
    if visualize is None:
        print("  (matplotlib assente: test dei grafici saltato)")
        return

    quality = calculate_quality_score("ALFA", financials=synthetic.ALFA)
    valuation = calculate_valuation(
        "ALFA", financials=synthetic.ALFA, market_data=synthetic.ALFA_MARKET,
    )

    # Il radar legge category_scores -> components -> <nome> -> score: se la struttura
    # cambiasse, qui uscirebbe un radar piatto invece di un errore. Lo verifichiamo.
    components = quality["category_scores"]["profitability"]["components"]
    assert "roic" in components and components["roic"]["score"] is not None

    universe_rows = []
    for ticker in ("AAA", "BBB", "CCC"):
        financials = synthetic.make_universe()[ticker]
        ticker_quality = calculate_quality_score(ticker, financials=financials)
        ticker_valuation = calculate_valuation(
            ticker, financials=financials,
            market_data={"price": 95.0, "shares_outstanding": 1e9, "beta": 1.0,
                         "market_cap": 95e9, "currency": "USD"},
        )
        universe_rows.append({
            "ticker": ticker,
            "quality_score": ticker_quality["quality_score"],
            "category_scores": ticker_quality["category_scores"],
            "margin_of_safety": ticker_valuation["margin_of_safety"],
        })

    with tempfile.TemporaryDirectory() as directory:
        charts = {
            "radar.png": lambda p: visualize.plot_quality_radar(quality, save=p),
            "storico.png": lambda p: visualize.plot_metrics_history(quality, save=p),
            "football.png": lambda p: visualize.plot_football_field(valuation, save=p),
            "sensitivita.png": lambda p: visualize.plot_sensitivity_surface(
                valuation["sensitivity"], kind="surface", percent_axes=True, save=p),
            "contour.png": lambda p: visualize.plot_sensitivity_surface(
                valuation["sensitivity"], kind="contour", percent_axes=True, save=p),
            "heatmap.png": lambda p: visualize.plot_universe_heatmap(universe_rows, save=p),
            "scatter.png": lambda p: visualize.plot_quality_value_scatter(universe_rows, save=p),
            "tearsheet.png": lambda p: visualize.create_tearsheet(
                quality=quality, valuation=valuation, save=p),
        }
        for filename, builder in charts.items():
            path = os.path.join(directory, filename)
            builder(path)
            assert os.path.exists(path), filename
            # Un PNG praticamente vuoto segnala un grafico che non ha disegnato nulla.
            assert os.path.getsize(path) > 8000, f"{filename} sospettosamente piccolo"
        visualize.plt.close("all")


def test_tearsheet_completo_con_backtest():
    if visualize is None:
        return
    universe = synthetic.make_universe()
    prices = synthetic.make_prices(list(universe))
    benchmark = synthetic.make_benchmark(prices.index)
    panel = build_signal_panel(universe, prices, rebalance_dates(prices.index, "annual"))
    backtest = run_backtest(prices=prices, panel=panel, benchmark=benchmark,
                            strategy={"top_n": 3})
    assert backtest["error"] is None

    quality = calculate_quality_score("ALFA", financials=synthetic.ALFA)
    valuation = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                    market_data=synthetic.ALFA_MARKET)
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "tearsheet.png")
        visualize.create_tearsheet(quality=quality, valuation=valuation,
                                   backtest=backtest, save=path)
        assert os.path.getsize(path) > 50000
        visualize.plt.close("all")


def test_grafici_su_dati_mancanti_non_esplodono():
    """Con risultati vuoti i grafici devono produrre un riquadro con la spiegazione."""
    if visualize is None:
        return
    empty_quality = {"ticker": "X", "quality_score": None, "category_scores": {},
                     "metrics": {}, "years_analyzed": []}
    empty_valuation = {"ticker": "X", "methods": {}, "error": "Nessun dato"}
    with tempfile.TemporaryDirectory() as directory:
        visualize.plot_quality_radar(empty_quality, save=os.path.join(directory, "a.png"))
        visualize.plot_metrics_history(empty_quality, save=os.path.join(directory, "b.png"))
        visualize.plot_football_field(empty_valuation, save=os.path.join(directory, "c.png"))
        visualize.plot_equity_curve({"error": "niente"}, save=os.path.join(directory, "d.png"))
        visualize.plot_universe_heatmap([], save=os.path.join(directory, "e.png"))
        visualize.plot_quality_value_scatter([], save=os.path.join(directory, "f.png"))
        visualize.plot_sensitivity_surface({}, save=os.path.join(directory, "g.png"))
        for name in "abcdefg":
            assert os.path.exists(os.path.join(directory, f"{name}.png"))
        visualize.plt.close("all")


def test_tabella_riepilogo():
    rows = [
        {"ticker": "AAA", "quality_score": 80.0, "price": 100.0, "fair_value": 150.0,
         "margin_of_safety": 0.33, "verdict": "Sconto significativo"},
        {"ticker": "BBB", "quality_score": None, "price": None, "fair_value": None,
         "margin_of_safety": None, "verdict": None},
    ]
    run_analysis.print_summary_table(rows)  # non deve sollevare eccezioni


if __name__ == "__main__":
    failures = 0
    for name, test in sorted(globals().items()):
        if not name.startswith("test_") or not callable(test):
            continue
        try:
            test()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
    print(f"\n{'TUTTI I TEST OK' if not failures else f'{failures} test falliti'}")
    sys.exit(1 if failures else 0)
