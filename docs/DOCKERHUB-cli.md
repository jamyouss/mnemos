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
bundled with the knowledge connectors. Its main job is scheduled ingestion:
run it as a cron job or a Kubernetes CronJob to keep Confluence spaces, Jira
projects and documentation sites in sync with the `mnemos_knowledge`
collection.

```bash
docker run --rm \
  -e MNEMOS_URL=http://mnemos.example.com:8100 -e MNEMOS_API_KEY=... \
  -e CONFLUENCE_URL=https://example.atlassian.net \
  -e CONFLUENCE_USER=bot@example.com -e CONFLUENCE_TOKEN=... \
  jamyouss/mnemos-cli ingest confluence --space ENG
```

The entrypoint is `mnemos`, so any CLI command works (`status`, `search`,
`ingest jira`, `ingest web`…).

## Tags

| Tag | Meaning |
|-----|---------|
| `edge` | Latest commit on `main` |
| `1.2.3`, `1.2`, `1` | Semver tags from git releases |
| `latest` | Most recent semver release |

All tags are multi-arch (`linux/amd64` + `linux/arm64`).

## Key environment variables

| Variable | What it does |
|----------|--------------|
| `MNEMOS_URL` | Server URL (image default `http://rag-server:8100`) |
| `MNEMOS_API_KEY` | Bearer key when the server enforces auth |
| `CONFLUENCE_URL` / `CONFLUENCE_USER` / `CONFLUENCE_TOKEN` | Confluence site and credentials |
| `JIRA_URL` / `JIRA_USER` / `JIRA_TOKEN` | Jira site and credentials |
| `HTTPS_PROXY` / `NO_PROXY` / `SSL_CERT_FILE` | Corporate proxy and CA bundle |

## Image facts

- **Base:** `python:3.12-slim`, runs as a non-root user
- **License:** MIT

## Links

- **Connectors guide:** [`docs/CONNECTORS.md`](https://github.com/jamyouss/mnemos/blob/main/docs/CONNECTORS.md)
- **Source & issues:** https://github.com/jamyouss/mnemos
