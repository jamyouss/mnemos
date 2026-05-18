# Contributing to Mnemos

Thanks for considering a contribution. Mnemos is a small, focused project —
PRs are very welcome, especially ones that come with tests.

## Getting set up

```bash
git clone https://github.com/jamyouss/mnemos.git
cd mnemos

# One-shot: venv + every package in editable mode + dev tools
make install-dev

# Activate the venv
source venv/bin/activate

# Bring the stack up (uses pre-built images)
make up
# Or, if you've edited code and want YOUR build:
make up-dev

# Verify
make doctor
```

Want to point Mnemos at your real code? Two ways:

```bash
# 1. Symlink it into ./data/codebase (default mount):
./scripts/link-codebase.sh ~/code

# 2. Or set MNEMOS_CODEBASE_HOST_PATH and restart:
MNEMOS_CODEBASE_HOST_PATH=~/code make restart
```

## Project layout

```
packages/
  core/       Shared library: indexer, search, LLM abstraction, sparse encoder, etc.
  eval/       Evaluation harness (used by `mnemos eval` CLI commands).
server/       FastAPI + MCP HTTP transport.
watcher/      Filesystem watcher service.
cli/          `mnemos` Click CLI.
tests/        pytest suite (run with `make test`).
scripts/      Operational scripts (git hooks, link-codebase, run-demo).
examples/     Bundled demo content used by `make demo`.
docs/         Architecture, deployment, evaluation, MCP integration, etc.
```

Read `docs/ARCHITECTURE.md` for the data-flow overview before touching the
retrieval pipeline.

## Running the tests

```bash
make test        # full unit test suite (~7 s, 178 tests)
make test-fast   # ultra-quick subset (no transformers / no qdrant)
make lint        # compile-check every .py file
```

Tests that need a live server/Qdrant (`test_api.py`, `test_cli.py`, …) are
**excluded by default** and live for manual / integration runs.

## Submitting a change

1. **Open an issue first** for non-trivial features. We'd rather discuss the
   shape of a change before you spend a week on it. Bug fixes don't need this.
2. **Branch from `main`** and use a descriptive branch name
   (`fix/skip-empty-chunks`, `feat/yaml-tenant-config`).
3. **Write a test** for the behaviour you changed. The CI workflow runs
   `pytest tests/` on every push and PR.
4. **One commit per logical change** with a body that explains the **why**.
   Past commits in this repo are decent examples of the style we're after.
5. **Update the docs.** Docs live next to the code in `docs/`. If you add a
   new env var, surface it in `.env.example` and in `docs/CONFIGURATION.md`.
6. **Open a PR**. The PR template will ask you a few light questions.

## Adding a new collection / project

Don't edit `packages/core/collections.py` — the four collections (`mnemos_skills`,
`mnemos_docs`, `mnemos_code`, `mnemos_memory`) are fixed by design.

Projects are not first-class entities: each chunk in `mnemos_code` carries a
`tags: list[str]` payload, and search filters with `tags_any` (OR) and
`tags_all` (AND). The default mapping `path → tags` is the cumulative path
segments (`foo/bar/baz/file.go` → `["foo", "foo/bar", "foo/bar/baz"]`). To
override that, copy `config/projects.example.yaml` → `config/projects.yaml`,
declare your `path-prefix → [tags]` rules, and restart the server.

## Adding a new LLM provider

`core/llm/` defines a single `LLMProvider` Protocol. New providers should:

1. Implement `complete()` and `complete_prompt()` (see `core/llm/ollama.py` for a
   minimal reference).
2. Register in `core/llm/factory.py:make_llm_provider`.
3. Lazy-import their SDK so they remain optional dependencies.
4. Add tests under `tests/test_llm/`.

## Code style

- Python 3.12+ everywhere.
- Type hints on every public function (`from __future__ import annotations`).
- One short docstring per public function explaining the **why** when it's not
  obvious from the name.
- No emojis in code (only in user-facing CLI output via `rich`).
- Run `make lint` before pushing — it's not a formatter, just a syntax sanity check.

## Quick links

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Eval results](docs/EVAL.md)
- [Evaluation HOWTO](docs/EVALUATION.md)
- [Deployment guide](docs/DEPLOYMENT.md)
- [Security policy](SECURITY.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
