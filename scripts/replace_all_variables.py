"""Replace `variables` (业务参数 / biz_params) on EVERY existing agent.

Run while the server is up:
    python scripts/replace_all_variables.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:8866"

NEW_VARS: dict = {
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
                    "本篇内容主要围绕“不好魔王”直播间展开，"
                    "通过“大咖”爸爸和“小咖”儿子乘坐时光机穿越时空的视角，"
                    "生动勾勒了中国远古神话与传说时代的开端。\n\n"
                    "## 一、 创世神话：盘古开天地\n\n"
                    "故事始于混沌时期。大约326.7万年前，世界宛如巨蛋，"
                    "盘古在其中孕育一万八千年后，用神斧劈开混沌，"
                    "使清气上升为天，浊气下降为地 。盘古倒下后，其躯体化为万物："
                    "气息成风云，声音变雷鸣，双眼化日月，"
                    "四肢及肌肤则演变为大地的四极与辽阔疆域 。\n\n"
                    "## 二、 三皇之首：伏羲的诞生\n\n"
                    "大咖父子随后见证了中国典籍记载中最早的王——伏羲的诞生 。\n\n"
                    "- **神奇受孕**：伏羲的母亲华胥氏在山上游玩时，因踩中巨人的大脚印而怀孕 。\n    \n"
                    "- **异象天生**：伏羲出生时呈现“人首蛇身”的神异长相 。\n    \n\n"
                    "## 三、 文明初启：部落生存困境\n\n"
                    "伏羲成年后成为部落首领，并与同样人首蛇身的妻子结合 。"
                    "当时人类尚未掌握农耕，生存完全依赖捕鱼和打猎 。"
                    "然而，由于工具落后（仅靠木叉），捕鱼效率极低，"
                    "且猎人在捕猎过程中常面临被野兽反噬的生命危险 。\n\n"
                    "## 四、 民族自豪与悬念\n\n"
                    "文末，大咖通过对比世界文明史，强调了中华文明五千年屹立不倒的民族自豪感 。"
                    "故事在伏羲如何解决部落食物危机的悬念中戛然而止，"
                    "并预告了下一集中大咖爸爸将面临的“意外” 。"
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
        }
    },
    "enable_system_time": True,
    "scope": "publish",
}


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
    agents = _http_get_json(f"{BASE}/api/agents", timeout=10)
    print(f"fetched {len(agents)} agents", flush=True)

    ok, fail = 0, 0
    for i, a in enumerate(agents, 1):
        a["variables"] = NEW_VARS
        # AgentUnderTest model needs id; keep all other fields untouched
        status, text = _http_put_json(f"{BASE}/api/agents/{a['id']}", a, timeout=20)
        if status == 200:
            ok += 1
            print(f"[{i:>2}/{len(agents)}] OK   {a.get('name','')}", flush=True)
        else:
            fail += 1
            print(f"[{i:>2}/{len(agents)}] FAIL ({status}) {a.get('name','')}: {text[:200]}", flush=True)

    print(f"\nDONE: ok={ok} fail={fail}", flush=True)
    return 0 if fail == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
