## Summary

<!-- One-paragraph description of what changes and why. -->

## Changes

<!-- Bullet list of the concrete edits. Reviewers should be able to map
each bullet to a file or two. -->

-
-

## How to verify

```bash
# Commands a reviewer can run to convince themselves the change works.
make test
# …
```

## Checklist

- [ ] Tests added or updated.
- [ ] Docs updated (`docs/`, `.env.example`, `CLAUDE.md`, README, …).
- [ ] No personal info or internal client names introduced.
- [ ] If you added an env var, it has a sensible default and is documented.
- [ ] If you touched the retrieval pipeline, `docs/EVAL.md` numbers are still
      meaningful (no schema break that invalidates them).
