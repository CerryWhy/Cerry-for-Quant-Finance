"""Test di ``backend/models/backtest.py``, con un titolo-trappola per il look-ahead.

Il test piu' importante di questo file e' ``test_niente_look_ahead``: un emittente
sintetico pessimo fino al 2021 e ottimo dal 2022 viene messo nell'universo, e si
verifica che il modello non lo scelga finche' quei bilanci non erano pubblici. E'
l'unico modo di dimostrare che il filtro point-in-time funziona davvero.

Esecuzione::

    python tests/test_backtest.py
    pytest tests/test_backtest.py
"""

from __future__ import annotations

import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "backend", "models"))
sys.path.insert(0, BASE)

import synthetic  # noqa: E402
from backtest import (  # noqa: E402
    build_signal_panel,
    format_backtest_report,
    performance_metrics,
    rank_snapshot,
    rebalance_dates,
    run_backtest,
    slice_financials,
    sweep_parameters,
)

UNIVERSE = synthetic.make_universe()
PRICES = synthetic.make_prices(list(UNIVERSE) + ["LOOK"])
BENCHMARK = synthetic.make_benchmark(PRICES.index)
DATES = rebalance_dates(PRICES.index, "annual")

#: Titolo-trappola: risultati pessimi fino al 2021, eccezionali dal 2022 in poi.
#: Un backtest con look-ahead lo comprerebbe gia' nel 2020.
TRAP = synthetic.make_financials(
    "LOOK",
    revenue=[50e9] * 6,
    operating_income=[25e9, 25e9, 25e9, 1e9, 1e9, 1e9],
    net_income=[20e9, 20e9, 20e9, 0.5e9, 0.5e9, 0.5e9],
    equity=[10e9] * 6, debt=[5e9] * 6, cash=[8e9] * 6, shares=1e9,
)


def test_slice_financials_taglia_il_futuro():
    sliced = slice_financials(UNIVERSE["AAA"], pd.Timestamp("2022-10-01"))
    assert sliced["years"] == [2021, 2020, 2019]
    expected = [pd.Timestamp(f"{year}-12-31") for year in (2021, 2020, 2019)]
    assert list(sliced["income_statement"].columns) == expected
    assert list(sliced["balance_sheet"].columns) == expected
    assert slice_financials(UNIVERSE["AAA"], pd.Timestamp("2010-01-01"))["years"] == []


def test_rebalance_dates():
    assert len(DATES) == 6 and all(date.month == 1 for date in DATES)
    assert len(rebalance_dates(PRICES.index, "quarterly")) == 24
    assert rebalance_dates(None, "annual") == []


def test_performance_metrics_su_serie_note():
    # raddoppio esatto in due anni -> CAGR = sqrt(2) - 1
    daily = 2 ** (1 / (252 * 2)) - 1
    returns = pd.Series([daily] * (252 * 2), index=pd.bdate_range("2020-01-01", periods=252 * 2))
    metrics = performance_metrics(returns)
    assert abs(metrics["cagr"] - (2 ** 0.5 - 1)) < 1e-6
    assert abs(metrics["total_return"] - 1.0) < 1e-9
    assert abs(metrics["max_drawdown"]) < 1e-12
    assert metrics["positive_periods_pct"] == 100.0

    # perdita del 50% e recupero parziale -> drawdown massimo esattamente -50%
    swing = pd.Series([0.0, -0.5, 0.0, 0.25], index=pd.bdate_range("2020-01-01", periods=4))
    assert abs(performance_metrics(swing)["max_drawdown"] + 0.5) < 1e-12

    assert performance_metrics(None)["cagr"] is None
    assert performance_metrics(pd.Series([], dtype=float))["cagr"] is None


def test_beta_e_alpha_contro_se_stessi():
    """Una serie confrontata con se stessa ha beta 1, alpha 0 e correlazione 1."""
    returns = PRICES["AAA"].pct_change().dropna()
    metrics = performance_metrics(returns, benchmark_returns=returns, risk_free_rate=0.02)
    assert abs(metrics["beta"] - 1.0) < 1e-9
    assert abs(metrics["alpha"]) < 1e-9
    assert abs(metrics["correlation"] - 1.0) < 1e-9
    assert abs(metrics["tracking_error"]) < 1e-9


def test_niente_look_ahead():
    """Il titolo-trappola non deve mai essere valutato su bilanci non ancora pubblici."""
    universe = dict(UNIVERSE)
    universe["LOOK"] = TRAP
    panel = build_signal_panel(universe, PRICES, DATES, reporting_lag_days=90)
    assert panel, "il pannello non dovrebbe essere vuoto"

    for date, snapshot in panel.items():
        for ticker, signals in snapshot.items():
            fiscal_year = int(signals["fiscal_year"])
            # Al ribilanciamento di gennaio dell'anno Y, l'ultimo bilancio con 90 giorni
            # di lag gia' trascorsi e' quello dell'anno Y-2.
            assert fiscal_year <= date.year - 2, (date, ticker, fiscal_year)

    # Nello specifico: prima del 2024 non puo' vedere gli ottimi conti dal 2022 in poi.
    for date, snapshot in panel.items():
        if date.year <= 2023 and "LOOK" in snapshot:
            assert int(snapshot["LOOK"]["fiscal_year"]) <= 2021


def test_backtest_completo():
    panel = build_signal_panel(UNIVERSE, PRICES, DATES)
    result = run_backtest(prices=PRICES, panel=panel, benchmark=BENCHMARK,
                          strategy={"top_n": 3, "rebalance": "annual"})
    assert result["error"] is None
    assert len(result["equity_curve"]) > 100
    assert result["metrics"]["cagr"] is not None
    assert result["metrics"]["beta"] is not None
    assert result["drawdown"].min() <= 0
    assert result["caveats"], "i limiti del backtest devono sempre essere dichiarati"

    for entry in result["holdings"]:
        assert len(entry["positions"]) <= 3
        assert abs(sum(position["weight"] for position in entry["positions"]) - 1.0) < 1e-3
    assert format_backtest_report(result)


def test_i_costi_riducono_il_rendimento():
    panel = build_signal_panel(UNIVERSE, PRICES, DATES)
    free = run_backtest(prices=PRICES, panel=panel, strategy={"top_n": 3,
                                                              "transaction_cost_bps": 0})
    costly = run_backtest(prices=PRICES, panel=panel, strategy={"top_n": 3,
                                                                "transaction_cost_bps": 200})
    assert costly["metrics"]["cagr"] < free["metrics"]["cagr"]


def test_pesi_per_punteggio_sono_validi():
    panel = build_signal_panel(UNIVERSE, PRICES, DATES)
    result = run_backtest(prices=PRICES, panel=panel, strategy={"top_n": 4,
                                                                "weighting": "score"})
    assert result["error"] is None
    for entry in result["holdings"]:
        weights = [position["weight"] for position in entry["positions"]]
        assert all(weight > 0 for weight in weights), "strategia long-only: nessun peso negativo"
        assert abs(sum(weights) - 1.0) < 1e-3


def test_filtro_di_qualita_minima():
    panel = build_signal_panel(UNIVERSE, PRICES, DATES)
    snapshot = panel[sorted(panel)[-1]]
    everything = rank_snapshot(snapshot, quality_weight=0.5, value_weight=0.5)
    filtered = rank_snapshot(snapshot, quality_weight=0.5, value_weight=0.5,
                             min_quality_score=60)
    assert len(filtered) <= len(everything)
    assert all(detail["quality_score"] >= 60 for _, _, detail in filtered)
    # l'ordinamento deve essere decrescente per punteggio composito
    scores = [score for _, score, _ in everything]
    assert scores == sorted(scores, reverse=True)


def test_sweep_parametri():
    panel = build_signal_panel(UNIVERSE, PRICES, DATES)
    sweep = sweep_parameters(
        prices=PRICES, panel=panel, benchmark=BENCHMARK,
        x_param="top_n", x_values=[2, 3, 4, 5],
        y_param="quality_weight", y_values=[0.0, 0.5, 1.0], metric="sharpe",
    )
    assert len(sweep["values"]) == 3 and len(sweep["values"][0]) == 4
    assert sweep["best"] is not None
    assert sweep["warning"], "lo sweep deve sempre avvisare del rischio di overfitting"


def test_casi_degradati():
    panel = build_signal_panel(UNIVERSE, PRICES, DATES)
    assert run_backtest(prices=None)["error"]
    assert run_backtest(prices=PRICES.iloc[:5], panel=panel)["error"]
    assert run_backtest(prices=PRICES)["error"]            # senza bilanci ne' pannello
    empty = run_backtest(prices=PRICES, panel={})
    assert empty["error"] and format_backtest_report(empty)


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
    if not failures:
        panel = build_signal_panel(UNIVERSE, PRICES, DATES)
        print("\nEsempio di report:\n")
        print(format_backtest_report(run_backtest(
            prices=PRICES, panel=panel, benchmark=BENCHMARK, strategy={"top_n": 3})))
    sys.exit(1 if failures else 0)
