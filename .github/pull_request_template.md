# Summary

<!-- What does this PR do and why? Link the issue it closes. -->
Closes #

## Type of change

- [ ] Bug fix
- [ ] New feature (engine adapter / provider / guardrail / dashboard)
- [ ] Docs
- [ ] Refactor / chore

## How was this tested?

<!-- Commands you ran + results. -->
- [ ] `poetry run pytest -m "not live and not integration"` passes
- [ ] `cd frontend && npm run build` passes (if the dashboard changed)
- [ ] Ran integration/live locally if relevant (`pytest -m integration` / `-m live`)

## Checklist

- [ ] Focused change; PR does one thing
- [ ] Docs/tests updated alongside behavior
- [ ] No secrets, real keys, or personal data in the diff (`.env` stays git-ignored)
- [ ] For a new engine: implements the `BackendEngine` port and passes `tests/test_anti_coupling.py`
- [ ] Uses plain hyphens in docs (no em dashes)
