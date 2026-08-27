"""Test dei profili utility regolata ed esplorazione e produzione.

I due settori hanno lo stesso problema di fondo — le metriche standard misurano qualcosa
che non e' quello che sembra — con due cause opposte:

* in una **utility** il rendimento non lo decide il mercato ma il regolatore, quindi un
  ROIC basso non e' debolezza competitiva e uno alto non sarebbe un moat;
* in un **E&P** l'utile dell'anno misura in buona parte dove stava il prezzo del greggio,
  non come e' stata gestita l'azienda, e l'attivo si consuma mentre lo si sfrutta.

Il test piu' importante di questo file e' ``test_greenwald_non_si_applica_ai_due_profili``:
verifica che il CapEx di mantenimento non venga stimato con un metodo che su questi
bilanci restituisce zero, perche' un flusso sovrastimato produce un fair value
sovrastimato — l'errore che costa.

Esecuzione::

    python tests/test_utility_energy.py
    pytest tests/test_utility_energy.py
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
from models.quality_score import (  # noqa: E402
    _DataQuality,
    calculate_quality_score,
    estimate_maintenance_capex,
    extract_fundamentals,
    format_report,
)
from models.valuation import calculate_valuation, format_valuation_report  # noqa: E402

UTILITY = synthetic.make_utility()
ENERGY = synthetic.make_energy()

UTILITY_MARKET = {"price": 42.0, "shares_outstanding": 700e6, "beta": 0.55,
                  "market_cap": 29400e6, "currency": "USD"}
ENERGY_MARKET = {"price": 18.0, "shares_outstanding": 900e6, "beta": 1.35,
                 "market_cap": 16200e6, "currency": "USD"}


# ---------------------------------------------------------------------------
# Rilevamento: due voci esclusive
# ---------------------------------------------------------------------------


def test_rilevamento_da_voci_esclusive():
    """Attivi regolatori e spesa di esplorazione non esistono altrove."""
    quality = sectors._DataQuality()
    assert sectors.detect_sector(UTILITY, quality) == sectors.UTILITY
    assert any("utility regolata" in nota for nota in quality.notes)

    quality = sectors._DataQuality()
    assert sectors.detect_sector(ENERGY, quality) == sectors.ENERGY
    assert any("esplorazione e produzione" in nota for nota in quality.notes)

    # e non devono rubare le aziende operative
    assert sectors.detect_sector(synthetic.ALFA) == sectors.INDUSTRIAL


def test_utility_riconosciuta_prima_del_reit():
    """Entrambe hanno molte immobilizzazioni: il marcatore esplicito deve vincere.

    Senza questa precedenza una utility con ammortamenti alti finirebbe nel profilo REIT,
    dove si calcolerebbero FFO e payout su un'azienda che non ha immobili da locare.
    """
    # la utility di prova ha immobilizzazioni per il 78% dell'attivo: territorio REIT
    fondamentali = extract_fundamentals(UTILITY, _DataQuality())
    quota_ppe = fondamentali[2024]["net_ppe"] / fondamentali[2024]["total_assets"]
    assert quota_ppe > 0.70, "il fixture deve stare nella zona di sovrapposizione col REIT"
    assert sectors.detect_sector(UTILITY) == sectors.UTILITY

    # togliendo la voce regolatoria il rilevamento cade sull'euristica, e va dichiarato
    senza_marcatore = synthetic.drop_row(UTILITY, "balance_sheet", "Regulatory Assets")
    quality = sectors._DataQuality()
    rilevato = sectors.detect_sector(senza_marcatore, quality)
    assert rilevato in (sectors.REIT, sectors.INDUSTRIAL)
    if rilevato == sectors.REIT:
        assert any("via indiretta" in nota for nota in quality.notes)


def test_profili_forzabili_a_mano():
    for settore in ("utility", "energy"):
        result = calculate_quality_score("X", financials=synthetic.ALFA, sector=settore)
        assert result["sector"] == settore
        assert result["quality_score"] is not None


# ---------------------------------------------------------------------------
# Utility: il rendimento e' regolato, la rate base e' il motore
# ---------------------------------------------------------------------------


def test_metriche_utility_contro_il_calcolo_a_mano():
    """Fixture: ROE al 9.8% (il rendimento ammesso), debito al 55% del capitale."""
    result = calculate_quality_score("RETE", financials=UTILITY)
    metrics, year = result["metrics"], 2024

    assert abs(metrics["roe"][year] - 9.8) < 0.05
    assert abs(metrics["debt_to_capital"][year] - 55.0) < 0.1
    # FFO = utile netto + ammortamenti, la definizione delle agenzie di rating
    atteso_ffo = metrics["net_income"][year] + 22000e6 * 0.032
    assert abs(metrics["ffo"][year] - atteso_ffo) < 1e4
    # CapEx sopra gli ammortamenti: la rate base cresce
    assert abs(metrics["capex_to_depreciation"][year] - 1.75) < 0.01
    assert metrics["capex_to_depreciation"][year] > 1.0


def test_niente_metriche_industriali_sulla_utility():
    """ROIC e Owner Earnings non compaiono: il rendimento non e' un vantaggio competitivo."""
    result = calculate_quality_score("RETE", financials=UTILITY)
    for assente in ("roic", "owner_earnings", "debt_to_equity", "current_ratio"):
        assert assente not in result["metrics"], f"{assente} non va calcolata su una utility"
    for presente in ("ffo_to_debt", "return_on_rate_base", "debt_to_capital", "rate_base"):
        assert presente in result["metrics"], f"{presente} manca nel profilo utility"


def test_utility_dichiara_cio_che_non_puo_sapere():
    """Allowed ROE e rate base ufficiale vivono nei procedimenti tariffari."""
    result = calculate_quality_score("RETE", financials=UTILITY)
    mancanti = " ".join(result["data_quality"]["missing"])
    assert "allowed ROE" in mancanti
    assert "proxy della rate base" in mancanti


def test_la_stabilita_e_il_punto_di_una_utility():
    """Una utility instabile va punita piu' di un industriale instabile.

    Le soglie di consistenza sono piu' severe (CV 0.35 -> 0 punti contro 0.60), perche' la
    stabilita' del rendimento e' la ragione per cui si compra una utility.
    """
    stabile = calculate_quality_score("RETE", financials=UTILITY)
    componenti = stabile["category_scores"]["consistency"]["components"]
    assert componenti["roe_stability"]["score"] > 80

    scala = componenti["roe_stability"]["scale"]
    assert "0.35" in scala, "la soglia deve essere quella severa del profilo utility"


# ---------------------------------------------------------------------------
# E&P: il prezzo domina l'utile, la cassa resta
# ---------------------------------------------------------------------------


def test_metriche_energy_contro_il_calcolo_a_mano():
    result = calculate_quality_score("PETROLIO", financials=ENERGY)
    metrics, year = result["metrics"], 2024

    # EBITDAX = EBITDA + esplorazione: neutralizza la scelta contabile fra
    # successful efforts e full cost
    assert metrics["ebitdax"][year] > metrics["net_income"][year]
    assert abs(metrics["exploration_intensity"][year] - 3.5) < 0.05
    assert abs(metrics["ebitdax_margin"][year] - 70.0) < 0.5
    assert abs(metrics["debt_to_ebitdax"][year] - 1.0) < 0.02


def test_il_fondo_di_ciclo_azzera_l_utile_ma_non_la_cassa():
    """E' il fenomeno per cui il profilo esiste.

    Nel 2020 il fixture ha il prezzo al 45%: l'utile netto va in perdita mentre il flusso
    di cassa operativo resta ampiamente positivo. Un modello che guarda l'utile vede
    un'azienda in crisi; uno che guarda la cassa vede un ciclo.
    """
    result = calculate_quality_score("PETROLIO", financials=ENERGY)
    metrics = result["metrics"]

    assert metrics["net_income"][2020] < 0, "il fondo di ciclo deve essere in perdita"
    assert metrics["operating_cash_flow"][2020] > 0, "ma la cassa resta positiva"
    assert metrics["ebitdax"][2020] > 0

    # e il punteggio non deve crollare per un solo anno negativo su sei
    assert result["quality_score"] > 50


def test_energy_dichiara_che_non_vede_le_riserve():
    """Senza riserve e PV-10 il profilo misura la cassa, non il valore dell'attivo."""
    result = calculate_quality_score("PETROLIO", financials=ENERGY)
    mancanti = " ".join(result["data_quality"]["missing"])
    assert "Riserve provate" in mancanti
    assert "PV-10" in mancanti


# ---------------------------------------------------------------------------
# Il CapEx di mantenimento: dove Greenwald si rompe
# ---------------------------------------------------------------------------


def test_greenwald_non_si_applica_ai_due_profili():
    """Su questi bilanci il metodo Greenwald sottostima o azzera il mantenimento.

    Le immobilizzazioni valgono 2,75 volte i ricavi in una utility e 1,89 in un E&P,
    contro lo 0,3-1,0 di un industriale: qualunque crescita dei ricavi assorbe sulla carta
    piu' CapEx di quanto l'azienda ne spenda. Il mantenimento risulta troppo basso, gli
    Owner Earnings escono gonfiati e il fair value con loro.
    """
    for financials, atteso_zero in ((UTILITY, False), (ENERGY, True)):
        quality = _DataQuality()
        fondamentali = extract_fundamentals(financials, quality)
        greenwald = estimate_maintenance_capex(fondamentali, quality)
        ammortamenti = abs(fondamentali[2024]["d_and_a"])
        capex = abs(fondamentali[2024]["capex"])

        assert greenwald[2024] < ammortamenti, (
            "Greenwald deve risultare sotto gli ammortamenti: e' il difetto documentato"
        )
        if atteso_zero:
            assert greenwald[2024] == 0.0, "su un E&P il metodo azzera il mantenimento"
        assert capex > 0

    # La convenzione per profilo e' dichiarata in un posto solo
    assert sectors.MAINTENANCE_CAPEX_RULE[sectors.UTILITY] == "depreciation"
    assert sectors.MAINTENANCE_CAPEX_RULE[sectors.ENERGY] == "total"
    assert sectors.MAINTENANCE_CAPEX_RULE[sectors.INDUSTRIAL] == "greenwald"


def test_la_valutazione_usa_la_convenzione_del_profilo():
    """Utility: mantenimento = ammortamenti. E&P: mantenimento = CapEx totale."""
    utility = calculate_valuation("RETE", financials=UTILITY, market_data=UTILITY_MARKET)
    assert utility["sector"] == sectors.UTILITY
    assert any("pari agli ammortamenti" in voce
               for voce in utility["data_quality"]["estimated"])

    energy = calculate_valuation("PETROLIO", financials=ENERGY, market_data=ENERGY_MARKET)
    assert energy["sector"] == sectors.ENERGY
    assert any("pari al CapEx totale" in voce
               for voce in energy["data_quality"]["estimated"])

    # e gli Owner Earnings che ne risultano non superano l'utile piu' gli ammortamenti
    for result, financials in ((utility, UTILITY), (energy, ENERGY)):
        fondamentali = extract_fundamentals(financials, _DataQuality())
        tetto = fondamentali[2024]["net_income"] + abs(fondamentali[2024]["d_and_a"])
        assert result["inputs"]["base_owner_earnings"] <= tetto * 1.05


def test_valutazioni_complete_e_serializzabili():
    for ticker, financials, market in (
        ("RETE", UTILITY, UTILITY_MARKET), ("PETROLIO", ENERGY, ENERGY_MARKET)
    ):
        quality = calculate_quality_score(ticker, financials=financials)
        valuation = calculate_valuation(ticker, financials=financials, market_data=market)
        assert valuation["error"] is None
        assert valuation["fair_value"]["point"] is not None
        assert format_report(quality)
        assert format_valuation_report(valuation)
        json.dumps(quality, default=str)
        json.dumps(valuation, default=str)


def test_stesso_bilancio_metri_diversi():
    """Il metro industriale su questi bilanci produce un giudizio diverso."""
    for financials in (UTILITY, ENERGY):
        corretto = calculate_quality_score("X", financials=financials)
        industriale = calculate_quality_score("X", financials=financials, sector="industrial")
        assert corretto["quality_score"] != industriale["quality_score"]
        # il profilo industriale produce comunque un numero: e' questo il pericolo
        assert industriale["quality_score"] is not None
        assert "roic" in industriale["metrics"]


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
