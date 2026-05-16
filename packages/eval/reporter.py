from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from eval.schema import EvalRun, IntentMetrics, MetricsReport


def render_console(report: MetricsReport, console: Console | None = None) -> None:
    console = console or Console()

    console.print(f"\n[bold]Eval run: {report.tag}[/bold]  ({report.n_questions} questions)")
    console.print(
        f"  latency p50: {report.latency_p50_ms:.0f}ms  p95: {report.latency_p95_ms:.0f}ms\n"
    )

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Intent")
    table.add_column("N", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("NDCG@5", justify="right")
    table.add_column("R@1", justify="right")
    table.add_column("R@3", justify="right")
    table.add_column("R@5", justify="right")
    table.add_column("R@10", justify="right")
    table.add_column("P@5", justify="right")
    table.add_column("Hit@5", justify="right")

    def add(row: IntentMetrics, label: str | None = None) -> None:
        table.add_row(
            label or row.intent,
            str(row.n_questions),
            f"{row.mrr:.3f}",
            f"{row.ndcg_at_5:.3f}",
            f"{row.recall_at_1:.3f}",
            f"{row.recall_at_3:.3f}",
            f"{row.recall_at_5:.3f}",
            f"{row.recall_at_10:.3f}",
            f"{row.precision_at_5:.3f}",
            f"{row.hit_rate_at_5:.3f}",
        )

    for intent_row in report.by_intent:
        add(intent_row)

    table.add_section()
    add(report.overall, label="[bold]ALL[/bold]")

    console.print(table)


def write_json(path: Path, run: EvalRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(run.model_dump(), fh, indent=2, ensure_ascii=False)


def compare_reports(a: MetricsReport, b: MetricsReport, console: Console | None = None) -> None:
    console = console or Console()
    console.print(
        f"\n[bold]Comparing {a.tag} vs {b.tag}[/bold]  "
        f"({a.n_questions} vs {b.n_questions} questions)\n"
    )

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Metric")
    table.add_column(a.tag, justify="right")
    table.add_column(b.tag, justify="right")
    table.add_column("Δ", justify="right")

    def diff(x: float, y: float) -> str:
        d = y - x
        if d > 0:
            return f"[green]+{d:.3f}[/green]"
        if d < 0:
            return f"[red]{d:.3f}[/red]"
        return "0.000"

    for label, getter in [
        ("MRR", lambda r: r.overall.mrr),
        ("NDCG@5", lambda r: r.overall.ndcg_at_5),
        ("Recall@5", lambda r: r.overall.recall_at_5),
        ("Precision@5", lambda r: r.overall.precision_at_5),
        ("Hit@5", lambda r: r.overall.hit_rate_at_5),
        ("Latency p50 (ms)", lambda r: r.latency_p50_ms),
        ("Latency p95 (ms)", lambda r: r.latency_p95_ms),
    ]:
        va, vb = getter(a), getter(b)
        table.add_row(label, f"{va:.3f}", f"{vb:.3f}", diff(va, vb))

    console.print(table)
