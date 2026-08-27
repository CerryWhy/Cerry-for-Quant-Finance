"""Analisi completa di uno o piu' titoli: qualita', valore, grafici e (facoltativo) backtest.

E' il punto d'ingresso che mette insieme i quattro moduli di ``backend/models``.

Esempi::

    python run_analysis.py AAPL
    python run_analysis.py AAPL MSFT KO PG --out risultati
    python run_analysis.py AAPL MSFT KO PG --backtest
    python run_analysis.py AAPL --growth 0.06 --wacc 0.09 --json
    python run_analysis.py --demo            # dati sintetici, senza rete
    python run_analysis.py AAPL --lang it    # etichette dei grafici in italiano
    python run_analysis.py JPM               # profilo bancario riconosciuto da solo
    python run_analysis.py BRK-B --sector insurance   # profilo forzato a mano
    python run_analysis.py O SPG                     # profilo REIT: FFO e AFFO
    python run_analysis.py JPM --sec                 # voci mancanti da SEC EDGAR
    python run_analysis.py ENI.MI --overrides dati/miei.json   # voci inserite a mano
    python run_analysis.py KO --buffett      # criteri e tasso di sconto di Buffett
    python run_analysis.py GOOGL --capitalize-rd          # R&S come investimento
    python run_analysis.py PFE --capitalize-rd --rd-life 10   # vita utile del farmaceutico

Ogni esecuzione produce: i report testuali a schermo, i grafici in PNG nella cartella
di output e, con ``--json``, il dizionario completo dei risultati.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from models.backtest import (  # noqa: E402
    build_signal_panel,
    fetch_universe_data,
    format_backtest_report,
    rebalance_dates,
    run_backtest,
    sweep_parameters,
)
from models.quality_score import (  # noqa: E402
    DEFAULT_RD_LIFE,
    LOW_COVERAGE,
    calculate_quality_score,
    fetch_financials,
    format_report,
)
from models.valuation import (  # noqa: E402
    buffett_scorecard,
    calculate_valuation,
    fetch_market_data,
    format_buffett_scorecard,
    format_valuation_report,
)

from models.datasources import enrich_financials  # noqa: E402

try:
    from models import visualize
except ImportError:  # pragma: no cover - matplotlib assente
    visualize = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Analisi di un titolo
# ---------------------------------------------------------------------------


def analyze_ticker(
    ticker: str,
    *,
    years: int = 10,
    growth_override: Optional[float] = None,
    wacc_override: Optional[float] = None,
    sector: Optional[str] = None,
    mode: str = "standard",
    capitalize_rd: bool = False,
    rd_life: int = DEFAULT_RD_LIFE,
    sec: bool = False,
    overrides_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Qualita' + valutazione di un titolo, con un solo download dei bilanci.

    Il settore (industriale, banca, assicurazione) viene riconosciuto dalla struttura
    del bilancio e decide sia le metriche sia i metodi di valutazione. Si puo' forzare
    con ``sector`` quando il riconoscimento automatico sbaglia.
    """
    financials = fetch_financials(ticker, years=years)
    # Le fonti aggiuntive arricchiscono i prospetti prima di ogni calcolo: cosi' il
    # settore, le metriche e la valutazione vedono lo stesso bilancio completato.
    if sec or overrides_path:
        financials = enrich_financials(
            financials, ticker, sec=sec, overrides_path=overrides_path,
            quality=financials.get("data_quality"),
        )
    quality = calculate_quality_score(
        ticker, years=years, financials=financials, sector=sector, mode=mode,
        capitalize_rd=capitalize_rd, rd_life=rd_life,
    )
    market_data = fetch_market_data(ticker)
    valuation = calculate_valuation(
        ticker, financials=financials, market_data=market_data,
        growth_override=growth_override, wacc_override=wacc_override, years=years,
        sector=sector, mode=mode, capitalize_rd=capitalize_rd, rd_life=rd_life,
    )
    analysis = {
        "ticker": ticker.upper(),
        "quality": quality,
        "valuation": valuation,
        "financials": financials,
    }
    if str(mode).lower() == "buffett":
        analysis["scorecard"] = buffett_scorecard(quality, valuation)
    return analysis


def _universe_row(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Riga sintetica per i grafici d'insieme (heatmap e matrice qualita'/sconto)."""
    quality = analysis["quality"]
    valuation = analysis["valuation"]
    return {
        "ticker": analysis["ticker"],
        "quality_score": quality.get("quality_score"),
        "score_coverage": quality.get("score_coverage"),
        "category_scores": quality.get("category_scores"),
        "sector": quality.get("sector"),
        "sector_label": quality.get("sector_label"),
        "margin_of_safety": valuation.get("margin_of_safety"),
        "price": valuation.get("price"),
        "fair_value": (valuation.get("fair_value") or {}).get("point"),
        "verdict": valuation.get("verdict"),
    }


def print_summary_table(rows: Sequence[Dict[str, Any]]) -> None:
    """Tabella riassuntiva dell'universo, ordinata per punteggio di qualita'."""
    if not rows:
        return
    ordered = sorted(rows, key=lambda row: row.get("quality_score") or -1, reverse=True)
    width = 92
    print("=" * width)
    print(" RIEPILOGO UNIVERSO")
    print("=" * width)
    print(f" {'Ticker':<8}{'Quality':>9}{'Prezzo':>12}{'Fair value':>13}{'Margine':>10}"
          f"  {'Profilo':<22}Giudizio")
    print(" " + "-" * (width - 2))
    for row in ordered:
        quality = row.get("quality_score")
        margin = row.get("margin_of_safety")
        price = row.get("price")
        fair_value = row.get("fair_value")
        # L'asterisco segnala un punteggio costruito su una parte delle componenti
        # previste: due numeri vicini in questa colonna non sono confrontabili se uno
        # dei due lo porta.
        coverage = row.get("score_coverage")
        partial = coverage is not None and coverage < LOW_COVERAGE
        quality_text = f"{quality:.1f}{'*' if partial else ''}" if quality is not None else "n/d"
        price_text = f"{price:,.2f}" if price else "n/d"
        fair_text = f"{fair_value:,.2f}" if fair_value else "n/d"
        margin_text = f"{margin * 100:+.1f}%" if margin is not None else "n/d"
        profile_text = (row.get("sector_label") or "")[:20]
        print(
            f" {row['ticker']:<8}{quality_text:>9}{price_text:>12}"
            f"{fair_text:>13}{margin_text:>10}  {profile_text:<22}{row.get('verdict') or ''}"
        )
    if any((row.get("score_coverage") or 1.0) < LOW_COVERAGE for row in ordered):
        print(f" * punteggio parziale: meno del {LOW_COVERAGE:.0%} del peso previsto dal "
              "profilo ha prodotto un valore.")
    print("=" * width + "\n")


# ---------------------------------------------------------------------------
# Grafici
# ---------------------------------------------------------------------------


def build_charts(
    analyses: Sequence[Dict[str, Any]],
    *,
    backtest_result: Optional[Dict[str, Any]] = None,
    sweep: Optional[Dict[str, Any]] = None,
    output_dir: str = "output",
    show: bool = False,
) -> List[str]:
    """Genera e salva tutti i grafici disponibili per l'analisi corrente."""
    if visualize is None:
        print("matplotlib non installato: grafici saltati (`pip install matplotlib`).")
        return []

    saved: List[str] = []
    italian = visualize.get_language() == "it"
    rows = [_universe_row(analysis) for analysis in analyses]

    for analysis in analyses:
        ticker = analysis["ticker"]
        quality, valuation = analysis["quality"], analysis["valuation"]
        prefix = os.path.join(output_dir, ticker.lower())
        if quality.get("quality_score") is not None:
            saved.append(visualize.plot_quality_radar(
                quality, save=f"{prefix}_qualita.png") and f"{prefix}_qualita.png")
            saved.append(visualize.plot_metrics_history(
                quality, save=f"{prefix}_storico.png") and f"{prefix}_storico.png")
        if valuation.get("methods"):
            saved.append(visualize.plot_football_field(
                valuation, save=f"{prefix}_valutazione.png") and f"{prefix}_valutazione.png")
        if valuation.get("sensitivity", {}).get("values"):
            saved.append(visualize.plot_sensitivity_surface(
                valuation["sensitivity"], kind="surface", percent_axes=True,
                title=(f"Sensitivita' del valore · {ticker}" if italian
                       else f"Value sensitivity · {ticker}"),
                save=f"{prefix}_sensitivita.png") and f"{prefix}_sensitivita.png")
        saved.append(visualize.create_tearsheet(
            quality=quality, valuation=valuation, backtest=backtest_result,
            save=f"{prefix}_tearsheet.png") and f"{prefix}_tearsheet.png")

    if len(rows) > 1:
        saved.append(visualize.plot_universe_heatmap(
            rows, save=os.path.join(output_dir, "universo_qualita.png"))
            and os.path.join(output_dir, "universo_qualita.png"))
        saved.append(visualize.plot_quality_value_scatter(
            rows, save=os.path.join(output_dir, "universo_qualita_valore.png"))
            and os.path.join(output_dir, "universo_qualita_valore.png"))

    if backtest_result and backtest_result.get("equity_curve") is not None:
        saved.append(visualize.plot_equity_curve(
            backtest_result, save=os.path.join(output_dir, "backtest_equity.png"))
            and os.path.join(output_dir, "backtest_equity.png"))
    if sweep and sweep.get("values"):
        saved.append(visualize.plot_sensitivity_surface(
            sweep, kind="surface",
            title=("Robustezza della strategia ai parametri" if italian
                   else "Strategy robustness across parameters"),
            save=os.path.join(output_dir, "backtest_sensitivita.png"))
            and os.path.join(output_dir, "backtest_sensitivita.png"))

    paths = [path for path in saved if isinstance(path, str)]
    if show:
        visualize.plt.show()
    else:
        visualize.plt.close("all")
    return paths


# ---------------------------------------------------------------------------
# Serializzazione
# ---------------------------------------------------------------------------


def _serializable(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Copia dell'analisi senza i DataFrame, pronta per JSON."""
    return {
        "ticker": analysis["ticker"],
        "quality": analysis["quality"],
        "valuation": analysis["valuation"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str]) -> Dict[str, Any]:
    options: Dict[str, Any] = {
        "tickers": [], "out": "output", "backtest": False, "charts": True,
        "show": False, "json": False, "demo": False, "years": 10,
        "growth": None, "wacc": None, "top_n": 5, "sweep": False, "lang": "en", "sector": None, "mode": "standard",
        "capitalize_rd": False, "rd_life": DEFAULT_RD_LIFE,
        "sec": False, "overrides": None,
    }
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--out" and index + 1 < len(argv):
            options["out"] = argv[index + 1]; index += 2
        elif argument == "--growth" and index + 1 < len(argv):
            options["growth"] = float(argv[index + 1]); index += 2
        elif argument == "--wacc" and index + 1 < len(argv):
            options["wacc"] = float(argv[index + 1]); index += 2
        elif argument == "--top" and index + 1 < len(argv):
            options["top_n"] = int(argv[index + 1]); index += 2
        elif argument == "--years" and index + 1 < len(argv):
            options["years"] = int(argv[index + 1]); index += 2
        elif argument == "--backtest":
            options["backtest"] = True; index += 1
        elif argument == "--sweep":
            options["sweep"] = True; options["backtest"] = True; index += 1
        elif argument == "--no-charts":
            options["charts"] = False; index += 1
        elif argument == "--show":
            options["show"] = True; index += 1
        elif argument == "--json":
            options["json"] = True; index += 1
        elif argument == "--buffett":
            options["mode"] = "buffett"; index += 1
        elif argument == "--sec":
            options["sec"] = True; index += 1
        elif argument == "--overrides" and index + 1 < len(argv):
            options["overrides"] = argv[index + 1]; index += 2
        elif argument == "--capitalize-rd":
            options["capitalize_rd"] = True; index += 1
        elif argument == "--rd-life" and index + 1 < len(argv):
            options["rd_life"] = int(argv[index + 1]); index += 2
        elif argument == "--sector" and index + 1 < len(argv):
            options["sector"] = argv[index + 1]; index += 2
        elif argument == "--lang" and index + 1 < len(argv):
            options["lang"] = argv[index + 1]; index += 2
        elif argument == "--demo":
            options["demo"] = True; index += 1
        elif argument in ("-h", "--help"):
            print(__doc__); sys.exit(0)
        elif not argument.startswith("-"):
            options["tickers"].append(argument.upper()); index += 1
        else:
            print(f"Opzione sconosciuta: {argument}"); index += 1
    if not options["tickers"]:
        options["tickers"] = ["AAPL"]
    return options


def main(argv: Optional[Sequence[str]] = None) -> int:
    options = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    if visualize is not None:
        # I report a schermo restano in italiano; le etichette dei grafici seguono --lang.
        visualize.set_language(options["lang"])

    if options["demo"]:
        if visualize is None:
            print("matplotlib non installato.")
            return 1
        print(f"Demo con dati sintetici -> {options['out']}/\n")
        visualize.demo(show=options["show"], output_dir=options["out"])
        return 0

    tickers = options["tickers"]
    print(f"Analisi di {', '.join(tickers)} ...\n")

    analyses: List[Dict[str, Any]] = []
    for ticker in tickers:
        analysis = analyze_ticker(
            ticker, years=options["years"],
            growth_override=options["growth"], wacc_override=options["wacc"],
            sector=options["sector"], mode=options["mode"],
            capitalize_rd=options["capitalize_rd"], rd_life=options["rd_life"],
            sec=options["sec"], overrides_path=options["overrides"],
        )
        analyses.append(analysis)
        print(format_report(analysis["quality"]))
        print()
        print(format_valuation_report(analysis["valuation"]))
        print()
        if analysis.get("scorecard"):
            print(format_buffett_scorecard(analysis["scorecard"]))
            print()

    rows = [_universe_row(analysis) for analysis in analyses]
    if len(rows) > 1:
        print_summary_table(rows)

    backtest_result = None
    sweep = None
    if options["backtest"]:
        if len(tickers) < 3:
            print("Il backtest ha senso su almeno 3 titoli: saltato.\n")
        else:
            print(f"Backtest su {len(tickers)} titoli ...\n")
            data = fetch_universe_data(tickers, years=options["years"], benchmark="SPY")
            if data["prices"] is None or not data["financials"]:
                print("Dati insufficienti per il backtest.\n")
            else:
                dates = rebalance_dates(data["prices"].index, "annual")
                panel = build_signal_panel(data["financials"], data["prices"], dates)
                backtest_result = run_backtest(
                    prices=data["prices"], panel=panel, benchmark=data["benchmark"],
                    strategy={"top_n": options["top_n"]},
                )
                print(format_backtest_report(backtest_result))
                print()
                if options["sweep"] and not backtest_result.get("error"):
                    print("Sweep dei parametri (puo' richiedere qualche minuto) ...\n")
                    sweep = sweep_parameters(
                        prices=data["prices"], panel=panel, benchmark=data["benchmark"],
                        x_param="top_n", x_values=[2, 3, 4, 5, 6],
                        y_param="quality_weight", y_values=[0.0, 0.25, 0.5, 0.75, 1.0],
                        metric="sharpe",
                    )
                    print(f"  Sharpe medio sulla griglia: {sweep['mean']}")
                    print(f"  Cella migliore: {sweep['best']}")
                    print(f"  ATTENZIONE: {sweep['warning']}\n")

    if options["charts"]:
        print(f"Genero i grafici in {options['out']}/ ...")
        paths = build_charts(
            analyses, backtest_result=backtest_result, sweep=sweep,
            output_dir=options["out"], show=options["show"],
        )
        for path in paths:
            print(f"  {path}")
        print()

    if options["json"]:
        os.makedirs(options["out"], exist_ok=True)
        target = os.path.join(options["out"], "analisi.json")
        payload = {
            "tickers": tickers,
            "analyses": [_serializable(analysis) for analysis in analyses],
            "summary": rows,
        }
        if backtest_result:
            payload["backtest"] = {
                "strategy": backtest_result.get("strategy"),
                "metrics": backtest_result.get("metrics"),
                "benchmark_metrics": backtest_result.get("benchmark_metrics"),
                "holdings": backtest_result.get("holdings"),
                "caveats": backtest_result.get("caveats"),
            }
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
        print(f"Risultati completi salvati in {target}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
