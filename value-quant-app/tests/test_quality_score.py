"""Test offline di ``backend/models/quality_score.py`` con bilanci sintetici.

Non serve rete: i tre prospetti sono costruiti a mano, cosi' la logica di calcolo
(ROIC, Owner Earnings, ratios, consistenza, scoring, gestione dei dati mancanti)
e' verificabile in modo deterministico.

Esecuzione::

    python tests/test_quality_score.py     # standalone, senza pytest
    pytest tests/test_quality_score.py     # se pytest e' installato
"""

from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from models.quality_score import (  # noqa: E402
    calculate_consistency,
    calculate_quality_score,
    fetch_financials,
    format_report,
)

YEARS = [2024, 2023, 2022, 2021, 2020, 2019]
COLUMNS = [pd.Timestamp(f"{year}-12-31") for year in YEARS]


def _frame(rows):
    """Costruisce un DataFrame nel formato yfinance: righe = voci, colonne = date."""
    return pd.DataFrame(
        {column: [values[i] for values in rows.values()] for i, column in enumerate(COLUMNS)},
        index=list(rows),
    )


REVENUE = [400e9, 383e9, 394e9, 366e9, 274e9, 260e9]
OPERATING_INCOME = [123e9, 114e9, 119e9, 108e9, 66e9, 63e9]
NET_INCOME = [99e9, 97e9, 100e9, 95e9, 57e9, 55e9]
EQUITY = [57e9, 62e9, 50e9, 63e9, 65e9, 90e9]
ASSETS = [364e9, 352e9, 352e9, 351e9, 323e9, 338e9]

INCOME_STATEMENT = _frame({
    "Total Revenue": REVENUE,
    "Gross Profit": [value * 0.44 for value in REVENUE],
    "Operating Income": OPERATING_INCOME,
    "Pretax Income": [value * 1.01 for value in OPERATING_INCOME],
    "Tax Provision": [value * 1.01 * 0.15 for value in OPERATING_INCOME],
    "Net Income": NET_INCOME,
    "Interest Expense": [3.9e9, 3.9e9, 2.9e9, 2.6e9, 2.9e9, 3.6e9],
})

BALANCE_SHEET = _frame({
    "Total Assets": ASSETS,
    "Total Liabilities Net Minority Interest": [a - e for a, e in zip(ASSETS, EQUITY)],
    "Stockholders Equity": EQUITY,
    "Total Debt": [106e9, 111e9, 120e9, 136e9, 122e9, 108e9],
    "Cash And Cash Equivalents": [30e9, 29e9, 24e9, 35e9, 38e9, 48e9],
    "Current Assets": [153e9, 143e9, 135e9, 134e9, 143e9, 162e9],
    "Current Liabilities": [176e9, 145e9, 154e9, 125e9, 105e9, 105e9],
})

CASH_FLOW = _frame({
    "Depreciation And Amortization": [11.4e9, 11.5e9, 11.1e9, 11.3e9, 11.0e9, 12.5e9],
    "Capital Expenditure": [-9.4e9, -11.0e9, -10.7e9, -11.1e9, -7.3e9, -10.5e9],
    "Change In Working Capital": [3.6e9, -6.6e9, 1.2e9, 14.0e9, 5.7e9, -3.5e9],
})

FINANCIALS = {
    "ticker": "ALFA",
    "company_name": "Societa' Alfa",
    "currency": "USD",
    "income_statement": INCOME_STATEMENT,
    "balance_sheet": BALANCE_SHEET,
    "cash_flow": CASH_FLOW,
    "years": YEARS,
    "data_quality": {"notes": [], "estimated": [], "missing": []},
}


def test_consistency_su_serie_nota():
    stats = calculate_consistency([10.0, 11.0, 10.5, 12.0, 13.5])
    assert stats["n"] == 5
    assert round(stats["mean"], 4) == 11.4
    assert round(stats["std_dev"], 3) == 1.387
    assert round(stats["coefficient_of_variation"], 3) == 0.122
    assert stats["growth_years"] == 3 and stats["comparisons"] == 4
    assert stats["growth_years_pct"] == 75.0
    assert stats["positive_years_pct"] == 100.0


def test_consistency_casi_limite():
    # dizionario {anno: valore}: viene riordinato cronologicamente prima del confronto
    assert calculate_consistency({2020: 1.0, 2022: 3.0, 2021: 2.0})["growth_years_pct"] == 100.0
    # serie vuota / con un solo punto: nessun crash, campi non calcolabili a None
    assert calculate_consistency([])["n"] == 0
    assert calculate_consistency([5.0])["std_dev"] is None
    # i valori mancanti vengono ignorati
    assert calculate_consistency([1.0, None, 2.0])["n"] == 2


def test_metriche_anno_per_anno():
    result = calculate_quality_score("ALFA", financials=FINANCIALS)
    metrics = result["metrics"]

    # ROIC 2024 = EBIT * (1 - aliquota) / (debito + equity - cassa)
    invested_capital = 106e9 + 57e9 - 30e9
    nopat = 123e9 * (1 - 0.15)
    assert abs(metrics["roic"][2024] - nopat / invested_capital * 100) < 1e-3

    # Owner Earnings 2024 = utile netto + D&A - CapEx + variazione circolante (impatto cassa)
    assert abs(metrics["owner_earnings"][2024] - (99e9 + 11.4e9 - 9.4e9 + 3.6e9)) < 1.0

    assert abs(metrics["roe"][2024] - 99 / 57 * 100) < 1e-3
    assert abs(metrics["roa"][2024] - 99 / 364 * 100) < 1e-3
    assert abs(metrics["operating_margin"][2024] - 123 / 400 * 100) < 1e-3
    assert abs(metrics["net_margin"][2024] - 99 / 400 * 100) < 1e-3
    assert abs(metrics["debt_to_equity"][2024] - 106 / 57) < 1e-3
    assert abs(metrics["debt_to_ebitda"][2024] - 106 / (123 + 11.4)) < 1e-3
    assert abs(metrics["interest_coverage"][2024] - 123 / 3.9) < 1e-3
    assert abs(metrics["current_ratio"][2024] - 153 / 176) < 1e-3

    # ogni serie copre tutti gli esercizi analizzati
    assert result["years_analyzed"] == YEARS
    for series in metrics.values():
        assert set(series) == set(YEARS)


def test_punteggio_e_pesi():
    default = calculate_quality_score("ALFA", financials=FINANCIALS)
    assert 0 <= default["quality_score"] <= 100
    assert default["weights"] == {"profitability": 0.40, "consistency": 0.30, "balance_sheet": 0.30}
    assert set(default["category_scores"]) == {"profitability", "consistency", "balance_sheet"}

    # pesi non normalizzati: vengono riportati a somma 1 e spostano il punteggio
    custom = calculate_quality_score(
        "ALFA", weights={"profitability": 6, "consistency": 2, "balance_sheet": 2},
        financials=FINANCIALS,
    )
    assert abs(sum(custom["weights"].values()) - 1.0) < 1e-9
    assert custom["weights"]["profitability"] == 0.6
    assert custom["quality_score"] > default["quality_score"]  # profittabilita' e' la categoria migliore

    # pesi non validi: si torna al default con una nota, senza eccezioni
    invalid = calculate_quality_score(
        "ALFA", weights={"bogus": 1, "profitability": -1}, financials=FINANCIALS
    )
    assert invalid["weights"] == {"profitability": 0.40, "consistency": 0.30, "balance_sheet": 0.30}
    assert invalid["data_quality"]["notes"]


def test_dati_mancanti_non_bloccano_il_calcolo():
    degraded = dict(FINANCIALS)
    degraded["cash_flow"] = pd.DataFrame()  # niente D&A, CapEx, variazione circolante
    degraded["balance_sheet"] = BALANCE_SHEET.drop(
        index=["Total Debt", "Cash And Cash Equivalents"]
    )
    result = calculate_quality_score("ALFA", financials=degraded)

    assert result["quality_score"] is not None
    assert result["metrics"]["debt_to_equity"][2024] is None      # debito non disponibile
    assert result["metrics"]["roic"][2024] is None                # capitale investito non ricostruibile
    assert result["metrics"]["current_ratio"][2024] is not None   # questo si' calcolabile
    assert result["data_quality"]["missing"]
    assert result["data_quality"]["estimated"]
    assert format_report(result)


def test_copertura_del_punteggio():
    """Un 92 costruito su tutto e un 92 costruito su meta' non sono la stessa cosa.

    La ridistribuzione dei pesi tiene in piedi il punteggio quando manca un dato, ma ne
    cambia il significato. La copertura e' la quota di **peso** che ha davvero prodotto
    un valore: pesa le componenti invece di contarle, perche' perdere il ROIC (peso 0.35)
    non equivale a perdere il margine lordo.
    """
    pieno = calculate_quality_score("ALFA", financials=FINANCIALS)
    assert pieno["score_coverage"] == 1.0
    for category in pieno["category_scores"].values():
        assert category["coverage"] == 1.0
        assert category["components_used"] == category["components_total"]

    mutilato = dict(FINANCIALS)
    mutilato["cash_flow"] = pd.DataFrame()
    mutilato["balance_sheet"] = BALANCE_SHEET.drop(
        index=["Total Debt", "Cash And Cash Equivalents",
               "Current Assets", "Current Liabilities"]
    )
    mutilato["income_statement"] = INCOME_STATEMENT.drop(
        index=["Gross Profit", "Operating Income", "Interest Expense"]
    )
    result = calculate_quality_score("ALFA", financials=mutilato)

    # il punteggio esiste ancora, ed e' altissimo: e' esattamente il caso pericoloso
    assert result["quality_score"] > 80
    assert result["score_coverage"] < 0.60

    bilancio = result["category_scores"]["balance_sheet"]
    assert bilancio["score"] is None and bilancio["coverage"] == 0.0
    assert bilancio["components_used"] == 0

    # nella profittabilita' manca solo il ROIC: 6 componenti su 6 diventano 5, ma il
    # peso perso e' 0.35, non 1/6
    redditivita = result["category_scores"]["profitability"]
    assert redditivita["components_used"] == redditivita["components_total"] - 1
    assert abs(redditivita["coverage"] - (1 - 0.35)) < 1e-9

    assert any("Copertura del punteggio" in voce for voce in result["data_quality"]["missing"])
    assert "Copertura: 47%" in format_report(result)


def test_nessun_dato_non_solleva_eccezioni():
    empty = {
        "ticker": "VUOTO", "income_statement": None, "balance_sheet": None,
        "cash_flow": None, "years": [],
    }
    result = calculate_quality_score("VUOTO", financials=empty)
    assert result["quality_score"] is None
    assert result["rating"] == "Non valutabile"
    assert result["error"]
    assert format_report(result)


def test_risultato_serializzabile_in_json():
    result = calculate_quality_score("ALFA", financials=FINANCIALS)
    assert json.loads(json.dumps(result))["ticker"] == "ALFA"


def test_fetch_financials_degrada_senza_crash():
    """Con rete assente o ticker inesistente deve restituire una struttura vuota, non un'eccezione."""
    result = fetch_financials("TICKER-INESISTENTE-XYZ", years=10)
    assert result["ticker"] == "TICKER-INESISTENTE-XYZ"
    assert isinstance(result["years"], list)
    assert "data_quality" in result


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
    print("\nEsempio di report sui bilanci sintetici:\n")
    print(format_report(calculate_quality_score("ALFA", financials=FINANCIALS)))
    sys.exit(1 if failures else 0)
