"""Profili di settore: cosa misurare, con quali soglie, a seconda del tipo di azienda.

Perche' esiste questo modulo
----------------------------
Il metro di un'azienda industriale non si applica a una banca, e non e' questione di
tarare qualche soglia: **cambia l'oggetto della misurazione**.

Per un industriale il debito e' come ti finanzi. Per una banca il debito (depositi,
raccolta interbancaria) e' la **materia prima**: compra denaro a un tasso e lo rivende
a un tasso piu' alto. Da questo discende che per una banca:

* non esiste un EBIT — non puoi "sommare indietro" gli oneri finanziari, perche' sono
  il costo del venduto. Quindi niente NOPAT e **niente ROIC**;
* Debt/Equity di 10x e' normale, non pericoloso;
* l'Interest Coverage divide una grandezza inesistente per il costo principale;
* il Current Ratio non e' definito: lo stato patrimoniale bancario non e' classificato
  in corrente / non corrente;
* il CapEx e' irrilevante, quindi gli Owner Earnings collassano sull'utile netto.

Applicare il profilo industriale a una banca non produce un errore: produce **numeri
plausibili e privi di significato**, che e' molto peggio. Questo modulo esiste per
evitarlo.

I sei profili
-------------
``INDUSTRIAL``  aziende operative: ROIC, Owner Earnings, leva finanziaria
``BANK``        banche commerciali: ROTCE, margine di interesse, efficienza, funding
``INSURANCE``   assicurazioni e holding: crescita del patrimonio per azione, combined ratio
``REIT``        immobiliari: FFO e AFFO, perche' l'ammortamento non e' un costo reale
``UTILITY``     utility regolate: il rendimento lo fissa il regolatore, non il mercato
``ENERGY``      esplorazione e produzione: il prezzo domina l'utile, l'attivo si consuma

Gli ultimi tre hanno la stessa radice dei primi con cause diverse. Per una **utility
regolata** il rendimento non lo determina l'azienda: lo autorizza il regolatore sulla rate
base, quindi un ROIC basso non e' debolezza competitiva e uno alto sarebbe un'anomalia
destinata a rientrare in tariffa. Per un **E&P** l'utile dell'anno misura in buona parte
dove stava il prezzo del greggio, e l'attivo si consuma mentre lo si sfrutta: chi non
rimpiazza le riserve puo' mostrare utili eccellenti per anni, che sono gli utili della
liquidazione.

Il caso REIT ha la stessa radice degli altri con una causa diversa. Per un'immobiliare il
problema non e' il debito ma l'**ammortamento**: il principio contabile deprezza un
fabbricato su 27,5 o 40 anni, mentre un immobile ben tenuto in una buona posizione non
perde valore, e spesso lo guadagna. E' un costo che non corrisponde a nessuna uscita e a
nessun logorio. Su un REIT sano l'utile netto puo' essere vicino a zero, e con lui ROE,
ROA e margine netto: il profilo industriale non sbaglia il calcolo, restituisce
un'azienda che sembra incapace di guadagnare.

Il rilevamento e' automatico e si basa sul **peso delle voci di bilancio** (i depositi,
i premi assicurativi, gli immobili) e non sull'etichetta di settore di Yahoo, che
mette "Financial Services" su banche, assicurazioni, asset manager e borse — soggetti
che vogliono trattamenti diversi.

Limite dichiarato
-----------------
yfinance non espone i **ratios di vigilanza** (CET1, NPL, LCR): quelli vivono nelle
segnalazioni regolamentari. Il profilo bancario usa quello che si puo' ricostruire dai
prospetti (patrimonio/attivo come proxy di leva, costo del credito, loan-to-deposit) e
dichiara esplicitamente in ``data_quality`` cosa manca. Un proxy segnalato e' onesto;
un proxy spacciato per il ratio vero no.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    from .quality_score import (
        BALANCE_ALIASES,
        CASHFLOW_ALIASES,
        INCOME_ALIASES,
        _DataQuality,
        _mean,
        _round,
        _row_index,
        _safe_div,
        _score_linear,
        _series_by_year,
        _to_float,
        calculate_balance_sheet_ratios,
        calculate_consistency,
    )
except ImportError:  # esecuzione come script standalone
    from quality_score import (  # type: ignore[no-redef]
        BALANCE_ALIASES,
        CASHFLOW_ALIASES,
        INCOME_ALIASES,
        _DataQuality,
        _mean,
        _round,
        _row_index,
        _safe_div,
        _score_linear,
        _series_by_year,
        _to_float,
        calculate_balance_sheet_ratios,
        calculate_consistency,
    )


__all__ = [
    "BANK",
    "BANK_DEPOSIT_SHARE",
    "BANK_INTEREST_SHARE",
    "ENERGY",
    "ENERGY_EXPLORATION_SHARE",
    "REIT",
    "REIT_DA_SHARE",
    "REIT_PPE_SHARE",
    "REIT_PROPERTY_SHARE",
    "MAINTENANCE_CAPEX_RULE",
    "UTILITY",
    "UTILITY_REGULATORY_SHARE",
    "BUFFETT",
    "BUFFETT_PROFILE",
    "INDUSTRIAL",
    "INSURANCE",
    "INSURANCE_PREMIUM_SHARE",
    "INSURANCE_RESERVE_SHARE",
    "PROFILES",
    "build_metrics",
    "detect_sector",
    "extract_sector_fundamentals",
    "resolve_profile",
    "score_categories",
]


INDUSTRIAL = "industrial"
BANK = "bank"
INSURANCE = "insurance"
REIT = "reit"
UTILITY = "utility"
ENERGY = "energy"


# ---------------------------------------------------------------------------
# Voci di bilancio specifiche dei settori finanziari
# ---------------------------------------------------------------------------

BANK_INCOME_ALIASES: Dict[str, Sequence[str]] = {
    "net_interest_income": (
        "Net Interest Income",
        "Net Interest Income After Provision For Loan Loss",
    ),
    "interest_income_total": ("Total Interest Income", "Interest Income"),
    "interest_expense_total": ("Total Interest Expense", "Interest Expense"),
    "non_interest_income": (
        "Total Non Interest Income", "Non Interest Income", "Noninterest Income",
        "Total Other Income",
    ),
    "non_interest_expense": (
        "Total Non Interest Expense", "Non Interest Expense", "Noninterest Expense",
        "Total Other Expenses", "Other Non Interest Expense",
    ),
    "credit_provision": (
        "Credit Losses Provision", "Provision For Loan Lease And Other Losses",
        "Provision For Credit Losses", "Provision For Doubtful Accounts",
    ),
}

BANK_BALANCE_ALIASES: Dict[str, Sequence[str]] = {
    "total_deposits": ("Total Deposits", "Deposits", "Customer Deposits", "Bank Deposits"),
    "net_loans": (
        "Net Loan", "Net Loans", "Loans And Advances", "Loans Receivable",
        "Gross Loan", "Total Loans",
    ),
    "goodwill": ("Goodwill",),
    "intangibles": (
        "Other Intangible Assets", "Intangible Assets",
        "Goodwill And Other Intangible Assets",
    ),
}

INSURANCE_INCOME_ALIASES: Dict[str, Sequence[str]] = {
    "premiums_earned": (
        "Total Premiums Earned", "Net Premiums Earned", "Net Premium Earned",
        "Premiums", "Earned Premiums",
    ),
    "policy_benefits": (
        "Policyholder Benefits Gross", "Total Policyholder Benefits",
        "Losses And Loss Adjustment Expenses", "Benefits Losses And Expenses",
        "Policyholder Benefits Ceded",
    ),
    "investment_income": (
        "Net Investment Income", "Total Investment Income", "Investment Income Net",
        "Interest Income",
    ),
    "underwriting_expense": (
        "Underwriting Expense", "Policy Acquisition Costs",
        "Total Non Interest Expense", "Other Operating Expenses",
    ),
}

INSURANCE_BALANCE_ALIASES: Dict[str, Sequence[str]] = {
    "total_investments": (
        "Total Investments", "Investments And Advances", "Investmentin Financial Assets",
        "Long Term Investments",
    ),
    "insurance_liabilities": (
        "Total Policy Liabilities", "Insurance Contract Liabilities",
        "Future Policy Benefits", "Policyholder Funds",
    ),
    "goodwill": ("Goodwill",),
    "intangibles": (
        "Other Intangible Assets", "Intangible Assets",
        "Goodwill And Other Intangible Assets",
    ),
}


# ---------------------------------------------------------------------------
# Rilevamento del settore
# ---------------------------------------------------------------------------
#
# La sola **presenza** di una voce non basta a decidere il profilo, e il caso che lo
# dimostra e' Alphabet: yfinance espone "Net Interest Income" anche per gli industriali
# con molta liquidita' parcheggiata (interessi attivi meno oneri finanziari), e su quel
# solo indizio Alphabet veniva classificata come banca. Il marcatore deve **pesare**.

#: Depositi / totale attivo oltre cui la raccolta e' la materia prima dell'azienda.
#: JPMorgan sta al 61%, Goldman Sachs al 26%: una banca vera non ci arriva per caso.
BANK_DEPOSIT_SHARE = 0.20

#: Margine di interesse / ricavi totali oltre cui il mestiere e' l'intermediazione.
#: E' la soglia che separa una banca (dove il margine e' la maggior parte del ricavo)
#: da un industriale con la tesoreria piena (dove e' rumore all'1-2%).
BANK_INTEREST_SHARE = 0.30

#: Premi / ricavi totali oltre cui il mestiere e' la sottoscrizione di rischi.
#:
#: Deliberatamente piu' bassa delle due soglie bancarie, per due motivi:
#:
#: 1. il falso positivo che ha motivato queste soglie e' specifico del margine di
#:    interesse; le voci di premio non compaiono per errore su chi non assicura;
#: 2. il caso di riferimento del profilo e' Berkshire, che sta al ~23% (83 mld di premi
#:    su 364 mld di ricavi nel 2023, il resto essendo ferrovie, energia, industria):
#:    una soglia al 30% espellerebbe dal profilo assicurativo l'azienda per cui e'
#:    stato scritto.
INSURANCE_PREMIUM_SHARE = 0.20

#: Riserve tecniche / totale attivo: seconda via al profilo assicurativo, per chi ha
#: molti ricavi non tecnici e sarebbe scartato dalla soglia sui premi.
INSURANCE_RESERVE_SHARE = 0.10

#: Immobili / totale attivo oltre cui gli immobili **sono** l'azienda, non un supporto.
#: Prima via al profilo REIT, e la sola affidabile: richiede una voce immobiliare
#: esplicita in bilancio.
REIT_PROPERTY_SHARE = 0.40

#: Seconda via, euristica, per chi classifica gli immobili dentro le immobilizzazioni
#: generiche: immobilizzazioni oltre il 70% dell'attivo **e** ammortamenti oltre il 20%
#: dei ricavi. Le due condizioni servono entrambe perche' la prima da sola cattura
#: qualunque azienda ad alta intensita' di capitale.
REIT_PPE_SHARE = 0.70
REIT_DA_SHARE = 0.20

#: Attivi regolatori / totale attivo oltre cui l'azienda vive di tariffe amministrate.
#: La soglia e' bassa di proposito: gli attivi regolatori sono pochi punti percentuali
#: dell'attivo anche nelle utility piu' regolate, ma **esistono solo la'**. Qui non si
#: misura un peso economico, si riconosce una voce che nessun altro settore ha.
UTILITY_REGULATORY_SHARE = 0.005

#: Spesa di esplorazione / ricavi oltre cui il mestiere e' cercare idrocarburi.
#: Anche questa e' una soglia di riconoscimento e non di rilevanza: un integrato come una
#: major ha esplorazione all'1-2% dei ricavi perche' la raffinazione e la distribuzione
#: gonfiano il denominatore, e resta comunque un E&P nella parte che conta.
ENERGY_EXPLORATION_SHARE = 0.002


def _median(values: Sequence[float]) -> Optional[float]:
    """Mediana di una sequenza gia' ripulita dai ``None``."""
    clean = sorted(values)
    if not clean:
        return None
    middle = len(clean) // 2
    if len(clean) % 2:
        return clean[middle]
    return (clean[middle - 1] + clean[middle]) / 2.0


def _weight_share(
    numerator_rows: Mapping[str, Any],
    numerator_aliases: Sequence[str],
    denominator_rows: Mapping[str, Any],
    denominator_aliases: Sequence[str],
) -> Optional[float]:
    """Peso **mediano** di una voce sul suo aggregato, anno per anno.

    La mediana e non l'ultimo esercizio: una riclassificazione o un anno anomalo non
    devono spostare l'azienda in un altro profilo di analisi.

    Restituisce ``None`` quando una delle due serie manca del tutto — che e' diverso da
    "pesa poco", e chi chiama deve poter distinguere i due casi.
    """
    numerator, _ = _series_by_year(numerator_rows, numerator_aliases)
    denominator, _ = _series_by_year(denominator_rows, denominator_aliases)
    if not numerator or not denominator:
        return None
    ratios = [
        numerator[year] / denominator[year]
        for year in sorted(set(numerator) & set(denominator))
        if denominator[year]
    ]
    return _median(ratios)


def detect_sector(
    financials: Mapping[str, Any],
    quality: Optional[_DataQuality] = None,
) -> str:
    """Riconosce il tipo di azienda dal **peso** delle voci di bilancio.

    Due condizioni per la banca, alternative fra loro: depositi oltre il 20% dell'attivo
    (la raccolta e' la materia prima) oppure margine di interesse oltre il 30% dei ricavi
    (l'intermediazione e' il mestiere). Per l'assicurazione: premi oltre il 20% dei ricavi
    oppure riserve tecniche oltre il 10% dell'attivo. Per il REIT: immobili oltre il 40%
    dell'attivo, oppure immobilizzazioni oltre il 70% dell'attivo **e** ammortamenti
    oltre il 20% dei ricavi. In assenza di tutto, azienda operativa: non si tira a
    indovinare.

    Due profili si riconoscono invece da voci **esclusive**, e per quelli la soglia serve
    solo a distinguere il dato dallo zero: gli *attivi regolatori* esistono unicamente
    dove la tariffa e' amministrata (utility), la *spesa di esplorazione* unicamente in
    chi cerca idrocarburi (E&P). Vengono controllati prima del REIT, perche' tutti e tre
    hanno molte immobilizzazioni e un marcatore esplicito batte un'euristica.

    Quando l'aggregato di riferimento manca (nessun totale attivo, nessun ricavo) la
    materialita' non e' verificabile e si ripiega sulla presenza della voce, ma solo per
    depositi e premi, che sono inequivocabili. **Non** per il margine di interesse, che
    e' esattamente il marcatore che genera falsi positivi. Il ripiego viene dichiarato
    in ``data_quality``.

    Non si usa il campo ``sector`` di Yahoo: mette "Financial Services" su banche,
    assicurazioni, asset manager e gestori di mercati, che richiedono metriche diverse,
    ed e' un endpoint spesso non raggiungibile.
    """
    quality = quality if quality is not None else _DataQuality()

    income_rows = _row_index(financials.get("income_statement"))
    balance_rows = _row_index(financials.get("balance_sheet"))
    cash_rows = _row_index(financials.get("cash_flow"))

    def present(rows: Mapping[str, Any], aliases: Sequence[str]) -> bool:
        values, _ = _series_by_year(rows, aliases)
        return bool(values)

    deposit_share = _weight_share(
        balance_rows, BANK_BALANCE_ALIASES["total_deposits"],
        balance_rows, BALANCE_ALIASES["total_assets"],
    )
    interest_share = _weight_share(
        income_rows, BANK_INCOME_ALIASES["net_interest_income"],
        income_rows, INCOME_ALIASES["revenue"],
    )
    premium_share = _weight_share(
        income_rows, INSURANCE_INCOME_ALIASES["premiums_earned"],
        income_rows, INCOME_ALIASES["revenue"],
    )
    reserve_share = _weight_share(
        balance_rows, INSURANCE_BALANCE_ALIASES["insurance_liabilities"],
        balance_rows, BALANCE_ALIASES["total_assets"],
    )
    # Terza via: i sinistri pagati. Serve a chi espone i costi tecnici ma non i premi
    # lordi, e resta un marcatore esclusivo del settore.
    benefit_share = _weight_share(
        income_rows, INSURANCE_INCOME_ALIASES["policy_benefits"],
        income_rows, INCOME_ALIASES["revenue"],
    )

    bank_reasons: List[str] = []
    bank_strength = 0.0
    if deposit_share is not None and deposit_share > BANK_DEPOSIT_SHARE:
        bank_reasons.append(f"depositi pari a {deposit_share:.0%} del totale attivo")
        bank_strength = max(bank_strength, deposit_share)
    if interest_share is not None and interest_share > BANK_INTEREST_SHARE:
        bank_reasons.append(f"margine di interesse pari a {interest_share:.0%} dei ricavi")
        bank_strength = max(bank_strength, interest_share)
    if not bank_reasons and deposit_share is None and present(
        balance_rows, BANK_BALANCE_ALIASES["total_deposits"]
    ):
        bank_reasons.append("presenza di depositi della clientela")
        bank_strength = max(bank_strength, BANK_DEPOSIT_SHARE)
        quality.estimate(
            "Depositi presenti ma totale attivo non leggibile: profilo bancario assegnato "
            "sulla presenza della voce, senza verifica del peso."
        )

    insurance_reasons: List[str] = []
    insurance_strength = 0.0
    if premium_share is not None and premium_share > INSURANCE_PREMIUM_SHARE:
        insurance_reasons.append(f"premi pari a {premium_share:.0%} dei ricavi")
        insurance_strength = max(insurance_strength, premium_share)
    if reserve_share is not None and reserve_share > INSURANCE_RESERVE_SHARE:
        insurance_reasons.append(f"riserve tecniche pari a {reserve_share:.0%} del totale attivo")
        insurance_strength = max(insurance_strength, reserve_share)
    if benefit_share is not None and benefit_share > INSURANCE_PREMIUM_SHARE:
        insurance_reasons.append(f"sinistri e prestazioni pari a {benefit_share:.0%} dei ricavi")
        insurance_strength = max(insurance_strength, benefit_share)
    if not insurance_reasons and premium_share is None and present(
        income_rows, INSURANCE_INCOME_ALIASES["premiums_earned"]
    ):
        insurance_reasons.append("presenza di premi assicurativi")
        insurance_strength = max(insurance_strength, INSURANCE_PREMIUM_SHARE)
        quality.estimate(
            "Premi presenti ma ricavi totali non leggibili: profilo assicurativo assegnato "
            "sulla presenza della voce, senza verifica del peso."
        )

    # Marcatori visti ma non materiali: vanno dichiarati, perche' sono la traccia del
    # falso positivo evitato. Chi legge il report deve sapere perche' una societa' con
    # una voce di margine di interesse **non** e' stata trattata come banca.
    if not bank_reasons and interest_share is not None and interest_share > 0:
        quality.note(
            f"Margine di interesse presente ma pari a {interest_share:.1%} dei ricavi "
            f"(soglia {BANK_INTEREST_SHARE:.0%}): non e' una banca. La voce compare anche "
            "sugli industriali con molta liquidita' in tesoreria."
        )
    if not bank_reasons and deposit_share is not None and deposit_share > 0:
        quality.note(
            f"Depositi presenti ma pari a {deposit_share:.1%} del totale attivo "
            f"(soglia {BANK_DEPOSIT_SHARE:.0%}): non sono la fonte di finanziamento "
            "principale, profilo bancario non applicato."
        )
    if not insurance_reasons and premium_share is not None and premium_share > 0:
        quality.note(
            f"Premi presenti ma pari a {premium_share:.1%} dei ricavi "
            f"(soglia {INSURANCE_PREMIUM_SHARE:.0%}): attivita' assicurativa non "
            "prevalente, profilo assicurativo non applicato."
        )

    if bank_reasons and insurance_reasons:
        # Conglomerato bancassicurativo: si sceglie il marcatore piu' pesante e lo si
        # dichiara, perche' l'altra meta' dell'azienda resta fuori dalle metriche.
        chosen = BANK if bank_strength >= insurance_strength else INSURANCE
        quality.note(
            "Marcatori bancari e assicurativi entrambi materiali ("
            + "; ".join(bank_reasons + insurance_reasons)
            + f"): applicato il profilo '{chosen}', quello del marcatore piu' pesante."
        )
        return chosen

    if bank_reasons:
        quality.note("Settore rilevato: banca (" + ", ".join(bank_reasons) + ").")
        return BANK

    if insurance_reasons:
        quality.note(
            "Settore rilevato: assicurazione/holding (" + ", ".join(insurance_reasons) + ")."
        )
        return INSURANCE

    # --- Utility regolate ---------------------------------------------------
    # Prima del REIT perche' entrambe hanno molte immobilizzazioni: la voce regolatoria
    # e' un marcatore esplicito e batte l'euristica sull'intensita' di capitale.
    regulatory_share = _weight_share(
        balance_rows, BALANCE_ALIASES["regulatory_assets"],
        balance_rows, BALANCE_ALIASES["total_assets"],
    )
    if regulatory_share is not None and regulatory_share > UTILITY_REGULATORY_SHARE:
        quality.note(
            f"Settore rilevato: utility regolata (attivi regolatori pari a "
            f"{regulatory_share:.1%} del totale attivo). Sono costi che il regolatore ha "
            "autorizzato a recuperare in tariffa: esistono solo dove la tariffa e' "
            "amministrata."
        )
        return UTILITY

    # --- Esplorazione e produzione -----------------------------------------
    exploration_share = _weight_share(
        income_rows, INCOME_ALIASES["exploration_expense"],
        income_rows, INCOME_ALIASES["revenue"],
    )
    if exploration_share is not None and exploration_share > ENERGY_EXPLORATION_SHARE:
        quality.note(
            f"Settore rilevato: esplorazione e produzione (spesa di esplorazione pari a "
            f"{exploration_share:.1%} dei ricavi)."
        )
        return ENERGY

    # --- REIT ---------------------------------------------------------------
    # Si controlla dopo banche e assicurazioni perche' un REIT non ha ne' depositi ne'
    # premi: nessun conflitto possibile.
    property_share = _weight_share(
        balance_rows, BALANCE_ALIASES["real_estate"],
        balance_rows, BALANCE_ALIASES["total_assets"],
    )
    if property_share is not None and property_share > REIT_PROPERTY_SHARE:
        quality.note(
            f"Settore rilevato: REIT/immobiliare (immobili pari a {property_share:.0%} "
            "del totale attivo)."
        )
        return REIT

    # Ripiego euristico per chi non espone una voce immobiliare separata: molte
    # immobilizzazioni **e** molti ammortamenti. Serve la congiunzione, perche' la sola
    # intensita' di capitale descrive anche utility, telecom e industria pesante.
    ppe_share = _weight_share(
        balance_rows, BALANCE_ALIASES["net_ppe"],
        balance_rows, BALANCE_ALIASES["total_assets"],
    )
    da_share = _weight_share(
        cash_rows, CASHFLOW_ALIASES["d_and_a"],
        income_rows, INCOME_ALIASES["revenue"],
    )
    if (
        ppe_share is not None and ppe_share > REIT_PPE_SHARE
        and da_share is not None and da_share > REIT_DA_SHARE
    ):
        quality.note(
            f"Settore rilevato: REIT/immobiliare per via indiretta (immobilizzazioni "
            f"{ppe_share:.0%} dell'attivo, ammortamenti {da_share:.0%} dei ricavi): "
            "nessuna voce immobiliare esplicita in bilancio. Se e' una utility o un "
            "industriale ad alta intensita' di capitale, forzare con --sector industrial."
        )
        return REIT

    return INDUSTRIAL


# ---------------------------------------------------------------------------
# Estrazione delle voci specifiche
# ---------------------------------------------------------------------------


def extract_sector_fundamentals(
    financials: Mapping[str, Any],
    sector: str,
    fundamentals: Dict[int, Dict[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
) -> Dict[int, Dict[str, Optional[float]]]:
    """Arricchisce i fondamentali di base con le voci proprie del settore.

    Modifica ``fundamentals`` sul posto e lo restituisce. Per i profili industriale e
    REIT non fa nulla: le voci servono gia' tutte.
    """
    quality = quality if quality is not None else _DataQuality()
    # Industriali e REIT usano le voci di bilancio ordinarie: il profilo REIT cambia
    # quali metriche si calcolano (FFO invece dell'utile netto), non quali righe servono.
    if sector in (INDUSTRIAL, REIT, UTILITY, ENERGY):
        return fundamentals

    income_rows = _row_index(financials.get("income_statement"))
    balance_rows = _row_index(financials.get("balance_sheet"))

    if sector == BANK:
        income_aliases, balance_aliases = BANK_INCOME_ALIASES, BANK_BALANCE_ALIASES
    else:
        income_aliases, balance_aliases = INSURANCE_INCOME_ALIASES, INSURANCE_BALANCE_ALIASES

    raw: Dict[str, Dict[int, float]] = {}
    for field, aliases in income_aliases.items():
        raw[field], _ = _series_by_year(income_rows, aliases)
    for field, aliases in balance_aliases.items():
        raw[field], _ = _series_by_year(balance_rows, aliases)

    for year, row in fundamentals.items():
        for field, series in raw.items():
            row[field] = series.get(year)

        # Patrimonio netto tangibile: e' il denominatore corretto per il ROTCE, perche'
        # l'avviamento non assorbe perdite in caso di difficolta'.
        equity = row.get("equity")
        if equity is not None:
            goodwill = row.get("goodwill") or 0.0
            intangibles = row.get("intangibles") or 0.0
            if row.get("goodwill") is None and row.get("intangibles") is None:
                row["tangible_equity"] = equity
                quality.estimate(
                    f"{year}: avviamento e immateriali non disponibili, "
                    "patrimonio tangibile assunto pari al patrimonio netto."
                )
            else:
                row["tangible_equity"] = equity - goodwill - intangibles
        else:
            row["tangible_equity"] = None

    if sector == BANK:
        quality.miss(
            "Ratios di vigilanza (CET1, NPL, LCR) non disponibili in questa fonte dati: "
            "al loro posto vengono usati patrimonio/attivo e costo del credito come proxy."
        )
    return fundamentals


# ---------------------------------------------------------------------------
# Calcolo delle metriche per settore
# ---------------------------------------------------------------------------


def _series(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    compute,
) -> Dict[int, Optional[float]]:
    """Applica una funzione riga per riga, restituendo ``{anno: valore}``."""
    output: Dict[int, Optional[float]] = {}
    for year, row in fundamentals.items():
        try:
            output[year] = compute(row)
        except Exception:  # pragma: no cover - difensivo
            output[year] = None
    return output


def _bank_metrics(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: _DataQuality,
) -> Dict[str, Dict[int, Optional[float]]]:
    """Metriche bancarie: redditivita' sul patrimonio, efficienza, funding, rischio."""

    def total_revenue(row: Mapping[str, Optional[float]]) -> Optional[float]:
        """Ricavo bancario = margine di interesse + commissioni e altri ricavi."""
        revenue = row.get("revenue")
        if revenue is not None:
            return revenue
        pieces = [row.get("net_interest_income"), row.get("non_interest_income")]
        if any(piece is not None for piece in pieces):
            return sum(piece for piece in pieces if piece is not None)
        return None

    quality.estimate(
        "NIM approssimato come margine di interesse / totale attivo: il calcolo corretto "
        "usa gli attivi fruttiferi medi, che questa fonte non espone."
    )

    return {
        "roe": _series(fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("equity"), scale=100.0)),
        "roa": _series(fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("total_assets"), scale=100.0)),
        "rotce": _series(fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("tangible_equity"), scale=100.0)),
        "net_interest_margin": _series(
            fundamentals,
            lambda r: _safe_div(r.get("net_interest_income"), r.get("total_assets"), scale=100.0),
        ),
        "efficiency_ratio": _series(
            fundamentals,
            lambda r: _safe_div(r.get("non_interest_expense"), total_revenue(r), scale=100.0),
        ),
        "fee_income_share": _series(
            fundamentals,
            lambda r: _safe_div(r.get("non_interest_income"), total_revenue(r), scale=100.0),
        ),
        "equity_to_assets": _series(
            fundamentals, lambda r: _safe_div(r.get("equity"), r.get("total_assets"), scale=100.0)
        ),
        "loan_to_deposit": _series(
            fundamentals, lambda r: _safe_div(r.get("net_loans"), r.get("total_deposits"))
        ),
        "cost_of_risk": _series(
            fundamentals,
            lambda r: _safe_div(abs(r["credit_provision"]) if r.get("credit_provision") else None,
                                r.get("net_loans"), scale=100.0),
        ),
        "tangible_book_per_share": _series(
            fundamentals, lambda r: _safe_div(r.get("tangible_equity"), r.get("shares_outstanding"))
        ),
        "revenue": _series(fundamentals, total_revenue),
        "net_income": _series(fundamentals, lambda r: r.get("net_income")),
        "total_assets": _series(fundamentals, lambda r: r.get("total_assets")),
    }


def _insurance_metrics(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: _DataQuality,
) -> Dict[str, Dict[int, Optional[float]]]:
    """Metriche assicurative: sottoscrizione, rendimento degli investimenti, patrimonio.

    Il perno e' la **crescita del patrimonio netto per azione**: e' il metro con cui
    Berkshire ha misurato se stessa per decenni, ed e' immune al problema che rende
    l'utile netto inaffidabile per questo settore — dal 2018 le regole contabili
    obbligano a far transitare nel conto economico le plusvalenze *non realizzate* del
    portafoglio titoli, con oscillazioni di miliardi che seguono il mercato e non
    l'andamento del business.
    """
    quality.note(
        "Per assicurazioni e holding l'utile netto include le plusvalenze non realizzate "
        "del portafoglio: la consistenza si basa quindi soprattutto sulla crescita del "
        "patrimonio per azione, non sulla stabilita' dell'utile."
    )

    def combined_ratio(row: Mapping[str, Optional[float]]) -> Optional[float]:
        """(sinistri + spese) / premi. Sotto 100 = utile tecnico di sottoscrizione."""
        premiums = row.get("premiums_earned")
        if not premiums or premiums <= 0:
            return None
        losses = row.get("policy_benefits")
        expenses = row.get("underwriting_expense")
        if losses is None and expenses is None:
            return None
        total = sum(abs(v) for v in (losses, expenses) if v is not None)
        return total / premiums * 100.0

    return {
        "roe": _series(fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("equity"), scale=100.0)),
        "roa": _series(fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("total_assets"), scale=100.0)),
        "rotce": _series(fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("tangible_equity"), scale=100.0)),
        "combined_ratio": _series(fundamentals, combined_ratio),
        "investment_yield": _series(
            fundamentals,
            lambda r: _safe_div(r.get("investment_income"), r.get("total_investments"), scale=100.0),
        ),
        "equity_to_assets": _series(
            fundamentals, lambda r: _safe_div(r.get("equity"), r.get("total_assets"), scale=100.0)
        ),
        "debt_to_equity": _series(
            fundamentals, lambda r: _safe_div(r.get("total_debt"), r.get("equity"))
        ),
        "book_value_per_share": _series(
            fundamentals, lambda r: _safe_div(r.get("equity"), r.get("shares_outstanding"))
        ),
        "tangible_book_per_share": _series(
            fundamentals, lambda r: _safe_div(r.get("tangible_equity"), r.get("shares_outstanding"))
        ),
        "premiums_earned": _series(fundamentals, lambda r: r.get("premiums_earned")),
        "revenue": _series(fundamentals, lambda r: r.get("revenue")),
        "net_income": _series(fundamentals, lambda r: r.get("net_income")),
    }


def _reit_metrics(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: _DataQuality,
) -> Dict[str, Dict[int, Optional[float]]]:
    """Metriche immobiliari: FFO, AFFO, payout, leva sul valore degli immobili.

    Perche' l'utile netto di un REIT non dice niente
    -----------------------------------------------
    L'ammortamento di un immobile e' una finzione contabile. Il principio impone di
    deprezzare un fabbricato su 27,5 o 40 anni, ma un immobile ben tenuto in una buona
    posizione **non perde valore nel tempo**: spesso lo guadagna. L'ammortamento resta
    quindi un costo che non corrisponde ad alcuna uscita e ad alcun logorio economico.

    La conseguenza e' che su un REIT sano l'utile netto puo' essere vicino a zero, e con
    lui ROE, ROA e margine netto. Applicare il profilo industriale a un REIT non produce
    un errore: produce un'azienda che sembra incapace di guadagnare. E' lo stesso
    problema del profilo bancario, con un'altra causa.

    Le due metriche del settore
    ---------------------------
    **FFO** (Funds From Operations, definizione NAREIT) rimette al loro posto le poste
    non economiche::

        FFO = Utile netto
            + Ammortamenti sugli immobili
            - Plusvalenze da cessione di immobili

    Le plusvalenze si escludono perche' vendere un palazzo non e' l'attivita' ricorrente
    di un REIT: gonfierebbe l'anno della vendita e lascerebbe un buco l'anno dopo.

    **AFFO** (Adjusted FFO) toglie dagli FFO quello che serve a tenere in piedi gli
    immobili::

        AFFO = FFO - CapEx

    ed e' la misura che paga il dividendo — l'equivalente per un REIT degli Owner
    Earnings, e per la stessa ragione: sottrae il capitale che l'azienda **deve**
    reinvestire per restare dov'e'.

    Perche' qui il metodo Greenwald non si usa
    ------------------------------------------
    Il profilo industriale separa il CapEx di mantenimento da quello di crescita
    moltiplicando il rapporto immobilizzazioni/ricavi per l'incremento dei ricavi. Su un
    REIT quel rapporto vale **6 o 7** (servono sei euro di immobili per un euro di
    canone annuo) contro lo 0,3-1,0 di un industriale. Il risultato e' che qualunque
    crescita dei ricavi assorbe, sulla carta, piu' CapEx di quanto l'azienda ne spenda:
    il CapEx di mantenimento risulta **zero** e gli AFFO coincidono con gli FFO.

    Non e' un dettaglio di taratura: l'AFFO e' la metrica che dice se il dividendo e'
    coperto, e farla coincidere con gli FFO significa dichiarare coperto un dividendo
    che potrebbe non esserlo. Un AFFO sovrastimato e' piu' pericoloso di uno prudente,
    quindi qui si sottrae il **CapEx totale** — che include lo sviluppo, e va letto per
    quello che e': una stima prudenziale, dichiarata.

    Limiti dichiarati
    -----------------
    L'ammortamento disponibile e' quello **totale** del rendiconto, non la sola quota
    immobiliare: per un REIT la differenza e' minima (gli immobili sono l'attivo), ma
    e' un'approssimazione e viene segnalata. Le plusvalenze da cessione spesso non
    compaiono come voce separata: quando mancano, gli FFO le includono e sono
    sovrastimati negli anni di dismissioni importanti.
    """
    ratios = calculate_balance_sheet_ratios(fundamentals, quality)

    quality.estimate(
        "FFO calcolati con l'ammortamento totale del rendiconto: la sola quota "
        "immobiliare non e' esposta separatamente. Su un REIT la differenza e' minima, "
        "ma resta un'approssimazione."
    )
    quality.estimate(
        "AFFO calcolati sottraendo il CapEx totale: la quota ricorrente non e' esposta "
        "separatamente, e il metodo Greenwald su un REIT darebbe zero (le "
        "immobilizzazioni valgono 6-7 volte i ricavi). Stima prudenziale: chi sta "
        "sviluppando ha AFFO piu' bassi del vero."
    )

    ffo: Dict[int, Optional[float]] = {}
    affo: Dict[int, Optional[float]] = {}
    plusvalenze_assenti = False
    for year, row in fundamentals.items():
        net_income = row.get("net_income")
        d_and_a = row.get("d_and_a")
        if net_income is None or d_and_a is None:
            ffo[year] = affo[year] = None
            if net_income is None:
                quality.miss(f"{year}: utile netto mancante, FFO non calcolabili.")
            else:
                quality.miss(f"{year}: ammortamenti mancanti, FFO non calcolabili.")
            continue
        gain = row.get("gain_on_sale")
        if gain is None:
            plusvalenze_assenti = True
            gain = 0.0
        value = net_income + abs(d_and_a) - gain
        ffo[year] = value
        capex = row.get("capex")
        affo[year] = value - abs(capex) if capex is not None else None

    if plusvalenze_assenti:
        quality.miss(
            "Plusvalenze da cessione di immobili non esposte come voce separata: gli FFO "
            "non le escludono e sono sovrastimati negli anni con dismissioni rilevanti."
        )

    def per_share(series: Mapping[int, Optional[float]]) -> Dict[int, Optional[float]]:
        return {
            year: _safe_div(series.get(year), fundamentals[year].get("shares_outstanding"))
            for year in fundamentals
        }

    return {
        "ffo": dict(ffo),
        "affo": dict(affo),
        "ffo_per_share": per_share(ffo),
        "affo_per_share": per_share(affo),
        "ffo_margin": {
            year: _safe_div(ffo.get(year), fundamentals[year].get("revenue"), scale=100.0)
            for year in fundamentals
        },
        "affo_margin": {
            year: _safe_div(affo.get(year), fundamentals[year].get("revenue"), scale=100.0)
            for year in fundamentals
        },
        # Rendimento degli immobili: gli FFO che il patrimonio investito produce. E'
        # l'equivalente del ROA per chi non puo' usare l'utile netto.
        "ffo_to_assets": {
            year: _safe_div(ffo.get(year), fundamentals[year].get("total_assets"), scale=100.0)
            for year in fundamentals
        },
        # Payout sugli FFO: sopra il 90% il dividendo non ha margine. I REIT americani
        # devono distribuire il 90% del reddito imponibile per mantenere lo status
        # fiscale, quindi un payout alto e' strutturale — ma sugli FFO, non sull'utile.
        "ffo_payout": {
            year: _safe_div(
                abs(fundamentals[year]["dividends_paid"])
                if fundamentals[year].get("dividends_paid") else None,
                ffo.get(year) if (ffo.get(year) or 0) > 0 else None,
                scale=100.0,
            )
            for year in fundamentals
        },
        # Leva sul valore di libro degli immobili: il proxy del loan-to-value, che il
        # settore guarda piu' del Debt/Equity.
        "debt_to_assets": _series(
            fundamentals, lambda r: _safe_div(r.get("total_debt"), r.get("total_assets"), scale=100.0)
        ),
        "debt_to_ebitda": ratios["debt_to_ebitda"],
        "interest_coverage": ratios["interest_coverage"],
        "revenue": _series(fundamentals, lambda r: r.get("revenue")),
        "net_income": _series(fundamentals, lambda r: r.get("net_income")),
        "total_assets": _series(fundamentals, lambda r: r.get("total_assets")),
        "d_and_a": _series(fundamentals, lambda r: abs(r["d_and_a"]) if r.get("d_and_a") else None),
        "capex": _series(fundamentals, lambda r: abs(r["capex"]) if r.get("capex") else None),
    }


def _utility_metrics(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: _DataQuality,
) -> Dict[str, Dict[int, Optional[float]]]:
    """Metriche di una utility regolata: rendimento concesso, rate base, copertura.

    Perche' il ROIC di una utility non misura un vantaggio competitivo
    ------------------------------------------------------------------
    Il rendimento di una utility regolata non lo decide il mercato: lo **fissa il
    regolatore**, che autorizza un rendimento (l'*allowed ROE*, negli Stati Uniti
    tipicamente fra il 9% e il 10,5%) sul capitale investito nella rete — la *rate base*.
    Un ROIC del 6% non e' un segno di debolezza competitiva e un ROIC del 15% non
    sarebbe un moat: sarebbe un'anomalia destinata a essere riportata in tariffa al
    prossimo procedimento.

    Ne segue che le domande cambiano. Non "quanto rende il capitale", che e' deciso
    altrove, ma:

    * il rendimento concesso viene **effettivamente conseguito**? Uno scostamento
      persistente verso il basso segnala costi fuori controllo o un regolatore ostile;
    * la **rate base cresce**? E' l'unica fonte di crescita strutturale degli utili: piu'
      rete in tariffa, piu' utile ammesso. Per questo il CapEx sopra gli ammortamenti e'
      un segnale positivo, l'opposto di quanto valga per un industriale;
    * il debito e' **sostenibile**? Una utility e' finanziata al 50-60% da debito per
      costruzione, e la struttura finanziaria e' approvata dal regolatore: il Debt/Equity
      non dice nulla. Le agenzie di rating guardano **FFO / debito**, ed e' la metrica
      che decide il costo del capitale e quindi, indirettamente, la tariffa.

    Cosa non e' ricostruibile
    -------------------------
    L'*allowed ROE* e la rate base **vera** vivono nei procedimenti tariffari, non nei
    bilanci. Qui la rate base e' approssimata dalle **immobilizzazioni nette**, che ne
    sono la componente dominante ma non l'identica: la rate base esclude i lavori in
    corso non ancora in tariffa e include il capitale circolante autorizzato. Il
    rendimento ammesso non c'e' affatto, quindi il modello misura il ROE **conseguito**
    e la sua stabilita', non lo scostamento dal concesso.
    """
    ratios = calculate_balance_sheet_ratios(fundamentals, quality)

    quality.miss(
        "Rendimento ammesso dal regolatore (allowed ROE) e rate base ufficiale non "
        "disponibili: vivono nei procedimenti tariffari. Il modello misura il ROE "
        "conseguito e usa le immobilizzazioni nette come proxy della rate base."
    )

    def ffo(row: Mapping[str, Optional[float]]) -> Optional[float]:
        """Utile netto + ammortamenti: la definizione che usano le agenzie di rating."""
        net_income = row.get("net_income")
        d_and_a = row.get("d_and_a")
        if net_income is None or d_and_a is None:
            return None
        return net_income + abs(d_and_a)

    funds = _series(fundamentals, ffo)

    return {
        "roe": _series(fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("equity"), scale=100.0)),
        "roa": _series(fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("total_assets"), scale=100.0)),
        # Rendimento sugli asset in tariffa: il proxy del rendimento sulla rate base.
        "return_on_rate_base": _series(
            fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("net_ppe"), scale=100.0)
        ),
        "operating_margin": _series(
            fundamentals, lambda r: _safe_div(r.get("operating_income"), r.get("revenue"), scale=100.0)
        ),
        "ffo": funds,
        "ffo_margin": {
            year: _safe_div(funds.get(year), fundamentals[year].get("revenue"), scale=100.0)
            for year in fundamentals
        },
        # La metrica delle agenzie: sopra il 20% una utility e' solida, sotto il 13% e'
        # sotto pressione. Decide il rating, e quindi il costo del capitale.
        "ffo_to_debt": {
            year: _safe_div(funds.get(year), fundamentals[year].get("total_debt"), scale=100.0)
            for year in fundamentals
        },
        # Sopra 1 la rate base cresce: per una utility e' un segnale positivo, al
        # contrario di quanto valga per un industriale.
        "capex_to_depreciation": _series(
            fundamentals,
            lambda r: _safe_div(
                abs(r["capex"]) if r.get("capex") else None,
                abs(r["d_and_a"]) if r.get("d_and_a") else None,
            ),
        ),
        # La struttura finanziaria e' approvata dal regolatore: si guarda il peso del
        # debito sul capitale totale, non il Debt/Equity.
        "debt_to_capital": _series(
            fundamentals,
            lambda r: _safe_div(
                r.get("total_debt"),
                (r["total_debt"] + r["equity"])
                if r.get("total_debt") is not None and r.get("equity") is not None else None,
                scale=100.0,
            ),
        ),
        "debt_to_ebitda": ratios["debt_to_ebitda"],
        "interest_coverage": ratios["interest_coverage"],
        "rate_base": _series(fundamentals, lambda r: r.get("net_ppe")),
        "revenue": _series(fundamentals, lambda r: r.get("revenue")),
        "net_income": _series(fundamentals, lambda r: r.get("net_income")),
        "capex": _series(fundamentals, lambda r: abs(r["capex"]) if r.get("capex") else None),
    }


def _energy_metrics(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    quality: _DataQuality,
) -> Dict[str, Dict[int, Optional[float]]]:
    """Metriche di un'azienda di esplorazione e produzione.

    Perche' l'utile di un E&P non dice quasi nulla
    ---------------------------------------------
    Due ragioni, e agiscono insieme.

    La prima e' il **prezzo**. Un produttore di idrocarburi non ha potere sul prezzo di
    cio' che vende: l'utile dell'anno misura in buona parte dove stava il Brent, non come
    e' stata gestita l'azienda. Normalizzare su un anno di picco produce un fair value che
    si sgonfia da solo, e su un anno di minimo l'opposto.

    La seconda e' che **l'attivo si consuma**. Un barile estratto e' un barile che non
    c'e' piu': il conto economico registra il *depletion*, la quota di riserve esaurita.
    Un E&P che non rimpiazza cio' che produce sta liquidando se stesso, e puo' farlo per
    anni mostrando utili eccellenti — sono proprio gli utili della liquidazione.

    Le metriche del settore, e quali sono ricostruibili
    --------------------------------------------------
    Il metro vero e' nelle **riserve**: riserve provate, vita residua (R/P), tasso di
    rimpiazzo, costo di ritrovamento e sviluppo per barile, e il **PV-10**, il valore
    attuale scontato al 10% dei flussi futuri delle riserve, che le societa' americane
    devono pubblicare nel 10-K. Nessuno di questi numeri sta nei tre prospetti: vivono
    nelle tabelle supplementari, e questo modello li vede solo attraverso i depositi
    XBRL (``--sec``). Quando non ci sono, il profilo lo dichiara e resta su cio' che il
    bilancio contiene:

    * **EBITDAX** — EBITDA piu' la spesa di esplorazione. Serve perche' chi spesa
      l'esplorazione e chi la capitalizza (*successful efforts* contro *full cost*) non
      sono confrontabili sull'EBITDA, e la differenza e' solo una scelta contabile;
    * **margine di cassa** e **flusso operativo sui ricavi**, che il prezzo sposta ma non
      falsifica;
    * **Debt/EBITDAX**, la misura di leva che il settore usa davvero;
    * **CapEx / flusso operativo** — sopra 1 l'azienda sta finanziando la sostituzione
      delle riserve con debito o con emissioni.
    """
    ratios = calculate_balance_sheet_ratios(fundamentals, quality)

    quality.miss(
        "Riserve provate, produzione, tasso di rimpiazzo e PV-10 non sono nei prospetti "
        "finanziari: vivono nelle tabelle supplementari del 10-K. Senza di essi il "
        "profilo misura la generazione di cassa e la leva, non il valore delle riserve — "
        "che per un E&P e' l'attivo principale."
    )

    def ebitdax(row: Mapping[str, Optional[float]]) -> Optional[float]:
        """EBITDA + esplorazione: neutralizza la scelta fra successful efforts e full cost."""
        ebitda = row.get("ebitda")
        if ebitda is None:
            return None
        exploration = row.get("exploration_expense")
        return ebitda + abs(exploration) if exploration is not None else ebitda

    values = _series(fundamentals, ebitdax)

    return {
        "roe": _series(fundamentals, lambda r: _safe_div(r.get("net_income"), r.get("equity"), scale=100.0)),
        "roic": _series(
            fundamentals,
            lambda r: _safe_div(
                r["ebit"] * (1.0 - (r.get("tax_rate") or 0.25)) if r.get("ebit") is not None else None,
                r.get("invested_capital_calc"),
                scale=100.0,
            ),
        ),
        "ebitdax": values,
        "ebitdax_margin": {
            year: _safe_div(values.get(year), fundamentals[year].get("revenue"), scale=100.0)
            for year in fundamentals
        },
        "operating_cash_margin": _series(
            fundamentals,
            lambda r: _safe_div(r.get("operating_cash_flow"), r.get("revenue"), scale=100.0),
        ),
        # Sopra 1 la sostituzione delle riserve non si autofinanzia.
        "capex_to_cash_flow": _series(
            fundamentals,
            lambda r: _safe_div(
                abs(r["capex"]) if r.get("capex") else None, r.get("operating_cash_flow")
            ),
        ),
        "debt_to_ebitdax": {
            year: _safe_div(fundamentals[year].get("total_debt"), values.get(year))
            for year in fundamentals
        },
        "debt_to_equity": ratios["debt_to_equity"],
        "interest_coverage": ratios["interest_coverage"],
        "exploration_intensity": _series(
            fundamentals,
            lambda r: _safe_div(
                abs(r["exploration_expense"]) if r.get("exploration_expense") else None,
                r.get("revenue"), scale=100.0,
            ),
        ),
        "revenue": _series(fundamentals, lambda r: r.get("revenue")),
        "net_income": _series(fundamentals, lambda r: r.get("net_income")),
        "operating_cash_flow": _series(fundamentals, lambda r: r.get("operating_cash_flow")),
        "capex": _series(fundamentals, lambda r: abs(r["capex"]) if r.get("capex") else None),
    }


def build_metrics(
    fundamentals: Mapping[int, Mapping[str, Optional[float]]],
    sector: str,
    quality: Optional[_DataQuality] = None,
) -> Dict[str, Dict[int, Optional[float]]]:
    """Serie annuali delle metriche proprie del settore (esclude il profilo industriale)."""
    quality = quality if quality is not None else _DataQuality()
    if sector == BANK:
        return _bank_metrics(fundamentals, quality)
    if sector == INSURANCE:
        return _insurance_metrics(fundamentals, quality)
    if sector == REIT:
        return _reit_metrics(fundamentals, quality)
    if sector == UTILITY:
        return _utility_metrics(fundamentals, quality)
    if sector == ENERGY:
        return _energy_metrics(fundamentals, quality)
    raise ValueError(f"build_metrics non si applica al settore '{sector}'.")


# ---------------------------------------------------------------------------
# Profili: soglie, pesi, tabelle
# ---------------------------------------------------------------------------
#
# Ogni componente dichiara da dove legge il valore:
#   ("average", <metrica>)                      -> media della serie
#   ("consistency", <metrica>, <statistica>)    -> statistica di consistenza
# e le due soglie di normalizzazione: `low` -> 0 punti, `high` -> 100 punti.
# Quando `low > high` la scala e' invertita (meno e' meglio).

PROFILES: Dict[str, Dict[str, Any]] = {
    INDUSTRIAL: {
        "label": "Azienda operativa",
        "categories": {
            "profitability": {
                "label": "Profittabilita' / ROIC",
                "components": {
                    "roic": {"label": "ROIC medio (%)", "source": ("average", "roic"),
                             "low": 4.0, "high": 25.0, "weight": 0.35},
                    "roe": {"label": "ROE medio (%)", "source": ("average", "roe"),
                            "low": 5.0, "high": 25.0, "weight": 0.15},
                    "roa": {"label": "ROA medio (%)", "source": ("average", "roa"),
                            "low": 1.0, "high": 12.0, "weight": 0.15},
                    "operating_margin": {"label": "Margine operativo medio (%)",
                                         "source": ("average", "operating_margin"),
                                         "low": 3.0, "high": 25.0, "weight": 0.15},
                    "net_margin": {"label": "Margine netto medio (%)",
                                   "source": ("average", "net_margin"),
                                   "low": 2.0, "high": 20.0, "weight": 0.10},
                    "owner_earnings_margin": {"label": "Owner Earnings / Ricavi medio (%)",
                                              "source": ("average", "owner_earnings_margin"),
                                              "low": 2.0, "high": 18.0, "weight": 0.10},
                },
            },
            "consistency": {
                "label": "Consistenza",
                "components": {
                    "roic_stability": {"label": "Coeff. di variazione ROIC",
                                       "source": ("consistency", "roic", "coefficient_of_variation"),
                                       "low": 0.60, "high": 0.05, "weight": 0.30},
                    "margin_stability": {"label": "Coeff. di variazione margine netto",
                                         "source": ("consistency", "net_margin", "coefficient_of_variation"),
                                         "low": 0.60, "high": 0.05, "weight": 0.20},
                    "revenue_growth_years": {"label": "Anni di crescita dei ricavi (%)",
                                             "source": ("consistency", "revenue", "growth_years_pct"),
                                             "low": 40.0, "high": 100.0, "weight": 0.20},
                    "owner_earnings_growth_years": {"label": "Anni di crescita degli Owner Earnings (%)",
                                                    "source": ("consistency", "owner_earnings", "growth_years_pct"),
                                                    "low": 40.0, "high": 100.0, "weight": 0.15},
                    "profitable_years": {"label": "Anni con utile netto positivo (%)",
                                         "source": ("consistency", "net_income", "positive_years_pct"),
                                         "low": 60.0, "high": 100.0, "weight": 0.15},
                },
            },
            "balance_sheet": {
                "label": "Solidita' di bilancio",
                "components": {
                    "debt_to_equity": {"label": "Debt/Equity medio",
                                       "source": ("average", "debt_to_equity"),
                                       "low": 2.50, "high": 0.10, "weight": 0.30},
                    "debt_to_ebitda": {"label": "Debt/EBITDA medio",
                                       "source": ("average", "debt_to_ebitda"),
                                       "low": 4.00, "high": 0.50, "weight": 0.30},
                    "interest_coverage": {"label": "Interest Coverage medio",
                                          "source": ("average", "interest_coverage"),
                                          "low": 2.0, "high": 15.0, "weight": 0.25},
                    "current_ratio": {"label": "Current Ratio medio",
                                      "source": ("average", "current_ratio"),
                                      "low": 0.80, "high": 2.00, "weight": 0.15},
                },
            },
        },
    },

    BANK: {
        "label": "Banca",
        "categories": {
            "profitability": {
                "label": "Redditivita' del capitale",
                "components": {
                    # Il ROTCE e' la metrica principale per una banca: rendimento sul
                    # capitale che assorbe davvero le perdite, avviamento escluso.
                    "rotce": {"label": "ROTCE medio (%)", "source": ("average", "rotce"),
                              "low": 6.0, "high": 18.0, "weight": 0.35},
                    "roe": {"label": "ROE medio (%)", "source": ("average", "roe"),
                            "low": 5.0, "high": 15.0, "weight": 0.20},
                    # Attenzione alla scala: l'1% di ROA per una banca e' buono, per un
                    # industriale sarebbe pessimo. E' la leva di ~10x a fare la differenza.
                    "roa": {"label": "ROA medio (%)", "source": ("average", "roa"),
                            "low": 0.40, "high": 1.50, "weight": 0.20},
                    "efficiency_ratio": {"label": "Cost/Income medio (%)",
                                         "source": ("average", "efficiency_ratio"),
                                         "low": 75.0, "high": 50.0, "weight": 0.15},
                    "net_interest_margin": {"label": "Margine di interesse / attivo (%)",
                                            "source": ("average", "net_interest_margin"),
                                            "low": 1.20, "high": 3.50, "weight": 0.10},
                },
            },
            "consistency": {
                "label": "Consistenza",
                "components": {
                    "rotce_stability": {"label": "Coeff. di variazione ROTCE",
                                        "source": ("consistency", "rotce", "coefficient_of_variation"),
                                        "low": 0.50, "high": 0.05, "weight": 0.30},
                    "tbvps_growth_years": {"label": "Anni di crescita del patrimonio tangibile/azione (%)",
                                           "source": ("consistency", "tangible_book_per_share", "growth_years_pct"),
                                           "low": 40.0, "high": 100.0, "weight": 0.25},
                    "profitable_years": {"label": "Anni con utile positivo (%)",
                                         "source": ("consistency", "net_income", "positive_years_pct"),
                                         "low": 60.0, "high": 100.0, "weight": 0.25},
                    # Ricavi commissionali alti = utili meno sensibili ai tassi.
                    "fee_income_share": {"label": "Quota di ricavi commissionali (%)",
                                         "source": ("average", "fee_income_share"),
                                         "low": 15.0, "high": 50.0, "weight": 0.20},
                },
            },
            "balance_sheet": {
                "label": "Capitale e rischio di credito",
                "components": {
                    # Proxy della leva: NON e' il CET1, che questa fonte dati non espone.
                    # Le soglie vanno lette su questa differenza. Il CET1 divide per gli
                    # attivi **ponderati per il rischio**, che per una banca commerciale
                    # sono circa la meta' del totale attivo: un titolo di Stato pesa zero,
                    # un mutuo garantito il 35%. Quindi 12% di patrimonio sul totale
                    # attivo corrisponderebbe a un CET1 intorno al 24%, un livello che
                    # nessuna grande banca ha ne' cerca. JPMorgan sta all'8.6% di
                    # patrimonio sull'attivo con un CET1 del ~15%, cioe' ampiamente sopra
                    # i requisiti: con la vecchia scala (5 -> 0, 12 -> 100) prendeva 51,
                    # un mediocre. La scala corretta e' 5% -> 0, 9% -> 100.
                    "equity_to_assets": {"label": "Patrimonio / attivo (%)",
                                         "source": ("average", "equity_to_assets"),
                                         "low": 5.0, "high": 9.0, "weight": 0.40},
                    # Sotto 1 significa finanziata dai depositi, non dal mercato: e' la
                    # differenza fra una raccolta stabile e una che evapora in una crisi.
                    "loan_to_deposit": {"label": "Impieghi / depositi",
                                        "source": ("average", "loan_to_deposit"),
                                        "low": 1.10, "high": 0.70, "weight": 0.30},
                    "cost_of_risk": {"label": "Costo del credito (%)",
                                     "source": ("average", "cost_of_risk"),
                                     "low": 1.50, "high": 0.20, "weight": 0.30},
                },
            },
        },
    },

    INSURANCE: {
        "label": "Assicurazione / holding",
        "categories": {
            "profitability": {
                "label": "Redditivita' e sottoscrizione",
                "components": {
                    # Sotto 100 l'assicurazione guadagna sull'attivita' tecnica, prima
                    # ancora dei rendimenti finanziari: e' il moat di Berkshire.
                    "combined_ratio": {"label": "Combined ratio medio",
                                       "source": ("average", "combined_ratio"),
                                       "low": 105.0, "high": 88.0, "weight": 0.30},
                    "rotce": {"label": "ROTCE medio (%)", "source": ("average", "rotce"),
                              "low": 5.0, "high": 15.0, "weight": 0.30},
                    "roe": {"label": "ROE medio (%)", "source": ("average", "roe"),
                            "low": 4.0, "high": 14.0, "weight": 0.25},
                    "investment_yield": {"label": "Rendimento degli investimenti (%)",
                                         "source": ("average", "investment_yield"),
                                         "low": 1.5, "high": 5.0, "weight": 0.15},
                },
            },
            "consistency": {
                "label": "Consistenza",
                "components": {
                    # Il metro storico di Berkshire, e l'unico immune al rumore
                    # contabile delle plusvalenze non realizzate.
                    "bvps_growth_years": {"label": "Anni di crescita del patrimonio/azione (%)",
                                          "source": ("consistency", "book_value_per_share", "growth_years_pct"),
                                          "low": 50.0, "high": 100.0, "weight": 0.35},
                    "profitable_years": {"label": "Anni con utile positivo (%)",
                                         "source": ("consistency", "net_income", "positive_years_pct"),
                                         "low": 70.0, "high": 100.0, "weight": 0.25},
                    "combined_ratio_stability": {"label": "Coeff. di variazione combined ratio",
                                                 "source": ("consistency", "combined_ratio", "coefficient_of_variation"),
                                                 "low": 0.25, "high": 0.02, "weight": 0.25},
                    # Soglia molto piu' larga che per gli industriali: qui l'oscillazione
                    # dell'utile e' in buona parte rumore contabile, non instabilita' vera.
                    "roe_stability": {"label": "Coeff. di variazione ROE",
                                      "source": ("consistency", "roe", "coefficient_of_variation"),
                                      "low": 0.90, "high": 0.15, "weight": 0.15},
                },
            },
            "balance_sheet": {
                "label": "Solidita' patrimoniale",
                "components": {
                    "equity_to_assets": {"label": "Patrimonio / attivo (%)",
                                         "source": ("average", "equity_to_assets"),
                                         "low": 10.0, "high": 35.0, "weight": 0.50},
                    # A differenza dei depositi bancari, qui il debito e' finanziamento
                    # vero e il rapporto con il patrimonio ha significato.
                    "debt_to_equity": {"label": "Debt/Equity medio",
                                       "source": ("average", "debt_to_equity"),
                                       "low": 1.00, "high": 0.10, "weight": 0.50},
                },
            },
        },
    },

    REIT: {
        "label": "REIT / immobiliare",
        "categories": {
            "profitability": {
                "label": "Redditivita' degli immobili",
                "components": {
                    # Il margine sugli FFO e' il metro primario: quanta parte del canone
                    # incassato resta dopo i costi di gestione e gli interessi.
                    "ffo_margin": {"label": "FFO / ricavi medio (%)",
                                   "source": ("average", "ffo_margin"),
                                   "low": 20.0, "high": 55.0, "weight": 0.30},
                    # AFFO: come sopra, ma al netto del capitale che serve a mantenere
                    # gli immobili. E' cio' che paga davvero il dividendo.
                    "affo_margin": {"label": "AFFO / ricavi medio (%)",
                                    "source": ("average", "affo_margin"),
                                    "low": 15.0, "high": 45.0, "weight": 0.30},
                    # Rendimento del patrimonio investito, l'equivalente del ROA per chi
                    # non puo' usare l'utile netto.
                    "ffo_to_assets": {"label": "FFO / totale attivo (%)",
                                      "source": ("average", "ffo_to_assets"),
                                      "low": 3.0, "high": 9.0, "weight": 0.25},
                    # Sopra il 90% il dividendo non ha margine di sicurezza. Scala
                    # inversa: meno e' meglio.
                    "ffo_payout": {"label": "Dividendi / FFO medio (%)",
                                   "source": ("average", "ffo_payout"),
                                   "low": 100.0, "high": 65.0, "weight": 0.15},
                },
            },
            "consistency": {
                "label": "Consistenza",
                "components": {
                    # Il metro con cui il settore misura se stesso: gli FFO per azione
                    # che crescono anno dopo anno.
                    "ffops_growth_years": {"label": "Anni di crescita degli FFO/azione (%)",
                                           "source": ("consistency", "ffo_per_share", "growth_years_pct"),
                                           "low": 40.0, "high": 100.0, "weight": 0.30},
                    "affo_stability": {"label": "Coeff. di variazione margine AFFO",
                                       "source": ("consistency", "affo_margin", "coefficient_of_variation"),
                                       "low": 0.40, "high": 0.05, "weight": 0.25},
                    "positive_ffo_years": {"label": "Anni con FFO positivi (%)",
                                           "source": ("consistency", "ffo", "positive_years_pct"),
                                           "low": 70.0, "high": 100.0, "weight": 0.25},
                    "revenue_growth_years": {"label": "Anni di crescita dei ricavi (%)",
                                             "source": ("consistency", "revenue", "growth_years_pct"),
                                             "low": 40.0, "high": 100.0, "weight": 0.20},
                },
            },
            "balance_sheet": {
                "label": "Leva e copertura",
                "components": {
                    # Proxy del loan-to-value: il settore guarda questo, non il
                    # Debt/Equity, perche' il patrimonio contabile e' deprezzato da
                    # ammortamenti che non riflettono il valore degli immobili.
                    "debt_to_assets": {"label": "Debito / totale attivo (%)",
                                       "source": ("average", "debt_to_assets"),
                                       "low": 60.0, "high": 30.0, "weight": 0.40},
                    # Il covenant piu' comune nel settore: sotto 6x e' considerato sano.
                    "debt_to_ebitda": {"label": "Debt/EBITDA medio",
                                       "source": ("average", "debt_to_ebitda"),
                                       "low": 8.00, "high": 4.50, "weight": 0.35},
                    "interest_coverage": {"label": "Interest Coverage medio",
                                          "source": ("average", "interest_coverage"),
                                          "low": 1.80, "high": 5.00, "weight": 0.25},
                },
            },
        },
    },

    UTILITY: {
        "label": "Utility regolata",
        "categories": {
            "profitability": {
                "label": "Rendimento concesso",
                "components": {
                    # Il ROE e' il numero che il regolatore fissa: le soglie sono quelle
                    # dei rendimenti ammessi tipici (9-10,5% negli Stati Uniti), non
                    # quelle di un industriale.
                    "roe": {"label": "ROE medio (%)", "source": ("average", "roe"),
                            "low": 6.0, "high": 12.0, "weight": 0.35},
                    # Rendimento sugli asset in tariffa. Con un ROE del 10% e una
                    # struttura 50/50 il rapporto utile/immobilizzazioni sta intorno al
                    # 4-5%: le soglie sono tarate su quello.
                    "return_on_rate_base": {"label": "Utile / rate base (%)",
                                            "source": ("average", "return_on_rate_base"),
                                            "low": 2.0, "high": 6.0, "weight": 0.25},
                    "operating_margin": {"label": "Margine operativo medio (%)",
                                         "source": ("average", "operating_margin"),
                                         "low": 10.0, "high": 28.0, "weight": 0.20},
                    "ffo_margin": {"label": "FFO / ricavi medio (%)",
                                   "source": ("average", "ffo_margin"),
                                   "low": 15.0, "high": 35.0, "weight": 0.20},
                },
            },
            "consistency": {
                "label": "Stabilita' e crescita della rate base",
                "components": {
                    # Per una utility la stabilita' non e' una virtu' fra le altre: e'
                    # la ragione per cui la si compra. La soglia e' piu' severa che per
                    # un industriale.
                    "roe_stability": {"label": "Coeff. di variazione ROE",
                                      "source": ("consistency", "roe", "coefficient_of_variation"),
                                      "low": 0.35, "high": 0.05, "weight": 0.30},
                    # Piu' rete in tariffa, piu' utile ammesso: e' l'unica crescita
                    # strutturale che una utility ha.
                    "rate_base_growth_years": {"label": "Anni di crescita della rate base (%)",
                                               "source": ("consistency", "rate_base", "growth_years_pct"),
                                               "low": 40.0, "high": 100.0, "weight": 0.30},
                    "profitable_years": {"label": "Anni con utile positivo (%)",
                                         "source": ("consistency", "net_income", "positive_years_pct"),
                                         "low": 80.0, "high": 100.0, "weight": 0.20},
                    "revenue_stability": {"label": "Coeff. di variazione ricavi",
                                          "source": ("consistency", "revenue", "coefficient_of_variation"),
                                          "low": 0.30, "high": 0.05, "weight": 0.20},
                },
            },
            "balance_sheet": {
                "label": "Sostenibilita' del debito",
                "components": {
                    # La metrica che decide il rating, e quindi il costo del capitale e
                    # indirettamente la tariffa. Sopra il 20% solida, sotto il 13%
                    # sotto pressione.
                    "ffo_to_debt": {"label": "FFO / debito (%)",
                                    "source": ("average", "ffo_to_debt"),
                                    "low": 10.0, "high": 25.0, "weight": 0.40},
                    # La struttura finanziaria e' approvata dal regolatore: 50-60% di
                    # debito e' normale, e il Debt/Equity non direbbe nulla.
                    "debt_to_capital": {"label": "Debito / capitale (%)",
                                        "source": ("average", "debt_to_capital"),
                                        "low": 70.0, "high": 45.0, "weight": 0.30},
                    "interest_coverage": {"label": "Interest Coverage medio",
                                          "source": ("average", "interest_coverage"),
                                          "low": 2.0, "high": 5.0, "weight": 0.30},
                },
            },
        },
    },

    ENERGY: {
        "label": "Esplorazione e produzione",
        "categories": {
            "profitability": {
                "label": "Generazione di cassa",
                "components": {
                    # EBITDAX invece dell'EBITDA: chi spesa l'esplorazione e chi la
                    # capitalizza non sono confrontabili, e la differenza e' contabile.
                    "ebitdax_margin": {"label": "EBITDAX / ricavi medio (%)",
                                       "source": ("average", "ebitdax_margin"),
                                       "low": 20.0, "high": 55.0, "weight": 0.35},
                    "operating_cash_margin": {"label": "Flusso operativo / ricavi (%)",
                                              "source": ("average", "operating_cash_margin"),
                                              "low": 15.0, "high": 45.0, "weight": 0.30},
                    # Il ROIC su un ciclico va letto sulla media del ciclo, ed e' per
                    # questo che entra con soglie larghe e peso contenuto.
                    "roic": {"label": "ROIC medio di ciclo (%)",
                             "source": ("average", "roic"),
                             "low": 2.0, "high": 18.0, "weight": 0.20},
                    "roe": {"label": "ROE medio (%)", "source": ("average", "roe"),
                            "low": 2.0, "high": 20.0, "weight": 0.15},
                },
            },
            "consistency": {
                "label": "Tenuta nel ciclo",
                "components": {
                    # Su un ciclico la domanda non e' se l'utile oscilla — oscilla per
                    # forza — ma se resta positivo anche in fondo al ciclo.
                    "profitable_years": {"label": "Anni con utile positivo (%)",
                                         "source": ("consistency", "net_income", "positive_years_pct"),
                                         "low": 50.0, "high": 100.0, "weight": 0.35},
                    "cash_flow_stability": {"label": "Coeff. di variazione flusso operativo",
                                            "source": ("consistency", "operating_cash_flow",
                                                       "coefficient_of_variation"),
                                            "low": 0.80, "high": 0.20, "weight": 0.25},
                    # Il margine di cassa dice quanto e' efficiente l'estrazione, e a
                    # differenza dell'utile non e' azzerato dalle svalutazioni.
                    "margin_stability": {"label": "Coeff. di variazione margine EBITDAX",
                                         "source": ("consistency", "ebitdax_margin",
                                                    "coefficient_of_variation"),
                                         "low": 0.60, "high": 0.15, "weight": 0.20},
                    "revenue_growth_years": {"label": "Anni di crescita dei ricavi (%)",
                                             "source": ("consistency", "revenue", "growth_years_pct"),
                                             "low": 30.0, "high": 80.0, "weight": 0.20},
                },
            },
            "balance_sheet": {
                "label": "Leva e autofinanziamento",
                "components": {
                    # La leva su un ciclico va misurata larga: il denominatore crolla
                    # proprio quando serve.
                    "debt_to_ebitdax": {"label": "Debt / EBITDAX medio",
                                        "source": ("average", "debt_to_ebitdax"),
                                        "low": 3.50, "high": 0.80, "weight": 0.40},
                    # Sopra 1 la sostituzione delle riserve e' finanziata da debito o da
                    # emissioni: e' il modo in cui un E&P si consuma senza sembrarlo.
                    "capex_to_cash_flow": {"label": "CapEx / flusso operativo",
                                           "source": ("average", "capex_to_cash_flow"),
                                           "low": 1.30, "high": 0.60, "weight": 0.35},
                    "interest_coverage": {"label": "Interest Coverage medio",
                                          "source": ("average", "interest_coverage"),
                                          "low": 2.0, "high": 12.0, "weight": 0.25},
                },
            },
        },
    },
}

#: Come stimare il CapEx **di mantenimento** in ciascun profilo.
#:
#: Il metodo Greenwald (immobilizzazioni/ricavi x incremento dei ricavi) e' pensato per
#: aziende in cui quel rapporto vale 0,3-1,0. Sopra quel livello si rompe, e si rompe
#: sempre nella stessa direzione: attribuisce alla crescita piu' CapEx di quanto
#: l'azienda ne spenda, il mantenimento risulta troppo basso o nullo, e gli Owner
#: Earnings escono **gonfiati**. Misurato sui bilanci di prova:
#:
#: ===========  ==============  ==================  ===================
#: profilo      PPE / ricavi    mant. Greenwald     riferimento
#: ===========  ==============  ==================  ===================
#: industriale  0,3 - 1,0       attendibile         --
#: utility      2,75            314 su 704          ammortamenti
#: E&P          1,89            **zero** su 900     CapEx totale
#: REIT         6 - 7           **zero**            CapEx totale
#: ===========  ==============  ==================  ===================
#:
#: Le convenzioni alternative non sono ripieghi generici, hanno un senso di settore:
#:
#: ``"depreciation"`` (utility) — una rete si rinnova al ritmo del proprio deprezzamento;
#:     quello che si spende in piu' entra in tariffa e produce utile aggiuntivo, quindi e'
#:     crescita vera e va tenuta fuori dal mantenimento;
#: ``"total"`` (E&P, REIT) — per un E&P il CapEx serve a rimpiazzare le riserve prodotte,
#:     e chi ne spende meno sta liquidando l'azienda mostrando utili; per un REIT vale il
#:     ragionamento gia' fatto sugli AFFO. In entrambi i casi la stima e' prudenziale e
#:     va dichiarata.
MAINTENANCE_CAPEX_RULE: Dict[str, str] = {
    INDUSTRIAL: "greenwald",
    UTILITY: "depreciation",
    ENERGY: "total",
    REIT: "total",
}


#: Metriche su cui calcolare le statistiche di consistenza, per settore.
CONSISTENCY_TARGETS: Dict[str, Tuple[str, ...]] = {
    INDUSTRIAL: ("roic", "roe", "roa", "operating_margin", "net_margin",
                 "revenue", "net_income", "owner_earnings"),
    BANK: ("rotce", "roe", "roa", "efficiency_ratio", "net_interest_margin",
           "tangible_book_per_share", "revenue", "net_income"),
    INSURANCE: ("roe", "rotce", "combined_ratio", "book_value_per_share",
                "premiums_earned", "net_income", "investment_yield"),
    REIT: ("ffo", "affo", "ffo_per_share", "affo_per_share", "ffo_margin",
           "affo_margin", "ffo_to_assets", "revenue", "net_income"),
    UTILITY: ("roe", "return_on_rate_base", "operating_margin", "ffo_margin",
              "ffo_to_debt", "rate_base", "revenue", "net_income"),
    ENERGY: ("ebitdax_margin", "operating_cash_margin", "roic", "roe",
             "operating_cash_flow", "revenue", "net_income"),
}

#: Metriche di cui calcolare la media, per settore.
AVERAGE_TARGETS: Dict[str, Tuple[str, ...]] = {
    INDUSTRIAL: ("roic", "roe", "roa", "operating_margin", "net_margin", "gross_margin",
                 "owner_earnings", "owner_earnings_margin",
                 "debt_to_equity", "debt_to_ebitda", "interest_coverage", "current_ratio"),
    BANK: ("rotce", "roe", "roa", "net_interest_margin", "efficiency_ratio",
           "fee_income_share", "equity_to_assets", "loan_to_deposit", "cost_of_risk",
           "tangible_book_per_share"),
    INSURANCE: ("roe", "roa", "rotce", "combined_ratio", "investment_yield",
                "equity_to_assets", "debt_to_equity", "book_value_per_share",
                "tangible_book_per_share"),
    REIT: ("ffo", "affo", "ffo_per_share", "affo_per_share", "ffo_margin", "affo_margin",
           "ffo_to_assets", "ffo_payout", "debt_to_assets", "debt_to_ebitda",
           "interest_coverage"),
    UTILITY: ("roe", "roa", "return_on_rate_base", "operating_margin", "ffo_margin",
              "ffo_to_debt", "capex_to_depreciation", "debt_to_capital", "debt_to_ebitda",
              "interest_coverage"),
    ENERGY: ("ebitdax_margin", "operating_cash_margin", "roic", "roe",
             "capex_to_cash_flow", "debt_to_ebitdax", "debt_to_equity",
             "interest_coverage", "exploration_intensity"),
}

#: Righe della tabella anno per anno nel report: (etichetta, metrica, formato).
TABLE_ROWS: Dict[str, Tuple[Tuple[str, str, str], ...]] = {
    INDUSTRIAL: (
        ("Ricavi", "revenue", "big"), ("Utile netto", "net_income", "big"),
        ("Owner Earnings", "owner_earnings", "big"), ("ROIC %", "roic", "pct"),
        ("ROE %", "roe", "pct"), ("ROA %", "roa", "pct"),
        ("Margine operativo %", "operating_margin", "pct"),
        ("Margine netto %", "net_margin", "pct"),
        ("Owner Earn. / Ricavi %", "owner_earnings_margin", "pct"),
        ("Debt / Equity", "debt_to_equity", "ratio"),
        ("Debt / EBITDA", "debt_to_ebitda", "ratio"),
        ("Interest Coverage", "interest_coverage", "ratio"),
        ("Current Ratio", "current_ratio", "ratio"),
    ),
    BANK: (
        ("Ricavi totali", "revenue", "big"), ("Utile netto", "net_income", "big"),
        ("Totale attivo", "total_assets", "big"),
        ("ROTCE %", "rotce", "pct"), ("ROE %", "roe", "pct"), ("ROA %", "roa", "pct"),
        ("Margine interesse %", "net_interest_margin", "pct"),
        ("Cost / Income %", "efficiency_ratio", "pct"),
        ("Commissioni / ricavi %", "fee_income_share", "pct"),
        ("Patrimonio / attivo %", "equity_to_assets", "pct"),
        ("Impieghi / depositi", "loan_to_deposit", "ratio"),
        ("Costo del credito %", "cost_of_risk", "pct"),
        ("Patrimonio tang./azione", "tangible_book_per_share", "ratio"),
    ),
    REIT: (
        ("Ricavi", "revenue", "big"), ("Utile netto", "net_income", "big"),
        ("FFO", "ffo", "big"), ("AFFO", "affo", "big"),
        ("FFO / azione", "ffo_per_share", "ratio"),
        ("AFFO / azione", "affo_per_share", "ratio"),
        ("FFO / ricavi %", "ffo_margin", "pct"),
        ("AFFO / ricavi %", "affo_margin", "pct"),
        ("FFO / attivo %", "ffo_to_assets", "pct"),
        ("Dividendi / FFO %", "ffo_payout", "pct"),
        ("Debito / attivo %", "debt_to_assets", "pct"),
        ("Debt / EBITDA", "debt_to_ebitda", "ratio"),
        ("Interest Coverage", "interest_coverage", "ratio"),
        ("Ammortamenti", "d_and_a", "big"), ("CapEx totale", "capex", "big"),
    ),
    UTILITY: (
        ("Ricavi", "revenue", "big"), ("Utile netto", "net_income", "big"),
        ("FFO", "ffo", "big"), ("Rate base (proxy)", "rate_base", "big"),
        ("ROE %", "roe", "pct"), ("Utile / rate base %", "return_on_rate_base", "pct"),
        ("Margine operativo %", "operating_margin", "pct"),
        ("FFO / ricavi %", "ffo_margin", "pct"),
        ("FFO / debito %", "ffo_to_debt", "pct"),
        ("CapEx / ammortamenti", "capex_to_depreciation", "ratio"),
        ("Debito / capitale %", "debt_to_capital", "pct"),
        ("Debt / EBITDA", "debt_to_ebitda", "ratio"),
        ("Interest Coverage", "interest_coverage", "ratio"),
        ("CapEx", "capex", "big"),
    ),
    ENERGY: (
        ("Ricavi", "revenue", "big"), ("Utile netto", "net_income", "big"),
        ("EBITDAX", "ebitdax", "big"),
        ("Flusso operativo", "operating_cash_flow", "big"),
        ("EBITDAX / ricavi %", "ebitdax_margin", "pct"),
        ("Flusso op. / ricavi %", "operating_cash_margin", "pct"),
        ("ROIC %", "roic", "pct"), ("ROE %", "roe", "pct"),
        ("Esplorazione / ricavi %", "exploration_intensity", "pct"),
        ("CapEx / flusso operativo", "capex_to_cash_flow", "ratio"),
        ("Debt / EBITDAX", "debt_to_ebitdax", "ratio"),
        ("Debt / Equity", "debt_to_equity", "ratio"),
        ("Interest Coverage", "interest_coverage", "ratio"),
        ("CapEx", "capex", "big"),
    ),
    INSURANCE: (
        ("Premi", "premiums_earned", "big"), ("Ricavi", "revenue", "big"),
        ("Utile netto", "net_income", "big"),
        ("Combined ratio", "combined_ratio", "pct"),
        ("ROE %", "roe", "pct"), ("ROTCE %", "rotce", "pct"), ("ROA %", "roa", "pct"),
        ("Rendimento investimenti %", "investment_yield", "pct"),
        ("Patrimonio / attivo %", "equity_to_assets", "pct"),
        ("Debt / Equity", "debt_to_equity", "ratio"),
        ("Patrimonio / azione", "book_value_per_share", "ratio"),
    ),
}


# ---------------------------------------------------------------------------
# Motore di punteggio, uguale per tutti i settori
# ---------------------------------------------------------------------------


def score_categories(
    profile: Mapping[str, Any],
    averages: Mapping[str, Optional[float]],
    consistency: Mapping[str, Mapping[str, Optional[float]]],
    quality: Optional[_DataQuality] = None,
) -> Dict[str, Dict[str, Any]]:
    """Trasforma medie e statistiche di consistenza nei punteggi delle tre categorie.

    E' lo stesso motore per industriali, banche e assicurazioni: cambiano solo le
    metriche e le soglie dichiarate nel profilo. Le componenti non calcolabili vengono
    escluse e il loro peso ridistribuito sulle altre della stessa categoria.

    La ridistribuzione tiene in piedi il punteggio quando manca un dato, ma ne cambia il
    significato: un 47 che nasce da tutte le componenti previste e un 47 che nasce da una
    sola non sono la stessa informazione. Per questo ogni categoria dichiara anche la
    propria **copertura**:

    ``components_used`` / ``components_total``
        quante componenti hanno prodotto un punteggio, su quante ne prevede il profilo;
    ``coverage``
        quota del peso previsto che ha davvero contribuito (0-1). E' la misura piu'
        onesta delle due, perche' pesa le componenti invece di contarle: perdere il
        ROIC (peso 0.35) non equivale a perdere il margine lordo (peso 0.10).
    """
    quality = quality if quality is not None else _DataQuality()
    categories: Dict[str, Dict[str, Any]] = {}

    for name, definition in profile["categories"].items():
        components: Dict[str, Dict[str, Any]] = {}
        for key, spec in definition["components"].items():
            source = spec["source"]
            if source[0] == "average":
                value = averages.get(source[1])
            else:
                value = (consistency.get(source[1]) or {}).get(source[2])

            inverted = spec["low"] > spec["high"]
            scale = (
                f"{spec['low']:g} -> 0 | {spec['high']:g} -> 100"
                + (" (inverso)" if inverted else "")
            )
            components[key] = {
                "label": spec["label"],
                "value": _round(value, 3),
                "score": _round(_score_linear(value, spec["low"], spec["high"]), 1),
                "weight": spec["weight"],
                "scale": scale,
            }

        usable = {k: c for k, c in components.items() if c["score"] is not None}
        skipped = [k for k in components if k not in usable]
        for key in skipped:
            quality.note(
                f"Componente '{key}' non calcolabile: peso ridistribuito nella categoria."
            )
        declared_weight = sum(c["weight"] for c in components.values())
        total_weight = sum(c["weight"] for c in usable.values())
        score = (
            sum(c["score"] * c["weight"] for c in usable.values()) / total_weight
            if total_weight > 0 else None
        )
        categories[name] = {
            "label": definition["label"],
            "score": _round(score, 1),
            "components_used": len(usable),
            "components_total": len(components),
            "coverage": _round(_safe_div(total_weight, declared_weight), 3),
            "components": components,
        }
    return categories


#: Variante del profilo industriale che segue i criteri pubblicati da Buffett negli
#: annual report di Berkshire (dal 1982) e la metrica della lettera 2007.
#:
#: Tre differenze rispetto al profilo industriale standard:
#:
#: 1. la metrica principale e' il **rendimento ante imposte sul capitale tangibile**
#:    ("return on unleveraged net tangible assets"), non il ROIC ordinario;
#: 2. al posto del Debt/EBITDA c'e' **Debito / Owner Earnings** — gli anni necessari a
#:    ripagare il debito con la cassa che il proprietario potrebbe estrarre. Buffett
#:    rifiuta apertamente l'EBITDA: "il management pensa che la fatina dei denti paghi
#:    il CapEx?" (lettera 2000);
#: 3. le soglie su debito e continuita' degli utili sono **piu' severe**: il criterio
#:    pubblicato dice "buoni rendimenti sul capitale proprio con poco o nessun debito"
#:    e "capacita' di reddito dimostrata", non "accettabile".
BUFFETT_PROFILE: Dict[str, Any] = {
    "label": "Azienda operativa (criteri Buffett)",
    "categories": {
        "profitability": {
            "label": "Economics del business",
            "components": {
                "return_on_tangible_capital": {
                    "label": "Rendimento ante imposte su capitale tangibile (%)",
                    "source": ("average", "return_on_tangible_capital"),
                    "low": 20.0, "high": 100.0, "weight": 0.40},
                "roe": {"label": "ROE medio (%)", "source": ("average", "roe"),
                        "low": 8.0, "high": 25.0, "weight": 0.25},
                "roic": {"label": "ROIC medio (%)", "source": ("average", "roic"),
                         "low": 4.0, "high": 25.0, "weight": 0.20},
                "owner_earnings_margin": {
                    "label": "Owner Earnings / Ricavi medio (%)",
                    "source": ("average", "owner_earnings_margin"),
                    "low": 3.0, "high": 20.0, "weight": 0.15},
            },
        },
        "consistency": {
            "label": "Capacita' di reddito dimostrata",
            "components": {
                # "Demonstrated consistent earning power": e' il criterio numero uno,
                # e chiede continuita', non media.
                "profitable_years": {"label": "Anni con utile positivo (%)",
                                     "source": ("consistency", "net_income", "positive_years_pct"),
                                     "low": 80.0, "high": 100.0, "weight": 0.30},
                "owner_earnings_growth_years": {
                    "label": "Anni di crescita degli Owner Earnings (%)",
                    "source": ("consistency", "owner_earnings", "growth_years_pct"),
                    "low": 50.0, "high": 100.0, "weight": 0.25},
                "tangible_return_stability": {
                    "label": "Coeff. di variazione del rendimento tangibile",
                    "source": ("consistency", "return_on_tangible_capital",
                               "coefficient_of_variation"),
                    "low": 0.50, "high": 0.05, "weight": 0.25},
                "revenue_growth_years": {"label": "Anni di crescita dei ricavi (%)",
                                         "source": ("consistency", "revenue", "growth_years_pct"),
                                         "low": 50.0, "high": 100.0, "weight": 0.20},
            },
        },
        "balance_sheet": {
            "label": "Poco o nessun debito",
            "components": {
                # Anni di Owner Earnings necessari a estinguere il debito: e' la domanda
                # che conta davvero, ed e' insensibile ai trucchi contabili dell'EBITDA.
                "debt_to_owner_earnings": {
                    "label": "Anni di Owner Earnings per ripagare il debito",
                    "source": ("average", "debt_to_owner_earnings"),
                    "low": 5.0, "high": 0.0, "weight": 0.40},
                "debt_to_equity": {"label": "Debt/Equity medio",
                                   "source": ("average", "debt_to_equity"),
                                   "low": 1.00, "high": 0.00, "weight": 0.35},
                "interest_coverage": {"label": "Interest Coverage medio",
                                      "source": ("average", "interest_coverage"),
                                      "low": 5.0, "high": 20.0, "weight": 0.25},
            },
        },
    },
}

BUFFETT = "buffett"
PROFILES[BUFFETT] = BUFFETT_PROFILE

CONSISTENCY_TARGETS[BUFFETT] = (
    "return_on_tangible_capital", "roic", "roe", "owner_earnings_margin",
    "revenue", "net_income", "owner_earnings",
)
AVERAGE_TARGETS[BUFFETT] = (
    "return_on_tangible_capital", "roic", "roe", "roa", "owner_earnings",
    "owner_earnings_margin", "debt_to_equity", "debt_to_owner_earnings",
    "interest_coverage", "maintenance_capex",
)
TABLE_ROWS[BUFFETT] = (
    ("Ricavi", "revenue", "big"),
    ("Utile netto", "net_income", "big"),
    ("Owner Earnings", "owner_earnings", "big"),
    ("CapEx totale", "capex", "big"),
    ("CapEx di mantenimento", "maintenance_capex", "big"),
    ("Rend. capitale tangibile %", "return_on_tangible_capital", "pct"),
    ("ROIC %", "roic", "pct"),
    ("ROE %", "roe", "pct"),
    ("Owner Earn. / Ricavi %", "owner_earnings_margin", "pct"),
    ("Debt / Equity", "debt_to_equity", "ratio"),
    ("Anni per ripagare il debito", "debt_to_owner_earnings", "ratio"),
    ("Interest Coverage", "interest_coverage", "ratio"),
)


def resolve_profile(sector: str, mode: str = "standard") -> Tuple[Dict[str, Any], str]:
    """Profilo effettivo e sua chiave, dato il settore e la modalita' di analisi.

    La modalita' ``"buffett"`` si applica solo alle aziende operative: i criteri
    pubblicati di Berkshire parlano di business industriali e commerciali, e su una
    banca non avrebbero senso (il "poco o nessun debito" e' incompatibile con il
    modello di business bancario). Su un finanziario la modalita' viene ignorata e
    resta il profilo di settore.
    """
    if str(mode).lower() == BUFFETT and sector == INDUSTRIAL:
        return PROFILES[BUFFETT], BUFFETT
    return PROFILES[sector], sector

