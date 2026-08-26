"""Test della capitalizzazione della R&S (approccio Damodaran).

Il punto del modulo e' correggere due distorsioni che il principio contabile introduce
su chi vive di ricerca: un ROIC gonfiato (il capitale investito ignora decenni di
sviluppo) e Owner Earnings depressi (la ricerca di crescita viene sottratta per intero
dall'utile). I test verificano le formule contro il calcolo analitico, non contro
l'output del codice.

Esecuzione::

    python tests/test_research.py
    pytest tests/test_research.py
"""

from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "backend"))
sys.path.insert(0, BASE)

import synthetic  # noqa: E402
from models.quality_score import (  # noqa: E402
    DEFAULT_RD_LIFE,
    _DataQuality,
    calculate_quality_score,
    capitalize_research_development,
    extract_fundamentals,
    format_report,
)
from models.valuation import calculate_valuation  # noqa: E402

#: R&S costante a 10 miliardi: il caso di regime stazionario, dove spesa e ammortamento
#: devono pareggiare esattamente.
COSTANTE = synthetic.make_rd_company("STABILE", rd=[10e9] * 6)

#: R&S in crescita: il caso normale nel tech, dove la rettifica sull'utile e' positiva.
CRESCENTE = synthetic.make_rd_company("CRESCE", rd=[20e9, 16e9, 13e9, 10e9, 8e9, 6e9])

MARKET = {"price": 60.0, "shares_outstanding": 2e9, "beta": 1.2,
          "market_cap": 120e9, "currency": "USD"}


def _capitalizza(financials, life=DEFAULT_RD_LIFE):
    """Estrae i fondamentali e applica la rettifica, restituendo entrambi."""
    quality = _DataQuality()
    fundamentals = extract_fundamentals(financials, quality)
    detail = capitalize_research_development(fundamentals, life, quality)
    return fundamentals, detail, quality


# ---------------------------------------------------------------------------
# Le formule
# ---------------------------------------------------------------------------


def test_regime_stazionario_ammortamento_pareggia_la_spesa():
    """R&S costante: l'asset non cresce piu' e la rettifica sull'utile e' nulla.

    Con spesa costante E e vita L, l'asset vale E x (L+1)/2 — la somma delle quote non
    ancora ammortizzate, L/L + (L-1)/L + ... + 1/L — e l'ammortamento vale esattamente E.
    L'utile non cambia: cambia solo il denominatore del ROIC. E' il test che dimostra
    che la capitalizzazione non e' un trucco per gonfiare gli utili.
    """
    _, detail, _ = _capitalizza(COSTANTE, life=5)
    recente = detail[2024]
    assert abs(recente["rd_expense"] - 10e9) < 1.0
    assert abs(recente["rd_amortization"] - 10e9) < 1.0
    assert abs(recente["research_asset"] - 10e9 * (5 + 1) / 2) < 1.0
    assert abs(recente["adjustment_pretax"]) < 1.0


def test_serie_crescente_contro_il_calcolo_a_mano():
    """R&S [20, 16, 13, 10, 8] con vita 5 anni, verificata voce per voce.

    asset = 20x(5/5) + 16x(4/5) + 13x(3/5) + 10x(2/5) + 8x(1/5) = 46.2
    amm   = (16 + 13 + 10 + 8 + 6) / 5 = 10.6
    """
    _, detail, _ = _capitalizza(CRESCENTE, life=5)
    recente = detail[2024]
    atteso_asset = (20e9 * 5 + 16e9 * 4 + 13e9 * 3 + 10e9 * 2 + 8e9 * 1) / 5
    atteso_amm = (16e9 + 13e9 + 10e9 + 8e9 + 6e9) / 5
    assert abs(recente["research_asset"] - atteso_asset) < 1.0
    assert abs(recente["rd_amortization"] - atteso_amm) < 1.0
    assert abs(recente["adjustment_pretax"] - (20e9 - atteso_amm)) < 1.0
    assert recente["adjustment_pretax"] > 0, "chi aumenta la ricerca ha una rettifica positiva"


def test_vita_utile_piu_lunga_ingrossa_l_asset_non_l_ammortamento():
    """Con R&S costante l'ammortamento resta pari alla spesa per qualunque vita utile.

    E' la differenza fra software (5 anni) e farmaceutico (10): cambia il capitale
    investito, e quindi il ROIC, non l'utile.
    """
    _, breve, _ = _capitalizza(COSTANTE, life=5)
    _, lunga, _ = _capitalizza(COSTANTE, life=10)
    assert abs(breve[2024]["rd_amortization"] - lunga[2024]["rd_amortization"]) < 1.0
    assert abs(lunga[2024]["research_asset"] - 10e9 * (10 + 1) / 2) < 1.0
    assert lunga[2024]["research_asset"] > breve[2024]["research_asset"]


# ---------------------------------------------------------------------------
# Effetto sulle metriche
# ---------------------------------------------------------------------------


def test_roic_scende_e_owner_earnings_salgono():
    """Le due distorsioni che la capitalizzazione esiste per correggere."""
    spesata = calculate_quality_score("CRESCE", financials=CRESCENTE)
    capitalizzata = calculate_quality_score("CRESCE", financials=CRESCENTE, capitalize_rd=True)

    assert capitalizzata["metrics"]["roic"][2024] < spesata["metrics"]["roic"][2024]
    assert capitalizzata["metrics"]["owner_earnings"][2024] > spesata["metrics"]["owner_earnings"][2024]
    assert capitalizzata["metrics"]["roe"][2024] < spesata["metrics"]["roe"][2024]
    # il margine operativo sale: la ricerca non e' piu' interamente un costo dell'anno
    assert capitalizzata["metrics"]["operating_margin"][2024] > spesata["metrics"]["operating_margin"][2024]


def test_owner_earnings_salgono_della_ricerca_di_crescita_netto_imposte():
    """OE rettificati = OE + (R&S - ammortamento) x (1 - aliquota), esattamente.

    Non serve toccare D&A ne' CapEx: la rettifica dell'utile netto si propaga da sola
    nella formula degli Owner Earnings. E' lo stesso trattamento che il modello riserva
    al CapEx di crescita col metodo Greenwald.
    """
    spesata = calculate_quality_score("CRESCE", financials=CRESCENTE)
    capitalizzata = calculate_quality_score("CRESCE", financials=CRESCENTE, capitalize_rd=True)

    rettifica = capitalizzata["research_capitalization"]["by_year"][2024]["adjustment_pretax"]
    aliquota = spesata["metrics"]["effective_tax_rate"][2024] / 100.0
    atteso = spesata["metrics"]["owner_earnings"][2024] + rettifica * (1 - aliquota)
    assert abs(capitalizzata["metrics"]["owner_earnings"][2024] - atteso) < 1e6


def test_capitale_tangibile_resta_tangibile():
    """L'asset di ricerca non deve entrare nel denominatore del metro di Buffett.

    Il "return on unleveraged net tangible assets" della lettera 2007 e' definito sui
    tangibili: una stima non ci entra. L'asset viene sommato agli immateriali proprio
    perche' il capitale tangibile resti identico a prima.
    """
    base, _, _ = _capitalizza(CRESCENTE)
    grezzo = extract_fundamentals(CRESCENTE, _DataQuality())

    def tangibile(row):
        return (row["invested_capital_calc"]
                - (row.get("goodwill") or 0.0) - (row.get("intangibles") or 0.0))

    assert abs(tangibile(base[2024]) - tangibile(grezzo[2024])) < 1.0
    # mentre il capitale investito, quello si', e' cresciuto dell'asset di ricerca
    assert base[2024]["invested_capital_calc"] > grezzo[2024]["invested_capital_calc"]


def test_rettifica_non_applicata_due_volte():
    """Chiamare due volte la funzione non deve raddoppiare l'asset."""
    fundamentals, primo, quality = _capitalizza(CRESCENTE)
    capitale_dopo_uno = fundamentals[2024]["invested_capital_calc"]
    secondo = capitalize_research_development(fundamentals, DEFAULT_RD_LIFE, quality)
    assert secondo == {}, "la seconda passata non deve produrre rettifiche"
    assert fundamentals[2024]["invested_capital_calc"] == capitale_dopo_uno


# ---------------------------------------------------------------------------
# Casi in cui non si applica
# ---------------------------------------------------------------------------


def test_azienda_senza_ricerca_non_viene_toccata():
    senza = calculate_quality_score("ALFA", financials=synthetic.ALFA, capitalize_rd=True)
    normale = calculate_quality_score("ALFA", financials=synthetic.ALFA)
    assert senza["quality_score"] == normale["quality_score"]
    assert senza["research_capitalization"] is None
    assert any("non e' presente" in nota for nota in senza["data_quality"]["notes"])


def test_ignorata_sui_finanziari():
    """Su una banca il capitale investito non e' definito: la rettifica non ha senso."""
    banca = calculate_quality_score("BANCA", financials=synthetic.make_bank(),
                                    capitalize_rd=True)
    assert banca["research_capitalization"] is None
    assert any("ignorata" in nota for nota in banca["data_quality"]["notes"])


def test_storia_insufficiente_viene_dichiarata():
    """Con vita utile 10 e sei esercizi disponibili la storia non basta: va detto."""
    result = calculate_quality_score("CRESCE", financials=CRESCENTE,
                                     capitalize_rd=True, rd_life=10)
    assert result["research_capitalization"]["life"] == 10
    assert any("Storia della R&S insufficiente" in voce
               for voce in result["data_quality"]["estimated"])


# ---------------------------------------------------------------------------
# Propagazione a valle
# ---------------------------------------------------------------------------


def test_valutazione_sconta_owner_earnings_rettificati():
    spesata = calculate_valuation("CRESCE", financials=CRESCENTE, market_data=MARKET)
    capitalizzata = calculate_valuation("CRESCE", financials=CRESCENTE,
                                        market_data=MARKET, capitalize_rd=True)
    assert (capitalizzata["inputs"]["base_owner_earnings"]
            > spesata["inputs"]["base_owner_earnings"])
    assert capitalizzata["fair_value"]["point"] > spesata["fair_value"]["point"]


def test_report_dichiara_la_rettifica():
    result = calculate_quality_score("CRESCE", financials=CRESCENTE, capitalize_rd=True)
    report = format_report(result)
    assert "CAPITALIZZAZIONE DELLA R&S" in report
    assert "Asset di ricerca" in report
    # e dichiara che i punteggi non sono confrontabili con quelli non rettificati
    assert any("non sono direttamente confrontabili" in nota
               for nota in result["data_quality"]["notes"])


def test_convive_con_la_modalita_buffett():
    """Le due opzioni sono ortogonali: nessuna delle due disattiva l'altra.

    In modalita' buffett la rettifica alza il rendimento sul capitale tangibile (il
    numeratore e' l'EBIT rettificato, il denominatore resta tangibile) e migliora
    Debito / Owner Earnings. Il punteggio sale, ed e' per questo che la nota dichiara
    che le due letture non sono confrontabili fra loro.
    """
    buffett = calculate_quality_score("CRESCE", financials=CRESCENTE, mode="buffett")
    entrambi = calculate_quality_score("CRESCE", financials=CRESCENTE, mode="buffett",
                                       capitalize_rd=True)
    assert entrambi["profile"] == "buffett"
    assert entrambi["research_capitalization"] is not None
    assert (entrambi["metrics"]["return_on_tangible_capital"][2024]
            > buffett["metrics"]["return_on_tangible_capital"][2024])


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
