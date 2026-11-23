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


def _headers() -> dict[str, str]:
    """Bearer header for servers running with MNEMOS_AUTH_ENABLED=true."""
    key = os.environ.get("MNEMOS_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


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
        r = httpx.get(f"{_base_url()}/health", timeout=5, headers=_headers())
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
        r = httpx.get(f"{_base_url()}/api/status", timeout=10, headers=_headers())
        r.raise_for_status()
        collections = r.json().get("collections", {})
        return f"{len(collections)} collections"
    _check("Qdrant reachable + collections enumerable", _status)

    # 3. Core collections present
    expected = {"mnemos_skills", "mnemos_docs", "mnemos_code", "mnemos_memory", "mnemos_knowledge"}
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
            headers=_headers(),
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
        resp = httpx.get(url, timeout=10, headers=_headers())
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
@click.option("--path-filter", default=None, help="Only this file: its exact indexed path, not a prefix.")
@click.option(
    "--tags",
    default=None,
    help="Comma-separated tags, OR semantics (sent as tags_any). Example: --tags acme,webshop",
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
        resp = httpx.post(url, json=payload, timeout=30, headers=_headers())
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
@click.option("--limit", default=3, show_default=True, help="Number of results to return.")
@click.option("--language", default=None, help="Filter by programming language.")
@click.option("--symbol-type", default=None, help="Filter by symbol type (func, type, etc.).")
@click.option(
    "--tags",
    "tags",
    default=None,
    help="Comma-separated tags (OR filter). Example: --tags acme,webshop-services",
)
@click.option(
    "--tags-all",
    "tags_all",
    default=None,
    help="Comma-separated tags (AND filter). Example: --tags-all acme,vue3",
)
@click.option("--path-filter", default=None, help="Only this file: its exact indexed path, not a prefix.")
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
        resp = httpx.post(url, json=payload, timeout=30, headers=_headers())
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
        resp = httpx.post(url, json=payload, timeout=30, headers=_headers())
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
    help="Comma-separated tags (AND filter). Example: --tags-all webshop,decision",
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
        resp = httpx.post(url, json=payload, timeout=30, headers=_headers())
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
    "(overrides auto-detected tags). Example: --tags webshop,globex,go",
)
@click.option(
    "--exclude-ext",
    "exclude_exts",
    multiple=True,
    help="Extra file extension to skip for this run (on top of the built-in "
    "deny list). Repeatable. Leading dot optional. Example: --exclude-ext .csv --exclude-ext proto",
)
@click.option(
    "--exclude-dir",
    "exclude_dirs",
    multiple=True,
    help="Extra directory name to skip for this run (matched anywhere in the "
    "path). Repeatable. Example: --exclude-dir mocks --exclude-dir fixtures",
)
def reindex(
    collection: str,
    path: str | None,
    full: bool,
    recreate: bool,
    workers: int,
    tags: str | None,
    exclude_exts: tuple[str, ...],
    exclude_dirs: tuple[str, ...],
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
    if exclude_exts:
        payload["exclude_exts"] = list(exclude_exts)
    if exclude_dirs:
        payload["exclude_dirs"] = list(exclude_dirs)

    try:
        resp = httpx.post(url, json=payload, timeout=600, headers=_headers())
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
    try:
        entries = _fetch_memories(status)
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
        resp = httpx.post(url, json=payload, timeout=10, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _handle_http_error(exc)
        return

    console.print(
        f"[green]Created[/green] memory entry with ID: [cyan]{data.get('id')}[/cyan]"
    )


def _fetch_memories(status: str | None) -> list[dict]:
    """Every memory with `status`, following the server's pages: one call
    stops at its `limit`, which silently hid entries past the first 1,000."""
    entries: list[dict] = []
    while True:
        params: dict = {"offset": len(entries), "limit": 1000}
        if status:
            params["status"] = status
        resp = httpx.get(f"{_base_url()}/api/memory", params=params, timeout=10, headers=_headers())
        resp.raise_for_status()
        body = resp.json()
        page = body.get("entries", [])
        entries.extend(page)
        if not page or len(entries) >= body.get("total", 0):
            return entries


def _fetch_pending() -> list[dict]:
    """Pending entries, oldest first — review in the order they arrived."""
    try:
        entries = _fetch_memories("pending")
    except Exception as exc:
        _handle_http_error(exc)
        return []
    return sorted(entries, key=lambda e: e.get("created_at") or "")


def _render_memory(entry: dict, position: str) -> None:
    """One entry, in full. The table view truncates, which is precisely why
    nobody reviews: you cannot judge what you cannot read."""
    tags = ", ".join(entry.get("tags") or []) or "—"
    console.print()
    console.print(f"[dim]{position}[/dim]  [bold]{entry.get('memory_type', '?')}[/bold]  [dim]tags:[/dim] {tags}")
    console.print(f"[dim]{entry.get('id')}[/dim]")
    console.print()
    console.print(entry.get("content", ""))
    console.print()


@memory.command("review")
@click.option("--approve-all", is_flag=True, help="Approve every pending entry without prompting.")
def memory_review(approve_all: bool) -> None:
    """Walk pending memories one by one and approve or reject each.

    Extraction is not the bottleneck — reviewing is. Approving by hand meant
    reading a truncated table, copying a UUID and running one command per
    entry, so nothing ever got approved and nothing became searchable.
    """
    entries = _fetch_pending()
    if not entries:
        console.print("[green]Nothing pending.[/green]")
        return

    if approve_all:
        console.print(f"About to approve [bold]{len(entries)}[/bold] entries without reading them.")
        if not click.confirm("Continue?", default=False):
            console.print("Aborted.")
            return
        for entry in entries:
            _review_memory(entry["id"], "approve")
        return

    counts = {"approve": 0, "reject": 0, "skip": 0}
    for index, entry in enumerate(entries, start=1):
        _render_memory(entry, f"{index}/{len(entries)}")
        choice = click.prompt(
            "[a]pprove / [r]eject / [s]kip / [q]uit",
            type=click.Choice(["a", "r", "s", "q"], case_sensitive=False),
            default="s",
            show_choices=False,
        ).lower()

        if choice == "q":
            break
        if choice == "a":
            _review_memory(entry["id"], "approve")
            counts["approve"] += 1
        elif choice == "r":
            _review_memory(entry["id"], "reject")
            counts["reject"] += 1
        else:
            counts["skip"] += 1

    console.print(
        f"\n[green]{counts['approve']} approved[/green], "
        f"[red]{counts['reject']} rejected[/red], "
        f"{counts['skip']} skipped."
    )
    remaining = len(entries) - counts["approve"] - counts["reject"]
    if remaining:
        console.print(f"[dim]{remaining} still pending — only approved entries are searchable.[/dim]")


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
        resp = httpx.post(url, json={"action": action}, timeout=10, headers=_headers())
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
            headers=_headers(),
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

    runner = EvalRunner(
        mnemos_url=_base_url(), limit=limit, api_key=os.environ.get("MNEMOS_API_KEY") or None
    )
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


# ---------------------------------------------------------------------------
# ingest — external knowledge connectors (packages/connectors)
# ---------------------------------------------------------------------------


@cli.group()
@click.option("-v", "--verbose", is_flag=True, help="Log every pushed/deleted document.")
def ingest(verbose: bool) -> None:
    """Pull Confluence, Jira and web content into mnemos_knowledge.

    Each space / project / host is synced against what the server already
    holds under its tag: changed documents are pushed, unchanged ones
    skipped, vanished ones deleted. Exits non-zero if anything failed.
    """
    import logging

    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


_deployment_option = click.option(
    "--deployment",
    type=click.Choice(["auto", "cloud", "datacenter"]),
    default="auto",
    show_default=True,
    help="Atlassian deployment. auto = cloud for *.atlassian.net hosts, datacenter otherwise.",
)
_tags_option = click.option(
    "--tags", default=None, help="Comma-separated extra tags added to every document."
)
_full_option = click.option(
    "--full", is_flag=True, default=False, help="Push every document, even unchanged ones."
)


def _run_sync(
    source: str,
    scopes: list[tuple[str, object]],
    tags: str | None,
    full: bool,
    collection: str = "mnemos_knowledge",
) -> None:
    """Sync each ``(scope, items)`` pair, print the counts, exit 1 on any failure."""
    from connectors.mnemos import MNEMOS_TIMEOUT, MnemosClient
    from connectors.sync import sync_scope
    from connectors.transport import make_client

    user_tags = _parse_tags(tags) if tags else []
    with make_client(headers=_headers(), timeout=MNEMOS_TIMEOUT) as http:
        mnemos = MnemosClient(_base_url(), http, collection=collection)
        results = [
            sync_scope(mnemos, source=source, scope=scope, items=items, user_tags=user_tags, full=full)
            for scope, items in scopes
        ]
    _print_sync_results(f"Ingest {source}", results)
    if not all(r.ok for r in results):
        sys.exit(1)


def _print_sync_results(title: str, results: list) -> None:
    table = Table(title=title)
    table.add_column("Scope", style="cyan")
    for column in ("Pushed", "Unchanged", "Deleted", "Failed"):
        table.add_column(column, justify="right")
    table.add_column("Note")
    for r in results:
        # First line only: httpx appends a "For more information" link to every status error.
        notes = [f"[red]{r.error.splitlines()[0]}[/red]"] if r.error else []
        if r.truncated:
            notes.append("[yellow]truncated, nothing deleted[/yellow]")
        if r.skipped:
            notes.append(f"{r.skipped} skipped (binary or too large)")
        failed = f"[red]{r.failed}[/red]" if r.failed else "0"
        table.add_row(r.scope_tag, str(r.pushed), str(r.unchanged), str(r.deleted), failed, "; ".join(notes))
    console.print(table)


@ingest.command("confluence")
@click.option("--url", envvar="CONFLUENCE_URL", required=True, help="Site URL, e.g. https://example.atlassian.net or https://wiki.example.com [env CONFLUENCE_URL].")
@click.option("--user", envvar="CONFLUENCE_USER", default=None, help="Account email for Cloud (Basic auth). Omit to send the token as a Bearer PAT [env CONFLUENCE_USER].")
@click.option("--token", envvar="CONFLUENCE_TOKEN", required=True, help="Cloud API token or Data Center personal access token [env CONFLUENCE_TOKEN].")
@click.option("--space", "spaces", multiple=True, required=True, help="Space key to sync (repeatable).")
@_deployment_option
@_tags_option
@_full_option
def ingest_confluence(
    url: str,
    user: str | None,
    token: str,
    spaces: tuple[str, ...],
    deployment: str,
    tags: str | None,
    full: bool,
) -> None:
    """Sync Confluence spaces (current pages) into mnemos_knowledge."""
    from connectors.atlassian import auth_kwargs
    from connectors.confluence import SOURCE, ConfluenceSource, scope_key
    from connectors.transport import make_client

    with make_client(**auth_kwargs(user, token)) as client:
        source = ConfluenceSource(client, url, deployment=deployment)
        _run_sync(SOURCE, [(scope_key(s), source.items(s)) for s in spaces], tags, full)


@ingest.command("jira")
@click.option("--url", envvar="JIRA_URL", required=True, help="Site URL, e.g. https://example.atlassian.net or https://jira.example.com [env JIRA_URL].")
@click.option("--user", envvar="JIRA_USER", default=None, help="Account email for Cloud (Basic auth). Omit to send the token as a Bearer PAT [env JIRA_USER].")
@click.option("--token", envvar="JIRA_TOKEN", required=True, help="Cloud API token or Data Center personal access token [env JIRA_TOKEN].")
@click.option("--project", "projects", multiple=True, required=True, help="Project key to sync (repeatable).")
@click.option("--jql", default=None, help="Extra JQL filter ANDed to `project = KEY` (no ORDER BY).")
@_deployment_option
@_tags_option
@_full_option
def ingest_jira(
    url: str,
    user: str | None,
    token: str,
    projects: tuple[str, ...],
    jql: str | None,
    deployment: str,
    tags: str | None,
    full: bool,
) -> None:
    """Sync Jira projects (issues + comments, no people) into mnemos_knowledge."""
    from connectors.atlassian import auth_kwargs
    from connectors.jira import SOURCE, JiraSource, scope_key
    from connectors.transport import make_client

    with make_client(**auth_kwargs(user, token)) as client:
        source = JiraSource(client, url, deployment=deployment)
        _run_sync(SOURCE, [(scope_key(p), source.items(p, jql)) for p in projects], tags, full)


@ingest.command("web")
@click.argument("urls", nargs=-1, required=True)
@click.option("--max-pages", default=200, show_default=True, type=click.IntRange(min=1), help="Pages fetched per host before the crawl stops.")
@_tags_option
@_full_option
def ingest_web(urls: tuple[str, ...], max_pages: int, tags: str | None, full: bool) -> None:
    """Crawl web sites from seed URLs (same host, under the seed path) into mnemos_knowledge."""
    from connectors.transport import make_client
    from connectors.web import SOURCE, WebSource, group_seeds

    try:
        groups = group_seeds(urls)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="URLS") from exc

    with make_client() as client:
        source = WebSource(client, max_pages=max_pages)
        _run_sync(SOURCE, [(scope, source.items(scope, seeds)) for scope, seeds in groups.items()], tags, full)


@ingest.command("git")
@click.argument("url")
@click.option("--branch", default=None, help="Branch to sync. Default: the remote's default branch.")
@click.option("--path", "paths", multiple=True, help="Only files under this repository path (repeatable).")
@click.option("--exclude", multiple=True, help="Glob on the repository path to leave out, e.g. '*.lock' (repeatable; '*' crosses '/').")
@click.option("--max-file-kb", default=512, show_default=True, type=click.IntRange(min=1), help="Larger files are skipped.")
@click.option("--collection", type=click.Choice(["mnemos_code", "mnemos_knowledge"]), default="mnemos_code", show_default=True)
@click.option("--user", envvar="GIT_USER", default=None, help="User name sent with the token (default oauth2) [env GIT_USER].")
@click.option("--token", envvar="GIT_TOKEN", default=None, help="Access token; omit for a public repository [env GIT_TOKEN].")
@_tags_option
@_full_option
def ingest_git(
    url: str,
    branch: str | None,
    paths: tuple[str, ...],
    exclude: tuple[str, ...],
    max_file_kb: int,
    collection: str,
    user: str | None,
    token: str | None,
    tags: str | None,
    full: bool,
) -> None:
    """Sync the files of a remote git repository (GitHub, GitLab, any HTTPS host)."""
    from connectors.git import SOURCE, GitSource, repo_scope

    try:
        scope = repo_scope(url)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="URL") from exc

    source = GitSource(
        url, user=user, token=token, branch=branch, paths=paths, exclude=exclude, max_file_kb=max_file_kb
    )
    _run_sync(SOURCE, [(scope, source.items(scope))], tags, full, collection=collection)


_config_option = click.option(
    "--config",
    "config_path",
    envvar="MNEMOS_CONNECTORS_CONFIG",
    default=None,
    type=click.Path(dir_okay=False),
    help="Read the sources from this file (template: config/connectors.example.yaml) instead "
    "of the server's /api/connectors [env MNEMOS_CONNECTORS_CONFIG].",
)


def _server_connectors() -> dict:
    """The server's /api/connectors snapshot; ConfigError when it cannot be read."""
    from connectors.config import ConfigError
    from connectors.transport import get_json, make_client

    url = f"{_base_url()}/api/connectors"
    try:
        with make_client(headers=_headers(), timeout=10) as http:
            body = get_json(http, url)
    except (httpx.HTTPError, ValueError) as exc:
        # First line only: httpx appends a "For more information" link to every status error.
        raise ConfigError(f"cannot read the connectors from {url}: {str(exc).splitlines()[0]}") from exc
    if not isinstance(body, dict) or not isinstance(body.get("revision"), int):
        raise ConfigError(f"{url}: not a connectors snapshot")
    return body


def _read_connectors(config_path: str | None):
    """`(revision, config)` from the file when given (revision None), else from the server.

    Raise ConfigError when the configuration cannot be read or is invalid.
    """
    from connectors.config import load_config, parse_config

    if config_path:
        return None, load_config(config_path)
    body = _server_connectors()
    raw = {"interval": body.get("interval"), "sources": body.get("sources")}
    return body["revision"], parse_config(raw, os.environ, origin="server")


def _load_connectors(config_path: str | None):
    from connectors.config import ConfigError

    try:
        return _read_connectors(config_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _sync_config(config, only: set[str] | None = None, full: bool = False) -> list:
    from connectors.mnemos import MNEMOS_TIMEOUT, MnemosClient
    from connectors.runner import run_sources
    from connectors.transport import make_client

    with make_client(headers=_headers(), timeout=MNEMOS_TIMEOUT) as http:
        return run_sources(config, MnemosClient(_base_url(), http), only=only, full=full)


@ingest.command("run")
@_config_option
@click.option("--only", multiple=True, help="Sync only this source `name` (repeatable).")
@_full_option
def ingest_run(config_path: str | None, only: tuple[str, ...], full: bool) -> None:
    """Sync every declared source once: the server's connectors, or --config's."""
    _, config = _load_connectors(config_path)
    unknown = sorted(set(only) - {s.name for s in config.sources})
    if unknown:
        names = ", ".join(s.name for s in config.sources)
        raise click.BadParameter(f"no source named {', '.join(unknown)} (known: {names})", param_hint="--only")

    results = _sync_config(config, set(only) or None, full)
    _print_sync_results("Ingest", results)
    if not all(r.ok for r in results):
        sys.exit(1)


@ingest.command("serve")
@_config_option
def ingest_serve(config_path: str | None) -> None:
    """Sync at start-up, then every `interval` of the connectors config.

    Without --config the sources come from the server (/api/connectors) and a
    save from the dashboard resyncs within seconds; with --config, editing the
    file does. Send SIGHUP to resync now (`docker compose kill -s HUP
    ingester`, `kill -HUP 1` in a pod). The config is re-read before every
    run; a broken or unreachable one keeps the previous version. A changed
    `interval` needs a restart.
    """
    import signal
    import threading
    from datetime import datetime

    import connectors.runner
    from connectors.config import ConfigError

    revision, config = _load_connectors(config_path)
    trigger, stop = threading.Event(), threading.Event()

    def run_once() -> None:
        nonlocal config
        try:
            _, config = _read_connectors(config_path)
        except ConfigError as exc:
            console.print(f"[red]{exc}[/red]\n[yellow]keeping the previous configuration[/yellow]")
        _print_sync_results(f"Sync {datetime.now():%Y-%m-%d %H:%M:%S}", _sync_config(config))

    def resync(_signum, _frame) -> None:
        trigger.set()

    def shutdown(_signum, _frame) -> None:
        # Syncs are idempotent: dropping one half-way only defers work to the next run.
        raise SystemExit(0)

    if config_path:
        origin = config_path
        watcher = threading.Thread(
            target=connectors.runner.watch_file, args=(config_path, trigger, stop), daemon=True
        )
    else:
        origin = f"{_base_url()}/api/connectors"
        watcher = threading.Thread(
            target=connectors.runner.watch_revision,
            args=(lambda: _server_connectors()["revision"], revision, trigger, stop),
            daemon=True,
        )
    previous = {sig: signal.signal(sig, handler) for sig, handler in
                ((signal.SIGHUP, resync), (signal.SIGTERM, shutdown))}
    watcher.start()
    every = f"every {config.interval}s, " if config.interval else ""
    console.print(
        f"Serving {len(config.sources)} source(s) from {origin}: "
        f"sync now, then {every}on config change and on SIGHUP."
    )
    try:
        connectors.runner.serve(run_once, interval=config.interval, trigger=trigger, stop=stop)
    finally:
        stop.set()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    cli()
