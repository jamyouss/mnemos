<p align="center">
  <img alt="Mnemos" src="https://raw.githubusercontent.com/digital-gigafactory/mnemos/main/docs/logo-light.svg" width="360">
</p>

<h3 align="center">Filesystem watcher for Mnemos.</h3>

<p align="center">
  <a href="https://github.com/digital-gigafactory/mnemos">GitHub</a> ·
  <a href="https://hub.docker.com/r/jamyouss/mnemos-server">mnemos-server image</a>
</p>

---

## What is this image

This is the companion watcher for [`jamyouss/mnemos-server`](https://hub.docker.com/r/jamyouss/mnemos-server).
It tails your mounted source code with `watchdog`, debounces filesystem
events, and pushes incremental indexing requests to the Mnemos server so
your AI coding agent always queries fresh embeddings.

You almost never pull this image directly — it ships as part of the
[Mnemos compose stack](https://github.com/digital-gigafactory/mnemos/blob/main/docker-compose.yml).

## Tags

| Tag | Meaning |
|-----|---------|
| `edge` | Latest commit on `main` |
| `1.2.3`, `1.2`, `1` | Semver tags from git releases |
| `latest` | Most recent semver release |

All tags are multi-arch (`linux/amd64` + `linux/arm64`).

## Key environment variables

| Variable | Default | What it does |
|----------|---------|--------------|
| `MNEMOS_SERVER_URL` | `http://rag-server:8100` | Where to push reindex requests |
| `CODEBASE_PATH` | `/data/codebase` | Directory to watch |
| `CLAUDE_CONFIG_PATH` | `/data/claude-config` | Optional Claude config to watch |
| `WATCHER_DEBOUNCE_MS` | `2000` | Coalesce bursts of FS events |

## Image facts

- **Size:** ~160 MB
- **Base:** `python:3.12-slim`
- **License:** MIT

## Links

- **Source & issues:** https://github.com/digital-gigafactory/mnemos
- **Quickstart:** [`docs/QUICKSTART.md`](https://github.com/digital-gigafactory/mnemos/blob/main/docs/QUICKSTART.md)
- **Architecture:** [`docs/ARCHITECTURE.md`](https://github.com/digital-gigafactory/mnemos/blob/main/docs/ARCHITECTURE.md)
