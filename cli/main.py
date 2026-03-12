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
    return os.environ.get("RAG_URL", DEFAULT_RAG_URL).rstrip("/")


def _handle_http_error(exc: Exception) -> None:
    console.print(f"[bold red]Error:[/bold red] {exc}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """RAG MCP CLI — search, reindex, and manage memory."""


# ---------------------------------------------------------------------------
# rag status
# ---------------------------------------------------------------------------


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
def search(query: str, limit: int, collection: tuple, file_type: tuple, path_filter: str | None) -> None:
    """Search across all indexed documents."""
    url = f"{_base_url()}/api/search"
    payload: dict = {"query": query, "limit": limit}
    if collection:
        payload["collections"] = list(collection)
    if file_type:
        payload["file_types"] = list(file_type)
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
# rag search-code
# ---------------------------------------------------------------------------


@cli.command("search-code")
@click.argument("query")
@click.option("--limit", default=5, show_default=True, help="Number of results to return.")
@click.option("--language", default=None, help="Filter by programming language.")
@click.option("--symbol-type", default=None, help="Filter by symbol type (func, type, etc.).")
@click.option("--project", default=None, help="Filter by project name.")
@click.option("--path-filter", default=None, help="Filter by file path substring.")
def search_code(
    query: str,
    limit: int,
    language: str | None,
    symbol_type: str | None,
    project: str | None,
    path_filter: str | None,
) -> None:
    """Search code-specific index."""
    url = f"{_base_url()}/api/search-code"
    payload: dict = {"query": query, "limit": limit}
    if language:
        payload["language"] = language
    if symbol_type:
        payload["symbol_type"] = symbol_type
    if project:
        payload["project"] = project
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
# rag reindex
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--collection", required=True, help="Collection to reindex.")
@click.option("--path", default=None, help="Base path to scan for files.")
@click.option("--full", is_flag=True, default=False, help="Recursively reindex all files under path.")
def reindex(collection: str, path: str | None, full: bool) -> None:
    """Trigger a reindex operation on the server."""
    url = f"{_base_url()}/api/reindex"
    payload: dict = {"collection": collection, "full": full}
    if path:
        payload["path"] = path

    try:
        resp = httpx.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        _handle_http_error(exc)
        return

    console.print(
        f"[green]Reindexed[/green] collection [cyan]{data.get('collection')}[/cyan] "
        f"— {data.get('chunks_indexed', 0)} chunks indexed."
    )


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


if __name__ == "__main__":
    cli()
