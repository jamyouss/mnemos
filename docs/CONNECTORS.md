# Knowledge connectors

`mnemos ingest` pulls knowledge that does not live in a git checkout —
Confluence pages, Jira issues, web documentation — into the
`mnemos_knowledge` collection, so agents find it with `mnemos_search`
(`collections: ["mnemos_knowledge"]`) next to code and memories.

```
mnemos ingest run | serve        (config/connectors.yaml)
mnemos ingest confluence|jira|web  (one source, from flags)
   │  source REST API / HTTP crawl        (httpx, retries, proxy-aware)
   ▼
connector → Markdown documents (# title, metadata, body)
   │
   ▼
sync engine ── GET  /api/index/mnemos_knowledge/files?tag=<source>:<scope>
            ── POST /api/index                     (new / changed documents)
            ── DELETE /api/index/mnemos_knowledge/<file_path>   (gone at the source)
   ▼
Mnemos server (chunk → embed → Qdrant)
```

The connectors are a client package (`packages/connectors`, installed by
`make install`). They talk to the server over its HTTP API only and keep no
local state: what is indexed under a scope tag **is** the sync state, so a
throwaway container — or a long-running `ingest serve` — is all it takes.

## Common setup

| Variable | Used by | Meaning |
|---|---|---|
| `MNEMOS_URL` | all | Server URL (default `http://localhost:8100`) |
| `MNEMOS_API_KEY` | all | Sent as `Authorization: Bearer …` when the server runs with `MNEMOS_AUTH_ENABLED=true` |

Options shared by every `ingest` command:

| Option | Meaning |
|---|---|
| `--tags a,b` | Extra tags added to every document (team, product…) |
| `--full` | Push every document even if unchanged (e.g. after changing the chunker) |
| `mnemos ingest -v …` | Log each pushed / deleted document |

Each command prints a table — pushed / unchanged / deleted / failed per
scope — and **exits 1 if anything failed**, so a scheduler flags the run.

## Configuration file

Declare every source once in `config/connectors.yaml` (gitignored; start from
[`config/connectors.example.yaml`](../config/connectors.example.yaml)):

```yaml
interval: 6h                     # `ingest serve` resync period: 45s, 30m, 6h, 1d

sources:
  - type: confluence
    name: wiki                   # optional, defaults to the type; unique
    url: https://wiki.example.com
    token_env: CONFLUENCE_TOKEN  # NAME of the variable holding the token
    # user: bot@example.com      # Cloud only (HTTP Basic); omit for a Data Center PAT
    # deployment: auto           # auto | cloud | datacenter
    spaces: [ENG, OPS]
    tags: [acme]
  - type: jira
    name: tracker
    url: https://jira.example.com
    token_env: JIRA_TOKEN
    projects: [OPS]
    # jql: resolution = Unresolved
  - type: web
    name: handbook
    urls: [https://docs.example.com/handbook/]
    max_pages: 200
```

Keys mirror the command-line options of each source below (`spaces` /
`projects` / `urls` are the repeatable `--space` / `--project` / URL
arguments). Tokens never go in the file: `token_env` names the variable, which
must be set when the file is loaded — a missing one, an unknown key, a typo in
`type` or a duplicate `name` stops the run before anything is synced, with
every problem listed at once.

The path is `--config`, else `MNEMOS_CONNECTORS_CONFIG`, else
`config/connectors.yaml` (relative to the working directory; `/app/config/`
in the `jamyouss/mnemos-cli` image).

### From the dashboard

The **Connectors** page of the dashboard (`http://localhost:8100/connectors`)
edits the same file: one card per source, the `interval`, and per scope tag
how many chunks `mnemos_knowledge` holds — `not synced yet` until the ingester
has delivered something. The server validates with the ingester's own parser
before writing, so a save the ingester would reject is refused with every
problem listed, and nothing is written.

- **Writing** needs `MNEMOS_UI_CONFIG_WRITABLE=true` on the server; otherwise
  the page is read-only. The compose file mounts `./config` writable into
  `rag-server` for this, and as a directory (not the file) into `ingester`, so
  it sees the atomic replace.
- **Tokens** never pass through the server: a card only names the variable
  (`token_env`), set in the ingester's environment. The server cannot check
  that it is set — the ingester reports a missing one at its next sync.
- Saving **rewrites the file and drops its comments**. Through a symlink that
  resolves inside the container, the target is written and the link kept.
- The `ingester` picks the change up within seconds (it watches the file).
  A new `interval` still needs `docker compose restart ingester`.
- Removing a source stops its syncs; what it already indexed stays.

### Sync once: `mnemos ingest run`

```bash
mnemos ingest run                       # every source
mnemos ingest run --only tracker        # one source by name (repeatable)
mnemos ingest run --only wiki --full    # force a re-push of unchanged pages
```

A source that fails (unreachable, 401, bad URL) is reported in the table and
the others still run; the command exits 1 if anything failed.

### Sync at start-up, then on a timer: `mnemos ingest serve`

`serve` is the long-running form: it syncs every source as soon as it starts,
then every `interval`, when the file changes (checked every 5 s), and
immediately on `SIGHUP`:

| To… | Do |
|---|---|
| resync now | `docker compose kill -s HUP ingester` · `kubectl exec <pod> -c ingester -- kill -HUP 1` |
| resync one source, or with `--full` | `mnemos ingest run --only <name> [--full]` from anywhere |
| apply an edited `connectors.yaml` | nothing: the change triggers a sync within seconds, and the file is re-read before every sync (a broken edit is reported and the previous version kept). A changed `interval` needs a restart |
| stop | `SIGTERM` — the service exits at once; an interrupted sync resumes at the next run |

Without `interval`, `serve` syncs at start-up, on file change and on `SIGHUP` only.

## Confluence

```bash
# Cloud: account email + API token (HTTP Basic)
export CONFLUENCE_URL=https://example.atlassian.net
export CONFLUENCE_USER=bot@example.com
export CONFLUENCE_TOKEN=<api token>
mnemos ingest confluence --space ENG --space OPS --tags acme

# Data Center: personal access token alone (Bearer)
export CONFLUENCE_URL=https://wiki.example.com/confluence
export CONFLUENCE_TOKEN=<personal access token>
unset CONFLUENCE_USER
mnemos ingest confluence --space ENG
```

| Option | Env | Meaning |
|---|---|---|
| `--url` | `CONFLUENCE_URL` | Site URL. Cloud: `https://<site>.atlassian.net` (a trailing `/wiki` is fine). Data Center: base URL including any context path |
| `--user` | `CONFLUENCE_USER` | Cloud account email. Omit it to send the token as a Bearer PAT |
| `--token` | `CONFLUENCE_TOKEN` | Cloud API token or Data Center personal access token (required) |
| `--space` | — | Space key, repeatable. Each space is its own sync scope |
| `--deployment` | — | `auto` (default: `cloud` for `*.atlassian.net`, `datacenter` otherwise), `cloud`, `datacenter`. Set `cloud` for a Cloud site behind a custom domain |

What is ingested: every **current** page of the space (no blog posts,
attachments, archived or trashed pages). Each document carries the source
URL, the space key, a breadcrumb of ancestor titles, the labels and the last
update date. The storage-format body is converted to Markdown: headings,
lists and tables stay structured, `code` / `noformat` macros become fenced
blocks, info/note/warning/tip panels become quotes, `expand` keeps its
content, macros with no text of their own (`toc`, `children`, `attachments`,
…) are dropped, and user mentions are removed.

APIs used:

| Deployment | Listing | Body (changed pages only) |
|---|---|---|
| Cloud | `GET /wiki/api/v2/spaces?keys=KEY`, then `GET /wiki/api/v2/spaces/{id}/pages?status=current&limit=250` (cursor in `_links.next` / `Link`) | `GET /wiki/api/v2/pages/{id}?body-format=storage&include-labels=true` |
| Data Center | `GET /rest/api/content?spaceKey=KEY&type=page&status=current&expand=version,ancestors,metadata.labels` (`start`/`limit`) | `GET /rest/api/content/{id}?expand=body.storage` |

The Cloud v1 `GET /wiki/rest/api/content` listing is no longer part of the
Cloud v1 API, hence REST v2 there. Bodies are fetched only for pages whose
version date changed, so a nightly run over a quiet space costs a few
listing calls. On Cloud, only the first 50 labels of a page are kept.

The token needs read access to the spaces; nothing is ever written to
Confluence.

## Jira

```bash
export JIRA_URL=https://example.atlassian.net      # or https://jira.example.com
export JIRA_USER=bot@example.com                   # Cloud only
export JIRA_TOKEN=<api token or PAT>
mnemos ingest jira --project OPS --project INFRA
mnemos ingest jira --project OPS --jql 'resolution = Unresolved OR updated >= -180d'
```

| Option | Env | Meaning |
|---|---|---|
| `--url` | `JIRA_URL` | Site URL |
| `--user` | `JIRA_USER` | Cloud account email; omit for a Data Center PAT (Bearer) |
| `--token` | `JIRA_TOKEN` | API token / PAT (required) |
| `--project` | — | Project key, repeatable. Each project is its own sync scope |
| `--jql` | — | Extra filter, ANDed: `project = "KEY" AND (<jql>)`. A filter clause only — no `ORDER BY` |
| `--deployment` | — | As for Confluence |

Each issue becomes one document: `KEY: summary` as title; type, status,
priority, resolution, labels, components, created and updated as metadata;
the description and every comment (with its date) as Markdown. **People are
never written out** — no assignee, reporter, comment author, e-mail or
@mention — they are personal data and useless for retrieval.

APIs used:

| Deployment | Search | Rich text | Extra comments |
|---|---|---|---|
| Cloud | `GET /rest/api/3/search/jql` (`nextPageToken`) | Atlassian Document Format → Markdown | `GET /rest/api/3/issue/{key}/comment` |
| Data Center | `GET /rest/api/2/search` (`startAt` / `total`) | wiki markup → Markdown | `GET /rest/api/2/issue/{key}/comment` |

Jira Cloud is removing the old `/rest/api/{2,3}/search` endpoints
(changelog CHANGE-2046) in favour of `/search/jql`; Data Center keeps
`/rest/api/2/search`. Comments beyond what the search embeds are fetched
only for issues being pushed.

`--jql` narrows the scope: an issue that stops matching the filter is
deleted from Mnemos on the next run, like an issue deleted in Jira. Run two
different filters on the same project and they will undo each other — use
one filter per project.

## Web sites

```bash
mnemos ingest web https://docs.example.com/guide/ --max-pages 500
mnemos ingest web https://docs.example.com/api/ https://help.example.org/ --tags vendor-docs
```

| Option | Meaning |
|---|---|
| `URL…` | Seed URLs. Seeds on the same host share one scope |
| `--max-pages` | Pages fetched per host before stopping (default 200) |

Crawl rules:

- **Same host, under the seed path.** `https://docs.example.com/guide/`
  (or `/guide`, or `/guide/index.html`) confines the crawl to `/guide/…`;
  a seed on `/` crawls the whole host. Other hosts are never fetched.
- **robots.txt** is honoured (user agent token `mnemos`), including
  `Crawl-delay`; `<meta name="robots" content="noindex|nofollow">` and
  `rel="nofollow"` too. A missing robots.txt (404) allows everything; an
  unreadable one (401/403/5xx) fails the run without deleting anything.
- URLs are normalised (lower-case host, no fragment, no default port);
  query strings are kept.
- Only `text/html` answers become documents. The main content is
  `<main>`, else `<article>`, else `<body>` minus nav/header/footer/aside,
  scripts and styles. The title comes from `<title>`, else `<h1>`.
- `updated` is the `Last-Modified` header. Pages served without it are
  re-pushed on every run (re-embedding cost: keep the scope tight).

## Conventions

| Source | `file_path` | Tags |
|---|---|---|
| Confluence | `confluence/<SPACEKEY>/<page_id>.md` | `[*--tags, "confluence", "confluence:<SPACEKEY>"]` |
| Jira | `jira/<PROJECTKEY>/<ISSUEKEY>.md` | `[*--tags, "jira", "jira:<PROJECTKEY>"]` |
| Web | `web/<host>/<sha1(normalized url)[:16]>.md` | `[*--tags, "web", "web:<host>"]` |

- The `<source>:<scope>` tag is the **sync key**: the files endpoint is
  queried with it. Keep it stable — renaming a scope means re-ingesting.
- Space and project keys are upper-cased (personal spaces `~user` keep
  their form). Besides matching how Atlassian shows them, it keeps a key
  such as `dist` or `vendor` out of the server's lowercase path deny-list
  (`core.path_filter`), which would otherwise drop the documents silently.
- For the same reason a single-label host (`http://build/`) gets a
  trailing dot in its path segment — `web/build./…` — while its tag stays
  `web:build`. Dotted hosts and `host:port` are used as-is.
- Every document is Markdown: `# <title>`, a `- Field: value` metadata
  list (always starting with `Source`), then the body. The server chunks it
  by headings and returns `metadata.source_url` with each hit.

Filter on these tags at query time, e.g. `tags_any: ["confluence:ENG"]` or
`mnemos search "rollback" --collection mnemos_knowledge --tags jira:OPS`.

## Sync semantics

For each scope, the engine lists what Mnemos holds under the scope tag,
then walks the source:

| Situation | Action |
|---|---|
| Not indexed yet | push |
| Source date ≠ stored `file_mtime` | push (the server replaces the file's chunks) |
| Source date = stored `file_mtime` | skip — no body download, no embedding |
| Source gives no date (web page without `Last-Modified`) | push |
| `--full` | push everything |
| Indexed but not seen this run | delete — **only** when the listing completed |

- A listing that fails part-way (network error, 5xx after retries, space
  not found, unreadable robots.txt, a crawled page that errors) **never
  deletes**: what was listed is still pushed, the run reports the error and
  exits 1.
- A web crawl stopped by `--max-pages` pushes what it saw and deletes
  nothing (unseen pages may just be beyond the cap); it is reported as
  truncated, not as a failure. Raise `--max-pages` until it no longer
  truncates if you want deletions on that host.
- One failed push is counted and logged; the run goes on and exits 1 at the
  end.
- Pushes are idempotent, so re-running after a failure is always safe.

## Scheduling

The `jamyouss/mnemos-cli` image packages the CLI and the connectors
(`ENTRYPOINT ["mnemos"]`). Pick one of two shapes.

**A long-running `ingest serve`** (syncs when it starts, then every
`interval`). With Docker Compose, the bundled `ingester` service is opt-in:

```bash
cp config/connectors.example.yaml config/connectors.yaml   # then edit it
# tokens (CONFLUENCE_TOKEN, …) and MNEMOS_API_KEY go in .env
docker compose --profile connectors up -d
docker compose logs -f ingester
```

On Kubernetes, run it as one more container next to the server (or its own
Deployment), with the file from a ConfigMap and the tokens from a Secret:

```yaml
- name: ingester
  image: jamyouss/mnemos-cli:latest
  args: ["ingest", "serve"]                     # reads /app/config/connectors.yaml
  env:
    - name: MNEMOS_URL
      value: http://127.0.0.1:8100              # same pod; else the Service URL
  envFrom:
    - secretRef:
        name: mnemos-ingest                     # MNEMOS_API_KEY + every token_env
  volumeMounts:
    - name: connectors-config                   # ConfigMap with key connectors.yaml
      mountPath: /app/config
      readOnly: true
  resources:
    requests: { cpu: 50m, memory: 128Mi }
    limits: { memory: 512Mi }
```

**Or a scheduler running one-shot commands** — `ingest run` with the same
file, or the per-source commands with flags.

Plain cron on a host with Docker:

```cron
# m h dom mon dow
15 2 * * * docker run --rm --env-file /etc/mnemos/ingest.env -v /etc/mnemos/connectors.yaml:/app/config/connectors.yaml:ro jamyouss/mnemos-cli ingest run
30 3 * * 0 docker run --rm --env-file /etc/mnemos/ingest.env jamyouss/mnemos-cli ingest web https://docs.example.com/guide/
```

Kubernetes CronJob:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: mnemos-ingest-confluence
spec:
  schedule: "15 2 * * *"
  concurrencyPolicy: Forbid          # never two syncs of the same scope at once
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: ingest
              image: jamyouss/mnemos-cli:latest
              args: ["ingest", "confluence", "--space", "ENG", "--space", "OPS"]
              env:
                - name: MNEMOS_URL
                  value: http://mnemos-server.mnemos.svc:8100
              envFrom:
                - secretRef:
                    name: mnemos-ingest   # MNEMOS_API_KEY, CONFLUENCE_URL/USER/TOKEN
              resources:
                requests: { cpu: 50m, memory: 128Mi }
                limits: { memory: 512Mi }
```

The first run of a big space pushes everything and can take a while (the
server embeds every chunk); later runs only touch what changed. Give the
first run a generous `activeDeadlineSeconds`, or run it once by hand.

## Network: proxies and private CAs

All HTTP goes through `httpx` with its environment support on:

- `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` are honoured — put the Mnemos
  server in `NO_PROXY` when it sits inside the cluster
  (`NO_PROXY=mnemos-server.mnemos.svc,localhost`).
- `SSL_CERT_FILE` (or `SSL_CERT_DIR`) points at a CA bundle for a corporate
  TLS-inspecting proxy or an internal CA. In the image, mount the bundle and
  set the variable, e.g. `-v /etc/ssl/corp-ca.pem:/certs/ca.pem:ro -e SSL_CERT_FILE=/certs/ca.pem`.

Requests time out after 60 s (10 s to connect) on sources and 300 s on the
Mnemos push (embedding is slow). 429 and 5xx answers, and connection
errors, are retried with exponential backoff (1 s, 2 s, 4 s — 4 attempts in
total), honouring `Retry-After` up to 60 s.

## Limits

- Confluence: pages only — blog posts, attachments (PDF, Office) and
  comments are not ingested. Page restrictions are not mirrored: anyone who
  can query Mnemos can read what the token could read. Use a token whose
  access matches the audience of the Mnemos instance.
- Jira: attachments, worklogs and custom fields are not ingested.
- Web: no JavaScript rendering — single-page apps that build their content
  client-side yield empty pages. No sitemap support; pages unreachable by
  links from the seeds are not found.
