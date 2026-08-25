"""Test della modalita' buffett: i criteri pubblicati di Berkshire, applicati alla lettera.

Le fonti dei numeri usati qui sono i criteri di acquisizione stampati in ogni annual
report dal 1982, la definizione di Owner Earnings dell'appendice alla lettera 1986, la
metrica sul capitale tangibile della lettera 2007 e la risposta sul tasso di sconto
all'assemblea del 1998.

Il test piu' importante e' ``test_filtro_di_prevedibilita_blocca_il_tasso_basso``:
scontare al 4% un business imprevedibile e' il modo piu' rapido di travisare Buffett,
perche' il tasso basso e' il complemento di una selezione preventiva severa, non un
regalo. Il modello deve rifiutarsi di applicarlo quando la selezione non passa.

Esecuzione::

    python tests/test_buffett.py
    pytest tests/test_buffett.py
"""

from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "backend"))
sys.path.insert(0, BASE)

import synthetic  # noqa: E402
from models import sectors  # noqa: E402
from models.quality_score import (  # noqa: E402
    calculate_owner_earnings,
    calculate_quality_score,
    calculate_return_on_tangible_capital,
    estimate_maintenance_capex,
    extract_fundamentals,
    format_report,
)
from models.valuation import (  # noqa: E402
    BUFFETT_ASSUMPTIONS,
    assess_predictability,
    buffett_scorecard,
    calculate_valuation,
    format_buffett_scorecard,
    format_valuation_report,
)

#: Azienda con immobilizzazioni dichiarate: serve a esercitare il metodo Greenwald,
#: che senza il rapporto immobilizzazioni/ricavi ripiegherebbe sugli ammortamenti.
YEARS = synthetic.YEARS
REVENUE = [200e9, 180e9, 160e9, 140e9, 130e9, 120e9]
CAPEX_TOTAL = [20e9, 18e9, 16e9, 14e9, 13e9, 12e9]

CAPITAL_INTENSIVE = synthetic.make_financials(
    "PPE",
    revenue=REVENUE,
    operating_income=[value * 0.25 for value in REVENUE],
    net_income=[value * 0.18 for value in REVENUE],
    equity=[100e9] * 6,
    debt=[20e9] * 6,
    cash=[15e9] * 6,
    shares=1e9,
)
# immobilizzazioni pari al 40% dei ricavi, e CapEx totale al 10%
CAPITAL_INTENSIVE["balance_sheet"] = synthetic.frame({
    **{row: list(CAPITAL_INTENSIVE["balance_sheet"].loc[row])
       for row in CAPITAL_INTENSIVE["balance_sheet"].index},
    "Net PPE": [value * 0.40 for value in REVENUE],
    "Goodwill": [10e9] * 6,
    "Other Intangible Assets": [5e9] * 6,
})
CAPITAL_INTENSIVE["cash_flow"] = synthetic.frame({
    "Depreciation And Amortization": [value * 0.05 for value in REVENUE],
    "Capital Expenditure": [-value for value in CAPEX_TOTAL],
    "Change In Working Capital": [0.0] * 6,
})


def _fundamentals(financials):
    return extract_fundamentals(financials)


# ---------------------------------------------------------------------------
# CapEx di mantenimento (metodo Greenwald)
# ---------------------------------------------------------------------------


def test_capex_di_mantenimento_separa_la_crescita():
    """CapEx di crescita = immobilizzazioni/ricavi x incremento dei ricavi."""
    fundamentals = _fundamentals(CAPITAL_INTENSIVE)
    maintenance = estimate_maintenance_capex(fundamentals)

    # 2024: ricavi da 180 a 200 (+20), rapporto 0.40 -> crescita 8, totale 20 -> mant. 12
    assert abs(maintenance[2024] - 12e9) < 1e7, maintenance[2024]
    # 2023: ricavi da 160 a 180 (+20) -> stessa crescita 8, totale 18 -> mant. 10
    assert abs(maintenance[2023] - 10e9) < 1e7

    # il mantenimento non puo' mai superare il CapEx totale
    for year, value in maintenance.items():
        if value is not None:
            assert value <= abs(fundamentals[year]["capex"]) + 1.0


def test_owner_earnings_piu_alti_col_capex_di_mantenimento():
    """Separare la crescita alza gli Owner Earnings: il proxy col totale era prudenziale."""
    fundamentals = _fundamentals(CAPITAL_INTENSIVE)
    maintenance = estimate_maintenance_capex(fundamentals)
    fedeli = calculate_owner_earnings(fundamentals, maintenance_capex=maintenance)
    prudenziali = calculate_owner_earnings(fundamentals)
    assert fedeli[2024] > prudenziali[2024]
    # la differenza e' esattamente il CapEx di crescita
    growth_capex = abs(fundamentals[2024]["capex"]) - maintenance[2024]
    assert abs((fedeli[2024] - prudenziali[2024]) - growth_capex) < 1e6


def test_ricavi_in_calo_niente_capex_di_crescita():
    """Se i ricavi scendono non c'e' crescita da finanziare: tutto e' mantenimento."""
    declining = dict(CAPITAL_INTENSIVE)
    declining["income_statement"] = synthetic.frame({
        **{row: list(CAPITAL_INTENSIVE["income_statement"].loc[row])
           for row in CAPITAL_INTENSIVE["income_statement"].index},
        "Total Revenue": [120e9, 130e9, 140e9, 160e9, 180e9, 200e9],   # in calo nel tempo
    })
    fundamentals = _fundamentals(declining)
    maintenance = estimate_maintenance_capex(fundamentals)
    assert abs(maintenance[2024] - abs(fundamentals[2024]["capex"])) < 1.0


# ---------------------------------------------------------------------------
# Rendimento sul capitale tangibile (lettera 2007)
# ---------------------------------------------------------------------------


def test_capitale_tangibile_esclude_avviamento():
    fundamentals = _fundamentals(CAPITAL_INTENSIVE)
    tangible = calculate_return_on_tangible_capital(fundamentals)

    row = fundamentals[2024]
    invested = row["total_debt"] + row["equity"] - row["cash"]
    atteso = row["ebit"] / (invested - 10e9 - 5e9) * 100.0
    assert abs(tangible[2024] - atteso) < 1e-6

    # togliendo l'avviamento il denominatore cala, quindi il rendimento sale
    from models.quality_score import calculate_roic
    assert tangible[2024] > calculate_roic(fundamentals)[2024]


# ---------------------------------------------------------------------------
# Il filtro di prevedibilita'
# ---------------------------------------------------------------------------


def test_filtro_di_prevedibilita_blocca_il_tasso_basso():
    """Senza capacita' di reddito dimostrata il tasso del Treasury non si applica."""
    erratic = synthetic.make_financials(
        "ERRATICA",
        revenue=[100e9, 60e9, 120e9, 40e9, 110e9, 50e9],
        operating_income=[20e9, -5e9, 25e9, -10e9, 22e9, -2e9],
        net_income=[14e9, -4e9, 18e9, -8e9, 15e9, -1e9],
        equity=[50e9] * 6, debt=[30e9] * 6, cash=[10e9] * 6, shares=1e9,
    )
    result = calculate_valuation(
        "ERRATICA", financials=erratic, mode="buffett",
        market_data={"price": 40.0, "shares_outstanding": 1e9, "beta": 1.3,
                     "market_cap": 40e9, "currency": "USD"},
    )
    assert result["predictability"]["passes"] is False
    assert result["predictability"]["failed"]
    assert result["cost_of_capital"]["discount_rate_source"] == "wacc_fallback"
    # il tasso resta quello prudenziale, non scende al 4%
    assert result["cost_of_capital"]["wacc"] > BUFFETT_ASSUMPTIONS.get("risk_free_rate", 0.04)


def test_azienda_prevedibile_ottiene_il_tasso_del_treasury():
    result = calculate_valuation(
        "ALFA", financials=synthetic.ALFA, market_data=synthetic.ALFA_MARKET,
        mode="buffett",
    )
    assert result["predictability"]["passes"] is True
    assert result["cost_of_capital"]["discount_rate_source"] == "treasury"
    assert abs(result["cost_of_capital"]["wacc"] - 0.04) < 1e-9


def test_assess_predictability_sui_casi_limite():
    assert assess_predictability({}, {})["passes"] is False
    stabile = {2020: 10.0, 2021: 11.0, 2022: 12.0, 2023: 13.0, 2024: 14.0}
    assert assess_predictability(stabile, stabile)["passes"] is True
    con_perdita = dict(stabile); con_perdita[2022] = -5.0
    assert assess_predictability(con_perdita, con_perdita)["passes"] is False


# ---------------------------------------------------------------------------
# Le ipotesi buffettiane vanno applicate insieme
# ---------------------------------------------------------------------------


def test_ipotesi_buffett_applicate_come_blocco():
    result = calculate_valuation(
        "ALFA", financials=synthetic.ALFA, market_data=synthetic.ALFA_MARKET,
        mode="buffett",
    )
    assunzioni = result["assumptions"]
    assert assunzioni["terminal_growth"] == 0.0          # nessuna crescita perpetua
    assert assunzioni["target_margin_of_safety"] == 0.50  # margine doppio
    assert assunzioni["max_growth"] == 0.10              # tetto piu' basso
    assert assunzioni["normalization"] == "median5"      # "media annua", non l'ultimo anno


def test_il_tasso_basso_alza_il_valore_ma_il_margine_lo_compensa():
    """Il contrappeso e' il punto: senza, il 4% farebbe sembrare tutto a sconto."""
    standard = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                   market_data=synthetic.ALFA_MARKET)
    buffett = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                  market_data=synthetic.ALFA_MARKET, mode="buffett")
    assert buffett["fair_value"]["point"] > standard["fair_value"]["point"]
    assert buffett["buy_below"] < buffett["fair_value"]["point"] * 0.55


def test_owner_earnings_yield_contro_il_titolo_di_stato():
    result = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                 market_data=synthetic.ALFA_MARKET, mode="buffett")
    oe_yield = result["owner_earnings_yield"]
    assert oe_yield is not None
    # coerenza interna: rendimento = 1 / multiplo
    assert abs(oe_yield["yield"] - 1.0 / oe_yield["multiple"]) < 1e-3
    assert abs(oe_yield["spread"] - (oe_yield["yield"] - oe_yield["risk_free_rate"])) < 1e-9


# ---------------------------------------------------------------------------
# Profilo di qualita' e scorecard
# ---------------------------------------------------------------------------


def test_profilo_buffett_e_piu_severo():
    standard = calculate_quality_score("ALFA", financials=synthetic.ALFA)
    buffett = calculate_quality_score("ALFA", financials=synthetic.ALFA, mode="buffett")
    assert buffett["profile"] == sectors.BUFFETT
    assert buffett["mode"] == "buffett"
    assert buffett["quality_score"] < standard["quality_score"]

    # niente Debt/EBITDA: Buffett lo rifiuta apertamente
    componenti = buffett["category_scores"]["balance_sheet"]["components"]
    assert "debt_to_ebitda" not in componenti
    assert "debt_to_owner_earnings" in componenti
    # e la metrica principale e' il capitale tangibile
    assert "return_on_tangible_capital" in buffett["category_scores"]["profitability"]["components"]
    assert format_report(buffett)


def test_modalita_ignorata_sui_finanziari():
    """I criteri di Berkshire parlano di aziende operative, non di banche."""
    result = calculate_quality_score("BANCA", financials=synthetic.make_bank(), mode="buffett")
    assert result["sector"] == sectors.BANK
    assert result["profile"] == sectors.BANK
    assert result["mode"] == "standard"
    assert any("ignorata" in note for note in result["data_quality"]["notes"])


def test_scorecard_completa_e_onesta():
    quality = calculate_quality_score("ALFA", financials=synthetic.ALFA, mode="buffett")
    valuation = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                    market_data=synthetic.ALFA_MARKET, mode="buffett")
    scorecard = buffett_scorecard(quality, valuation)

    assert scorecard["total"] == len(scorecard["checks"])
    assert 0 <= scorecard["passed"] <= scorecard["total"]
    # i criteri non misurabili devono essere dichiarati, non nascosti
    assert len(scorecard["manual_judgment"]) == 3
    assert "cerchio di competenza" in " ".join(scorecard["manual_judgment"].values())
    assert format_buffett_scorecard(scorecard)
    assert format_valuation_report(valuation)


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
