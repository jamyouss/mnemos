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
npm run typecheck    # vue-tsc over pages, components and utils
```

Built with [shadcn-vue](https://shadcn-vue.com) (Reka UI + Tailwind CSS v4)
and Unovis charts. Generated primitives live in `ui/components/ui/`; add more
with `npx shadcn-vue@latest add <name>` from `ui/` — `components.json` holds
the aliases.

> The server starts fine without a dashboard. If `/app/ui` is absent — a build
> without the UI stage — it logs the fact and serves the API alone.

## The screens

### Dashboard

One screen for the questions that used to need a shell, over a 24 h / 7 d /
30 d window:

- **Health** — Qdrant reachable, the LLM answering (a model-list call, no
  tokens spent, cached 30 s), the query log recording.
- **KPIs** — indexed chunks, searches, p95 latency, cache-hit rate, share of
  searches returning nothing, approved memories.
- **Charts** — traffic per hour or day stacked by intent, p50/p95 latency,
  chunks per collection, top-1 score shape, code chunks per project tag,
  most-returned files, memories by type.
- **Retrieval stages** — read from `/api/config`, so on/off is what the server
  runs rather than what the UI assumes, each with its **measured cost**. The
  question is never "is the reranker enabled" but "is 8 seconds per query
  worth it here", so the number sits next to the switch.

Search figures come from the last 5,000 log entries; when they start after
the window opens, the screen says the totals undercount.

### Search

Reads `/api/query-log`: p50/p95, empty-result share, intents, the top-1 score
distribution, the files returned most often, and the last searches with their
top file, score, latency and the stages that ran.

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

### Configuration

Every server setting, grouped by section, with **where its value comes from**
(`default`, `env`, `override`) and **when a change applies** (`live`,
`restart`, `reindex`). Changes are reviewed as a diff before they are sent.

Read-only unless the server runs with `MNEMOS_UI_CONFIG_WRITABLE=true`.
Secrets, URLs, paths and the embedding model are never editable here, and the
API key is never sent to the browser. Saved values are overrides in
`<state dir>/settings.json` and win over `.env`; the reset button next to an
override removes it. Details: [CONFIGURATION.md](CONFIGURATION.md#dashboard-overrides).

### Appearance

Two layers, the higher one winning:

1. **Instance theme** — `config/theme.css`, served at `/theme.css`, overrides
   the shadcn CSS variables (`--primary`, `--radius`, `--chart-1`…) for every
   visitor, without rebuilding the image. Start from `config/theme.example.css`.
2. **Per browser** — the *Appearance* panel in the sidebar: light / dark /
   system, an accent preset and a corner radius, kept in localStorage.
   *Instance* in either picker keeps the deployment's theme.

## Configuration

| Var | Default | Note |
|-----|---------|------|
| `MNEMOS_QUERY_LOG_ENABLED` | `false` | **Required for the Search screen.** Without it there is nothing to display |
| `MNEMOS_QUERY_LOG_PATH` | `/data/state/query-log.jsonl` | Read by tailing, so the file can grow |
| `MNEMOS_EVAL_RUNS_PATH` | `/app/evals/runs` | Bind-mounted read-only; runs are local artefacts and never baked into the image |
| `MNEMOS_UI_PATH` | `/app/ui` | Absent → API only |
| `MNEMOS_UI_CONFIG_WRITABLE` | `false` | Lets the Configuration page save overrides. See [CONFIGURATION.md](CONFIGURATION.md#dashboard-overrides) |
| `MNEMOS_UI_THEME_PATH` | `/app/config/theme.css` | Instance theme served at `/theme.css`. See [CONFIGURATION.md](CONFIGURATION.md#instance-theme) |

## Not included

**No login of its own.** With `MNEMOS_AUTH_ENABLED=true` the dashboard files
load without a key; the first `401` from the API prompts for one, which is
kept in the browser's localStorage (`mnemos.apiKey`).

**No writing memories, no triggering a reindex.** The CLI and the MCP tools
write; this screen prunes, which was the missing gesture. And a button that
starts hours of LLM work needs guard rails that do not exist yet. Settings are
the one exception, behind an opt-in flag.
