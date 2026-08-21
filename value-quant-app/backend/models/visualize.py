"""Grafici del modello: tear sheet in tema scuro, pronti da salvare o mostrare.

Tutti i grafici usano una sola palette, scelta per essere leggibile anche da chi ha
un deficit di visione dei colori (ogni coppia adiacente e' separata in modo
verificato, non "a occhio"). Le tre regole che tengono insieme l'insieme:

* **magnitudine -> una sola tinta**, dal chiaro allo scuro. Niente scale arcobaleno:
  su una superficie 3D l'arcobaleno inventa confini che nei dati non esistono.
* **polarita' -> due tinte opposte** (blu/rosso) con il grigio nel mezzo, cosi' lo
  zero si legge come "niente".
* **identita' -> slot fissi**, assegnati sempre nello stesso ordine, mai riciclati.

Grafici disponibili
-------------------
``plot_equity_curve``      curva di capitale + drawdown (il grafico del backtest)
``plot_football_field``    intervallo di valore per metodo vs prezzo di mercato
``plot_quality_radar``     profilo di qualita' su piu' assi
``plot_metrics_history``   piccole serie storiche affiancate (ROIC, margini, OE)
``plot_universe_heatmap``  mappa di calore dei punteggi su piu' titoli
``plot_quality_value_scatter``  la matrice qualita' / sconto, con i quadranti
``plot_sensitivity_surface``    superficie 3D (o curve di livello) di sensitivita'
``create_tearsheet``       tutto insieme in una pagina sola

Uso::

    python visualize.py               # demo con dati sintetici -> cartella output/
    python visualize.py --show        # mostra i grafici invece di salvarli
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import matplotlib
    if not os.environ.get("DISPLAY") and sys.platform != "darwin":
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.gridspec import GridSpec
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter
except ImportError:  # pragma: no cover - ambiente senza matplotlib
    matplotlib = None  # type: ignore[assignment]
    plt = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore[assignment]


__all__ = [
    "PALETTE",
    "apply_style",
    "create_tearsheet",
    "demo",
    "plot_equity_curve",
    "plot_football_field",
    "plot_metrics_history",
    "plot_quality_radar",
    "plot_quality_value_scatter",
    "plot_sensitivity_surface",
    "plot_universe_heatmap",
    "save_figure",
]


# ---------------------------------------------------------------------------
# Palette e stile
# ---------------------------------------------------------------------------

#: Palette per superficie scura. Gli slot categorici vanno assegnati **in ordine**:
#: e' l'ordine stesso a garantire la separazione fra tinte adiacenti.
PALETTE: Dict[str, Any] = {
    "surface": "#1a1a19",
    "page": "#0d0d0d",
    "ink": "#ffffff",
    "ink_secondary": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "series": [
        "#3987e5",  # 1 blu
        "#d95926",  # 2 arancio
        "#199e70",  # 3 acqua
        "#c98500",  # 4 giallo
        "#d55181",  # 5 magenta
        "#008300",  # 6 verde
        "#9085e9",  # 7 viola
        "#e66767",  # 8 rosso
    ],
    # Rampa sequenziale mono-tono (chiaro -> scuro) per le magnitudini.
    "sequential": [
        "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b",
    ],
    # Poli divergenti caldo/freddo con grigio neutro al centro.
    "diverging": ("#3987e5", "#383835", "#d03b3b"),
    "status": {
        "good": "#0ca30c",
        "warning": "#fab219",
        "serious": "#ec835a",
        "critical": "#d03b3b",
    },
}


def _cmap_sequential():
    return LinearSegmentedColormap.from_list("vq_sequential", PALETTE["sequential"])


def _cmap_diverging():
    low, mid, high = PALETTE["diverging"]
    # Il rosso sta in basso (sopravvalutato) e il blu in alto (sconto): la scala
    # va quindi da rosso a blu passando per il grigio.
    return LinearSegmentedColormap.from_list("vq_diverging", [high, mid, low])


def apply_style() -> None:
    """Imposta lo stile globale: superficie scura, griglia sottile, testi in inchiostro."""
    if plt is None:
        return
    plt.rcParams.update({
        "figure.facecolor": PALETTE["page"],
        "figure.edgecolor": PALETTE["page"],
        "savefig.facecolor": PALETTE["page"],
        "savefig.edgecolor": PALETTE["page"],
        "axes.facecolor": PALETTE["surface"],
        "axes.edgecolor": PALETTE["axis"],
        "axes.labelcolor": PALETTE["ink_secondary"],
        "axes.titlecolor": PALETTE["ink"],
        "axes.titlesize": 11,
        "axes.titleweight": "600",
        "axes.labelsize": 9,
        "axes.linewidth": 0.8,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": PALETTE["grid"],
        "grid.linewidth": 0.6,
        "grid.linestyle": "-",          # mai tratteggiata: il tratteggio significa altro
        "xtick.color": PALETTE["muted"],
        "ytick.color": PALETTE["muted"],
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "text.color": PALETTE["ink_secondary"],
        "legend.frameon": False,
        "legend.fontsize": 8.5,
        "legend.labelcolor": PALETTE["ink_secondary"],
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Segoe UI", "Helvetica", "Arial"],
        "figure.dpi": 110,
        "lines.linewidth": 2.0,
        "lines.solid_capstyle": "round",
    })


def _clean_axes(ax: Any, *, grid_axis: str = "y") -> None:
    """Toglie le spine superflue e lascia una griglia sottile su un asse solo."""
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(PALETTE["axis"])
    ax.grid(True, axis=grid_axis, color=PALETTE["grid"], linewidth=0.6)
    if grid_axis != "both":
        other = "x" if grid_axis == "y" else "y"
        ax.grid(False, axis=other)


def save_figure(figure: Any, path: str, *, dpi: int = 150) -> str:
    """Salva la figura creando la cartella se serve, e restituisce il percorso."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    figure.savefig(path, dpi=dpi, bbox_inches="tight", facecolor=PALETTE["page"])
    return path


def _require_matplotlib() -> None:
    if plt is None:
        raise ImportError("matplotlib non installato: `pip install matplotlib`.")


def _annotate_missing(ax: Any, message: str) -> None:
    """Riquadro vuoto con la spiegazione, invece di un grafico senza dati o di un errore."""
    # Gli assi 3D hanno una firma di text() diversa: si scrive sulla figura.
    if hasattr(ax, "get_zlim"):
        ax.figure.text(0.5, 0.5, message, ha="center", va="center",
                       color=PALETTE["muted"], fontsize=9)
        ax.set_axis_off()
        return
    ax.text(0.5, 0.5, message, transform=ax.transAxes, ha="center", va="center",
            color=PALETTE["muted"], fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)


# ---------------------------------------------------------------------------
# 1. Curva di capitale e drawdown
# ---------------------------------------------------------------------------


def plot_equity_curve(
    backtest: Mapping[str, Any],
    *,
    axes: Optional[Sequence[Any]] = None,
    log_scale: bool = True,
    title: str = "Strategia quality + value vs benchmark",
    save: Optional[str] = None,
) -> Any:
    """Curva di capitale con il drawdown in un pannello separato sotto.

    Il drawdown sta in un pannello proprio e non su un secondo asse y: sovrapporre
    due scale diverse sullo stesso riquadro fa nascere correlazioni che nei dati
    non ci sono.
    """
    _require_matplotlib()
    apply_style()

    if axes is None:
        figure, (ax_equity, ax_drawdown) = plt.subplots(
            2, 1, figsize=(11, 6.5), height_ratios=[3, 1], sharex=True,
        )
    else:
        ax_equity, ax_drawdown = axes
        figure = ax_equity.figure

    equity = backtest.get("equity_curve")
    if equity is None or len(equity) == 0:
        _annotate_missing(ax_equity, backtest.get("error") or "Backtest non disponibile")
        _annotate_missing(ax_drawdown, "")
        if save:
            save_figure(figure, save)
        return figure

    benchmark = backtest.get("benchmark_curve")

    ax_equity.plot(equity.index, equity.values, color=PALETTE["series"][0],
                   linewidth=2.0, label="Strategia", zorder=3)
    if benchmark is not None and len(benchmark):
        ax_equity.plot(benchmark.index, benchmark.values, color=PALETTE["series"][1],
                       linewidth=1.6, label="Benchmark", zorder=2)

    # Etichette dirette solo sull'ultimo punto: un numero su ogni punto sarebbe illeggibile.
    ax_equity.annotate(
        f"{equity.iloc[-1]:,.0f}", xy=(equity.index[-1], equity.iloc[-1]),
        xytext=(6, 0), textcoords="offset points", va="center",
        color=PALETTE["ink"], fontsize=9, fontweight="600",
    )
    if benchmark is not None and len(benchmark):
        ax_equity.annotate(
            f"{benchmark.iloc[-1]:,.0f}", xy=(benchmark.index[-1], benchmark.iloc[-1]),
            xytext=(6, 0), textcoords="offset points", va="center",
            color=PALETTE["ink_secondary"], fontsize=9,
        )

    if log_scale:
        ax_equity.set_yscale("log")
        ax_equity.set_ylabel("Capitale (scala log)")
    else:
        ax_equity.set_ylabel("Capitale")
    ax_equity.set_title(title, loc="left", pad=12)
    ax_equity.legend(loc="upper left", ncols=2)
    # Le date le porta il pannello del drawdown, subito sotto e con la stessa scala x.
    ax_equity.tick_params(labelbottom=False)
    _clean_axes(ax_equity)

    metrics = backtest.get("metrics") or {}
    stats = [
        ("CAGR", metrics.get("cagr"), "pct"),
        ("Vol", metrics.get("volatility"), "pct"),
        ("Sharpe", metrics.get("sharpe"), "num"),
        ("Max DD", metrics.get("max_drawdown"), "pct"),
    ]
    label = "   ".join(
        f"{name} {(value * 100):.1f}%" if (kind == "pct" and value is not None)
        else f"{name} {value:.2f}" if value is not None else f"{name} n/d"
        for name, value, kind in stats
    )
    ax_equity.text(
        1.0, 1.02, label, transform=ax_equity.transAxes, ha="right", va="bottom",
        color=PALETTE["muted"], fontsize=8.5,
    )

    drawdown = backtest.get("drawdown")
    if drawdown is not None and len(drawdown):
        values = drawdown.values * 100.0
        ax_drawdown.fill_between(drawdown.index, values, 0, color=PALETTE["status"]["critical"],
                                 alpha=0.28, linewidth=0)
        ax_drawdown.plot(drawdown.index, values, color=PALETTE["status"]["critical"],
                         linewidth=1.2)
        worst = float(drawdown.min()) * 100.0
        worst_date = drawdown.idxmin()
        # L'etichetta va sopra il minimo, cosi' non finisce sotto il bordo del riquadro.
        ax_drawdown.annotate(
            f"{worst:.1f}%", xy=(worst_date, worst), xytext=(0, 6),
            textcoords="offset points", ha="center", va="bottom",
            color=PALETTE["ink_secondary"], fontsize=8.5,
        )
        ax_drawdown.set_ylim(worst * 1.15, 0)
    ax_drawdown.set_ylabel("Drawdown %")
    ax_drawdown.set_xlim(ax_equity.get_xlim())
    _clean_axes(ax_drawdown)

    if axes is None:
        figure.tight_layout()
    if save:
        save_figure(figure, save)
    return figure


# ---------------------------------------------------------------------------
# 2. Football field della valutazione
# ---------------------------------------------------------------------------


def plot_football_field(
    valuation: Mapping[str, Any],
    *,
    ax: Optional[Any] = None,
    save: Optional[str] = None,
) -> Any:
    """Confronto fra i metodi di valutazione e il prezzo di mercato.

    La banda azzurra e' l'intervallo dei metodi che concorrono al fair value; la
    linea bianca e' il prezzo. Se la linea sta a sinistra della banda il titolo
    tratta a sconto, se sta a destra a premio: la posizione relativa e' l'intero
    messaggio del grafico.
    """
    _require_matplotlib()
    apply_style()

    if ax is None:
        figure, ax = plt.subplots(figsize=(10, 5))
    else:
        figure = ax.figure

    methods = valuation.get("methods") or {}
    price = valuation.get("price")
    fair = valuation.get("fair_value") or {}
    scenarios = valuation.get("scenarios") or {}

    rows: List[Tuple[str, Optional[float], Optional[float], Optional[float], bool]] = []
    for key, method in methods.items():
        value = method.get("value_per_share")
        if value is None:
            continue
        low = high = None
        if key == "dcf_owner_earnings" and scenarios:
            bear = (scenarios.get("bear") or {}).get("value_per_share")
            bull = (scenarios.get("bull") or {}).get("value_per_share")
            if bear is not None and bull is not None:
                low, high = min(bear, bull), max(bear, bull)
        rows.append((method.get("label", key), value, low, high, bool(method.get("aggregated"))))

    if not rows:
        _annotate_missing(ax, valuation.get("error") or "Valutazione non disponibile")
        if save:
            save_figure(figure, save)
        return figure

    rows.reverse()  # il primo metodo finisce in alto
    positions = list(range(len(rows)))
    blue = PALETTE["series"][0]

    if fair.get("low") is not None and fair.get("high") is not None:
        ax.axvspan(fair["low"], fair["high"], color=blue, alpha=0.12, linewidth=0, zorder=1)

    for position, (label, value, low, high, aggregated) in zip(positions, rows):
        color = blue if aggregated else PALETTE["muted"]
        if low is not None and high is not None:
            ax.barh(position, high - low, left=low, height=0.34, color=color,
                    alpha=0.55, linewidth=0, zorder=2)
            ax.plot([low, high], [position, position], color=color, linewidth=1.0, zorder=3)
        ax.plot([value], [position], marker="D", markersize=9, color=color,
                markeredgecolor=PALETTE["surface"], markeredgewidth=2, zorder=4)
        ax.annotate(f"{value:,.1f}", xy=(value, position), xytext=(0, 11),
                    textcoords="offset points", ha="center", fontsize=8.5,
                    color=PALETTE["ink_secondary"])

    if fair.get("point") is not None:
        ax.axvline(fair["point"], color=blue, linewidth=1.6, zorder=5)
        ax.annotate("fair value", xy=(fair["point"], len(rows) - 0.35), xytext=(5, 0),
                    textcoords="offset points", color=blue, fontsize=8.5, fontweight="600")
    if price:
        ax.axvline(price, color=PALETTE["ink"], linewidth=1.8, zorder=6)
        ax.annotate(f"prezzo {price:,.1f}", xy=(price, -0.7), xytext=(5, 0),
                    textcoords="offset points", color=PALETTE["ink"], fontsize=9,
                    fontweight="600")

    ax.set_yticks(positions)
    ax.set_yticklabels([row[0] for row in rows], color=PALETTE["ink_secondary"], fontsize=9)
    ax.set_xlabel(f"Valore per azione ({valuation.get('currency') or ''})")
    ax.set_ylim(-1.1, len(rows) - 0.1)

    margin = valuation.get("margin_of_safety")
    verdict = valuation.get("verdict", "")
    if margin is not None:
        status = PALETTE["status"]["good"] if margin >= 0.10 else (
            PALETTE["status"]["critical"] if margin <= -0.10 else PALETTE["status"]["warning"]
        )
        # Il colore di stato non viaggia mai da solo: accanto c'e' sempre il testo.
        ax.set_title(
            f"{valuation.get('company_name') or valuation.get('ticker')}"
            f"  ·  margine di sicurezza {margin * 100:+.1f}%  ·  {verdict}",
            loc="left", pad=12, color=status,
        )
    else:
        ax.set_title(
            f"{valuation.get('company_name') or valuation.get('ticker')}  ·  metodi di valutazione",
            loc="left", pad=12,
        )

    _clean_axes(ax, grid_axis="x")
    handles = [
        Patch(facecolor=blue, alpha=0.55, label="metodo incluso nel fair value"),
        Patch(facecolor=PALETTE["muted"], alpha=0.55, label="riferimento (escluso)"),
        Line2D([0], [0], color=PALETTE["ink"], linewidth=1.8, label="prezzo di mercato"),
    ]
    # Sotto il riquadro: dentro finirebbe sopra la barra del DCF, che occupa tutta la larghezza.
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncols=3)
    if ax.figure is figure and len(figure.axes) == 1:
        figure.tight_layout()
    if save:
        save_figure(figure, save)
    return figure


# ---------------------------------------------------------------------------
# 3. Radar della qualita'
# ---------------------------------------------------------------------------


def plot_quality_radar(
    quality_results: Any,
    *,
    ax: Optional[Any] = None,
    save: Optional[str] = None,
) -> Any:
    """Profilo di qualita' su piu' assi (una traccia per titolo, massimo tre).

    Oltre tre tracce il radar diventa illeggibile: con piu' titoli conviene la
    mappa di calore.
    """
    _require_matplotlib()
    apply_style()

    results = quality_results if isinstance(quality_results, (list, tuple)) else [quality_results]
    results = [r for r in results if r and r.get("category_scores")][:3]

    if ax is None:
        figure = plt.figure(figsize=(6.5, 6))
        ax = figure.add_subplot(111, projection="polar")
    else:
        figure = ax.figure

    if not results:
        _annotate_missing(ax, "Punteggi di qualita' non disponibili")
        if save:
            save_figure(figure, save)
        return figure

    axes_labels = [
        ("ROIC", ("profitability", "roic")),
        ("Margini", ("profitability", "operating_margin")),
        ("Owner Earnings", ("profitability", "owner_earnings_margin")),
        ("Stabilita' ROIC", ("consistency", "roic_stability")),
        ("Crescita ricavi", ("consistency", "revenue_growth_years")),
        ("Debito", ("balance_sheet", "debt_to_equity")),
        ("Copertura interessi", ("balance_sheet", "interest_coverage")),
        ("Liquidita'", ("balance_sheet", "current_ratio")),
    ]
    angles = [n / len(axes_labels) * 2 * math.pi for n in range(len(axes_labels))]
    angles += angles[:1]

    for index, result in enumerate(results):
        categories = result.get("category_scores") or {}
        values: List[float] = []
        for _, (category, component) in axes_labels:
            score = (
                ((categories.get(category) or {}).get("components") or {})
                .get(component, {})
                .get("score")
            )
            values.append(float(score) if score is not None else 0.0)
        values += values[:1]
        color = PALETTE["series"][index]
        label = result.get("ticker", f"serie {index + 1}")
        ax.plot(angles, values, color=color, linewidth=2.0, label=label, zorder=3)
        ax.fill(angles, values, color=color, alpha=0.16, zorder=2)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels([label for label, _ in axes_labels], fontsize=8.5,
                       color=PALETTE["ink_secondary"])
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25", "50", "75", "100"], fontsize=7.5, color=PALETTE["muted"])
    ax.set_facecolor(PALETTE["surface"])
    ax.spines["polar"].set_color(PALETTE["axis"])
    ax.grid(color=PALETTE["grid"], linewidth=0.6)
    title = (
        f"Profilo di qualita' · {results[0].get('ticker')}"
        if len(results) == 1 else "Profilo di qualita' a confronto"
    )
    ax.set_title(title, pad=22, color=PALETTE["ink"])
    if len(results) > 1:
        ax.legend(loc="upper right", bbox_to_anchor=(1.22, 1.12))

    if save:
        save_figure(figure, save)
    return figure


# ---------------------------------------------------------------------------
# 4. Storico delle metriche (small multiples)
# ---------------------------------------------------------------------------


def plot_metrics_history(
    quality: Mapping[str, Any],
    *,
    metrics: Sequence[Tuple[str, str, str]] = (
        ("roic", "ROIC", "%"),
        ("operating_margin", "Margine operativo", "%"),
        ("net_margin", "Margine netto", "%"),
        ("owner_earnings", "Owner Earnings", "abs"),
        ("debt_to_equity", "Debt / Equity", "x"),
        ("interest_coverage", "Interest Coverage", "x"),
    ),
    axes: Optional[Sequence[Any]] = None,
    save: Optional[str] = None,
) -> Any:
    """Piccole serie storiche affiancate: una metrica per riquadro, stessa scala visiva.

    I riquadri separati sono il modo corretto di mettere a confronto grandezze con
    unita' diverse (percentuali e multipli) senza sovrapporre due assi.
    """
    _require_matplotlib()
    apply_style()

    data = quality.get("metrics") or {}
    years = sorted(quality.get("years_analyzed") or [])

    if axes is None:
        columns = 3
        rows = math.ceil(len(metrics) / columns)
        figure, axes_grid = plt.subplots(rows, columns, figsize=(11, 2.5 * rows))
        axes_list = list(axes_grid.flatten()) if hasattr(axes_grid, "flatten") else [axes_grid]
    else:
        axes_list = list(axes)
        figure = axes_list[0].figure

    blue = PALETTE["series"][0]
    for index, (key, label, unit) in enumerate(metrics):
        if index >= len(axes_list):
            break
        ax = axes_list[index]
        series = data.get(key) or {}
        points = [(year, series.get(year)) for year in years if series.get(year) is not None]
        if len(points) < 2:
            _annotate_missing(ax, f"{label}\nn/d")
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        ax.plot(xs, ys, color=blue, linewidth=2.0, marker="o", markersize=4,
                markeredgecolor=PALETTE["surface"], markeredgewidth=1.5, zorder=3)
        ax.fill_between(xs, ys, min(min(ys), 0), color=blue, alpha=0.12, linewidth=0)
        suffix = "%" if unit == "%" else ("x" if unit == "x" else "")
        last = ys[-1]
        text = f"{last:,.1f}{suffix}" if unit != "abs" else _compact(last)
        ax.annotate(text, xy=(xs[-1], last), xytext=(0, 8), textcoords="offset points",
                    ha="center", fontsize=8.5, color=PALETTE["ink"], fontweight="600")
        ax.set_title(label, loc="left", fontsize=9.5)
        ax.set_xticks(xs)
        ax.set_xticklabels([str(year) for year in xs], fontsize=7.5)
        if unit == "abs":
            ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: _compact(value)))
        _clean_axes(ax)

    for index in range(len(metrics), len(axes_list)):
        axes_list[index].set_visible(False)

    if axes is None:
        figure.tight_layout()
    if save:
        save_figure(figure, save)
    return figure


def _compact(value: Optional[float]) -> str:
    if value is None:
        return "n/d"
    for divisor, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= divisor:
            return f"{value / divisor:,.1f}{unit}"
    return f"{value:,.0f}"


# ---------------------------------------------------------------------------
# 5. Mappa di calore dell'universo
# ---------------------------------------------------------------------------


def plot_universe_heatmap(
    rows: Sequence[Mapping[str, Any]],
    *,
    ax: Optional[Any] = None,
    save: Optional[str] = None,
) -> Any:
    """Punteggi di qualita' di piu' titoli, tutti sulla stessa scala 0-100.

    Una sola scala di magnitudine, quindi una sola tinta e una sola legenda. Ogni
    cella riporta anche il numero: il colore non e' mai l'unico modo di leggere il dato.
    """
    _require_matplotlib()
    apply_style()

    if ax is None:
        figure, ax = plt.subplots(figsize=(9, 0.5 * max(len(rows), 3) + 2.2))
    else:
        figure = ax.figure

    usable = [row for row in rows if row.get("quality_score") is not None]
    if not usable or np is None:
        _annotate_missing(ax, "Nessun punteggio disponibile per l'universo")
        if save:
            save_figure(figure, save)
        return figure

    usable = sorted(usable, key=lambda row: row["quality_score"], reverse=True)
    columns = [
        ("Quality Score", lambda r: r.get("quality_score")),
        ("Profittabilita'", lambda r: (r.get("category_scores") or {}).get("profitability", {}).get("score")),
        ("Consistenza", lambda r: (r.get("category_scores") or {}).get("consistency", {}).get("score")),
        ("Solidita'", lambda r: (r.get("category_scores") or {}).get("balance_sheet", {}).get("score")),
    ]
    matrix = np.array([
        [(getter(row) if getter(row) is not None else np.nan) for _, getter in columns]
        for row in usable
    ], dtype=float)

    colormap = _cmap_sequential()
    colormap.set_bad(PALETTE["grid"])
    image = ax.imshow(matrix, cmap=colormap, vmin=0, vmax=100, aspect="auto")

    ax.set_xticks(range(len(columns)))
    ax.set_xticklabels([name for name, _ in columns], fontsize=8.5,
                       color=PALETTE["ink_secondary"])
    ax.set_yticks(range(len(usable)))
    ax.set_yticklabels([row.get("ticker", "?") for row in usable], fontsize=9,
                       color=PALETTE["ink_secondary"])

    for row_index in range(matrix.shape[0]):
        for column_index in range(matrix.shape[1]):
            value = matrix[row_index, column_index]
            if np.isnan(value):
                continue
            # Inchiostro chiaro sui fondi scuri, scuro sui chiari: la leggibilita'
            # del numero non deve dipendere dal valore della cella.
            text_color = PALETTE["surface"] if value < 50 else PALETTE["ink"]
            ax.text(column_index, row_index, f"{value:.0f}", ha="center", va="center",
                    fontsize=8.5, color=text_color)

    # Separatori di 2px in colore superficie invece dei bordi attorno alle celle.
    ax.set_xticks([x - 0.5 for x in range(1, len(columns))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(usable))], minor=True)
    ax.grid(which="minor", color=PALETTE["surface"], linewidth=2)
    ax.grid(which="major", visible=False)
    ax.tick_params(which="minor", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    colorbar = figure.colorbar(image, ax=ax, fraction=0.025, pad=0.02)
    colorbar.set_label("punteggio 0-100", color=PALETTE["ink_secondary"], fontsize=8.5)
    colorbar.ax.tick_params(colors=PALETTE["muted"], labelsize=7.5)
    colorbar.outline.set_edgecolor(PALETTE["axis"])

    ax.set_title("Qualita' dell'universo analizzato", loc="left", pad=12)
    figure.tight_layout()
    if save:
        save_figure(figure, save)
    return figure


# ---------------------------------------------------------------------------
# 6. Matrice qualita' / sconto
# ---------------------------------------------------------------------------


def plot_quality_value_scatter(
    rows: Sequence[Mapping[str, Any]],
    *,
    quality_threshold: float = 65.0,
    margin_threshold: float = 0.30,
    ax: Optional[Any] = None,
    save: Optional[str] = None,
) -> Any:
    """Qualita' (x) contro margine di sicurezza (y), con i quadranti operativi.

    In alto a destra c'e' l'unico quadrante che interessa a un value investor:
    aziende buone comprabili a sconto. Gli altri tre servono a ricordare perche'
    la maggior parte delle idee va scartata.
    """
    _require_matplotlib()
    apply_style()

    if ax is None:
        figure, ax = plt.subplots(figsize=(8.5, 6))
    else:
        figure = ax.figure

    points = [
        row for row in rows
        if row.get("quality_score") is not None and row.get("margin_of_safety") is not None
    ]
    if not points:
        _annotate_missing(ax, "Servono sia il punteggio di qualita' sia il margine di sicurezza")
        if save:
            save_figure(figure, save)
        return figure

    xs = [row["quality_score"] for row in points]
    ys = [row["margin_of_safety"] * 100.0 for row in points]

    ax.axhspan(margin_threshold * 100.0, max(max(ys) + 15, 100), xmin=0, xmax=1,
               color=PALETTE["status"]["good"], alpha=0.07, linewidth=0, zorder=0)
    ax.axhline(margin_threshold * 100.0, color=PALETTE["axis"], linewidth=1.0, zorder=1)
    ax.axhline(0, color=PALETTE["axis"], linewidth=1.0, zorder=1)
    ax.axvline(quality_threshold, color=PALETTE["axis"], linewidth=1.0, zorder=1)

    blue = PALETTE["series"][0]
    ax.scatter(xs, ys, s=110, color=blue, edgecolor=PALETTE["surface"], linewidth=2, zorder=3)
    for row, x, y in zip(points, xs, ys):
        ax.annotate(row.get("ticker", "?"), xy=(x, y), xytext=(0, 11),
                    textcoords="offset points", ha="center", fontsize=8.5,
                    color=PALETTE["ink"])

    ax.text(0.99, 0.98, "qualita' alta, a sconto", transform=ax.transAxes, ha="right",
            va="top", fontsize=8.5, color=PALETTE["status"]["good"])
    ax.text(0.01, 0.02, "qualita' bassa, cara", transform=ax.transAxes, ha="left",
            va="bottom", fontsize=8.5, color=PALETTE["muted"])

    ax.set_xlabel("Quality Score (0-100)")
    ax.set_ylabel("Margine di sicurezza %")
    ax.set_title("Dove comprare: qualita' contro sconto", loc="left", pad=12)
    _clean_axes(ax, grid_axis="both")
    figure.tight_layout()
    if save:
        save_figure(figure, save)
    return figure


# ---------------------------------------------------------------------------
# 7. Superficie di sensitivita'
# ---------------------------------------------------------------------------


def plot_sensitivity_surface(
    grid: Mapping[str, Any],
    *,
    kind: str = "surface",
    diverging_at: Optional[float] = None,
    percent_axes: bool = False,
    title: Optional[str] = None,
    ax: Optional[Any] = None,
    save: Optional[str] = None,
) -> Any:
    """Superficie 3D (o curve di livello) di una griglia di sensitivita'.

    Args:
        grid: output di ``valuation.sensitivity_grid`` o ``backtest.sweep_parameters``.
        kind: ``"surface"`` per il 3D, ``"contour"`` per la versione piatta —
            piu' brutta e piu' precisa, perche' in 3D la prospettiva nasconde celle.
        diverging_at: se valorizzato, usa la scala divergente centrata su questo
            valore (es. ``0`` per un extra-rendimento: sotto e sopra sono opposti).
        percent_axes: formatta gli assi x/y come percentuali.

    La scala di colore e' a **tinta unica** quando misura una magnitudine: un
    arcobaleno crea bande che il lettore scambia per soglie.
    """
    _require_matplotlib()
    apply_style()
    if np is None:
        raise ImportError("numpy non installato.")

    x_values = np.array(grid.get("x_values") or [], dtype=float)
    y_values = np.array(grid.get("y_values") or [], dtype=float)
    raw = grid.get("values") or []
    matrix = np.array(
        [[np.nan if cell is None else float(cell) for cell in row] for row in raw],
        dtype=float,
    ) if raw else np.zeros((0, 0))

    # La forma dei dati si controlla prima di creare gli assi: senza griglia non
    # serve (e non funzionerebbe) una proiezione 3D.
    usable = matrix.size > 0 and matrix.shape == (len(y_values), len(x_values))

    if ax is None:
        figure = plt.figure(figsize=(9, 6.5))
        ax = figure.add_subplot(111, projection="3d" if (kind == "surface" and usable) else None)
    else:
        figure = ax.figure

    if not usable:
        _annotate_missing(ax, "Griglia di sensitivita' non disponibile")
        if save:
            save_figure(figure, save)
        return figure

    if diverging_at is not None:
        spread = np.nanmax(np.abs(matrix - diverging_at))
        colormap = _cmap_diverging()
        norm = Normalize(vmin=diverging_at - spread, vmax=diverging_at + spread)
    else:
        colormap = _cmap_sequential()
        norm = Normalize(vmin=np.nanmin(matrix), vmax=np.nanmax(matrix))

    mesh_x, mesh_y = np.meshgrid(x_values, y_values)
    scale = 100.0 if percent_axes else 1.0
    label_suffix = "%" if percent_axes else ""

    x_label = f"{grid.get('x_label', 'x')} (%)" if percent_axes else str(grid.get("x_label", "x"))
    y_label = f"{grid.get('y_label', 'y')} (%)" if percent_axes else str(grid.get("y_label", "y"))

    if kind == "surface":
        surface = ax.plot_surface(
            mesh_x * scale, mesh_y * scale, matrix, cmap=colormap, norm=norm,
            linewidth=0.3, edgecolor=PALETTE["surface"], antialiased=True, alpha=0.95,
        )
        ax.set_xlabel(x_label, labelpad=12)
        ax.set_ylabel(y_label, labelpad=12)
        ax.set_zlabel(grid.get("z_label", "z"), labelpad=12)
        # Tacche solo sui valori realmente calcolati: le posizioni intermedie
        # suggerirebbero una risoluzione che la griglia non ha.
        ax.set_xticks(x_values * scale)
        ax.set_yticks(y_values * scale)
        ax.view_init(elev=26, azim=-135)
        ax.set_facecolor(PALETTE["surface"])
        for pane_axis in (ax.xaxis, ax.yaxis, ax.zaxis):
            pane_axis.set_pane_color((0.102, 0.102, 0.098, 1.0))
            pane_axis._axinfo["grid"]["color"] = PALETTE["grid"]
            pane_axis._axinfo["grid"]["linewidth"] = 0.5
        # L'asse z nomina gia' la grandezza: la barra dei colori porta solo la scala.
        colorbar = figure.colorbar(surface, ax=ax, fraction=0.026, pad=0.12, shrink=0.65)
    else:
        image = ax.pcolormesh(
            mesh_x * scale, mesh_y * scale, matrix, cmap=colormap, norm=norm, shading="nearest",
        )
        contours = ax.contour(
            mesh_x * scale, mesh_y * scale, matrix, colors=PALETTE["surface"], linewidths=0.8,
        )
        ax.clabel(contours, inline=True, fontsize=7.5, colors=PALETTE["ink_secondary"])
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_xticks(x_values * scale)
        ax.set_yticks(y_values * scale)
        _clean_axes(ax, grid_axis="both")
        ax.grid(False)
        colorbar = figure.colorbar(image, ax=ax, fraction=0.035, pad=0.02)
        colorbar.set_label(grid.get("z_label", ""), color=PALETTE["ink_secondary"], fontsize=8.5)

    colorbar.ax.tick_params(colors=PALETTE["muted"], labelsize=7.5)
    colorbar.outline.set_edgecolor(PALETTE["axis"])

    ax.set_title(
        title or f"Sensitivita': {grid.get('z_label', '')}"
                 f" al variare di {grid.get('x_label')} e {grid.get('y_label')}",
        loc="left", pad=16,
    )
    if save:
        save_figure(figure, save)
    return figure


# ---------------------------------------------------------------------------
# 8. Tear sheet composito
# ---------------------------------------------------------------------------


def create_tearsheet(
    *,
    quality: Optional[Mapping[str, Any]] = None,
    valuation: Optional[Mapping[str, Any]] = None,
    backtest: Optional[Mapping[str, Any]] = None,
    title: Optional[str] = None,
    save: Optional[str] = None,
) -> Any:
    """Una pagina sola con il quadro completo: qualita', valore e comportamento storico."""
    _require_matplotlib()
    apply_style()

    figure = plt.figure(figsize=(15, 15.5))
    grid = GridSpec(
        4, 3, figure=figure, height_ratios=[0.32, 1.15, 1.0, 1.5],
        hspace=0.42, wspace=0.28,
    )

    # Intestazione con i numeri chiave, come stat tile: la cifra e' il grafico.
    header = figure.add_subplot(grid[0, :])
    header.axis("off")
    ticker = (
        (quality or {}).get("ticker") or (valuation or {}).get("ticker") or ""
    )
    company = (
        (valuation or {}).get("company_name") or (quality or {}).get("company_name") or ticker
    )
    header.text(0, 0.72, title or f"{company}  ({ticker})", fontsize=19,
                color=PALETTE["ink"], fontweight="600", va="top")

    tiles: List[Tuple[str, str, str]] = []
    if quality and quality.get("quality_score") is not None:
        tiles.append(("Quality Score", f"{quality['quality_score']:.0f}", quality.get("rating", "")))
    if valuation:
        if valuation.get("price") is not None:
            tiles.append(("Prezzo", f"{valuation['price']:,.2f}", valuation.get("currency") or ""))
        fair_value = (valuation.get("fair_value") or {}).get("point")
        if fair_value is not None:
            tiles.append(("Fair value", f"{fair_value:,.2f}", valuation.get("currency") or ""))
        if valuation.get("margin_of_safety") is not None:
            tiles.append((
                "Margine di sicurezza",
                f"{valuation['margin_of_safety'] * 100:+.1f}%",
                valuation.get("verdict", ""),
            ))
        implied = (valuation.get("reverse_dcf") or {}).get("implied_growth")
        if implied is not None:
            tiles.append(("Crescita implicita", f"{implied * 100:.1f}%", "scontata dal prezzo"))
    if backtest and (backtest.get("metrics") or {}).get("cagr") is not None:
        tiles.append(("CAGR strategia", f"{backtest['metrics']['cagr'] * 100:.1f}%",
                      f"Sharpe {backtest['metrics'].get('sharpe') or 0:.2f}"))

    for index, (label, value, note) in enumerate(tiles[:6]):
        x = index * (1.0 / 6)
        header.text(x, 0.30, label, fontsize=8.5, color=PALETTE["muted"], va="top")
        header.text(x, 0.16, value, fontsize=17, color=PALETTE["ink"], va="top")
        header.text(x, -0.24, note, fontsize=8, color=PALETTE["ink_secondary"], va="top")

    # Riga 2: football field + radar
    if valuation:
        plot_football_field(valuation, ax=figure.add_subplot(grid[1, :2]))
    else:
        _annotate_missing(figure.add_subplot(grid[1, :2]), "Valutazione non calcolata")
    if quality:
        plot_quality_radar(quality, ax=figure.add_subplot(grid[1, 2], projection="polar"))
    else:
        _annotate_missing(figure.add_subplot(grid[1, 2]), "Qualita' non calcolata")

    # Riga 3: storico delle metriche
    if quality:
        history_axes = [figure.add_subplot(grid[2, column]) for column in range(3)]
        plot_metrics_history(
            quality,
            metrics=(("roic", "ROIC", "%"), ("operating_margin", "Margine operativo", "%"),
                     ("owner_earnings", "Owner Earnings", "abs")),
            axes=history_axes,
        )

    # Riga 4: backtest
    if backtest:
        equity_axes = (figure.add_subplot(grid[3, :]), None)
        inner = grid[3, :].subgridspec(2, 1, height_ratios=[3, 1], hspace=0.08)
        equity_axes[0].remove()
        plot_equity_curve(
            backtest,
            axes=(figure.add_subplot(inner[0]), figure.add_subplot(inner[1])),
        )

    if save:
        save_figure(figure, save)
    return figure


# ---------------------------------------------------------------------------
# Demo da riga di comando
# ---------------------------------------------------------------------------


def demo(show: bool = False, output_dir: str = "output") -> List[str]:
    """Genera tutti i grafici con dati sintetici: serve a vedere lo stile senza rete."""
    if np is None or pd is None:
        print("numpy/pandas non disponibili.")
        return []

    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2019-01-02", "2024-12-31")
    equity = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0006, 0.010, len(dates)))), index=dates)
    benchmark = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.011, len(dates)))), index=dates)
    try:  # le metriche mostrate devono venire dalla serie disegnata, non da numeri finti
        from .backtest import performance_metrics
    except ImportError:
        from backtest import performance_metrics  # type: ignore[no-redef]
    backtest = {
        "equity_curve": equity,
        "benchmark_curve": benchmark,
        "drawdown": equity / equity.cummax() - 1.0,
        "metrics": performance_metrics(equity.pct_change().dropna()),
    }

    years = [2019, 2020, 2021, 2022, 2023, 2024]
    quality = {
        "ticker": "ALFA", "company_name": "Societa' Alfa", "quality_score": 78.7,
        "rating": "Buona", "years_analyzed": years,
        "metrics": {
            "roic": dict(zip(years, [35.7, 37.7, 56.0, 69.3, 67.3, 78.6])),
            "operating_margin": dict(zip(years, [24.2, 24.1, 29.5, 30.2, 29.8, 30.8])),
            "net_margin": dict(zip(years, [21.2, 20.8, 26.0, 25.4, 25.3, 24.8])),
            "owner_earnings": dict(zip(years, [53.5e9, 66.4e9, 109.2e9, 101.6e9, 90.9e9, 104.6e9])),
            "debt_to_equity": dict(zip(years, [1.20, 1.88, 2.16, 2.40, 1.79, 1.86])),
            "interest_coverage": dict(zip(years, [17.5, 22.8, 41.5, 41.0, 29.2, 31.5])),
        },
        "category_scores": {
            "profitability": {"score": 100.0, "components": {
                "roic": {"score": 100.0}, "operating_margin": {"score": 100.0},
                "owner_earnings_margin": {"score": 100.0}}},
            "consistency": {"score": 67.6, "components": {
                "roic_stability": {"score": 53.3}, "revenue_growth_years": {"score": 66.7}}},
            "balance_sheet": {"score": 61.3, "components": {
                "debt_to_equity": {"score": 25.8}, "interest_coverage": {"score": 100.0},
                "current_ratio": {"score": 26.5}}},
        },
    }

    valuation = {
        "ticker": "ALFA", "company_name": "Societa' Alfa", "currency": "USD", "price": 180.0,
        "fair_value": {"point": 213.1, "low": 160.4, "high": 226.2},
        "margin_of_safety": 0.155, "verdict": "Moderatamente sottovalutata",
        "reverse_dcf": {"implied_growth": 0.061},
        "methods": {
            "dcf": {"label": "DCF Owner Earnings", "value_per_share": 226.2, "aggregated": True},
            "epv": {"label": "EPV (crescita zero)", "value_per_share": 160.4, "aggregated": True},
            "multiples": {"label": "Multipli storici", "value_per_share": 208.7, "aggregated": True},
            "graham": {"label": "Graham Number", "value_per_share": 92.4, "aggregated": False},
        },
        "scenarios": {
            "bear": {"value_per_share": 150.1}, "base": {"value_per_share": 226.2},
            "bull": {"value_per_share": 318.4},
        },
    }
    # Il football field usa la chiave del DCF per riconoscere la riga con lo scenario.
    valuation["methods"]["dcf_owner_earnings"] = valuation["methods"].pop("dcf")

    universe = []
    for index, ticker in enumerate(["ALFA", "BETA", "GAMMA", "DELTA", "EPSILON", "ZETA"]):
        universe.append({
            "ticker": ticker,
            "quality_score": 88 - index * 9.5,
            "margin_of_safety": 0.42 - index * 0.14,
            "category_scores": {
                "profitability": {"score": 95 - index * 11},
                "consistency": {"score": 80 - index * 8},
                "balance_sheet": {"score": 72 - index * 6},
            },
        })

    wacc_axis = [0.07, 0.08, 0.09, 0.10, 0.11]
    growth_axis = [0.015, 0.020, 0.025, 0.030, 0.035]
    values = [
        [round(120 * (0.09 / w) ** 1.6 * (1 + (g - 0.025) * 8), 2) for w in wacc_axis]
        for g in growth_axis
    ]
    sensitivity = {
        "x_label": "WACC", "x_values": wacc_axis, "y_label": "Crescita terminale",
        "y_values": growth_axis, "z_label": "Valore per azione", "values": values,
    }

    outputs: List[str] = []
    charts = [
        ("equity_curve.png", lambda path: plot_equity_curve(backtest, save=path)),
        ("football_field.png", lambda path: plot_football_field(valuation, save=path)),
        ("quality_radar.png", lambda path: plot_quality_radar(quality, save=path)),
        ("metrics_history.png", lambda path: plot_metrics_history(quality, save=path)),
        ("universe_heatmap.png", lambda path: plot_universe_heatmap(universe, save=path)),
        ("quality_value_scatter.png", lambda path: plot_quality_value_scatter(universe, save=path)),
        ("sensitivity_surface.png",
         lambda path: plot_sensitivity_surface(sensitivity, kind="surface", percent_axes=True, save=path)),
        ("sensitivity_contour.png",
         lambda path: plot_sensitivity_surface(sensitivity, kind="contour", percent_axes=True, save=path)),
        ("tearsheet.png",
         lambda path: create_tearsheet(quality=quality, valuation=valuation,
                                       backtest=backtest, save=path)),
    ]
    for filename, builder in charts:
        path = os.path.join(output_dir, filename)
        builder(path)
        outputs.append(path)
        print(f"  salvato {path}")
        if not show:
            plt.close("all")

    if show:
        plt.show()
    return outputs


if __name__ == "__main__":
    _require_matplotlib()
    show_charts = "--show" in sys.argv
    target = "output"
    for index, argument in enumerate(sys.argv):
        if argument == "--out" and index + 1 < len(sys.argv):
            target = sys.argv[index + 1]
    print(f"Demo dei grafici con dati sintetici -> {target}/\n")
    demo(show=show_charts, output_dir=target)
