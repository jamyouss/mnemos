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
npm test             # vitest unit tests on the helpers in ui/utils/
npm run typecheck    # vue-tsc over pages, components and utils
```

Built with [shadcn-vue](https://shadcn-vue.com) (Reka UI + Tailwind CSS v4)
and Unovis charts. Generated primitives live in `ui/components/ui/`; add more
with `npx shadcn-vue@latest add <name>` from `ui/` — `components.json` holds
the aliases.

> `package-lock.json` must be written by npm ≥ 11.19, the version in the
> image's `node:24` stage: older npm drops the optional packages Tailwind's
> WASM fallback needs, and the image build then fails at `npm ci`. Use
> `npx -y npm@11.19.1 install`, or regenerate it on Linux:
> `docker run --rm -v "$PWD":/app -w /app node:24-alpine npm install --package-lock-only`.

> The server starts fine without a dashboard. If `/app/ui` is absent — a build
> without the UI stage — it logs the fact and serves the API alone.

## The screens

### Alerts

A banner above every page while something is broken: the LLM unreachable,
connectors configured but no sync service ever seen, the sync service silent
for over 3 minutes or a source's last sync failed, the watcher silent for
over 3 minutes (it beats every minute; a watcher never seen raises nothing),
audit entries that could not be stored, a reindex job failed or left failed
files in the last 24 h. Each links to the page that explains it, can be
dismissed for the session, and shows only to keys that can act on it
(`GET /api/alerts`). An unreachable Qdrant shows as the server being down.

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
  runs rather than what the UI assumes, each next to its cost as benchmarked
  in [EVAL.md](EVAL.md) (not measured on this index). The question is never
  "is the reranker enabled" but "is 8 seconds per query worth it here", so the
  number sits next to the switch.

Search figures come from the last 5,000 log entries; when they start after
the window opens, the screen says the totals undercount. Search traffic,
latency, scores, most-returned files and retrieval stages are shown to admin
keys only; other keys see the index, projects and memories.

### Search log

Reads `/api/query-log`: p50/p95, empty-result share, intents, the top-1 score
distribution, the files returned most often, and the last searches with their
top file, score, latency and the stages that ran — paged, over the same 1,000
searches the figures describe.

Two things are deliberate:

- **No score threshold.** The top-1 scale moves with the fusion weights and
  the reranker — weighted RRF puts it above 1 — so the buckets span the scores
  actually seen, and the distribution is read as a shape: no absolute cutoff
  separates a good result from a bad one.
- **Most-returned files.** A file topping that list across unrelated queries is
  usually noise. This is the view that exposed `coverage.out` — a Go coverage
  profile — as the single most-returned file in a sample of real searches.

An empty screen states *why* it is empty: nothing searched, or nothing
recorded. Those are different problems.

Playground searches are left out of this screen and the dashboard unless
*Include Playground searches* is on: test traffic would move the very figures
being read.

### Indexing

Admin keys only. What the index went through, from every channel:

- the files indexed, deleted, skipped, failed and refused (no rights) over the
  last 24 hours, and the files walked by bulk reindex jobs;
- when each channel — watcher, git hooks, connectors, CLI, dashboard, MCP,
  API, scripts — last sent anything, every channel listed even when it never
  did, and in amber once silent for a day: the first thing to check when
  results look stale. The channel is what the client declares
  (`X-Mnemos-Client`), a hint rather than proof;
- the sync service (alive, silent, never seen), the requests waiting for it
  and what each connector source last did (details on the Connectors page);
- bulk reindex jobs (CLI, API, MCP) with their progress, chunks and failed
  files: *Done*, *Done with errors* when some files failed, *Failed*, or
  *Stalled* for a running job whose progress has not moved for two minutes
  (its server likely restarted);
- every file event, newest first, filtered by collection, channel, outcome
  or directory, with the error of a failed or refused one.

The page refreshes every 3 s while a reindex runs, every 15 s otherwise.
History is kept `MNEMOS_INDEX_EVENTS_RETENTION_DAYS` (30 by default).

### Index

What is in the index, file by file. Pick a collection, narrow by tag or
directory, open a file to read its chunks in order (type, symbol, section,
contextual preamble). A tagged collection shows a key only what its `read`
scope covers. From a file: **Reindex** it again from disk (keys with
`write: "*"`, files under a server mount) or **Remove** it from the index
(same rule as `DELETE /api/index/…`). **Reindex a folder** starts a bulk job,
followed on the Indexing page.

The **path tester** answers "why is this file (not) indexed?": give a path,
relative to the codebase or absolute, and it tells the collection it would go
to, whether it is indexed and in how many chunks, the tags it would get and
from which project prefix, the chunker, and when refused, the rule that
refuses it — *built-in* policy, the *project*'s ignore rules, or *declared
only* (`MNEMOS_ONLY_DECLARED_PATHS` and no project prefix covers it). Same
code as the indexer, so the answer cannot drift from what indexing does.

### Playground

Runs a search exactly as an agent would — any of the four endpoints, with
their filters (tags suggested from what is indexed), limit and preview/full —
and shows why each result came back.

- **Trace.** Per stage: cache, embedding, retrieval per collection, grader,
  rewriter variants, reranker, MMR, with timings. Per candidate: its rank on
  the dense side, on the BM25 side, after fusion and after the reranker, its
  grade, and whether it made the final list. A rank of `—` means outside that
  side's top 20. This is the view for "why is this file first?" and "why is
  the right one missing?".
- **Bypass cache** (on by default, `/api/search` only) skips the semantic cache
  both ways: the trace shows the live pipeline, and nothing is stored that
  real traffic would then be served.
- **Copy as curl / MCP call** replays the same search outside the dashboard,
  without the Playground-only fields. The API key is never inlined; the curl
  reads `$MNEMOS_API_KEY`.

Every search endpoint accepts the same two fields for this:

| Field | Effect |
|-------|--------|
| `trace: true` | Adds `"trace": {total_ms, stages, candidates}` to the response. Costs two extra Qdrant queries per collection — the fused query does not report where each hit stood on either side — so it is off by default |
| `source: "playground"` | Tags the query-log entry. Only that value is accepted, so no client can write arbitrary labels into the log |

`/api/search` also takes `use_cache: false`.

### Memories

Browse, filter by type, tag, text and status; create, edit (text, type, tags,
project), delete, reject or approve again. Filtering, newest-first sorting and
paging run on the server over every memory (`GET /api/memory?status=&type=&q=&offset=&limit=`
returns the page and the `total`), so no entry hides past a page boundary; tab
counts come from `/api/memory/stats`. An edited text is embedded again
(`PATCH /api/memory/{id}`); a delete is final (`DELETE /api/memory/{id}`).
Both need write rights on the memory's tags and are audited without the text.

Memories are written **searchable**; this screen takes a wrong one back out
rather than letting good ones in. Search returns `approved` entries only, so
a rejected memory disappears from retrieval immediately.

### Configuration

Every server setting, grouped by section, with **where its value comes from**
(*Default*, *Environment*, *Dashboard*) and **when a change applies** (*Live*,
*After restart*, *Needs reindex*). Changes are reviewed as a diff before they
are sent.

Admin keys only. Read-only unless the server runs with
`MNEMOS_UI_CONFIG_WRITABLE=true`. Only the settings the server marks
editable can change here (retrieval stages, cache, LLM provider and model,
dedup, contextual chunking, declared-paths indexing, query log); secrets,
URLs, paths, Qdrant, authentication and the embedding model stay env-only,
and no secret is ever sent to the browser — the page only shows whether each
is set. Saved
values are overrides stored in the database, applied by every server pod, and
win over `.env`; the reset button next to an override removes it. A save
checks the revision the page loaded: if someone saved in between, the server
answers `409` and nothing is overwritten; the page offers to refresh. Details:
[CONFIGURATION.md](CONFIGURATION.md#dashboard-overrides).

### Connectors

Edits the server's `connectors` configuration: the sync `interval` and one card per
Confluence, Jira, web or git source, with the fields of
[CONNECTORS.md](CONNECTORS.md#configuration-file). Saved sources start
collapsed to their name, type and chunk count; a click on the header opens
the form. Each card shows its scope tags (`jira:OPS`, `web:docs.example.com`)
and how many chunks `mnemos_knowledge` (or `mnemos_code`, for a git source)
holds under each — `not synced yet` is the answer to "did the ingester ever
run?". A search box filters the cards on every word typed (name, URL,
spaces, projects, branch, tags), and the list pages by 10, 15, 30, 50 or 100.

**Sync all** in the header and **Sync** on a saved card ask the ingester to
sync now; asking again while the same request waits does not queue a twin.
Each card says what the ingester last reported: *Waiting — requested 2 min
ago by admin* until it picks the request up (within ~5 s, amber when the
service is silent), *Syncing* while it runs, then *synced 4 min ago* or *Sync
failed*; opened, the card lists the last sync per scope (pushed, unchanged,
deleted, failed) with the error in full. Above the cards, a warning says when
no ingester ever reported to this server or one has been silent for three
minutes — the usual answer to "why does nothing sync?". With unsaved changes
both buttons are off, as the bar at the bottom says (a sync runs the saved
configuration), and Refresh updates the statuses without touching the form.

The server validates with the ingester's own parser and refuses a
configuration the ingester would reject, listing every problem; one that is
already broken (an imported file, for instance) is flagged the same way on
load. Tokens are never shown or sent: a card names the variable (`token_env`)
that holds one in the ingester's environment. An ingester reading the server
resyncs within seconds of a save. Saves use the same revision check as
Configuration (`409` → reload). Visible to admin keys and keys with
`write: "*"`; saving needs an admin key and `MNEMOS_UI_CONFIG_WRITABLE=true`.
Details in [CONNECTORS.md](CONNECTORS.md#from-the-dashboard).

### Projects

Lists the `path → tags` mapping grouped by organisation, with a filter on
path or tag; each prefix opens in a side panel to edit its tags and its
ignore rules (`exclude_dirs`, `exclude_exts`, described in
[CONFIGURATION.md](CONFIGURATION.md#per-project-ignore-rules)).
Changes are saved together from the bar at the bottom. The server refuses an
empty or absolute prefix, a duplicate prefix and an entry without tags
(`422`, every problem listed). A save applies to every pod within
`MNEMOS_CONFIG_POLL_SECONDS`, from the next indexing event on; already
indexed chunks keep their tags until reindexed. Same visibility, save rules
and revision check as Connectors.

### Tenants

Admin keys only. Lists the API keys' tenants with their role and `read` /
`write` scopes (`*` or a list of tags); creates a tenant, edits its scopes,
rotates or revokes its key. A new or rotated key is shown **once**, then only
its SHA-256 is kept. A revoked key stops working on every pod within
`MNEMOS_CONFIG_POLL_SECONDS`. The last admin cannot be removed or demoted.
Each row shows when its key was last used and from which address — a key
nobody used for months is a revocation candidate. Uses are noted per request
and stored every minute, so the time is accurate to about a minute.
The root tenant (`admin`, from `MNEMOS_BOOTSTRAP_ADMIN_KEY`) is locked: its
row shows a padlock, and its key changes only with the variable. Saving needs
`MNEMOS_UI_CONFIG_WRITABLE=true`. Scope rules:
[DEPLOYMENT.md](DEPLOYMENT.md#tenants-and-scopes).

### Activity

Admin keys only, in the Observe group. Who changed what: tenants (created,
changed, key rotated, revoked), projects, settings, connectors and sync
requests, bulk reindexes, file deletions made by a person (connector and
watcher housekeeping stays on the Indexing page) and memories — each with the
tenant, the client (`dashboard`, `cli`, `mcp`, `api`…) and what changed, old
→ new. A change refused for lack of rights is listed too, marked *Refused*,
with the reason, and so is an unknown API key (*Rejected an API key*); a
repeat of the same refusal within a minute is recorded once. Filter by area,
tenant, outcome or exact target ("who changed tenant `ci`?"); a row opens its
full detail, whose target applies the filter. A banner appears when this
server could not store some entries since it started. Kept
`MNEMOS_AUDIT_RETENTION_DAYS` (365 by default); see
[DEPLOYMENT.md](DEPLOYMENT.md#audit-trail).

The sidebar hides what the key cannot open: Search log, Indexing, Activity,
Configuration and Tenants need an admin key; Connectors and Projects need
admin or `write: "*"`.

### Appearance

Two layers, the higher one winning:

1. **Instance theme** — `config/theme.css`, served at `/theme.css`, overrides
   the shadcn CSS variables (`--primary`, `--radius`, `--chart-1`…) for every
   visitor, without rebuilding the image. Start from `config/theme.example.css`.
2. **Per browser** — the *Appearance* panel in the sidebar: light / dark /
   system, an accent preset and a corner radius, kept in localStorage.
   *Default* in either picker keeps the deployment's theme.

## Configuration

| Var | Default | Note |
|-----|---------|------|
| `MNEMOS_QUERY_LOG_ENABLED` | `false` | **Required for the Search log.** Without it there is nothing to display |
| `MNEMOS_QUERY_LOG_PATH` | `/data/state/query-log.jsonl` | Read by tailing, so the file can grow |
| `MNEMOS_UI_PATH` | `/app/ui` | Absent → API only |
| `MNEMOS_UI_CONFIG_WRITABLE` | `false` | Lets the Configuration, Connectors, Projects and Tenants pages save. See [CONFIGURATION.md](CONFIGURATION.md#dashboard-overrides) |
| `MNEMOS_CONFIG_POLL_SECONDS` | `5` | How fast a save made through one server pod reaches the others |
| `MNEMOS_UI_THEME_PATH` | `/app/config/theme.css` | Instance theme served at `/theme.css`. See [CONFIGURATION.md](CONFIGURATION.md#instance-theme) |

## Not included

**No accounts.** With `MNEMOS_AUTH_ENABLED=true` the dashboard opens on a
sign-in page that takes a tenant's API key, checks it against `/api/whoami`
and keeps it in the browser's localStorage (`mnemos.apiKey`). Sign out from
the sidebar footer; a key revoked or rotated elsewhere sends the browser back
to the sign-in page at its next request.

**No writing memories, no triggering a reindex.** The CLI and the MCP tools
write; this screen prunes, which was the missing gesture. And a button that
starts hours of LLM work needs guard rails that do not exist yet. The shared
configuration (settings, connectors, projects, tenants) is the exception,
behind an opt-in flag; the dashboard edits what the ingester syncs, it never
runs a sync itself.
