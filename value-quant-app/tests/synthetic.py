"""Bilanci e prezzi sintetici condivisi dai test: nessuna rete, risultati deterministici."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

YEARS: List[int] = [2024, 2023, 2022, 2021, 2020, 2019]


def frame(rows: Dict[str, Sequence[float]], years: Sequence[int] = tuple(YEARS)) -> pd.DataFrame:
    """DataFrame nel formato yfinance: righe = voci di bilancio, colonne = date di chiusura."""
    columns = [pd.Timestamp(f"{year}-12-31") for year in years]
    return pd.DataFrame(
        {column: [values[i] for values in rows.values()] for i, column in enumerate(columns)},
        index=list(rows),
    )


def make_financials(
    ticker: str,
    *,
    revenue: Sequence[float],
    operating_income: Sequence[float],
    net_income: Sequence[float],
    equity: Sequence[float],
    debt: Sequence[float],
    cash: Sequence[float],
    shares: float,
    years: Sequence[int] = tuple(YEARS),
) -> Dict[str, object]:
    """Costruisce i tre prospetti coerenti fra loro per un emittente fittizio."""
    count = len(revenue)
    return {
        "ticker": ticker,
        "company_name": f"Societa' {ticker}",
        "currency": "USD",
        "income_statement": frame({
            "Total Revenue": revenue,
            "Gross Profit": [value * 0.45 for value in revenue],
            "Operating Income": operating_income,
            "Pretax Income": [value * 0.98 for value in operating_income],
            "Tax Provision": [value * 0.98 * 0.21 for value in operating_income],
            "Net Income": net_income,
            "Interest Expense": [value * 0.04 for value in debt],
        }, years),
        "balance_sheet": frame({
            "Total Assets": [e + d + c for e, d, c in zip(equity, debt, cash)],
            "Total Liabilities Net Minority Interest": [
                d + c * 0.2 for d, c in zip(debt, cash)
            ],
            "Stockholders Equity": list(equity),
            "Total Debt": list(debt),
            "Cash And Cash Equivalents": list(cash),
            "Current Assets": [value * 0.40 for value in revenue],
            "Current Liabilities": [value * 0.30 for value in revenue],
            "Ordinary Shares Number": [shares] * count,
        }, years),
        "cash_flow": frame({
            "Depreciation And Amortization": [value * 0.04 for value in revenue],
            "Capital Expenditure": [-value * 0.05 for value in revenue],
            "Change In Working Capital": [value * 0.005 for value in revenue],
        }, years),
        "years": list(years),
        "data_quality": {"notes": [], "estimated": [], "missing": []},
    }


#: Emittente di riferimento: alta redditivita', crescita costante, debito contenuto.
ALFA = make_financials(
    "ALFA",
    revenue=[400e9, 383e9, 394e9, 366e9, 274e9, 260e9],
    operating_income=[123e9, 114e9, 119e9, 108e9, 66e9, 63e9],
    net_income=[99e9, 97e9, 100e9, 95e9, 57e9, 55e9],
    equity=[57e9, 62e9, 50e9, 63e9, 65e9, 90e9],
    debt=[106e9, 111e9, 120e9, 136e9, 122e9, 108e9],
    cash=[30e9, 29e9, 24e9, 35e9, 38e9, 48e9],
    shares=15.5e9,
)

ALFA_MARKET = {
    "price": 180.0, "shares_outstanding": 15.5e9, "beta": 1.25,
    "market_cap": 180.0 * 15.5e9, "currency": "USD", "company_name": "Societa' Alfa",
}


def make_universe(tickers: Sequence[str] = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")) -> Dict[str, dict]:
    """Universo con qualita' decrescente: il primo ticker e' il migliore su ogni asse."""
    universe: Dict[str, dict] = {}
    for index, ticker in enumerate(tickers):
        scale = 1.0 - index * 0.12
        revenue = [100e9 * scale * (1.08 ** (5 - k)) for k in range(6)]
        universe[ticker] = make_financials(
            ticker,
            revenue=revenue,
            operating_income=[value * (0.30 - index * 0.035) for value in revenue],
            net_income=[value * (0.22 - index * 0.03) for value in revenue],
            equity=[40e9 * scale] * 6,
            debt=[20e9 * (1 + index * 0.5)] * 6,
            cash=[15e9 * scale] * 6,
            shares=1e9,
        )
    return universe


def make_prices(
    tickers: Sequence[str],
    *,
    start: str = "2019-01-02",
    end: str = "2024-12-31",
    seed: int = 42,
) -> pd.DataFrame:
    """Serie giornaliere deterministiche (moto browniano geometrico con seme fisso)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    prices = pd.DataFrame(index=dates)
    for index, ticker in enumerate(tickers):
        drift = 0.0002 + index * 0.00005
        prices[ticker] = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.012, len(dates))))
    return prices


def make_benchmark(index: pd.Index, *, seed: int = 99) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.00035, 0.010, len(index)))), index=index)


# ---------------------------------------------------------------------------
# Emittenti finanziari sintetici
# ---------------------------------------------------------------------------


def make_bank(
    ticker: str = "BANCA",
    *,
    scale: float = 1.0,
    equity_share: float = 0.10,
    years: Sequence[int] = tuple(YEARS),
) -> Dict[str, object]:
    """Banca commerciale ben capitalizzata e finanziata dai depositi.

    Numeri di riferimento (esercizio piu' recente): ROE 15%, ROA 1.5%, ROTCE 18%,
    patrimonio/attivo 10%, impieghi/depositi 0.59, cost/income 56%.
    """
    count = len(years)
    # Le colonne vanno dal piu' recente al piu' vecchio: il divisore cresce
    # andando indietro nel tempo, cosi' la serie e' crescente nel tempo.
    step = [1.0 + 0.04 * k for k in range(count)]

    assets = [3000e9 * scale / s for s in step]
    # ``equity_share`` e' il patrimonio sul totale attivo: 10% di default, il livello di
    # una banca commerciale molto capitalizzata. Serve a tarare le soglie del profilo.
    equity = [a * equity_share for a in assets]
    goodwill = [50e9 * scale] * count
    net_income = [45e9 * scale / s for s in step]
    net_interest_income = [90e9 * scale / s for s in step]
    fee_income = [70e9 * scale / s for s in step]
    revenue = [n + f for n, f in zip(net_interest_income, fee_income)]

    return {
        "ticker": ticker,
        "company_name": f"Banca {ticker}",
        "currency": "USD",
        "income_statement": frame({
            "Total Revenue": revenue,
            "Net Interest Income": net_interest_income,
            "Total Non Interest Income": fee_income,
            "Total Non Interest Expense": [r * 0.5625 for r in revenue],
            "Credit Losses Provision": [8e9 * scale / s for s in step],
            "Pretax Income": [n / 0.75 for n in net_income],
            "Tax Provision": [n / 0.75 * 0.25 for n in net_income],
            "Net Income": net_income,
            "Interest Expense": [40e9 * scale / s for s in step],
        }, years),
        "balance_sheet": frame({
            "Total Assets": assets,
            "Total Liabilities Net Minority Interest": [a - e for a, e in zip(assets, equity)],
            "Stockholders Equity": equity,
            "Total Deposits": [a * 0.733 for a in assets],
            "Net Loan": [a * 0.433 for a in assets],
            "Goodwill": goodwill,
            "Other Intangible Assets": [5e9 * scale] * count,
            "Total Debt": [a * 0.10 for a in assets],
            "Cash And Cash Equivalents": [a * 0.08 for a in assets],
            "Ordinary Shares Number": [3e9] * count,
        }, years),
        "cash_flow": frame({
            "Depreciation And Amortization": [2e9 * scale] * count,
            "Capital Expenditure": [-3e9 * scale] * count,
        }, years),
        "years": list(years),
        "data_quality": {"notes": [], "estimated": [], "missing": []},
    }


def make_insurer(
    ticker: str = "ASSICURA",
    *,
    combined_ratio: float = 0.867,
    revenue_multiple: float = 1.12,
    policy_liability_share: float = 0.55,
    years: Sequence[int] = tuple(YEARS),
) -> Dict[str, object]:
    """Assicurazione danni con utile tecnico: combined ratio sotto 100.

    Riferimento: combined ratio ~87%, ROE 15%, patrimonio/attivo 25%, Debt/Equity 0.25.
    """
    count = len(years)
    step = [1.0 + 0.05 * k for k in range(count)]

    premiums = [45e9 / s for s in step]
    equity = [60e9 / s for s in step]
    assets = [240e9 / s for s in step]
    net_income = [9e9 / s for s in step]

    return {
        "ticker": ticker,
        "company_name": f"Assicurazioni {ticker}",
        "currency": "USD",
        "income_statement": frame({
            "Total Revenue": [p * revenue_multiple for p in premiums],
            "Total Premiums Earned": premiums,
            "Losses And Loss Adjustment Expenses": [p * combined_ratio * 0.69 for p in premiums],
            "Underwriting Expense": [p * combined_ratio * 0.31 for p in premiums],
            "Net Investment Income": [5e9 / s for s in step],
            "Pretax Income": [n / 0.8 for n in net_income],
            "Tax Provision": [n / 0.8 * 0.2 for n in net_income],
            "Net Income": net_income,
            "Interest Expense": [0.6e9] * count,
        }, years),
        "balance_sheet": frame({
            "Total Assets": assets,
            "Total Liabilities Net Minority Interest": [a - e for a, e in zip(assets, equity)],
            "Stockholders Equity": equity,
            "Total Investments": [a * 0.54 for a in assets],
            "Total Policy Liabilities": [a * policy_liability_share for a in assets],
            "Goodwill": [20e9] * count,
            "Other Intangible Assets": [0.0] * count,
            "Total Debt": [e * 0.25 for e in equity],
            "Cash And Cash Equivalents": [a * 0.03 for a in assets],
            "Ordinary Shares Number": [400e6] * count,
        }, years),
        "cash_flow": frame({
            "Depreciation And Amortization": [0.4e9] * count,
            "Capital Expenditure": [-0.5e9] * count,
        }, years),
        "years": list(years),
        "data_quality": {"notes": [], "estimated": [], "missing": []},
    }


# ---------------------------------------------------------------------------
# Casi limite del rilevamento di settore
# ---------------------------------------------------------------------------


def add_row(
    financials: Dict[str, object],
    statement: str,
    label: str,
    values: Sequence[float],
) -> Dict[str, object]:
    """Copia i prospetti aggiungendo (o sostituendo) una riga in uno di essi."""
    updated = dict(financials)
    frame_copy = financials[statement].copy()  # type: ignore[union-attr]
    frame_copy.loc[label] = list(values)
    updated[statement] = frame_copy
    return updated


def drop_row(financials: Dict[str, object], statement: str, label: str) -> Dict[str, object]:
    """Copia i prospetti togliendo una riga: serve a simulare una fonte incompleta."""
    updated = dict(financials)
    updated[statement] = financials[statement].drop(index=label)  # type: ignore[union-attr]
    return updated


def make_cash_rich_tech(ticker: str = "TECH") -> Dict[str, object]:
    """Industriale con la tesoreria piena, che espone un margine di interesse minimo.

    E' il caso Alphabet: yfinance riporta "Net Interest Income" anche per chi non fa
    intermediazione (interessi attivi sulla liquidita' meno oneri finanziari). Qui pesa
    l'1% dei ricavi, e su quel solo indizio l'azienda veniva classificata come banca.
    """
    revenue = [350e9, 307e9, 282e9, 257e9, 182e9, 161e9]
    base = make_financials(
        ticker,
        revenue=revenue,
        operating_income=[value * 0.32 for value in revenue],
        net_income=[value * 0.24 for value in revenue],
        equity=[290e9, 273e9, 256e9, 251e9, 222e9, 201e9],
        debt=[28e9, 29e9, 30e9, 28e9, 27e9, 25e9],
        cash=[110e9, 114e9, 116e9, 139e9, 137e9, 120e9],
        shares=12.5e9,
    )
    return add_row(base, "income_statement", "Net Interest Income",
                   [value * 0.01 for value in revenue])


def make_conglomerate(ticker: str = "MISTA") -> Dict[str, object]:
    """Banca che vende anche polizze: marcatori bancari e assicurativi entrambi materiali.

    Serve a verificare il criterio di scelta quando le due strutture convivono: vince il
    marcatore piu' pesante (qui i depositi, al 73% dell'attivo, contro premi al 25% dei
    ricavi) e la decisione viene dichiarata.
    """
    bank = make_bank(ticker)
    revenue = list(bank["income_statement"].loc["Total Revenue"])  # type: ignore[union-attr]
    return add_row(bank, "income_statement", "Total Premiums Earned",
                   [value * 0.25 for value in revenue])


def make_diversified_holding(ticker: str = "HOLDING") -> Dict[str, object]:
    """Holding con dentro un'assicurazione, ma anche molto altro: il caso Berkshire.

    I premi sono il 23% dei ricavi (Berkshire 2023: 83 mld su 364, il resto essendo
    ferrovie, energia, industria e distribuzione) e le riserve tecniche solo il 5%
    dell'attivo. E' il caso che decide la soglia sui premi: al 30% questa azienda
    finirebbe nel profilo industriale, dove la crescita del patrimonio per azione — il
    metro con cui Berkshire ha misurato se stessa per decenni — non viene nemmeno
    calcolata.
    """
    return make_insurer(ticker, revenue_multiple=1 / 0.23, policy_liability_share=0.05)
