"""Test di ``backend/models/valuation.py``, con verifiche analitiche dove possibile.

Le formule non vengono confrontate con "un numero che sembra giusto" ma con il
risultato che devono dare per costruzione: una rendita perpetua, un reverse DCF che
deve ritrovare la crescita da cui e' partito, un WACC calcolato a mano.

Esecuzione::

    python tests/test_valuation.py
    pytest tests/test_valuation.py
"""

from __future__ import annotations

import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "backend", "models"))
sys.path.insert(0, BASE)

import synthetic  # noqa: E402
from valuation import (  # noqa: E402
    DEFAULT_ASSUMPTIONS,
    calculate_valuation,
    cost_of_capital,
    dcf_value_per_share,
    epv_value_per_share,
    format_valuation_report,
    graham_number,
    growth_path,
    historical_cagr,
    ncav_per_share,
    normalize_series,
    reverse_dcf,
)


def test_dcf_riproduce_una_rendita_perpetua():
    """Con crescita zero e sconto del 10%, il valore deve essere esattamente flusso/r."""
    rate, years = 0.10, 10
    result = dcf_value_per_share(
        100.0, discount_rate=rate, initial_growth=0.0, terminal_growth=0.0,
        net_debt=0.0, shares=1.0, projection_years=years, fade_years=0, mid_year=False,
    )
    expected_explicit = sum(100.0 / (1 + rate) ** t for t in range(1, years + 1))
    expected_terminal = (100.0 / rate) / (1 + rate) ** years
    assert abs(result["pv_explicit"] - expected_explicit) < 1e-6
    assert abs(result["pv_terminal"] - expected_terminal) < 1e-6
    assert abs(result["value_per_share"] - 1000.0) < 1e-6


def test_dcf_convenzione_mid_year_alza_il_valore():
    """Incassare a meta' anno invece che a fine anno vale di piu': deve vedersi."""
    common = dict(discount_rate=0.10, initial_growth=0.0, terminal_growth=0.0,
                  net_debt=0.0, shares=1.0, projection_years=10, fade_years=0)
    full_year = dcf_value_per_share(100.0, mid_year=False, **common)["value_per_share"]
    mid_year = dcf_value_per_share(100.0, mid_year=True, **common)["value_per_share"]
    assert mid_year > full_year


def test_dcf_rifiuta_input_impossibili():
    # tasso di sconto non superiore alla crescita terminale: il valore divergerebbe
    assert dcf_value_per_share(100.0, discount_rate=0.02, initial_growth=0.0,
                               terminal_growth=0.03, net_debt=0, shares=1)["error"]
    # flusso base negativo o assente
    assert dcf_value_per_share(-50.0, discount_rate=0.1, initial_growth=0.0,
                               terminal_growth=0.02, net_debt=0, shares=1)["error"]
    assert dcf_value_per_share(None, discount_rate=0.1, initial_growth=0.0,
                               terminal_growth=0.02, net_debt=0, shares=1)["error"]
    assert dcf_value_per_share(100.0, discount_rate=0.1, initial_growth=0.0,
                               terminal_growth=0.02, net_debt=0, shares=0)["error"]


def test_growth_path_scende_fino_alla_crescita_terminale():
    path = growth_path(0.10, 0.02, 10, 5)
    assert len(path) == 10
    assert path[:5] == [0.10] * 5
    assert abs(path[-1] - 0.02) < 1e-12
    assert path == sorted(path, reverse=True)          # monotono decrescente
    assert growth_path(0.10, 0.02, 5, 0) == [0.10] * 5  # senza fade resta piatto


def test_reverse_dcf_ritrova_la_crescita_di_partenza():
    common = dict(discount_rate=0.09, terminal_growth=0.025, net_debt=1000.0,
                  shares=100.0, projection_years=10, fade_years=5, mid_year=True)
    price = dcf_value_per_share(500.0, initial_growth=0.07, **common)["value_per_share"]
    implied = reverse_dcf(price, 500.0, **common)["implied_growth"]
    assert abs(implied - 0.07) < 1e-3

    assert reverse_dcf(1e12, 500.0, **common)["at_bound"] == "max"
    assert reverse_dcf(0.01, 500.0, **common)["at_bound"] == "min"
    assert reverse_dcf(None, 500.0, **common)["error"]


def test_epv_graham_e_ncav():
    epv = epv_value_per_share(1000.0, tax_rate=0.25, discount_rate=0.10,
                              net_debt=2000.0, shares=100.0)
    assert abs(epv["enterprise_value"] - 7500.0) < 1e-9    # 1000 * 0.75 / 0.10
    assert abs(epv["value_per_share"] - 55.0) < 1e-9       # (7500 - 2000) / 100
    assert epv_value_per_share(-100.0, tax_rate=0.25, discount_rate=0.10,
                               net_debt=0, shares=100)["error"]

    assert abs(graham_number(5.0, 20.0) - (22.5 * 5 * 20) ** 0.5) < 1e-9
    assert graham_number(-1.0, 20.0) is None               # utile negativo: non applicabile
    assert graham_number(5.0, None) is None

    assert abs(ncav_per_share(1000.0, 400.0, 100.0) - 6.0) < 1e-9
    assert ncav_per_share(None, 400.0, 100.0) is None


def test_wacc_calcolato_a_mano():
    capital = cost_of_capital(
        beta=1.2, market_cap=800e9, total_debt=100e9, interest_expense=4e9,
        tax_rate=0.15, assumptions=DEFAULT_ASSUMPTIONS,
    )
    cost_equity = 0.04 + 1.2 * 0.05
    cost_debt = 4.0 / 100.0
    expected = (800 / 900) * cost_equity + (100 / 900) * cost_debt * 0.85
    assert abs(capital["wacc"] - expected) < 1e-9
    assert abs(capital["cost_of_equity"] - cost_equity) < 1e-12

    # senza alcun dato deve comunque restituire un WACC plausibile, non un errore
    fallback = cost_of_capital(beta=None, market_cap=None, total_debt=None,
                               interest_expense=None, tax_rate=None,
                               assumptions=DEFAULT_ASSUMPTIONS)
    assert DEFAULT_ASSUMPTIONS["min_wacc"] <= fallback["wacc"] <= DEFAULT_ASSUMPTIONS["max_wacc"]

    forced = cost_of_capital(beta=1.0, market_cap=100e9, total_debt=0,
                             interest_expense=0, tax_rate=0.2,
                             assumptions=DEFAULT_ASSUMPTIONS, override=0.11)
    assert abs(forced["wacc"] - 0.11) < 1e-12


def test_normalizzazione_e_cagr():
    series = {2024: 100.0, 2023: 50.0, 2022: 90.0, 2021: 10.0}
    assert normalize_series(series, "last") == 100.0
    assert normalize_series(series, "median3") == 90.0          # mediana di 100, 50, 90
    assert abs(normalize_series(series, "mean3") - 80.0) < 1e-9
    assert normalize_series({}, "median3") is None

    # da 100 a 121 in 2 anni -> 10% annuo
    assert abs(historical_cagr({2020: 100.0, 2022: 121.0}) - 0.10) < 1e-12
    assert historical_cagr({2020: -10.0, 2022: 50.0}) is None   # base negativa: non ha senso
    assert historical_cagr({2020: 100.0}) is None


def test_valutazione_completa_e_coerente():
    result = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                 market_data=synthetic.ALFA_MARKET)
    assert result["error"] is None
    assert result["fair_value"]["point"] is not None
    assert result["fair_value"]["low"] <= result["fair_value"]["point"] <= result["fair_value"]["high"]

    scenarios = result["scenarios"]
    assert (scenarios["bear"]["value_per_share"]
            < scenarios["base"]["value_per_share"]
            < scenarios["bull"]["value_per_share"])

    # somma dei pesi dei metodi aggregati = 1
    weights = result["fair_value"]["weights"]
    assert abs(sum(weights.values()) - 1.0) < 1e-3
    # Graham e NCAV restano fuori dalla sintesi
    assert result["methods"]["graham_number"]["aggregated"] is False
    assert result["methods"]["ncav"]["aggregated"] is False

    grid = result["sensitivity"]
    middle_row = [cell for cell in grid["values"][2] if cell is not None]
    assert middle_row == sorted(middle_row, reverse=True), "il valore deve calare se il WACC sale"
    first_column = [grid["values"][i][0] for i in range(len(grid["y_values"]))]
    assert first_column == sorted(first_column), "il valore deve salire con la crescita terminale"

    json.dumps(result, default=str)
    assert format_valuation_report(result)


def test_valutazione_degrada_senza_dati():
    empty = {"ticker": "X", "income_statement": None, "balance_sheet": None,
             "cash_flow": None, "years": []}
    result = calculate_valuation("X", financials=empty,
                                 market_data={"price": 10.0, "shares_outstanding": 100.0})
    assert result["error"] and result["fair_value"] == {}
    assert format_valuation_report(result)

    no_price = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                   market_data={"price": None, "shares_outstanding": None})
    assert no_price["margin_of_safety"] is None
    assert no_price["data_quality"]["missing"]


def test_override_utente():
    base = calculate_valuation("ALFA", financials=synthetic.ALFA,
                               market_data=synthetic.ALFA_MARKET)
    slower = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                 market_data=synthetic.ALFA_MARKET, growth_override=0.02)
    pricier = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                  market_data=synthetic.ALFA_MARKET, wacc_override=0.14)
    assert slower["fair_value"]["point"] < base["fair_value"]["point"]
    assert pricier["fair_value"]["point"] < base["fair_value"]["point"]
    assert abs(pricier["cost_of_capital"]["wacc"] - 0.14) < 1e-9


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
        print("\nEsempio di report:\n")
        print(format_valuation_report(calculate_valuation(
            "ALFA", financials=synthetic.ALFA, market_data=synthetic.ALFA_MARKET)))
    sys.exit(1 if failures else 0)
