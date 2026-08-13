"""Shared result types + console formatting for the gateway sanity suite.

PASS  = the check succeeded.
FAIL  = a real defect in OUR gateway/wiring (fails the process, exit code 1).
SKIP  = a precondition was absent (missing creds, provider has no embeddings,
        provider-side unavailability like quota/503/timeout, deprecated model).
        Never fails the suite - provider realities are not our bug.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

_C = {"PASS": "\033[92m", "FAIL": "\033[91m", "SKIP": "\033[90m", "H": "\033[96m",
      "B": "\033[1m", "0": "\033[0m", "Y": "\033[93m"}


def _tty() -> bool:
    return sys.stdout.isatty()


def c(txt: str, key: str) -> str:
    if not _tty():
        return txt
    return f"{_C.get(key, '')}{txt}{_C['0']}"


@dataclass
class Result:
    name: str            # "provider :: capability"
    status: str          # PASS | FAIL | SKIP
    detail: str = ""     # short human note
    ms: float = 0.0


@dataclass
class Section:
    title: str
    results: list[Result] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", ms: float = 0.0) -> Result:
        r = Result(name, status, detail, ms)
        self.results.append(r)
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "-"}[status]
        line = f"  {c(icon, status)} {c(status, status)}  {name}"
        if detail:
            line += f"  {c('· ' + detail, 'SKIP')}"
        if ms:
            line += c(f"  ({ms:.0f}ms)", "SKIP")
        print(line, flush=True)
        return r

    def counts(self) -> tuple[int, int, int]:
        p = sum(1 for r in self.results if r.status == PASS)
        f = sum(1 for r in self.results if r.status == FAIL)
        s = sum(1 for r in self.results if r.status == SKIP)
        return p, f, s


def header(title: str) -> None:
    print("\n" + c(f"── {title} ", "H") + c("─" * max(0, 60 - len(title)), "H"), flush=True)


def summarize(command: str, sections: list[Section]) -> int:
    all_results = [r for sec in sections for r in sec.results]
    p = sum(1 for r in all_results if r.status == PASS)
    f = sum(1 for r in all_results if r.status == FAIL)
    s = sum(1 for r in all_results if r.status == SKIP)
    print("\n" + c(f"SUMMARY [{command}]: ", "B")
          + f"{c(str(p) + ' passed', 'PASS')}, "
          + f"{c(str(f) + ' failed', 'FAIL' if f else 'SKIP')}, "
          + f"{c(str(s) + ' skipped', 'SKIP')}")
    if f:
        print(c("  failures:", "FAIL"))
        for r in all_results:
            if r.status == FAIL:
                print(f"    {c('✗', 'FAIL')} {r.name}  {c(r.detail, 'SKIP')}")
    return 1 if f else 0
