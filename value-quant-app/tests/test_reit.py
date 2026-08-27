"""Test del profilo REIT: l'ammortamento degli immobili non e' un costo economico.

Il test centrale e' ``test_utile_netto_fa_sembrare_mediocre_un_reit_sano``: dimostra che
il profilo industriale, applicato a un REIT, non sbaglia il calcolo — restituisce numeri
plausibili e privi di significato, che e' il modo peggiore di sbagliare.

Esecuzione::

    python tests/test_reit.py
    pytest tests/test_reit.py
"""

from __future__ import annotations

import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "backend"))
sys.path.insert(0, BASE)

import synthetic  # noqa: E402
from models import sectors  # noqa: E402
from models.quality_score import calculate_quality_score, format_report  # noqa: E402
from models.valuation import calculate_valuation, format_valuation_report  # noqa: E402

REIT = synthetic.make_reit()
REIT_MARKET = {"price": 28.0, "shares_outstanding": 300e6, "beta": 0.9,
               "market_cap": 8400e6, "currency": "USD"}


# ---------------------------------------------------------------------------
# Rilevamento
# ---------------------------------------------------------------------------


def test_rilevamento_da_voce_immobiliare_esplicita():
    quality = sectors._DataQuality()
    assert sectors.detect_sector(REIT, quality) == sectors.REIT
    assert any("REIT/immobiliare" in nota for nota in quality.notes)
    # e non deve rubare le aziende operative
    assert sectors.detect_sector(synthetic.ALFA) == sectors.INDUSTRIAL


def test_rilevamento_euristico_richiede_entrambe_le_condizioni():
    """Senza voce immobiliare servono immobilizzazioni **e** ammortamenti alti.

    La sola intensita' di capitale descrive anche utility, telecom e industria pesante:
    prenderle per REIT sarebbe un falso positivo peggiore del problema che si risolve.
    """
    senza_voce = synthetic.drop_row(REIT, "balance_sheet", "Real Estate")
    assert sectors.detect_sector(senza_voce) == sectors.REIT, (
        "immobilizzazioni all'86% e ammortamenti al 30% dei ricavi: e' un REIT"
    )

    # utility-like: molte immobilizzazioni ma ammortamenti ordinari -> industriale
    utility = synthetic.make_reit("UTILITY", depreciation_share=0.08)
    utility = synthetic.drop_row(utility, "balance_sheet", "Real Estate")
    assert sectors.detect_sector(utility) == sectors.INDUSTRIAL

    # e il ripiego euristico va dichiarato, perche' e' quello che puo' sbagliare
    quality = sectors._DataQuality()
    sectors.detect_sector(senza_voce, quality)
    assert any("via indiretta" in nota for nota in quality.notes)


def test_profilo_forzabile_a_mano():
    forzato = calculate_quality_score("ALFA", financials=synthetic.ALFA, sector="reit")
    assert forzato["sector"] == sectors.REIT
    assert forzato["sector_label"] == "REIT / immobiliare"


# ---------------------------------------------------------------------------
# Le formule del settore
# ---------------------------------------------------------------------------


def test_ffo_e_affo_contro_il_calcolo_a_mano():
    """FFO = utile netto + ammortamenti; AFFO = FFO - CapEx.

    Fixture: ricavi 2000, utile netto 200, ammortamenti 600, CapEx 160.
    """
    result = calculate_quality_score("IMMOBILE", financials=REIT)
    metrics, year = result["metrics"], 2024

    assert abs(metrics["net_income"][year] - 200e6) < 1e3
    assert abs(metrics["ffo"][year] - (200e6 + 600e6)) < 1e3
    assert abs(metrics["affo"][year] - (800e6 - 160e6)) < 1e3
    # le serie nel risultato sono arrotondate a quattro decimali
    assert abs(metrics["ffo_per_share"][year] - 800e6 / 300e6) < 1e-3
    assert abs(metrics["affo_per_share"][year] - 640e6 / 300e6) < 1e-3
    assert abs(metrics["ffo_margin"][year] - 40.0) < 0.01
    assert abs(metrics["affo_margin"][year] - 32.0) < 0.01
    assert abs(metrics["ffo_payout"][year] - 75.0) < 0.1
    assert abs(metrics["debt_to_assets"][year] - 42.0) < 0.1


def test_affo_non_usa_il_metodo_greenwald():
    """Su un REIT il metodo Greenwald darebbe CapEx di mantenimento zero.

    Le immobilizzazioni valgono 6-7 volte i ricavi, quindi qualunque crescita dei ricavi
    assorbe piu' CapEx di quanto l'azienda ne spenda: il mantenimento risulta zero e gli
    AFFO coinciderebbero con gli FFO, dichiarando coperto un dividendo che potrebbe non
    esserlo. Il profilo sottrae il CapEx totale e lo dichiara.
    """
    result = calculate_quality_score("IMMOBILE", financials=REIT)
    metrics = result["metrics"]
    assert metrics["affo"][2024] < metrics["ffo"][2024], "gli AFFO non devono coincidere con gli FFO"
    assert any("Greenwald" in voce for voce in result["data_quality"]["estimated"])


def test_utile_netto_fa_sembrare_mediocre_un_reit_sano():
    """Il punto del profilo: col metro industriale lo stesso bilancio cambia giudizio."""
    corretto = calculate_quality_score("IMMOBILE", financials=REIT)
    industriale = calculate_quality_score("IMMOBILE", financials=REIT, sector="industrial")

    # il margine netto industriale e' il 10%: sembra una societa' appena redditizia
    assert abs(industriale["metrics"]["net_margin"][2024] - 10.0) < 0.5
    # mentre il margine sugli FFO, che e' la misura vera, e' il 40%
    assert abs(corretto["metrics"]["ffo_margin"][2024] - 40.0) < 0.5
    assert corretto["quality_score"] != industriale["quality_score"]
    assert corretto["quality_score"] is not None


def test_niente_metriche_fuorvianti_sul_profilo_reit():
    """ROIC e Owner Earnings non devono comparire: sul profilo REIT non hanno senso."""
    result = calculate_quality_score("IMMOBILE", financials=REIT)
    for assente in ("roic", "owner_earnings", "owner_earnings_margin", "current_ratio"):
        assert assente not in result["metrics"], f"{assente} non va calcolata su un REIT"
    for presente in ("ffo", "affo", "ffo_per_share", "ffo_payout", "debt_to_assets"):
        assert presente in result["metrics"], f"{presente} manca nel profilo REIT"


def test_limiti_dichiarati():
    """Le due approssimazioni del profilo vanno dette, non nascoste."""
    result = calculate_quality_score("IMMOBILE", financials=REIT)
    stimati = " ".join(result["data_quality"]["estimated"])
    assert "ammortamento totale" in stimati, "la quota immobiliare non e' separabile"
    mancanti = " ".join(result["data_quality"]["missing"])
    assert "Plusvalenze" in mancanti, "l'assenza delle plusvalenze da cessione va dichiarata"


def test_ffo_non_calcolabili_senza_ammortamenti():
    senza_da = dict(REIT)
    senza_da["cash_flow"] = REIT["cash_flow"].drop(index=["Depreciation And Amortization"])
    result = calculate_quality_score("IMMOBILE", financials=senza_da)
    assert result["metrics"]["ffo"][2024] is None
    assert result["score_coverage"] < 1.0
    assert format_report(result)


# ---------------------------------------------------------------------------
# Valutazione
# ---------------------------------------------------------------------------


def test_valutazione_sconta_gli_affo():
    result = calculate_valuation("IMMOBILE", financials=REIT, market_data=REIT_MARKET)
    assert result["sector"] == sectors.REIT
    assert result["error"] is None
    assert result["methods"]["dcf_owner_earnings"]["label"] == "DCF AFFO"

    # gli AFFO scontati sono quelli del profilo, non gli Owner Earnings industriali
    industriale = calculate_valuation("IMMOBILE", financials=REIT,
                                      market_data=REIT_MARKET, sector="industrial")
    assert (result["inputs"]["base_owner_earnings"]
            != industriale["inputs"]["base_owner_earnings"])

    assert result["fair_value"]["point"] is not None
    assert format_valuation_report(result)
    json.dumps(result, default=str)


def test_epv_resta_a_schermo_ma_fuori_dalla_sintesi():
    """L'EPV assume crescita zero: su canoni che seguono l'inflazione e' un errore."""
    result = calculate_valuation("IMMOBILE", financials=REIT, market_data=REIT_MARKET)
    epv = result["methods"]["epv"]
    assert epv["value_per_share"] is not None, "resta visibile come confronto"
    assert epv["aggregated"] is False, "ma non deve entrare nel fair value"
    assert "dcf_owner_earnings" in result["fair_value"]["weights"]
    assert "epv" not in result["fair_value"]["weights"]


def test_modalita_buffett_ignorata_sui_reit():
    result = calculate_valuation("IMMOBILE", financials=REIT,
                                 market_data=REIT_MARKET, mode="buffett")
    assert any("ignorata" in nota for nota in result["data_quality"]["notes"])
    assert result["sector"] == sectors.REIT

    punteggio = calculate_quality_score("IMMOBILE", financials=REIT, mode="buffett")
    assert punteggio["profile"] == sectors.REIT


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
        print("\nEsempio: REIT\n")
        print(format_report(calculate_quality_score("IMMOBILE", financials=REIT)))
    sys.exit(1 if failures else 0)
