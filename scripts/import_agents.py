"""Bulk-import agents from SpiderTest/agents.json into the running platform.

Dedup by app_id against existing agents fetched from /api/agents.
Each new entry is created with adapter=bailian, industry=education,
default Coze PAT (read from running server's /api/agent_defaults), and
the SAMPLE_VARS biz_params used in the new-agent modal.

If the server has ATF_PASSWORD set, pass it via env var or arg:
    ATF_PASSWORD=xxx python scripts/import_agents.py
    python scripts/import_agents.py --password xxx

Run while server is up:
    python scripts/import_agents.py
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Force UTF-8 stdout on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:8866"
SOURCE = Path(r"D:\AI_code\agent_test_framework\SpiderTest\agents.json")

# Mirror of SAMPLE_VARS in web/index.html (一键填充 按钮的样例数据，脱敏虚拟值)
SAMPLE_VARS: dict = {
    "stream": True,
    "background": False,
    "n": 1,
    "session_id": "6c46141021194b649c5f39278f18bad8",
    "biz_params": {
        "user_prompt_params": {
            "course_info": {
                "course_custom": "这是课程的的自定义参数",
                "course_description": "这是这个课程的描述",
                "course_name": "从三皇五帝到春秋战国",
            },
            "level_config": {
                "lecture_material": (
                    "本篇内容主要围绕\"不好魔王\"直播间展开，通过\"大咖\"爸爸和\"小咖\"儿子"
                    "乘坐时光机穿越时空的视角，生动勾勒了中国远古神话与传说时代的开端。\n\n"
                    "## 一、 创世神话：盘古开天地\n\n"
                    "故事始于混沌时期。大约326.7万年前，世界宛如巨蛋，盘古在其中孕育"
                    "一万八千年后，用神斧劈开混沌，使清气上升为天，浊气下降为地 。\n\n"
                    "## 二、 三皇之首：伏羲的诞生\n\n"
                    "大咖父子随后见证了中国典籍记载中最早的王——伏羲的诞生 。\n\n"
                    "- 神奇受孕：伏羲的母亲华胥氏在山上游玩时，因踩中巨人的大脚印而怀孕\n"
                    "- 异象天生：伏羲出生时呈现\"人首蛇身\"的神异长相\n"
                ),
                "level_custom": "关卡自定义参数",
                "level_index": 1,
                "level_name": "成语猜猜（百炼）",
                "pack_name": "第1节_上_三皇始祖伏羲",
            },
            "user_info": {
                "age": -1,
                "city": "",
                "gender": "男",
                "grade": "",
                "name": "cUcTzxWZkK",
                "user_id": 4117711,
            },
        },
    },
    "enable_system_time": True,
    "scope": "publish",
}


# Shared cookie jar so we keep atf_sid across requests
_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))


def _http_get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with _opener.open(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def _http_post_json(url: str, payload: dict, timeout: float = 15.0) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def maybe_login(password: str) -> bool:
    """Probe /api/auth/status. If auth is enabled and we're not yet authed,
    POST password and rely on the Set-Cookie atf_sid. Returns True if ready
    to call protected endpoints."""
    try:
        status = _http_get_json(f"{BASE}/api/auth/status", timeout=5)
    except Exception as e:
        print(f"ERROR: cannot reach {BASE}/api/auth/status: {e}", flush=True)
        return False
    if not status.get("enabled"):
        print("auth: disabled, no login needed", flush=True)
        return True
    if status.get("authenticated"):
        print("auth: already authenticated", flush=True)
        return True
    if not password:
        print(
            "ERROR: ATF_PASSWORD required (server has auth enabled). "
            "Set env ATF_PASSWORD=... or pass --password ...",
            flush=True,
        )
        return False
    code, text = _http_post_json(f"{BASE}/api/auth/login", {"password": password}, timeout=10)
    if code != 200:
        print(f"ERROR: login failed ({code}): {text[:200]}", flush=True)
        return False
    print("auth: login OK", flush=True)
    return True


def fetch_existing() -> tuple[set[str], set[str]]:
    agents = _http_get_json(f"{BASE}/api/agents", timeout=10)
    app_ids: set[str] = set()
    names: set[str] = set()
    for a in agents:
        cfg = a.get("config") or {}
        aid = cfg.get("app_id")
        if aid:
            app_ids.add(aid)
        nm = a.get("name")
        if nm:
            names.add(nm)
    return app_ids, names


def main() -> int:
    global BASE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--password", default=os.environ.get("ATF_PASSWORD", ""),
                        help="服务器 ATF_PASSWORD；优先读 env，再读此参数")
    parser.add_argument("--source", default=str(SOURCE),
                        help="agents.json 路径")
    parser.add_argument("--base", default=BASE, help="API 地址")
    parser.add_argument("--industry", default="教育", help="新建 agent 的 industry")
    args = parser.parse_args()

    BASE = args.base.rstrip("/")

    if not maybe_login(args.password):
        return 4

    src_path = Path(args.source)
    src = json.loads(src_path.read_text(encoding="utf-8"))
    if not isinstance(src, list):
        print("ERROR: agents.json is not a list", flush=True)
        return 1

    existing_app_ids, existing_names = fetch_existing()
    print(f"existing agents: {len(existing_app_ids)} app_ids, {len(existing_names)} names", flush=True)

    to_add: list[dict] = []
    skipped_app_id = 0
    skipped_name = 0
    skipped_invalid = 0
    for entry in src:
        name = (entry.get("name") or "").strip()
        app_id = (entry.get("app_id") or "").strip()
        prompt = (entry.get("prompt") or "").strip()
        if not (name and app_id and prompt):
            skipped_invalid += 1
            continue
        if app_id in existing_app_ids:
            skipped_app_id += 1
            continue
        if name in existing_names:
            skipped_name += 1
            continue
        to_add.append({"name": name, "app_id": app_id, "prompt": prompt})

    print(
        f"plan: total={len(src)} skip(app_id-dup)={skipped_app_id} "
        f"skip(name-dup)={skipped_name} skip(invalid)={skipped_invalid} "
        f"to_add={len(to_add)}",
        flush=True,
    )

    if not to_add:
        print("nothing to import.", flush=True)
        return 0

    api_key = ""
    try:
        defaults = _http_get_json(f"{BASE}/api/agent_defaults", timeout=5)
        api_key = defaults.get("api_key") or ""
    except Exception as e:
        print(f"WARN: failed to fetch /api/agent_defaults: {e}", flush=True)
    if not api_key:
        print("ERROR: no api_key from /api/agent_defaults (check AGENT_DEFAULT_API_KEY in .env); aborting", flush=True)
        return 2

    ok = 0
    fail = 0
    for i, item in enumerate(to_add, 1):
        payload = {
            "name": item["name"],
            "system_prompt": item["prompt"],
            "adapter": "bailian",
            "industry": args.industry,
            "config": {
                "api_key": api_key,
                "app_id": item["app_id"],
                "endpoint": "https://dashscope.aliyuncs.com",
            },
            "variables": SAMPLE_VARS,
        }
        try:
            status, text = _http_post_json(f"{BASE}/api/agents", payload, timeout=15)
            if status == 200:
                ok += 1
                print(f"[{i:>2}/{len(to_add)}] OK   {item['name']}", flush=True)
            else:
                fail += 1
                print(f"[{i:>2}/{len(to_add)}] FAIL ({status}) {item['name']}: {text[:200]}", flush=True)
        except Exception as e:
            fail += 1
            print(f"[{i:>2}/{len(to_add)}] FAIL (exc) {item['name']}: {e}", flush=True)

    print(f"\nDONE: ok={ok} fail={fail}", flush=True)
    return 0 if fail == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
