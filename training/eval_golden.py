#!/usr/bin/env python3
"""Golden eval harness against OpenAI-compat POST /v1/chat/completions."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List


def load_cases(path: Path) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def chat(base_url: str, model: str, prompt: str, api_key: str | None) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return (payload["choices"][0]["message"]["content"] or "").strip()


def score(reply: str, expect_contains: List[str], expect_equals: str | None) -> bool:
    if expect_equals is not None:
        return reply.strip() == expect_equals.strip()
    return all(tok.lower() in reply.lower() for tok in expect_contains)


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--cases",
        type=Path,
        default=Path("training/golden/cases.jsonl"),
        help="Golden cases JSONL",
    )
    p.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000/v1",
        help="OpenAI-compat base URL ending in /v1",
    )
    p.add_argument("--model", default="mock_model")
    p.add_argument("--api-key", default="")
    args = p.parse_args(argv)

    cases = load_cases(args.cases)
    if not cases:
        print("no golden cases", file=sys.stderr)
        return 2

    passed = 0
    failed = 0
    for i, case in enumerate(cases):
        prompt = case["prompt"]
        expect_contains = case.get("expect_contains") or []
        expect_equals = case.get("expect_equals")
        try:
            reply = chat(
                args.base_url,
                case.get("model") or args.model,
                prompt,
                args.api_key or None,
            )
        except Exception as e:
            print(f"[{i}] ERROR {e}", file=sys.stderr)
            failed += 1
            continue
        ok = score(reply, expect_contains, expect_equals)
        if ok:
            passed += 1
            print(f"[{i}] PASS", flush=True)
        else:
            failed += 1
            short = reply if len(reply) <= 240 else reply[:240] + "…"
            print(f"[{i}] FAIL reply={short!r}", flush=True)

    print(f"summary passed={passed} failed={failed} total={len(cases)}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
