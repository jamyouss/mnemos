# Dashboard

A local web UI for the parts of mnemos that were previously only reachable
with `curl`, `docker logs` or a JSONL file: what is indexed, what is being
searched, what has been remembered, and how retrieval scored.

It exists because every fault this project has hit was a **silent** one. A
dead watcher, LLM calls failing for months, a query router that never once
fired, a third of the index made of duplicates — the data was always there,
and nothing showed it.

## Running it

The dashboard is built into the server image and served at the root, so
nothing extra runs in production:

```
http://localhost:8100/
```

The API stays where it was — `/api/*`, `/mcp/` and `/health` are matched
before the UI, which only catches what is left.

For development, run the SPA separately with hot reload:

```bash
cd ui
npm install
npm run dev          # http://localhost:3000
```

Nitro proxies `/api` to `:8100`, so the browser stays on one origin and the
server needs no CORS configuration.

```bash
npm run generate     # static build into ui/.output/public
npm test             # unit tests on the aggregation helpers
```

> The server starts fine without a dashboard. If `/app/ui` is absent — a build
> without the UI stage — it logs the fact and serves the API alone.

## The screens

### System

Collections and their volume, and the retrieval stages **with their measured
cost** rather than just an on/off flag. The question is never "is the reranker
enabled" but "is 8 seconds per query worth it here", so the number sits next
to the switch.

Also reports whether the query log is recording. A dashboard that cannot say
that is how a logger stays off for a week without anyone noticing.

### Search

Reads `/api/query-log`: volume by day and by intent, p50/p95, the top-1 score
distribution, and the files returned most often.

Two things are deliberate:

- **No score threshold.** Hybrid fusion puts a normal top-1 between 0.5 and
  0.8. The distribution is shown as a shape because no absolute cutoff
  separates a good result from a bad one.
- **Most-returned files.** A file topping that list across unrelated queries is
  usually noise. This is the view that exposed `coverage.out` — a Go coverage
  profile — as the single most-returned file in a sample of real searches.

An empty screen states *why* it is empty: nothing searched, or nothing
recorded. Those are different problems.

### Memories

Browse, filter by type, tag and status, and reject in one click.

Memories are written **searchable**; this screen takes a wrong one back out
rather than letting good ones in. Search returns `approved` entries only, so
a rejected memory disappears from retrieval immediately.

### Evals

The runs in `evals/runs/` side by side. Absolute numbers depend on the golden
set, so the delta between two runs on the same set is what carries meaning.

The screen repeats the caveat from [EVAL.md](EVAL.md): the harness routes
`code_search` to `/api/search-code`, which never runs the grader, and a
generated golden set is mostly code_search. A grader A/B through the harness
returns identical tables — that reads as "no effect" when it means "never ran".

## Configuration

| Var | Default | Note |
|-----|---------|------|
| `MNEMOS_QUERY_LOG_ENABLED` | `false` | **Required for the Search screen.** Without it there is nothing to display |
| `MNEMOS_QUERY_LOG_PATH` | `/data/state/query-log.jsonl` | Read by tailing, so the file can grow |
| `MNEMOS_EVAL_RUNS_PATH` | `/app/evals/runs` | Bind-mounted read-only; runs are local artefacts and never baked into the image |
| `MNEMOS_UI_PATH` | `/app/ui` | Absent → API only |

## Not included

**No authentication.** The server listens on localhost. `MNEMOS_AUTH_ENABLED`
already exists for deployed mode and will have to cover the UI before this is
exposed anywhere.

**No writing memories, no triggering a reindex.** The CLI and the MCP tools
write; this screen prunes, which was the missing gesture. And a button that
starts hours of LLM work needs guard rails that do not exist yet.
