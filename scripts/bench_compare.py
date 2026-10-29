#!/usr/bin/env python3
"""Compare RootPilot Arena reports from several phones (or settings) head to head.

Copy each phone's report from ~/.local/state/rootpilot/bench/<id>.json (or the Benchmark tab),
then:  python3 scripts/bench_compare.py phoneA.json phoneB.json [--markdown]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def label(r: dict[str, Any]) -> str:
    d, s = r.get("device", {}), r.get("settings", {})
    device = f"{d.get('name', '?')} (iOS {d.get('ios', '?')})"
    return f"{device} · {s.get('model', '?')} · thinking {s.get('effort', '?')}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+", type=Path)
    args = ap.parse_args()
    reports = [json.loads(p.read_text()) for p in args.reports]
    seeds = {(r.get("suite"), r.get("seed")) for r in reports}
    if len(seeds) > 1:
        print(f"note: reports use different suites/seeds {sorted(seeds)}; puzzles differ\n")

    names = [f"#{i + 1}" for i in range(len(reports))]
    for n, r in zip(names, reports):
        print(f"{n}: {label(r)}")
    print()
    games: list[str] = []
    for r in reports:
        for g in r.get("games", []):
            if g["game"] not in games:
                games.append(g["game"])
    head = "| Game | " + " | ".join(names) + " |"
    print(head)
    print("|" + "---|" * (len(names) + 1))
    for gid in games:
        cells = []
        for r in reports:
            g = next((x for x in r.get("games", []) if x["game"] == gid), None)
            if g is None:
                cells.append("—")
                continue
            score = f"{g['correct']}/{g['total']} · {g['mistakes']} miss"
            cells.append(f"{score} · {g['seconds']} s · {g['steps']} steps")
        title = next(x["title"] for r in reports for x in r.get("games", []) if x["game"] == gid)
        print(f"| {title} | " + " | ".join(cells) + " |")
    cells = []
    for r in reports:
        t = r.get("totals", {})
        tokens = (t.get("input_tokens", 0) + t.get("output_tokens", 0)) // 1000
        cells.append(
            f"**{t.get('score')}%** · {t.get('seconds')} s · {t.get('steps')} steps · {tokens}k tok"
        )
    print("| **Total** | " + " | ".join(cells) + " |")

    best = max(
        range(len(reports)),
        key=lambda i: (reports[i]["totals"]["score"], -reports[i]["totals"]["seconds"]),
    )
    print(f"\nWinner: {names[best]} — {label(reports[best])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
