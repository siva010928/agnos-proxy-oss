"""demo/anti_coupling_demo.py - prove the decoupling is enforced by the BUILD, not by hope.

Our core (governance + the vault) must talk to the Bifrost engine ONLY over its OpenAI-shaped
HTTP surface. If any engine-specific detail (a `x-bf-*` header, `bifrost_key_name` interpreted
outside the boundary, a `/api/guardrails|virtualkeys|budgets` call, ...) leaks into our layer,
`tests/test_anti_coupling.py` FAILS and it can't ship. That is what "swappable engine" actually
means - and it's checkable.

This demo shows both sides, live and non-destructively:
  1. PASS  - run the audit on the real tree (green).
  2. FAIL  - drop a file into gateway/core that couples to Bifrost internals; the audit catches
             it and fails (red), naming the violation.
  3. PASS  - remove that file; the audit is green again.

    python demo/anti_coupling_demo.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "gateway" / "core" / "_coupling_violation_demo.py"

# A file that COUPLES to Bifrost internals - exactly what the audit must forbid outside the
# engine boundary: a Bifrost-only header, an interpreted key-select side-channel, and a call to
# Bifrost's private governance API instead of our own layer.
VIOLATION = '''\
"""TEMP demo violation - simulates a dev coupling our core to Bifrost internals."""
import httpx


def leak(bifrost_key_name: str):
    # ❌ interpreting the Bifrost key-select side-channel in our core
    # ❌ sending a Bifrost-only header
    # ❌ calling Bifrost's private governance API instead of our own layer
    headers = {"x-bf-api-key": bifrost_key_name}
    return httpx.post("http://bifrost:8080/api/guardrails", headers=headers)
'''

BAR = "=" * 78


def audit() -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_anti_coupling.py", "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=REPO, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def show(title: str, ok: bool, out: str, expect_pass: bool) -> bool:
    """Print the audit tail + a verdict framed by what SHOULD happen. When we deliberately inject
    coupling, the audit is SUPPOSED to fail - so that failure is shown as a green ✓ ('as it must')."""
    tail = "\n".join(out.splitlines()[-6:])
    if expect_pass:
        verdict = "\033[92mPASS ✓  the core is decoupled\033[0m" if ok \
            else "\033[91mUNEXPECTED FAIL ✗  (should have passed - investigate)\033[0m"
    else:
        verdict = ("\033[92mFAILS ✓  exactly as it must - the guard caught the injected coupling "
                   "(this is the GOOD outcome)\033[0m") if not ok \
            else "\033[91mUNEXPECTED PASS ✗  the guard MISSED the coupling - investigate\033[0m"
    print(f"\n{BAR}\n{title}\n{BAR}\n{tail}\n  -> {verdict}")
    return ok == expect_pass


def main() -> int:
    print("Anti-coupling audit - the engine is swappable because the build guarantees it.\n"
          "Rule: our core reaches Bifrost ONLY over OpenAI-shaped HTTP; no engine-isms leak in.\n"
          "There are 3 steps; ALL THREE should end in a green check.")

    ok1, out1 = audit()
    c1 = show("1) The real code tree - is our core decoupled from the engine?", ok1, out1, expect_pass=True)

    print(f"\n  >> Now I INTENTIONALLY inject a coupling violation:  {PROBE.relative_to(REPO)}")
    print("     (a Bifrost-only x-bf-api-key header + interpreting bifrost_key_name + calling")
    print("      Bifrost's /api/guardrails from our core). The next audit SHOULD fail - and that")
    print("      failure is the whole point: it proves the guard actually rejects coupling.")
    PROBE.write_text(VIOLATION)
    try:
        ok2, out2 = audit()
        c2 = show("2) With coupling injected - the guard MUST reject it (a failure here = success)", ok2, out2, expect_pass=False)
    finally:
        PROBE.unlink(missing_ok=True)

    ok3, out3 = audit()
    c3 = show("3) Violation removed - back to green", ok3, out3, expect_pass=True)

    good = c1 and c2 and c3
    print(f"\n{BAR}")
    if good:
        print("\033[92mPROVED: decoupling is ENFORCED BY THE BUILD. Clean tree passes; the moment "
              "coupling is added the audit fails and blocks it; remove it and it's green again.\n"
              "That is why 'swap the engine' is a guarantee, not a slogan.\033[0m")
    else:
        print("\033[91mUnexpected result - one of the three steps didn't behave as expected. See above.\033[0m")
    print(BAR)
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
