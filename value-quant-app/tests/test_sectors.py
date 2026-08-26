"""Test dei profili di settore: banche e assicurazioni non vanno misurate col metro industriale.

Il test piu' importante di questo file e' ``test_niente_metriche_industriali_sui_finanziari``:
verifica che su una banca il modello **non produca** ROIC e Owner Earnings. Un numero
mancante avverte chi legge; un numero plausibile ma privo di significato no — ed e'
esattamente cosi' che un modello industriale applicato a una banca inganna.

Esecuzione::

    python tests/test_sectors.py
    pytest tests/test_sectors.py
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
from models.valuation import (  # noqa: E402
    calculate_valuation,
    format_valuation_report,
    justified_price_to_book,
    residual_income_value_per_share,
    reverse_residual_income,
)

BANK = synthetic.make_bank()
INSURER = synthetic.make_insurer()

BANK_MARKET = {"price": 95.0, "shares_outstanding": 3e9, "beta": 1.1,
               "market_cap": 285e9, "currency": "USD"}
INSURER_MARKET = {"price": 170.0, "shares_outstanding": 400e6, "beta": 0.8,
                  "market_cap": 68e9, "currency": "USD"}


# ---------------------------------------------------------------------------
# Rilevamento del settore
# ---------------------------------------------------------------------------


def test_rilevamento_dalla_struttura_del_bilancio():
    assert sectors.detect_sector(BANK) == sectors.BANK
    assert sectors.detect_sector(INSURER) == sectors.INSURANCE
    assert sectors.detect_sector(synthetic.ALFA) == sectors.INDUSTRIAL
    # in assenza di marcatori si assume un'azienda operativa, non si tira a indovinare
    assert sectors.detect_sector({"income_statement": None, "balance_sheet": None}) == sectors.INDUSTRIAL


def test_settore_forzabile_a_mano():
    forced = calculate_quality_score("BANCA", financials=BANK, sector="industrial")
    assert forced["sector"] == "industrial"
    sconosciuto = calculate_quality_score("ALFA", financials=synthetic.ALFA, sector="banca-italiana")
    assert sconosciuto["sector"] == "industrial"
    assert any("sconosciuto" in note for note in sconosciuto["data_quality"]["notes"])


def test_marcatore_presente_ma_non_materiale_non_fa_una_banca():
    """Il caso che ha motivato le soglie: Alphabet classificata come banca.

    yfinance espone "Net Interest Income" anche per un industriale con la tesoreria
    piena. Sulla sola presenza della voce il modello sceglieva il profilo bancario e
    smetteva di calcolare ROIC e Owner Earnings — cioe' proprio le metriche che
    servono per quell'azienda.
    """
    tech = synthetic.make_cash_rich_tech()
    assert sectors.detect_sector(tech) == sectors.INDUSTRIAL

    result = calculate_quality_score("TECH", financials=tech)
    assert result["sector"] == sectors.INDUSTRIAL
    assert result["metrics"]["roic"][2024] is not None
    assert "owner_earnings" in result["metrics"]
    # il marcatore scartato viene comunque dichiarato: e' la traccia della decisione
    assert any("Margine di interesse presente" in nota
               for nota in result["data_quality"]["notes"])


def test_entrambe_le_soglie_bancarie_devono_scattare_dal_peso():
    """Depositi al 10% dell'attivo e margine di interesse al 10% dei ricavi: non e' banca."""
    banca = synthetic.make_bank()
    attivo = list(banca["balance_sheet"].loc["Total Assets"])
    ricavi = list(banca["income_statement"].loc["Total Revenue"])

    marginale = synthetic.add_row(banca, "balance_sheet", "Total Deposits",
                                  [v * 0.10 for v in attivo])
    marginale = synthetic.add_row(marginale, "income_statement", "Net Interest Income",
                                  [v * 0.10 for v in ricavi])
    assert sectors.detect_sector(marginale) == sectors.INDUSTRIAL

    # basta superare una delle due soglie perche' il profilo cambi
    depositi_materiali = synthetic.add_row(marginale, "balance_sheet", "Total Deposits",
                                           [v * 0.25 for v in attivo])
    assert sectors.detect_sector(depositi_materiali) == sectors.BANK
    interessi_materiali = synthetic.add_row(marginale, "income_statement",
                                            "Net Interest Income",
                                            [v * 0.35 for v in ricavi])
    assert sectors.detect_sector(interessi_materiali) == sectors.BANK


def test_holding_diversificata_resta_nel_profilo_assicurativo():
    """Premi al 23% dei ricavi: il caso Berkshire, che tara la soglia sui premi."""
    holding = synthetic.make_diversified_holding()
    assert sectors.detect_sector(holding) == sectors.INSURANCE

    result = calculate_quality_score("HOLDING", financials=holding)
    assert result["sector"] == sectors.INSURANCE
    # ed e' il punto: nel profilo industriale questa metrica non esisterebbe
    assert result["consistency"]["book_value_per_share"]["growth_years_pct"] is not None


def test_conglomerato_sceglie_il_marcatore_piu_pesante():
    mista = synthetic.make_conglomerate()
    quality = sectors._DataQuality()
    assert sectors.detect_sector(mista, quality) == sectors.BANK
    # la meta' che resta fuori dalle metriche va dichiarata, non nascosta
    assert any("entrambi materiali" in nota for nota in quality.notes)


def test_ripiego_sulla_presenza_solo_per_le_voci_inequivocabili():
    """Senza l'aggregato di riferimento la materialita' non e' verificabile.

    Depositi e premi bastano da soli (nessun industriale li espone), il margine di
    interesse no: e' esattamente il marcatore che genera falsi positivi.
    """
    cieca = synthetic.drop_row(
        synthetic.drop_row(synthetic.make_bank(), "balance_sheet", "Total Assets"),
        "income_statement", "Total Revenue",
    )
    quality = sectors._DataQuality()
    assert sectors.detect_sector(cieca, quality) == sectors.BANK
    assert any("senza verifica del peso" in voce for voce in quality.estimated)

    solo_interessi = synthetic.drop_row(
        synthetic.make_cash_rich_tech(), "income_statement", "Total Revenue"
    )
    assert sectors.detect_sector(solo_interessi) == sectors.INDUSTRIAL


def test_patrimonio_su_attivo_tarato_sulla_leva_non_ponderata():
    """8.6% di patrimonio sull'attivo e' una banca solida, non una mediocre.

    La soglia precedente (5% -> 0, 12% -> 100) era tarata come se il rapporto fosse un
    CET1, che pero' divide per gli attivi **ponderati per il rischio** — circa la meta'
    del totale. JPMorgan, con CET1 ~15%, prendeva 51 su 100. La scala corretta e'
    5% -> 0, 9% -> 100.
    """
    solida = calculate_quality_score("SOLIDA", financials=synthetic.make_bank(equity_share=0.086))
    componente = solida["category_scores"]["balance_sheet"]["components"]["equity_to_assets"]
    assert abs(componente["value"] - 8.6) < 0.1
    assert abs(componente["score"] - (8.6 - 5.0) / (9.0 - 5.0) * 100) < 0.5
    assert componente["score"] > 85

    # sotto il minimo di vigilanza il punteggio resta zero: la scala non e' stata ammorbidita
    fragile = calculate_quality_score("FRAGILE", financials=synthetic.make_bank(equity_share=0.045))
    assert fragile["category_scores"]["balance_sheet"]["components"]["equity_to_assets"]["score"] == 0.0


# ---------------------------------------------------------------------------
# Il punto centrale: metriche diverse, non soglie diverse
# ---------------------------------------------------------------------------


def test_niente_metriche_industriali_sui_finanziari():
    """Su una banca ROIC e Owner Earnings non devono esistere, non essere sbagliati."""
    result = calculate_quality_score("BANCA", financials=BANK)
    assert result["sector"] == sectors.BANK

    for absent in ("roic", "owner_earnings", "owner_earnings_margin",
                   "debt_to_ebitda", "interest_coverage", "current_ratio"):
        assert absent not in result["metrics"], f"{absent} non deve essere calcolata su una banca"

    for present in ("rotce", "net_interest_margin", "efficiency_ratio",
                    "loan_to_deposit", "cost_of_risk", "equity_to_assets"):
        assert present in result["metrics"], f"{present} manca nel profilo bancario"

    # e le approssimazioni industriali non devono nemmeno essere tentate
    stimati = " ".join(result["data_quality"]["estimated"])
    assert "CapEx di mantenimento" not in stimati
    assert "EBITDA stimato" not in stimati
    # mentre il limite vero della fonte dati va dichiarato
    assert any("CET1" in voce for voce in result["data_quality"]["missing"])


def test_metriche_bancarie_calcolate_correttamente():
    result = calculate_quality_score("BANCA", financials=BANK)
    metrics, year = result["metrics"], max(result["years_analyzed"])

    # attivo 3000, patrimonio 300, utile 45, avviamento 50 + immateriali 5
    assert abs(metrics["roe"][year] - 15.0) < 0.1          # 45 / 300
    assert abs(metrics["roa"][year] - 1.5) < 0.01          # 45 / 3000
    assert abs(metrics["rotce"][year] - 45 / 245 * 100) < 0.1
    assert abs(metrics["equity_to_assets"][year] - 10.0) < 0.1
    assert abs(metrics["net_interest_margin"][year] - 3.0) < 0.05   # 90 / 3000
    assert abs(metrics["efficiency_ratio"][year] - 56.25) < 0.1
    assert abs(metrics["loan_to_deposit"][year] - 0.433 / 0.733) < 0.01
    assert 0 <= result["quality_score"] <= 100
    # bilancio bancario completo: tutte le componenti del profilo devono contribuire
    assert result["score_coverage"] == 1.0
    for categoria in result["category_scores"].values():
        assert categoria["components_used"] == categoria["components_total"]
    assert format_report(result)


def test_profilo_assicurativo():
    result = calculate_quality_score("ASSICURA", financials=INSURER)
    assert result["sector"] == sectors.INSURANCE
    metrics, year = result["metrics"], max(result["years_analyzed"])

    # combined ratio costruito all'86.7%: sotto 100 significa utile tecnico
    assert abs(metrics["combined_ratio"][year] - 86.7) < 0.5
    assert "book_value_per_share" in metrics
    assert "roic" not in metrics
    assert 0 <= result["quality_score"] <= 100
    # la crescita del patrimonio per azione e' il perno della consistenza
    assert result["consistency"]["book_value_per_share"]["growth_years_pct"] is not None


def test_stesso_bilancio_metri_diversi():
    """La stessa banca letta col profilo industriale da' un risultato diverso e ingannevole."""
    corretto = calculate_quality_score("BANCA", financials=BANK)
    sbagliato = calculate_quality_score("BANCA", financials=BANK, sector="industrial")
    assert corretto["quality_score"] != sbagliato["quality_score"]
    # il profilo industriale produce comunque un numero: e' questo il pericolo
    assert sbagliato["quality_score"] is not None
    assert "roic" in sbagliato["metrics"]


# ---------------------------------------------------------------------------
# Valutazione: residual income invece del DCF
# ---------------------------------------------------------------------------


def test_residual_income_senza_rendimenti_in_eccesso_vale_il_patrimonio():
    """Se ROE = costo dell'equity il valore deve essere esattamente il book value."""
    outcome = residual_income_value_per_share(
        100.0, return_on_equity=0.10, cost_of_equity=0.10, growth=0.03,
        projection_years=10, fade_years=5,
    )
    assert abs(outcome["value_per_share"] - 100.0) < 1e-9
    assert abs(outcome["pv_excess_returns"]) < 1e-9

    # con ROE sopra il costo del capitale il valore deve superare il patrimonio
    migliore = residual_income_value_per_share(
        100.0, return_on_equity=0.16, cost_of_equity=0.10, growth=0.03,
        projection_years=10, fade_years=5,
    )
    assert migliore["value_per_share"] > 100.0
    # e sotto, deve valere meno
    peggiore = residual_income_value_per_share(
        100.0, return_on_equity=0.05, cost_of_equity=0.10, growth=0.03,
        projection_years=10, fade_years=5,
    )
    assert peggiore["value_per_share"] < 100.0


def test_residual_income_rifiuta_input_impossibili():
    assert residual_income_value_per_share(100.0, return_on_equity=0.12,
                                           cost_of_equity=0.05, growth=0.06)["error"]
    assert residual_income_value_per_share(-10.0, return_on_equity=0.12,
                                           cost_of_equity=0.10, growth=0.03)["error"]
    assert residual_income_value_per_share(None, return_on_equity=0.12,
                                           cost_of_equity=0.10, growth=0.03)["error"]


def test_price_to_book_giustificato():
    """(ROE - g) / (Ke - g): con ROE = Ke il multiplo giusto e' esattamente 1."""
    pari = justified_price_to_book(50.0, return_on_equity=0.10,
                                   cost_of_equity=0.10, growth=0.03)
    assert abs(pari["justified_multiple"] - 1.0) < 1e-12
    assert abs(pari["value_per_share"] - 50.0) < 1e-12

    forte = justified_price_to_book(50.0, return_on_equity=0.15,
                                    cost_of_equity=0.10, growth=0.03)
    assert abs(forte["justified_multiple"] - (0.15 - 0.03) / (0.10 - 0.03)) < 1e-12

    # ROE sotto la crescita: il multiplo sarebbe negativo, meglio un errore esplicito
    assert justified_price_to_book(50.0, return_on_equity=0.02,
                                   cost_of_equity=0.10, growth=0.03)["error"]


def test_roe_implicito_ritrova_il_punto_di_partenza():
    parametri = dict(cost_of_equity=0.10, growth=0.03, projection_years=10, fade_years=5)
    prezzo = residual_income_value_per_share(
        80.0, return_on_equity=0.14, **parametri
    )["value_per_share"]
    implicito = reverse_residual_income(prezzo, 80.0, **parametri)["implied_roe"]
    assert abs(implicito - 0.14) < 1e-3

    assert reverse_residual_income(1e6, 80.0, **parametri)["at_bound"] == "max"
    assert reverse_residual_income(None, 80.0, **parametri)["error"]


def test_valutazione_bancaria_completa():
    result = calculate_valuation("BANCA", financials=BANK, market_data=BANK_MARKET)
    assert result["sector"] == sectors.BANK
    assert result["error"] is None

    # i metodi industriali non devono comparire
    assert "dcf_owner_earnings" not in result["methods"]
    assert "epv" not in result["methods"]
    assert "ncav" not in result["methods"]
    assert result["methods"]["residual_income"]["value_per_share"] is not None

    # si sconta al costo dell'equity, non al WACC
    assert result["reverse_dcf"]["model"] == "residual_income"
    assert result["reverse_dcf"]["implied_roe"] is not None
    assert any("costo dell'equity" in note for note in result["data_quality"]["notes"])

    scenari = result["scenarios"]
    assert (scenari["bear"]["value_per_share"]
            < scenari["base"]["value_per_share"]
            < scenari["bull"]["value_per_share"])
    assert "return_on_equity" in scenari["base"]

    grid = result["sensitivity"]
    assert grid["x_label"] == "Cost of equity"
    riga = [c for c in grid["values"][0] if c is not None]
    assert riga == sorted(riga, reverse=True), "il valore deve calare se sale il costo dell'equity"

    json.dumps(result, default=str)
    assert format_valuation_report(result)


def test_valutazione_assicurativa():
    result = calculate_valuation("ASSICURA", financials=INSURER, market_data=INSURER_MARKET)
    assert result["sector"] == sectors.INSURANCE
    assert result["fair_value"].get("point") is not None
    assert result["margin_of_safety"] is not None
    assert format_valuation_report(result)


def test_industriale_resta_sul_percorso_dcf():
    """La modifica non deve spostare le aziende operative sul percorso finanziario."""
    result = calculate_valuation("ALFA", financials=synthetic.ALFA,
                                 market_data=synthetic.ALFA_MARKET)
    assert result["sector"] == sectors.INDUSTRIAL
    assert "dcf_owner_earnings" in result["methods"]
    assert "residual_income" not in result["methods"]
    assert result["reverse_dcf"].get("implied_growth") is not None


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
        print("\nEsempio: banca\n")
        print(format_report(calculate_quality_score("BANCA", financials=BANK)))
    sys.exit(1 if failures else 0)
