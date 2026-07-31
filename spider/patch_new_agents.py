"""
补丁脚本：只抓已知的新增智能体
不依赖列表 parse，直接根据 ID 在第 1 页找卡片并点进去
"""
import json
import re
import sys
import io
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

DEBUG_PORT = 9222
OUTPUT_FILE = Path(__file__).parent / "agents.json"
DEBUG_DIR = Path(__file__).parent / "debug"
CARD_SEL = ".card__nGdDV"
CM_EDITOR = ".cm-editor"
BACK_SEL = ".spark-icon-spark-leftArrow-line"
APP_ID_RE = re.compile(r"\b[0-9a-f]{32}\b")

NEW_IDS = [
    "a652338350a54be38b459e408ecee49b",
    "6a63d18a57f14499acf6d588fb11a031",
    "a1e7ec932ed94720a4088cc148020753",
    "1432cf4441124b27b1dcf26c78e26058",
    "640f48fdb0d6433681e786a1cae12674",
    "d349e174e5c84033a85c272ce08254dd",
]

GET_PROMPT_JS = r"""
const cands = [
  document.querySelector('.cm-editor'),
  document.querySelector('.cm-content'),
  document.querySelector('.cm-scroller'),
].filter(Boolean);
for (const el of cands) {
  for (const k of Object.getOwnPropertyNames(el)) {
    try {
      const v = el[k];
      if (v && v.state && v.state.doc && typeof v.state.doc.toString === 'function')
        return { method: 'state', text: v.state.doc.toString() };
      if (v && v.view && v.view.state && v.view.state.doc)
        return { method: 'state.view', text: v.view.state.doc.toString() };
    } catch(e) {}
  }
}
const c = document.querySelector('.cm-content');
return { method: 'innerText', text: c ? (c.innerText || '') : '' };
"""


def find_bailian_tab(driver):
    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            if "bailian" in (driver.current_url or "").lower():
                if driver.find_elements(By.CSS_SELECTOR, CARD_SEL):
                    return True
        except Exception:
            continue
    return False


def goto_page_1(driver):
    driver.execute_script("""
    const btns = document.querySelectorAll('li[title="1"], .efm_ant-pagination-item-1');
    for (const b of btns) { if (b.offsetParent) { b.click(); return true; } }
    return false;
    """)
    time.sleep(2)


def find_card_by_id(driver, app_id):
    """根据 app_id 在当前页找到对应的卡片元素 (返回 WebElement 或 None)"""
    cards = driver.find_elements(By.CSS_SELECTOR, CARD_SEL)
    for c in cards:
        text = c.text or ""
        # 既匹配完整 32 位 ID，也匹配截断的前缀（前 18 位足够区分）
        if app_id in text or app_id[:18] in text:
            return c
    return None


def extract_card_name(card, app_id):
    text = card.text or ""
    skip = {"未发布", "已发布", "应用ID", "选用模型"}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line in skip:
            continue
        if APP_ID_RE.fullmatch(line):
            continue
        if line.startswith(app_id[:18]):  # 截断的 ID 行
            continue
        return line
    return ""


def extract_prompt(driver, app_id):
    deadline = time.time() + 20
    while time.time() < deadline:
        if driver.find_elements(By.CSS_SELECTOR, CM_EDITOR):
            break
        time.sleep(1)
    else:
        raise TimeoutException("no .cm-editor in 20s")
    time.sleep(2.5)
    res = driver.execute_script(GET_PROMPT_JS)
    method = res.get("method", "?")
    text = res.get("text", "")
    print(f"    提示词来源={method} 长度={len(text)}")
    (DEBUG_DIR / f"{app_id}.txt").write_text(
        f"[method={method}]\n\n{text}", encoding="utf-8"
    )
    return text


def click_back(driver):
    btns = driver.find_elements(By.CSS_SELECTOR, BACK_SEL)
    if btns:
        try:
            driver.execute_script("arguments[0].closest('button,a,div').click()", btns[0])
            return
        except Exception:
            btns[0].click()
            return
    driver.back()


def main():
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    driver = webdriver.Edge(options=opts)

    try:
        if not find_bailian_tab(driver):
            print("[ERR] 没找到带卡片的百炼 tab")
            return

        # 加载已有数据
        if not OUTPUT_FILE.exists():
            OUTPUT_FILE.write_text("[]", encoding="utf-8")
        results = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        existing_ids = {r["app_id"] for r in results}
        print(f"[INFO] 已有 {len(results)} 条数据")

        # 翻到第 1 页
        goto_page_1(driver)
        cards = driver.find_elements(By.CSS_SELECTOR, CARD_SEL)
        print(f"[INFO] 第 1 页有 {len(cards)} 张卡片")

        # 调试：先打印第 1 页所有卡片文本的前 80 字符
        print("\n[DEBUG] 第 1 页前 10 张卡片文本预览:")
        for i, c in enumerate(cards[:10]):
            t = (c.text or "").replace("\n", " | ")[:120]
            print(f"  [{i+1}] {t}")
        print()

        for nid in NEW_IDS:
            if nid in existing_ids:
                print(f"[SKIP] {nid} 已存在")
                continue
            print(f"\n>>> 处理 {nid}")
            card = find_card_by_id(driver, nid)
            if not card:
                print(f"    [WARN] 第 1 页没找到该 ID 对应的卡片")
                results.append({"name": "[未找到卡片]", "app_id": nid, "prompt": "[未找到]"})
                OUTPUT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                continue
            name = extract_card_name(card, nid)
            print(f"    name={name!r}")
            try:
                card.click()
            except Exception as e:
                print(f"    [WARN] 点击失败: {e}")
                results.append({"name": name, "app_id": nid, "prompt": f"[点击失败:{e}]"})
                OUTPUT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                continue

            try:
                prompt = extract_prompt(driver, nid)
            except TimeoutException:
                prompt = "[未找到 .cm-editor]"
                print(f"    [WARN] {prompt}")
            except Exception as e:
                prompt = f"[抓取异常:{e}]"
                print(f"    [WARN] {prompt}")

            results.append({"name": name, "app_id": nid, "prompt": prompt})
            OUTPUT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            existing_ids.add(nid)

            # 回列表
            try:
                click_back(driver)
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SEL))
                )
                time.sleep(1.5)
                goto_page_1(driver)
            except TimeoutException:
                print("    [WARN] 返回列表超时，强制刷新")
                driver.get(driver.current_url)
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SEL))
                )
                time.sleep(1.5)
                goto_page_1(driver)

        print(f"\n[OK] 当前 agents.json 共 {len(results)} 条")
    finally:
        try:
            driver.command_executor._conn.clear()
        except Exception:
            pass


if __name__ == "__main__":
    main()
