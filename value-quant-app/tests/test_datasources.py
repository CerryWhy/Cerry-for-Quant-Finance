"""Test delle fonti dati aggiuntive: SEC EDGAR (XBRL) e override manuali.

Nessuna rete: i companyfacts della SEC sono costruiti a mano nel formato reale
dell'endpoint, cosi' il parsing e' verificabile in modo deterministico — comprese le
insidie vere di XBRL, che sono tre: i periodi trimestrali mescolati agli annuali, i
depositi rettificati dello stesso esercizio, e i tag che cambiano nome fra emittenti.

Il test che conta e' ``test_sec_sblocca_la_categoria_credito``: dimostra sul modello che
fornire una sola voce (``Net Loan``) riporta la copertura della categoria "capitale e
rischio di credito" dal 40% al 100%.

Esecuzione::

    python tests/test_datasources.py
    pytest tests/test_datasources.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "backend"))
sys.path.insert(0, BASE)

import synthetic  # noqa: E402
from models import datasources  # noqa: E402
from models.quality_score import (  # noqa: E402
    _DataQuality,
    calculate_quality_score,
    format_report,
)


def _fact(value, end, *, start=None, form="10-K", filed=None, fy=None):
    """Un fatto XBRL nel formato dell'endpoint companyfacts."""
    entry = {"val": value, "end": end, "form": form, "fy": fy or int(end[:4]),
             "fp": "FY", "filed": filed or f"{int(end[:4]) + 1}-02-15"}
    if start:
        entry["start"] = start
    return entry


def _companyfacts(**concepts):
    """Costruisce un companyfacts con i concetti passati come ``tag=[fatti]``."""
    return {
        "cik": 19617,
        "entityName": "BANCA SINTETICA",
        "facts": {"us-gaap": {
            tag: {"label": tag, "units": {"USD": entries}}
            for tag, entries in concepts.items()
        }},
    }


#: Companyfacts di una banca: le tre voci che yfinance non espone mai.
BANK_FACTS = _companyfacts(
    LoansAndLeasesReceivableNetReportedAmount=[
        _fact(1_299_000_000_000, "2024-12-31"),
        _fact(1_249_000_000_000, "2023-12-31"),
        _fact(1_198_000_000_000, "2022-12-31"),
        _fact(1_150_000_000_000, "2021-12-31"),
        _fact(1_100_000_000_000, "2020-12-31"),
        _fact(1_050_000_000_000, "2019-12-31"),
    ],
    Deposits=[
        _fact(2_400_000_000_000, "2024-12-31"),
        _fact(2_350_000_000_000, "2023-12-31"),
        _fact(2_300_000_000_000, "2022-12-31"),
        _fact(2_250_000_000_000, "2021-12-31"),
        _fact(2_200_000_000_000, "2020-12-31"),
        _fact(2_150_000_000_000, "2019-12-31"),
    ],
    ProvisionForLoanLeaseAndOtherLosses=[
        _fact(9_000_000_000, "2024-12-31", start="2024-01-01"),
        _fact(8_500_000_000, "2023-12-31", start="2023-01-01"),
        _fact(8_000_000_000, "2022-12-31", start="2022-01-01"),
        _fact(7_500_000_000, "2021-12-31", start="2021-01-01"),
        _fact(7_000_000_000, "2020-12-31", start="2020-01-01"),
        _fact(6_500_000_000, "2019-12-31", start="2019-01-01"),
    ],
)


# ---------------------------------------------------------------------------
# Parsing dei fatti XBRL
# ---------------------------------------------------------------------------


def test_scarta_i_periodi_non_annuali():
    """Un trimestre non e' un esercizio: va scartato, non sommato ne' confuso."""
    facts = _companyfacts(ResearchAndDevelopmentExpense=[
        _fact(1_000, "2024-12-31", start="2024-01-01"),   # annuale: 365 giorni
        _fact(250, "2024-03-31", start="2024-01-01", form="10-Q"),  # trimestre
        _fact(260, "2024-06-30", start="2024-04-01"),     # trimestre in un 10-K
        _fact(500, "2024-06-30", start="2024-01-01"),     # semestre
    ])
    rows = datasources.sec_rows(facts)
    serie = rows["income_statement"]["Research And Development"]
    assert serie == {2024: 1_000.0}, "solo il periodo di durata annuale deve passare"


def test_esercizio_a_52_53_settimane():
    """Chi chiude a fine gennaio o a 52 settimane ha esercizi di 360-372 giorni."""
    facts = _companyfacts(Revenues=[
        _fact(50_000, "2025-02-01", start="2024-02-04"),   # 363 giorni
        _fact(48_000, "2024-02-03", start="2023-01-29"),   # 370 giorni
    ])
    rows = datasources.sec_rows(facts)
    assert set(rows["income_statement"]["Total Revenue"]) == {2025, 2024}


def test_vince_il_deposito_piu_recente():
    """A parita' di esercizio conta il dato rettificato, come fa yfinance."""
    facts = _companyfacts(Assets=[
        _fact(1_000, "2024-12-31", filed="2025-02-15"),
        _fact(1_050, "2024-12-31", filed="2026-02-15"),   # rettifica successiva
    ])
    rows = datasources.sec_rows(facts)
    assert rows["balance_sheet"]["Total Assets"] == {2024: 1_050.0}


def test_solo_i_form_annuali():
    facts = _companyfacts(Assets=[
        _fact(1_000, "2024-09-30", form="10-Q"),
        _fact(2_000, "2024-12-31", form="10-K"),
    ])
    rows = datasources.sec_rows(facts)
    assert rows["balance_sheet"]["Total Assets"] == {2024: 2_000.0}


def test_primo_tag_disponibile_vince():
    """I tag non sono uniformi fra emittenti: si prova in ordine e si dichiara."""
    quality = _DataQuality()
    # il primo tag della lista non c'e', il terzo si'
    facts = _companyfacts(ProvisionForLoanAndLeaseLosses=[
        _fact(500, "2024-12-31", start="2024-01-01"),
    ])
    rows = datasources.sec_rows(facts, quality)
    assert rows["income_statement"]["Credit Losses Provision"] == {2024: 500.0}
    assert any("SEC EDGAR" in nota for nota in quality.notes)


def test_capex_e_dividendi_con_segno_negativo():
    """In XBRL sono pagamenti positivi, nel rendiconto di yfinance uscite negative."""
    facts = _companyfacts(
        PaymentsToAcquirePropertyPlantAndEquipment=[
            _fact(3_000, "2024-12-31", start="2024-01-01")],
        PaymentsOfDividendsCommonStock=[
            _fact(1_200, "2024-12-31", start="2024-01-01")],
    )
    rows = datasources.sec_rows(facts)
    assert rows["cash_flow"]["Capital Expenditure"][2024] == -3_000.0
    assert rows["cash_flow"]["Cash Dividends Paid"][2024] == -1_200.0


def test_nessun_fatto_us_gaap_non_esplode():
    quality = _DataQuality()
    rows = datasources.sec_rows({"facts": {}}, quality)
    assert rows == {"income_statement": {}, "balance_sheet": {}, "cash_flow": {}}
    assert any("nessun fatto us-gaap" in voce for voce in quality.missing)


# ---------------------------------------------------------------------------
# Il punto: le voci mancanti tornano
# ---------------------------------------------------------------------------


def test_sec_sblocca_la_categoria_credito():
    """Una sola voce (``Net Loan``) riporta la copertura dal 40% al 100%.

    E' la misura del problema: senza gli impieghi netti cadono due componenti su tre —
    impieghi/depositi e costo del credito — e la categoria resta appesa al solo
    patrimonio/attivo.
    """
    banca = synthetic.make_bank()
    povera = synthetic.drop_row(banca, "balance_sheet", "Net Loan")

    prima = calculate_quality_score("BANCA", financials=povera)
    categoria = prima["category_scores"]["balance_sheet"]
    assert categoria["components_used"] == 1
    assert abs(categoria["coverage"] - 0.4) < 1e-9

    arricchita = datasources.enrich_financials(povera, "BANCA", sec=False,
                                               sec_facts=BANK_FACTS)
    dopo = calculate_quality_score("BANCA", financials=arricchita)
    categoria = dopo["category_scores"]["balance_sheet"]
    assert categoria["components_used"] == 3
    assert categoria["coverage"] == 1.0
    assert dopo["metrics"]["loan_to_deposit"][2024] is not None
    assert dopo["metrics"]["cost_of_risk"][2024] is not None
    assert format_report(dopo)


def test_sec_non_sovrascrive_i_dati_esistenti():
    """EDGAR riempie i buchi. Il modello e' tarato su yfinance: non si cambia base."""
    banca = synthetic.make_bank()
    originale = banca["balance_sheet"].loc["Total Deposits"].iloc[0]

    arricchita = datasources.enrich_financials(banca, "BANCA", sec=False,
                                               sec_facts=BANK_FACTS)
    assert arricchita["balance_sheet"].loc["Total Deposits"].iloc[0] == originale


def test_discrepanza_fra_fonti_dichiarata():
    """Due fonti che divergono molto sono un'informazione, non un conflitto da nascondere."""
    quality = _DataQuality()
    banca = synthetic.make_bank()
    datasources.enrich_financials(banca, "BANCA", sec=False, sec_facts=BANK_FACTS,
                                  quality=quality)
    assert any("differisce fra il dato di partenza" in nota for nota in quality.notes)


def test_provenienza_dichiarata():
    quality = _DataQuality()
    povera = synthetic.drop_row(synthetic.make_bank(), "balance_sheet", "Net Loan")
    datasources.enrich_financials(povera, "BANCA", sec=False, sec_facts=BANK_FACTS,
                                  quality=quality)
    provenienza = [nota for nota in quality.notes if "Provenienza" in nota]
    assert provenienza, "ogni riga aggiunta deve dichiarare da dove viene"
    assert "Net Loan (aggiunta)" in provenienza[0]


def test_storia_piu_lunga_di_quella_di_yfinance():
    """Se la fonte ha piu' esercizi, gli anni in piu' diventano colonne nuove."""
    quality = _DataQuality()
    corta = synthetic.make_bank(years=(2024, 2023, 2022))
    facts = _companyfacts(Deposits=[
        _fact(2_400e9, "2024-12-31"), _fact(2_350e9, "2023-12-31"),
        _fact(2_300e9, "2022-12-31"), _fact(2_250e9, "2021-12-31"),
        _fact(2_200e9, "2020-12-31"), _fact(2_150e9, "2019-12-31"),
    ])
    arricchita = datasources.enrich_financials(corta, "BANCA", sec=False,
                                               sec_facts=facts, quality=quality)
    assert len(arricchita["years"]) == 6
    assert any("Storia estesa" in nota for nota in quality.notes)


# ---------------------------------------------------------------------------
# Override manuali
# ---------------------------------------------------------------------------


def _scrivi_override(contenuto, suffix=".json"):
    handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    json.dump(contenuto, handle)
    handle.close()
    return handle.name


def test_override_sovrascrive_sempre():
    """Se un numero e' scritto a mano e' perche' quello automatico e' sbagliato."""
    path = _scrivi_override({"BANCA": {"balance_sheet": {
        "Total Deposits": {"2024": 1_111_000_000_000},
    }}})
    try:
        quality = _DataQuality()
        arricchita = datasources.enrich_financials(
            synthetic.make_bank(), "BANCA", sec=False,
            overrides_path=path, quality=quality,
        )
        colonna = arricchita["balance_sheet"].columns[0]
        assert arricchita["balance_sheet"].loc["Total Deposits", colonna] == 1_111_000_000_000
        assert any("inserite a mano" in voce for voce in quality.estimated)
    finally:
        os.unlink(path)


def test_override_aggiunge_voci_mancanti():
    povera = synthetic.drop_row(synthetic.make_bank(), "balance_sheet", "Net Loan")
    path = _scrivi_override({"BANCA": {"balance_sheet": {
        "Net Loan": {str(anno): 1_000e9 for anno in synthetic.YEARS},
    }}})
    try:
        arricchita = datasources.enrich_financials(povera, "BANCA", sec=False,
                                                   overrides_path=path)
        result = calculate_quality_score("BANCA", financials=arricchita)
        assert result["metrics"]["loan_to_deposit"][2024] is not None
    finally:
        os.unlink(path)


def test_override_ticker_diverso_non_si_applica():
    path = _scrivi_override({"ALTRA": {"balance_sheet": {"Net Loan": {"2024": 1.0}}}})
    try:
        quality = _DataQuality()
        rows = datasources.load_overrides(path, "BANCA", quality)
        assert rows["balance_sheet"] == {}
    finally:
        os.unlink(path)


def test_override_malformato_non_esplode():
    quality = _DataQuality()
    assert datasources.load_overrides("/percorso/inesistente.json", "X", quality)
    assert any("non trovato" in voce for voce in quality.missing)

    path = _scrivi_override({"BANCA": {"bilancio_sbagliato": {"X": {"2024": 1}}}})
    try:
        quality = _DataQuality()
        datasources.load_overrides(path, "BANCA", quality)
        assert any("ignorato" in nota for nota in quality.notes)
    finally:
        os.unlink(path)

    path = _scrivi_override({"BANCA": {"balance_sheet": {"Net Loan": "non un dizionario"}}})
    try:
        quality = _DataQuality()
        rows = datasources.load_overrides(path, "BANCA", quality)
        assert rows["balance_sheet"] == {}
        assert any("serve {anno: valore}" in nota for nota in quality.notes)
    finally:
        os.unlink(path)


def test_override_ha_precedenza_su_sec():
    """Le due fonti insieme: l'ordine di precedenza deve reggere."""
    povera = synthetic.drop_row(synthetic.make_bank(), "balance_sheet", "Net Loan")
    path = _scrivi_override({"BANCA": {"balance_sheet": {
        "Net Loan": {"2024": 42_000_000_000},
    }}})
    try:
        arricchita = datasources.enrich_financials(
            povera, "BANCA", sec=False, sec_facts=BANK_FACTS, overrides_path=path,
        )
        colonna = [c for c in arricchita["balance_sheet"].columns if c.year == 2024][0]
        assert arricchita["balance_sheet"].loc["Net Loan", colonna] == 42_000_000_000
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Robustezza
# ---------------------------------------------------------------------------


def test_senza_fonti_restituisce_l_input_invariato():
    banca = synthetic.make_bank()
    invariata = datasources.enrich_financials(banca, "BANCA", sec=False)
    assert invariata["balance_sheet"].equals(banca["balance_sheet"])


def test_prospetto_assente_viene_creato():
    """Un bilancio senza rendiconto deve poter ricevere le righe di cassa."""
    banca = dict(synthetic.make_bank())
    banca["cash_flow"] = None
    facts = _companyfacts(DepreciationDepletionAndAmortization=[
        _fact(2_000, "2024-12-31", start="2024-01-01"),
    ])
    arricchita = datasources.enrich_financials(banca, "BANCA", sec=False, sec_facts=facts)
    assert arricchita["cash_flow"] is not None
    assert "Depreciation And Amortization" in list(arricchita["cash_flow"].index)


def test_l_arricchimento_non_muta_l_input():
    banca = synthetic.make_bank()
    prima = banca["balance_sheet"].copy()
    povera = synthetic.drop_row(banca, "balance_sheet", "Net Loan")
    datasources.enrich_financials(povera, "BANCA", sec=False, sec_facts=BANK_FACTS)
    assert banca["balance_sheet"].equals(prima)


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
