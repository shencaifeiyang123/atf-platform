"""Replace `config.api_key` on EVERY existing agent.

Run while the server is up. The key is read from env (never hardcoded):
    set AGENT_API_KEY=sk-...   (Windows)
    export AGENT_API_KEY=sk-...  (Unix)
    python scripts/replace_all_api_keys.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.environ.get("PLATFORM_BASE", "http://127.0.0.1:8866")
NEW_KEY = os.environ.get("AGENT_API_KEY", "").strip()


def _http_get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _http_put_json(url: str, payload: dict, timeout: float = 15.0) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def main() -> int:
    if not NEW_KEY:
        print("ERROR: AGENT_API_KEY env var is empty. Set it before running.", flush=True)
        return 2

    agents = _http_get_json(f"{BASE}/api/agents", timeout=10)
    print(f"fetched {len(agents)} agents from {BASE}", flush=True)

    ok, fail, skipped = 0, 0, 0
    for i, a in enumerate(agents, 1):
        cfg = a.get("config")
        if not isinstance(cfg, dict):
            cfg = {}
            a["config"] = cfg
        old = cfg.get("api_key", "")
        if old == NEW_KEY:
            skipped += 1
            print(f"[{i:>2}/{len(agents)}] SKIP {a.get('name','')} (already up to date)", flush=True)
            continue
        cfg["api_key"] = NEW_KEY
        status, text = _http_put_json(f"{BASE}/api/agents/{a['id']}", a, timeout=20)
        if status == 200:
            ok += 1
            print(f"[{i:>2}/{len(agents)}] OK   {a.get('name','')}", flush=True)
        else:
            fail += 1
            print(f"[{i:>2}/{len(agents)}] FAIL ({status}) {a.get('name','')}: {text[:200]}", flush=True)

    print(f"\nDONE: ok={ok} skip={skipped} fail={fail}", flush=True)
    return 0 if fail == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
