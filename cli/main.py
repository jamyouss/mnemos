from __future__ import annotations

import os
import sys

import click
import httpx
from rich.console import Console
from rich.table import Table

console = Console()

DEFAULT_RAG_URL = "http://localhost:8100"


def _base_url() -> str:
    return os.environ.get("MNEMOS_URL", DEFAULT_RAG_URL).rstrip("/")


def _handle_http_error(exc: Exception) -> None:
    console.print(f"[bold red]Error:[/bold red] {exc}")
    sys.exit(1)


def _parse_tags(value: str) -> list[str]:
    """Split a comma-separated tag list and drop empty/whitespace entries."""
    return [t.strip() for t in value.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """Mnemos CLI — search, reindex, and manage memory."""


# ---------------------------------------------------------------------------
# rag status
# ---------------------------------------------------------------------------


@cli.command()
def doctor() -> None:
    """End-to-end health check: server up, Qdrant up, LLM reachable, sample search works."""
    ok = True

    def _check(label: str, fn) -> bool:
        nonlocal ok
        try:
            detail = fn()
            console.print(f"  [green]✓[/green] {label}" + (f" [dim]({detail})[/dim]" if detail else ""))
            return True
        except Exception as exc:
            console.print(f"  [red]✗[/red] {label}: [red]{exc}[/red]")
            ok = False
            return False

    console.print(f"[bold]Mnemos doctor[/bold]  target = {_base_url()}\n")

    # 1. /health
    def _health():
        r = httpx.get(f"{_base_url()}/health", timeout=5)
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "healthy":
            raise RuntimeError(f"unexpected body: {body!r}")
        return "OK"
    _check("server /health responds healthy", _health)

    # 2. /api/status — collections + counts
    collections: dict = {}
    def _status():
        nonlocal collections
        r = httpx.get(f"{_base_url()}/api/status", timeout=10)
        r.raise_for_status()
        collections = r.json().get("collections", {})
        return f"{len(collections)} collections"
    _check("Qdrant reachable + collections enumerable", _status)

    # 3. Core collections present
    expected = {"mnemos_skills", "mnemos_docs", "mnemos_code", "mnemos_memory"}
    missing = expected - set(collections.keys())
    if missing:
        console.print(f"  [yellow]⚠[/yellow] missing core collections: {sorted(missing)}")
    else:
        console.print(f"  [green]✓[/green] core collections present "
                      f"[dim](mnemos_code: {collections.get('mnemos_code',{}).get('points_count',0)} points)[/dim]")

    # 4. At least one collection has data
    has_data = any((info.get("points_count") or 0) > 0 for info in collections.values())
    if not has_data:
        console.print("  [yellow]⚠[/yellow] no points indexed yet — run `mnemos reindex --recreate "
                      "--full --collection mnemos_code --path /data/codebase`")

    # 5. Sample search round-trip
    def _search():
        r = httpx.post(
            f"{_base_url()}/api/search",
            json={"query": "function", "limit": 1},
            timeout=30,
        )
        r.raise_for_status()
        return f"{len(r.json().get('results', []))} hits"
    _check("/api/search round-trip", _search)

    console.print()
    if ok:
        console.print("[bold green]All checks passed.[/bold green]")
    else:
        console.print("[bold red]Some checks failed.[/bold red] See above.")
        sys.exit(1)


@cli.command()
def status() -> None:
    """Show server health and collection counts."""
    url = f"{_base_url()}/api/status"
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _handle_http_error(exc)
        return

    server_status = data.get("status", "unknown")
    color = "green" if server_status == "healthy" else "red"
    console.print(f"Status: [{color}]{server_status}[/{color}]")

    collections = data.get("collections", {})
    if not collections:
        console.print("No collections found.")
        return

    table = Table(title="Collections")
    table.add_column("Collection", style="cyan")
    table.add_column("Vectors", justify="right")
    table.add_column("Points", justify="right")
    table.add_column("Status")

    for name, info in collections.items():
        if "error" in info:
            table.add_row(name, "-", "-", f"[red]{info['error']}[/red]")
        else:
            table.add_row(
                name,
                str(info.get("vectors_count", "-")),
                str(info.get("points_count", "-")),
                str(info.get("status", "-")),
            )

    console.print(table)


# ---------------------------------------------------------------------------
# rag search
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("query")
@click.option("--limit", default=5, show_default=True, help="Number of results to return.")
@click.option("--collection", multiple=True, help="Restrict to specific collections.")
@click.option("--file-type", multiple=True, help="Restrict to specific file types.")
@click.option("--path-filter", default=None, help="Filter by file path substring.")
@click.option(
    "--tags",
    default=None,
    help="Comma-separated tags, OR semantics (sent as tags_any). Example: --tags acme,moby",
)
@click.option(
    "--tags-all",
    "tags_all",
    default=None,
    help="Comma-separated tags, AND semantics (sent as tags_all). Example: --tags-all acme,vue3",
)
@click.option(
    "--mode",
    type=click.Choice(["preview", "full"]),
    default="preview",
    show_default=True,
    help="preview truncates each chunk; full returns whole chunks.",
)
def search(
    query: str,
    limit: int,
    collection: tuple,
    file_type: tuple,
    path_filter: str | None,
    tags: str | None,
    tags_all: str | None,
    mode: str,
) -> None:
    """Search across all indexed documents."""
    url = f"{_base_url()}/api/search"
    payload: dict = {"query": query, "limit": limit, "mode": mode}
    if collection:
        payload["collections"] = list(collection)
    if file_type:
        payload["file_types"] = list(file_type)
    if path_filter:
        payload["path_filter"] = path_filter
    if tags:
        payload["tags_any"] = _parse_tags(tags)
    if tags_all:
        payload["tags_all"] = _parse_tags(tags_all)

    try:
        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        _handle_http_error(exc)
        return

    _print_results(results)


# ---------------------------------------------------------------------------
# rag search-code
# ---------------------------------------------------------------------------


@cli.command("search-code")
@click.argument("query")
@click.option("--limit", default=5, show_default=True, help="Number of results to return.")
@click.option("--language", default=None, help="Filter by programming language.")
@click.option("--symbol-type", default=None, help="Filter by symbol type (func, type, etc.).")
@click.option(
    "--tags",
    "tags",
    default=None,
    help="Comma-separated tags (OR filter). Example: --tags acme,moby-services",
)
@click.option(
    "--tags-all",
    "tags_all",
    default=None,
    help="Comma-separated tags (AND filter). Example: --tags-all acme,vue3",
)
@click.option("--path-filter", default=None, help="Filter by file path substring.")
@click.option(
    "--mode",
    type=click.Choice(["preview", "full"]),
    default="preview",
    show_default=True,
    help="preview truncates each chunk; full returns whole chunks.",
)
def search_code(
    query: str,
    limit: int,
    language: str | None,
    symbol_type: str | None,
    tags: str | None,
    tags_all: str | None,
    path_filter: str | None,
    mode: str,
) -> None:
    """Search code-specific index."""
    url = f"{_base_url()}/api/search-code"
    payload: dict = {"query": query, "limit": limit, "mode": mode}
    if language:
        payload["language"] = language
    if symbol_type:
        payload["symbol_type"] = symbol_type
    if tags:
        payload["tags_any"] = _parse_tags(tags)
    if tags_all:
        payload["tags_all"] = _parse_tags(tags_all)
    if path_filter:
        payload["path_filter"] = path_filter

    try:
        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        _handle_http_error(exc)
        return

    _print_results(results)


# ---------------------------------------------------------------------------
# rag search-skills
# ---------------------------------------------------------------------------


@cli.command("search-skills")
@click.argument("query")
@click.option("--limit", default=3, show_default=True, help="Number of results to return.")
def search_skills(query: str, limit: int) -> None:
    """Search the skills index."""
    url = f"{_base_url()}/api/search-skills"
    payload: dict = {"query": query, "limit": limit}

    try:
        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        _handle_http_error(exc)
        return

    _print_skill_results(results)


# ---------------------------------------------------------------------------
# rag search-memory
# ---------------------------------------------------------------------------


@cli.command("search-memory")
@click.argument("query")
@click.option("--limit", default=5, show_default=True, help="Number of results to return.")
@click.option(
    "--tags",
    "tags",
    default=None,
    help="Comma-separated tags (OR filter). Example: --tags decision,convention",
)
@click.option(
    "--tags-all",
    "tags_all",
    default=None,
    help="Comma-separated tags (AND filter). Example: --tags-all moby,decision",
)
@click.option("--type", "memory_type", default=None, help="Filter by memory_type (decision, pattern, lesson, convention).")
@click.option(
    "--mode",
    type=click.Choice(["preview", "full"]),
    default="preview",
    show_default=True,
    help="preview truncates each entry's content; full returns whole entries.",
)
def search_memory(
    query: str,
    limit: int,
    tags: str | None,
    tags_all: str | None,
    memory_type: str | None,
    mode: str,
) -> None:
    """Search approved memory entries."""
    url = f"{_base_url()}/api/search-memory"
    payload: dict = {"query": query, "limit": limit, "mode": mode}
    if tags:
        payload["tags_any"] = _parse_tags(tags)
    if tags_all:
        payload["tags_all"] = _parse_tags(tags_all)
    if memory_type:
        payload["memory_type"] = memory_type

    try:
        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        _handle_http_error(exc)
        return

    if not results:
        console.print("[yellow]No memories found.[/yellow]")
        return

    for i, r in enumerate(results, start=1):
        score = r.get("score", 0)
        mtype = r.get("memory_type", "")
        result_tags = ", ".join(r.get("tags", []) or [])
        console.print(
            f"[bold]{i}.[/bold] [cyan]{mtype}[/cyan] "
            f"[dim](score: {score:.3f})[/dim]"
        )
        console.print(f"   {r.get('content', '')[:300]}")
        if result_tags:
            console.print(f"   [dim]tags: {result_tags}[/dim]")
        console.print()


# ---------------------------------------------------------------------------
# rag reindex
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--collection", required=True, help="Collection to reindex.")
@click.option("--path", default=None, help="Base path to scan for files.")
@click.option("--full", is_flag=True, default=False, help="Recursively reindex all files under path.")
@click.option(
    "--recreate",
    is_flag=True,
    default=False,
    help="Drop the collection before reindexing (required to migrate to the hybrid schema).",
)
@click.option(
    "--workers",
    default=1,
    show_default=True,
    type=int,
    help="Parallel worker threads (use 4 for contextual chunking).",
)
@click.option(
    "--tags",
    "tags",
    default=None,
    help="Comma-separated tags to attach to every file under --path "
    "(overrides auto-detected tags). Example: --tags moby,dgf,go",
)
def reindex(
    collection: str,
    path: str | None,
    full: bool,
    recreate: bool,
    workers: int,
    tags: str | None,
) -> None:
    """Trigger a reindex operation on the server."""
    url = f"{_base_url()}/api/reindex"
    payload: dict = {
        "collection": collection,
        "full": full,
        "recreate": recreate,
        "workers": workers,
    }
    if path:
        payload["path"] = path
    if tags:
        payload["tags"] = _parse_tags(tags)

    try:
        resp = httpx.post(url, json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _handle_http_error(exc)
        return

    status = data.get("status", "")
    if status == "reindex_started":
        suffix = " [yellow](recreated)[/yellow]" if data.get("recreated") else ""
        console.print(
            f"[green]Reindex started[/green] for collection [cyan]{data.get('collection')}[/cyan]"
            f"{suffix} at path [dim]{data.get('path', '')}[/dim] with [bold]{data.get('workers', 1)}[/bold] workers. "
            "Use [bold]mnemos status[/bold] to monitor progress."
        )
    else:
        console.print(f"[yellow]{status}[/yellow]: {data}")


# ---------------------------------------------------------------------------
# rag memory
# ---------------------------------------------------------------------------


@cli.group()
def memory() -> None:
    """Manage memory entries."""


@memory.command("list")
@click.option("--status", default="pending", show_default=True, help="Filter by status (pending, approved, rejected).")
def memory_list(status: str | None) -> None:
    """List memory entries (defaults to pending)."""
    url = f"{_base_url()}/api/memory"
    params: dict = {}
    if status:
        params["status"] = status

    try:
        resp = httpx.get(url, params=params, timeout=10)
        resp.raise_for_status()
        entries = resp.json().get("entries", [])
    except Exception as exc:
        _handle_http_error(exc)
        return
    if not entries:
        console.print("No memory entries found.")
        return

    table = Table(title="Memory Entries")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Type")
    table.add_column("Content")

    for entry in entries:
        entry_status = entry.get("status", "")
        status_color = {"approved": "green", "rejected": "red", "pending": "yellow"}.get(
            entry_status, "white"
        )
        table.add_row(
            str(entry.get("id", "")),
            f"[{status_color}]{entry_status}[/{status_color}]",
            str(entry.get("memory_type", "")),
            str(entry.get("content", ""))[:80],
        )

    console.print(table)


@memory.command("add")
@click.argument("content")
@click.option("--project", default=None, help="Associated project.")
@click.option("--type", "memory_type", default="general", show_default=True, help="Memory type.")
@click.option("--tags", multiple=True, help="Tags for this memory entry.")
def memory_add(content: str, project: str | None, memory_type: str, tags: tuple) -> None:
    """Add a new memory entry (status: approved)."""
    url = f"{_base_url()}/api/memory"
    payload: dict = {
        "content": content,
        "memory_type": memory_type,
        "tags": list(tags),
        "status": "approved",
    }
    if project:
        payload["project"] = project

    try:
        resp = httpx.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _handle_http_error(exc)
        return

    console.print(
        f"[green]Created[/green] memory entry with ID: [cyan]{data.get('id')}[/cyan]"
    )


@memory.command("approve")
@click.argument("mem_id")
def memory_approve(mem_id: str) -> None:
    """Approve a pending memory entry."""
    _review_memory(mem_id, "approve")


@memory.command("reject")
@click.argument("mem_id")
def memory_reject(mem_id: str) -> None:
    """Reject a pending memory entry."""
    _review_memory(mem_id, "reject")


def _review_memory(mem_id: str, action: str) -> None:
    url = f"{_base_url()}/api/memory/{mem_id}/review"
    try:
        resp = httpx.post(url, json={"action": action}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _handle_http_error(exc)
        return

    new_status = data.get("status", action + "d")
    color = "green" if new_status == "approved" else "red"
    console.print(
        f"Memory [{color}]{new_status}[/{color}]: [cyan]{data.get('id')}[/cyan]"
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _print_results(results: list) -> None:
    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    for i, item in enumerate(results, start=1):
        score = item.get("score", 0)
        file_path = item.get("file_path", "")
        content = item.get("content", "")
        console.print(f"[bold]{i}.[/bold] [cyan]{file_path}[/cyan] [dim](score: {score:.3f})[/dim]")
        if content:
            preview = content[:200].replace("\n", " ")
            console.print(f"   {preview}")
        console.print()


def _print_skill_results(results: list) -> None:
    if not results:
        console.print("[yellow]No skills found.[/yellow]")
        return

    for i, item in enumerate(results, start=1):
        score = item.get("score", 0)
        skill_name = item.get("skill_name", item.get("file_path", ""))
        description = item.get("description", item.get("content", ""))
        console.print(f"[bold]{i}.[/bold] [cyan]{skill_name}[/cyan] [dim](score: {score:.3f})[/dim]")
        if description:
            preview = description[:200].replace("\n", " ")
            console.print(f"   {preview}")
        console.print()


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------


@cli.group()
def eval() -> None:
    """Evaluation harness: golden set, baseline, comparisons."""


def _eval_paths() -> dict:
    from pathlib import Path

    root = Path(os.environ.get("MNEMOS_EVAL_ROOT", "evals"))
    return {
        "root": root,
        "golden": root / "dataset" / "golden.yaml",
        "candidates": root / "dataset" / "_candidates.yaml",
        "runs": root / "runs",
    }


@eval.command("generate")
@click.option("--collection", required=True, help="Collection to sample chunks from.")
@click.option("--count", default=10, show_default=True, help="Number of candidate questions to generate.")
@click.option(
    "--provider",
    default=lambda: os.environ.get("MNEMOS_LLM_PROVIDER", "ollama"),
    type=click.Choice(["ollama", "anthropic", "openai"]),
    help="LLM provider (default: $MNEMOS_LLM_PROVIDER or ollama).",
)
@click.option(
    "--model",
    default=lambda: os.environ.get("MNEMOS_LLM_MODEL", "llama3.1:8b"),
    help="Model name (default: $MNEMOS_LLM_MODEL).",
)
@click.option(
    "--api-key",
    default=lambda: os.environ.get("MNEMOS_LLM_API_KEY", ""),
    help="API key for anthropic/openai (default: $MNEMOS_LLM_API_KEY).",
)
@click.option(
    "--base-url",
    default=lambda: os.environ.get("MNEMOS_LLM_BASE_URL", "") or os.environ.get("MNEMOS_OLLAMA_URL", ""),
    help="Provider base URL override.",
)
@click.option("--seed", default=None, type=int, help="Optional random seed for sampling.")
def eval_generate(
    collection: str,
    count: int,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    seed: int | None,
) -> None:
    """Generate candidate Q/A pairs from a collection via the configured LLM."""
    from eval import GoldenGenerator
    from eval.loader import load_candidates, save_candidates
    from core.llm import LLMConfig, make_llm_provider

    sample_url = f"{_base_url()}/api/eval/sample"
    try:
        resp = httpx.post(
            sample_url,
            json={"collection": collection, "count": count, "seed": seed},
            timeout=30,
        )
        resp.raise_for_status()
        chunks = resp.json().get("chunks", [])
    except Exception as exc:
        _handle_http_error(exc)
        return

    if not chunks:
        console.print(f"[yellow]No chunks returned from {collection}.[/yellow]")
        return

    console.print(f"Generating questions from {len(chunks)} chunks via {provider}:{model}…")
    llm = make_llm_provider(
        LLMConfig(provider=provider, model=model, api_key=api_key, base_url=base_url)
    )
    generator = GoldenGenerator(llm=llm)
    new_candidates = generator.generate(chunks, count=len(chunks))

    if not new_candidates:
        console.print("[yellow]No candidates were generated (LLM returned empty).[/yellow]")
        return

    paths = _eval_paths()
    existing = load_candidates(paths["candidates"])
    save_candidates(paths["candidates"], existing + new_candidates)
    console.print(
        f"[green]+{len(new_candidates)}[/green] candidates written to {paths['candidates']}. "
        "Edit the file to set [bold]reviewed: true[/bold] and [bold]accepted: true[/bold] for items "
        "to keep, then run [bold]mnemos eval promote[/bold]."
    )


@eval.command("promote")
def eval_promote() -> None:
    """Move reviewed+accepted candidates into the golden set."""
    from eval.loader import promote_candidates

    paths = _eval_paths()
    promoted, remaining = promote_candidates(paths["candidates"], paths["golden"])
    console.print(
        f"Promoted [green]{promoted}[/green] candidates. "
        f"{remaining} candidates remain pending review."
    )


@eval.command("run")
@click.option("--tag", required=True, help="Tag for this run (e.g. baseline-2026-05-13).")
@click.option("--limit", default=10, show_default=True, help="Top-K to request per query.")
def eval_run(tag: str, limit: int) -> None:
    """Execute the eval harness against the running Mnemos server."""
    from eval import (
        EvalRun,
        EvalRunner,
        aggregate_metrics,
        load_golden,
        render_console,
        write_json,
    )

    paths = _eval_paths()
    items = load_golden(paths["golden"])
    if not items:
        console.print("[red]Golden set is empty.[/red] Generate questions first with [bold]mnemos eval generate[/bold].")
        sys.exit(1)

    runner = EvalRunner(mnemos_url=_base_url(), limit=limit)
    try:
        results = runner.run(items)
    except Exception as exc:
        _handle_http_error(exc)
        return

    report = aggregate_metrics(items, results, tag=tag)
    run = EvalRun(
        tag=tag,
        mnemos_url=_base_url(),
        golden_path=str(paths["golden"]),
        results=results,
        report=report,
    )
    out_path = paths["runs"] / f"{tag}.json"
    write_json(out_path, run)
    render_console(report)
    console.print(f"\n[dim]Run saved to {out_path}[/dim]")


@eval.command("compare")
@click.argument("tag_a")
@click.argument("tag_b")
def eval_compare(tag_a: str, tag_b: str) -> None:
    """Compare two eval runs by tag."""
    import json
    from eval.reporter import compare_reports
    from eval.schema import MetricsReport

    paths = _eval_paths()

    def _load(tag: str) -> MetricsReport:
        path = paths["runs"] / f"{tag}.json"
        if not path.exists():
            console.print(f"[red]Run not found:[/red] {path}")
            sys.exit(1)
        with path.open() as fh:
            data = json.load(fh)
        return MetricsReport(**data["report"])

    compare_reports(_load(tag_a), _load(tag_b))


@eval.command("list")
def eval_list() -> None:
    """List available eval runs."""
    from pathlib import Path

    paths = _eval_paths()
    runs_dir: Path = paths["runs"]
    if not runs_dir.exists():
        console.print("[yellow]No runs directory yet.[/yellow]")
        return
    runs = sorted(runs_dir.glob("*.json"))
    if not runs:
        console.print("[yellow]No runs found.[/yellow]")
        return
    table = Table(title="Eval runs")
    table.add_column("Tag", style="cyan")
    table.add_column("Path")
    for run in runs:
        table.add_row(run.stem, str(run))
    console.print(table)


if __name__ == "__main__":
    cli()
