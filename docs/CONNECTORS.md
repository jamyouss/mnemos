# Knowledge connectors

`mnemos ingest` pulls knowledge that does not live in a local checkout —
Confluence pages, Jira issues, web documentation, remote git repositories —
into Mnemos: `mnemos_knowledge` for documents, `mnemos_code` (by default) for
repositories, so agents find them with `mnemos_search` / `mnemos_search_code`
next to local code and memories.

```
mnemos ingest run | serve              (server's connectors, or --config FILE)
mnemos ingest confluence|jira|web|git  (one source, from flags)
   │  source REST API / HTTP crawl / git clone   (retries, proxy-aware)
   ▼
connector → Markdown documents (# title, metadata, body) · git: files as they are
   │
   ▼
sync engine ── GET  /api/index/<collection>/files?tag=<source>:<scope>
            ── POST /api/index                     (new / changed documents)
            ── DELETE /api/index/<collection>/<file_path>   (gone at the source)
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
scope, plus skipped files in the note — and **exits 1 if anything failed**,
so a scheduler flags the run.

## Configuration file

Every source is declared once in the server's `connectors` configuration,
stored in Qdrant and shared by every server pod (see
[`CONFIGURATION.md`](CONFIGURATION.md#shared-configuration)). Edit it from the
dashboard's Connectors page or `PUT /api/connectors`. At the first start the
server imports `config/connectors.yaml` (`MNEMOS_CONNECTORS_CONFIG`; start
from [`config/connectors.example.yaml`](../config/connectors.example.yaml))
if the database holds no connectors yet; after that the file is ignored by
the server. The shape is the same in the file and the API:

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
  - type: git
    name: app
    url: https://github.com/acme/app.git
    # token_env: GITHUB_TOKEN    # private repository: read-only token
    # branch: main               # default: the remote's default branch
    # paths: [docs/, services/]  # default: the whole repository
    # exclude: ["*.lock"]
    # max_file_kb: 512
    # collection: mnemos_code    # or mnemos_knowledge
```

Keys mirror the command-line options of each source below (`spaces` /
`projects` / `urls` are the repeatable `--space` / `--project` / URL
arguments). Tokens never go in the configuration: `token_env` names the
variable, which must be set in the ingester's environment when it loads the
sources (optional for git: a public repository
needs none) — a missing one, an unknown key, a typo in `type` or a duplicate
`name` stops the run before anything is synced, with every problem listed at
once.

Where `mnemos ingest run` / `serve` read the sources: `--config FILE`, else
`MNEMOS_CONNECTORS_CONFIG`, else the server's `GET /api/connectors` (on
`MNEMOS_URL`, with an `MNEMOS_API_KEY` that has the admin role or
`write: "*"`). A file is for one-off or local runs; deployed ingesters read
the server. Under Compose the `ingester` reads `.env` like every service, so
an `MNEMOS_CONNECTORS_CONFIG` set there switches it to a file its container
does not mount: leave that line commented.

### From the dashboard

The **Connectors** page of the dashboard (`http://localhost:8100/connectors`)
edits that configuration: one card per source, the `interval`, and per scope tag
how many chunks `mnemos_knowledge` (`mnemos_code` for a git source, by
default) holds — `not synced yet` until the ingester
has delivered something. The server validates with the ingester's own parser
before saving, so a save the ingester would reject is refused (`422`) with
every problem listed, and nothing is stored. A save carries the revision the
page loaded; if someone saved in between, it answers `409`, nothing is
overwritten, and the page offers to refresh.

- **Writing** needs `MNEMOS_UI_CONFIG_WRITABLE=true` on the server and an
  admin key; otherwise the page is read-only.
- **Tokens** never pass through the server: a card only names the variable
  (`token_env`), set in the ingester's environment. The server cannot check
  that it is set — the ingester reports a missing one at its next sync.
- An `ingest serve` reading the server picks the change up within 5 s (it
  polls the revision) and resyncs. A new `interval` still needs
  `docker compose restart ingester`.
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
then every `interval`, when the configuration changes (the server's revision,
or the `--config` file, checked every 5 s), when the dashboard asks, and
immediately on `SIGHUP`:

| To… | Do |
|---|---|
| resync now, every source or one | **Sync all**, or **Sync** on a source card, in the dashboard's Connectors page (`POST /api/connectors/sync`) |
| resync now, from a shell | `docker compose kill -s HUP ingester` · `kubectl exec deploy/mnemos-ingester -- kill -HUP 1` |
| resync one source with `--full` | `mnemos ingest run --only <name> --full` from anywhere |
| apply an edited configuration | nothing: a save (or an edit of the `--config` file) triggers a sync within seconds, and the sources are re-read before every sync (a broken one is reported and the previous version kept). A changed `interval` needs a restart |
| stop | `SIGTERM` — the service exits at once; an interrupted sync resumes at the next run |

A dashboard request is stored on the server (`connectors_sync`, the last 20
kept) and read by `serve` at its next poll, with the connectors. `serve`
queues the sources it names, then acknowledges it (`POST
/api/connectors/sync/ack`), which is what the page shows: *Sync requested*
until then. Requests that arrive during a sync merge into the next one.
Requests filed while `serve` was down are covered by its start-up sync. Only
`serve` reading the server sees them: with `--config`, use `SIGHUP`.

Without `interval`, `serve` syncs at start-up, on configuration change, on
request and on `SIGHUP` only.

`serve` and `run` also report to the server (`POST /api/connectors/sync/report`):
each source as it starts and ends, with the per-scope counts and errors, and
`serve` checks in every minute. The Connectors page reads that to show
*Syncing*, *synced 4 min ago* or *Sync failed* with the error, and warns when
no sync service ever reported or one has been silent for three minutes. A
report the server refuses (a key without `admin` or `write: "*"`) is logged
and the sync goes on.

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
  `Crawl-delay` (capped at 30 s); `<meta name="robots" content="noindex|nofollow">` and
  `rel="nofollow"` too. A missing robots.txt (404) allows everything; an
  unreadable one (401/403/5xx) fails the run without deleting anything.
- URLs are normalised (lower-case host, no fragment, no default port);
  query strings are kept.
- Only HTML answers (`text/html`, `application/xhtml+xml`) become documents.
  Scripts, styles and `<nav>` are stripped; the main content is `<main>`,
  else `<article>`, else `role="main"`, else `<body>` minus
  header/footer/aside/forms. The title comes from `<title>`, else `<h1>`.
- `updated` is the `Last-Modified` header. Pages served without it are
  re-pushed on every run (re-embedding cost: keep the scope tight).

## Git repositories

```bash
mnemos ingest git https://github.com/acme/app.git
GIT_TOKEN=glpat-… mnemos ingest git https://gitlab.example.com/group/app --branch develop \
    --path docs/ --path services/api --exclude '*.lock' --collection mnemos_knowledge
```

| Option | Meaning |
|---|---|
| `URL` | HTTPS repository URL, any host (GitHub, GitLab, Gitea, self-hosted). SSH URLs are refused |
| `--branch` | Branch to sync (default: the remote's default branch) |
| `--path` | Only files under this repository path (repeatable) |
| `--exclude` | `fnmatch` glob on the repository path, `*` crossing `/` — `*.lock`, `*/testdata/*` (repeatable) |
| `--max-file-kb` | Larger files are skipped (default 512) |
| `--collection` | `mnemos_code` (default: AST chunking, found by `mnemos_search_code`) or `mnemos_knowledge` |
| `--token` / `GIT_TOKEN` | Access token; omit for a public repository |
| `--user` / `GIT_USER` | User name sent with the token (default `oauth2`, which GitLab expects and GitHub ignores) |

How a run works:

- **Blobless clone** (`git clone --filter=blob:none`) into a temporary
  directory: commits and trees, no file contents. One `git log` dates every
  file by the last commit touching it — that date is the sync's
  `file_mtime`, so a file whose last commit did not change is skipped
  without being downloaded. Contents are fetched one by one, only for what
  is pushed. The clone is deleted at the end of the run; nothing persists.
- **Files**: regular files of the branch's HEAD only — no symlinks, no
  submodules. Binary extensions (images, archives, fonts, office
  documents…) are never fetched; a fetched file over `max_file_kb`, or with
  a NUL byte in its first 8 KB, is skipped — and its indexed copy removed
  if it had one. The server's path filter applies on top: `node_modules/`,
  `vendor/`, `dist/`, lockfiles… stay out as for a local checkout.
- **Token**: sent as an `Authorization` header scoped to the repository's
  origin, through `GIT_CONFIG_*` environment variables — never in a URL,
  on the command line or in `.git/config`. Use a read-only token
  (GitHub fine-grained *Contents: read*; GitLab `read_repository`). Without
  a token, git's own credential helper applies (handy for `ingest git` on a
  workstation). `SSL_CERT_FILE` is passed on to git as `GIT_SSL_CAINFO`.
- **Paths and tags**: `git/<host>/<owner~repo>/<path in repo>`, tags
  `[*--tags, "git", "git:<host>/<owner>/<repo>"]`. Owner and repository share
  one path segment so that a repository called `build` or `vendor` is not
  dropped by the server's path filter. Results link to the file on GitHub
  (`/blob/<branch>/`) or GitLab (`/-/blob/<branch>/`); on other forges, to
  the repository.
- A repository also mounted locally and indexed by the watcher would be
  indexed twice, under two paths: pick one.

## Conventions

| Source | `file_path` | Tags |
|---|---|---|
| Confluence | `confluence/<SPACEKEY>/<page_id>.md` | `[*--tags, "confluence", "confluence:<SPACEKEY>"]` |
| Jira | `jira/<PROJECTKEY>/<ISSUEKEY>.md` | `[*--tags, "jira", "jira:<PROJECTKEY>"]` |
| Web | `web/<host>/<sha1(normalized url)[:16]>.md` | `[*--tags, "web", "web:<host>"]` |
| Git | `git/<host>/<owner~repo>/<path in repo>` | `[*--tags, "git", "git:<host>/<owner>/<repo>"]` |

- The `<source>:<scope>` tag is the **sync key**: the files endpoint is
  queried with it. Keep it stable — renaming a scope means re-ingesting.
- Space and project keys are upper-cased (personal spaces `~user` keep
  their form). Besides matching how Atlassian shows them, it keeps a key
  such as `dist` or `vendor` out of the server's lowercase path deny-list
  (`core.path_filter`), which would otherwise drop the documents silently.
- For the same reason a single-label host (`http://build/`) gets a
  trailing dot in its path segment — `web/build./…` — while its tag stays
  `web:build`. Dotted hosts and `host:port` are used as-is.
- Every Confluence, Jira and web document is Markdown: `# <title>`, a
  `- Field: value` metadata list (always starting with `Source`), then the
  body. Git files are pushed as they are, so the server picks the chunker by
  extension (Go AST, Vue SFC, Markdown headings…). Every hit carries
  `metadata.source_url`.

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
# sources: dashboard Connectors page (or config/connectors.yaml before the first start)
# tokens (CONFLUENCE_TOKEN, …) and MNEMOS_API_KEY go in .env
docker compose --profile connectors up -d
docker compose logs -f ingester
```

On Kubernetes, run it as its own single-replica Deployment with
`strategy: Recreate` — never as a sidecar of a multi-replica server, where
every replica would sync every source. It reads the sources from the server,
so no ConfigMap; the tokens come from a Secret:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mnemos-ingester
spec:
  replicas: 1
  strategy: { type: Recreate }      # never two ingesters at once
  selector: { matchLabels: { app: mnemos-ingester } }
  template:
    metadata: { labels: { app: mnemos-ingester } }
    spec:
      containers:
        - name: ingester
          image: jamyouss/mnemos-cli:latest
          args: ["ingest", "serve"]                 # sources from GET /api/connectors
          env:
            - name: MNEMOS_URL
              value: http://mnemos-server.mnemos.svc:8100
          envFrom:
            - secretRef:
                name: mnemos-ingest                 # MNEMOS_API_KEY (write "*") + every token_env
          resources:
            requests: { cpu: 50m, memory: 128Mi }
            limits: { memory: 512Mi }
```

**Or a scheduler running one-shot commands** — `ingest run` (sources from the
server), or the per-source commands with flags. On Kubernetes use a CronJob
with `concurrencyPolicy: Forbid`.

Plain cron on a host with Docker:

```cron
# m h dom mon dow
15 2 * * * docker run --rm --env-file /etc/mnemos/ingest.env jamyouss/mnemos-cli ingest run
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
Mnemos push (embedding is slow). 429, 500, 502, 503 and 504 answers, and connection
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
