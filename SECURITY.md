# Security Policy

Agnos Proxy is a security tool - the control plane holds your keys and governs every
call. We take vulnerabilities seriously and appreciate responsible disclosure.

## Reporting a vulnerability

**Do not open a public issue, PR, or discussion for a security problem.**

Report privately via GitHub:

1. Go to the repository's **Security** tab -> **Report a vulnerability** (GitHub Private Vulnerability Reporting).
2. Include: affected version/commit, a description, reproduction steps or PoC, and impact.

We aim to acknowledge within **3 business days** and to provide a remediation timeline
after triage. Please give us a reasonable window to fix and release before any public
disclosure; we are happy to credit you.

## Scope

In scope:

- The gateway control plane (`gateway/`): auth, guardrails, budgets, routing, the
  credential vault, admin APIs, and the dashboard it serves.
- Credential handling and the `BackendEngine` boundary (e.g. any way an engine or a
  component could reach a provider key it should never see).
- Guardrail bypasses (PII/secret leakage past a configured rule).

Out of scope:

- Vulnerabilities in third-party engines (Bifrost, LiteLLM, Portkey) or model
  providers themselves - report those upstream. (Agnos Proxy's design intentionally
  contains a compromised engine to a single in-flight key; reports showing that
  containment failing are very much in scope.)
- Findings that require a pre-compromised host or a misconfiguration explicitly warned
  against in the docs.

## Handling of secrets

- Provider keys are encrypted at rest (Fernet) with `GATEWAY_MASTER_KEY` and are only
  decrypted per request, in flight. Never commit real keys - `.env` is git-ignored.
- The fake values in demos/tests (`AKIA...EXAMPLE`, `sk-ant-...FAKE-DEMO`) are
  intentional fixtures for the secret detector and are not live credentials.

## Supported versions

This project is pre-1.0; security fixes land on `main`. Pin a commit/tag and watch
releases for updates.
