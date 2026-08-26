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

I tre profili
-------------
``INDUSTRIAL``  aziende operative: ROIC, Owner Earnings, leva finanziaria
``BANK``        banche commerciali: ROTCE, margine di interesse, efficienza, funding
``INSURANCE``   assicurazioni e holding: crescita del patrimonio per azione, combined ratio

Il rilevamento e' automatico e si basa sulla **struttura del bilancio** (la presenza
dei depositi, dei premi assicurativi) e non sull'etichetta di settore di Yahoo, che
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
        INCOME_ALIASES,
        _DataQuality,
        _mean,
        _round,
        _row_index,
        _safe_div,
        _score_linear,
        _series_by_year,
        _to_float,
        calculate_consistency,
    )
except ImportError:  # esecuzione come script standalone
    from quality_score import (  # type: ignore[no-redef]
        BALANCE_ALIASES,
        INCOME_ALIASES,
        _DataQuality,
        _mean,
        _round,
        _row_index,
        _safe_div,
        _score_linear,
        _series_by_year,
        _to_float,
        calculate_consistency,
    )


__all__ = [
    "BANK",
    "BANK_DEPOSIT_SHARE",
    "BANK_INTEREST_SHARE",
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
    oppure riserve tecniche oltre il 10% dell'attivo. In assenza di tutto, azienda
    operativa: non si tira a indovinare.

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

    Modifica ``fundamentals`` sul posto e lo restituisce. Per il profilo industriale
    non fa nulla: le voci servono gia' tutte.
    """
    quality = quality if quality is not None else _DataQuality()
    if sector == INDUSTRIAL:
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
}

#: Metriche su cui calcolare le statistiche di consistenza, per settore.
CONSISTENCY_TARGETS: Dict[str, Tuple[str, ...]] = {
    INDUSTRIAL: ("roic", "roe", "roa", "operating_margin", "net_margin",
                 "revenue", "net_income", "owner_earnings"),
    BANK: ("rotce", "roe", "roa", "efficiency_ratio", "net_interest_margin",
           "tangible_book_per_share", "revenue", "net_income"),
    INSURANCE: ("roe", "rotce", "combined_ratio", "book_value_per_share",
                "premiums_earned", "net_income", "investment_yield"),
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

