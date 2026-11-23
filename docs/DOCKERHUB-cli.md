<p align="center">
  <img alt="Mnemos" src="https://raw.githubusercontent.com/jamyouss/mnemos/main/docs/logo-light.svg" width="360">
</p>

<h3 align="center">Mnemos CLI and knowledge connectors.</h3>

<p align="center">
  <a href="https://github.com/jamyouss/mnemos">GitHub</a> ·
  <a href="https://hub.docker.com/r/jamyouss/mnemos-server">mnemos-server image</a>
</p>

---

## What is this image

The `mnemos` command-line client for [`jamyouss/mnemos-server`](https://hub.docker.com/r/jamyouss/mnemos-server),
bundled with the knowledge connectors. Its main job is ingestion: keep
Confluence spaces, Jira projects and documentation sites in sync with the
`mnemos_knowledge` collection, and remote git repositories with `mnemos_code`.
Run it as the long-lived `ingest serve` (the compose `ingester` service, one
replica only) or as a cron job / Kubernetes CronJob running `ingest run`.
Both read the sources from the server's `GET /api/connectors`, so the key
needs admin rights or `write: "*"`.

```bash
docker run --rm \
  -e MNEMOS_URL=http://mnemos.example.com:8100 -e MNEMOS_API_KEY=... \
  -e CONFLUENCE_TOKEN=... -e JIRA_TOKEN=... \
  jamyouss/mnemos-cli ingest run
```

The entrypoint is `mnemos`, so any CLI command works (`status`, `search`,
`ingest confluence --space ENG`, `ingest git`…).

## Tags

| Tag | Meaning |
|-----|---------|
| `edge` | Manual build of `main` (workflow dispatch) |
| `1.2.3`, `1.2` | Semver tags from git releases (`1` too, from 1.0 on) |
| `latest` | Most recent semver release |

All tags are multi-arch (`linux/amd64` + `linux/arm64`).

## Key environment variables

| Variable | What it does |
|----------|--------------|
| `MNEMOS_URL` | Server URL (image default `http://rag-server:8100`) |
| `MNEMOS_API_KEY` | Bearer key when the server enforces auth |
| `MNEMOS_CONNECTORS_CONFIG` | Read the sources from this file instead of the server (also `--config`) |
| Each source's `token_env` (`CONFLUENCE_TOKEN`, `JIRA_TOKEN`, …) | Tokens of the declared sources |
| `CONFLUENCE_URL` / `CONFLUENCE_USER` / `CONFLUENCE_TOKEN` | Defaults of `ingest confluence` |
| `JIRA_URL` / `JIRA_USER` / `JIRA_TOKEN` | Defaults of `ingest jira` |
| `GIT_USER` / `GIT_TOKEN` | Defaults of `ingest git` |
| `HTTPS_PROXY` / `NO_PROXY` / `SSL_CERT_FILE` | Corporate proxy and CA bundle |

## Image facts

- **Base:** `python:3.12-slim`, runs as a non-root user
- **License:** MIT

## Links

- **Connectors guide:** [`docs/CONNECTORS.md`](https://github.com/jamyouss/mnemos/blob/main/docs/CONNECTORS.md)
- **Source & issues:** https://github.com/jamyouss/mnemos
