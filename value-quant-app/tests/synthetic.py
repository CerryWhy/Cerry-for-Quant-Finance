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
