"""Modelli di analisi fondamentale e quantitativa del progetto value-quant-app.

``visualize`` non viene importato qui di proposito: dipende da matplotlib, e il resto
dei moduli deve restare utilizzabile anche in un ambiente senza librerie grafiche
(server, notebook headless, pipeline batch). Va importato esplicitamente::

    from models import visualize
"""

from .backtest import (  # noqa: F401
    DEFAULT_STRATEGY,
    build_signal_panel,
    fetch_universe_data,
    performance_metrics,
    run_backtest,
    sweep_parameters,
)
from .quality_score import (  # noqa: F401
    DEFAULT_WEIGHTS,
    calculate_balance_sheet_ratios,
    calculate_consistency,
    calculate_margins,
    calculate_owner_earnings,
    calculate_quality_score,
    calculate_roa,
    calculate_roe,
    calculate_roic,
    fetch_financials,
    format_report,
)
from .valuation import (  # noqa: F401
    DEFAULT_ASSUMPTIONS,
    calculate_valuation,
    cost_of_capital,
    dcf_value_per_share,
    fetch_market_data,
    format_valuation_report,
    reverse_dcf,
)
