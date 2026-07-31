"""
百炼智能体爬虫 - 基于 Selenium IDE 录制改写
- 持久化登录态（chrome_profile 目录）
- 列表 -> 进详情抓 CodeMirror 提示词 -> 返回 -> 翻页
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
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

LIST_URL = ("https://bailian.console.aliyun.com/cn-beijing/"
            "?tab=app&productCode=p_efm#/app-center")
DEBUG_PORT = 9222          # 必须和 start_edge.bat 里一致
OUTPUT_FILE = Path(__file__).parent / "agents.json"
DEBUG_DIR = Path(__file__).parent / "debug"; DEBUG_DIR.mkdir(exist_ok=True)

CARD_SEL  = ".card__nGdDV"
CM_EDITOR = ".cm-editor"
BACK_SEL  = ".spark-icon-spark-leftArrow-line"

APP_ID_RE = re.compile(r"\b[0-9a-f]{32}\b")
SKIP_LINES = {"未发布", "已发布", "应用ID", "选用模型"}

# ---- 提示词提取（绕过 CodeMirror 虚拟滚动）----
GET_PROMPT_JS = r"""
const cands = [
  document.querySelector('.cm-editor'),
  document.querySelector('.cm-content'),
  document.querySelector('.cm-scroller'),
].filter(Boolean);
for (const el of cands) {
  // 先扫 own properties，找带 state.doc 的对象（CM6 把 view 挂在 DOM 上）
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
// 退路：innerText（虚拟滚动可能漏行）
const c = document.querySelector('.cm-content');
return { method: 'innerText', text: c ? (c.innerText || '') : '' };
"""


def parse_card_text(text: str):
    """从卡片 .text 解析 name + app_id"""
    m = APP_ID_RE.search(text)
    app_id = m.group(0) if m else ""
    name = ""
    for line in text.split("\n"):
        line = line.strip()
        if not line or line in SKIP_LINES:
            continue
        if APP_ID_RE.fullmatch(line):
            continue
        name = line
        break
    return name, app_id


def list_cards(driver):
    cards = driver.find_elements(By.CSS_SELECTOR, CARD_SEL)
    out = []
    for c in cards:
        try:
            name, app_id = parse_card_text(c.text)
            out.append({"name": name, "app_id": app_id})
        except StaleElementReferenceException:
            pass
    return out


def extract_prompt(driver, app_id, wait):
    # 多等几秒让 CM 加载（有些智能体页面慢）
    deadline = time.time() + 20
    found = False
    while time.time() < deadline:
        if driver.find_elements(By.CSS_SELECTOR, CM_EDITOR):
            found = True
            break
        time.sleep(1)
    if not found:
        raise TimeoutException("no .cm-editor in 20s")
    time.sleep(2.5)  # 给 CM 加载内容
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
        # 点 SVG 父级更稳
        try:
            driver.execute_script("arguments[0].closest('button,a,div').click()", btns[0])
            return True
        except Exception:
            btns[0].click()
            return True
    # 退路：浏览器后退
    driver.back()
    return True


def goto_next_page(driver):
    """翻页：尝试 antd 风格的下一页按钮"""
    js = """
    const cands = [
      ...document.querySelectorAll('button[aria-label="Next Page"]'),
      ...document.querySelectorAll('.efm_ant-pagination-next'),
      ...document.querySelectorAll('.next-pagination-item.next-next'),
      ...document.querySelectorAll('li.efm_ant-pagination-next'),
    ];
    for (const b of cands) {
      const cls = b.className || '';
      const disabled = b.disabled || cls.includes('disabled') ||
        b.getAttribute('aria-disabled') === 'true';
      if (!disabled) { b.click(); return true; }
    }
    return false;
    """
    return bool(driver.execute_script(js))


def ensure_filters(driver):
    """确保「智能体」tab + 「已发布」筛选都已生效"""
    print("[筛选] 检查并设置筛选条件...")

    # === 1. 切到「智能体」segmented ===
    try:
        is_agent = driver.execute_script("""
        const sel = document.querySelectorAll('.efm_ant-segmented-item-selected');
        for (const e of sel) if (e.offsetParent && (e.innerText || '').trim() === '智能体') return true;
        return false;
        """)
        if is_agent:
            print("  [OK] 已选中「智能体」")
        else:
            clicked = driver.execute_script("""
            const items = document.querySelectorAll('.efm_ant-segmented-item');
            for (const it of items) {
              if (it.offsetParent && (it.innerText || '').trim() === '智能体') {
                it.click(); return true;
              }
            }
            return false;
            """)
            if clicked:
                print("  [OK] 已点击「智能体」segmented")
                time.sleep(2)
            else:
                print("  [WARN] 找不到「智能体」segmented，请手动切换")
    except Exception as e:
        print(f"  [WARN] 智能体筛选异常: {e}")

    # === 2. 已发布 下拉筛选 ===
    time.sleep(1)
    try:
        # 检查右上角下拉框当前文本
        current = driver.execute_script("""
        const selects = document.querySelectorAll('.efm_ant-select-selector, .efm_ant-select-selection-item');
        for (const s of selects) {
          const t = (s.innerText || '').trim();
          if (t === '已发布' || t === '全部' || t === '未发布') return t;
        }
        return null;
        """)
        if current == '已发布':
            print("  [OK] 已筛选「已发布」")
            return
        elif current == '全部':
            print(f"  [INFO] 当前筛选={current}，尝试切换到「已发布」")
        else:
            print(f"  [INFO] 当前筛选={current or '未知'}，尝试切换到「已发布」")

        # 点开下拉框
        opened = driver.execute_script("""
        const selects = document.querySelectorAll('.efm_ant-select-selector');
        for (const s of selects) {
          const t = (s.innerText || '').trim();
          if (t === '全部' || t === '未发布' || t === '已发布') { s.click(); return true; }
        }
        return false;
        """)
        if not opened:
            print("    [WARN] 找不到发布状态下拉框")
            return
        time.sleep(0.8)

        # 选「已发布」
        chosen = driver.execute_script("""
        const items = document.querySelectorAll('.efm_ant-select-item, .efm_ant-dropdown-menu-item, [role="option"]');
        for (const it of items) {
          if (!it.offsetParent) continue;
          const t = (it.innerText || '').trim();
          if (t === '已发布') { it.click(); return true; }
        }
        return false;
        """)
        if chosen:
            print("    [OK] 已选择「已发布」")
            time.sleep(1.5)
        else:
            print("    [WARN] 下拉菜单里找不到「已发布」选项")
    except Exception as e:
        print(f"  [WARN] 发布状态筛选异常: {e}")


def main():
    opts = Options()
    # 关键：附加到已经在跑的 Edge（debug 端口由 start_edge.bat 启动）
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    try:
        driver = webdriver.Edge(options=opts)
    except Exception as e:
        print(f"[ERR] 无法连接到 Edge 调试端口 {DEBUG_PORT}：{e}")
        print("     请先双击 start_edge.bat 启动调试模式 Edge，再跑此脚本")
        return
    wait = WebDriverWait(driver, 30)

    try:
        print("=" * 50)
        print(f"已附加到 Edge (端口 {DEBUG_PORT})")
        # 自动找到真正渲染了智能体卡片的百炼 tab
        # （Edge 里可能有多个 bailian tab，挑有 card__ 元素的那个）
        bailian_handle = None
        bailian_candidates = []
        all_urls = []
        for h in driver.window_handles:
            try:
                driver.switch_to.window(h)
                url = driver.current_url or ""
                all_urls.append(url)
                if "bailian" not in url.lower():
                    continue
                bailian_candidates.append((h, url))
                # 看这个 tab 是否真的有 card__ 元素
                probe = driver.execute_script("""
                    const cls = new Set();
                    document.querySelectorAll('[class]').forEach(e => {
                      (e.className.toString().match(/card__\\w+/g) || []).forEach(c => cls.add(c));
                    });
                    return [...cls];
                """) or []
                if probe:
                    bailian_handle = h
                    print(f"[OK] 找到带卡片的百炼 tab: {url}")
                    print(f"     发现 card__ 类: {probe}")
                    break
            except Exception:
                continue
        if not bailian_handle:
            # 没有带卡片的，回退到第一个 bailian tab，由后续轮询继续等
            if bailian_candidates:
                bailian_handle, url = bailian_candidates[0]
                driver.switch_to.window(bailian_handle)
                print(f"[WARN] 暂无带卡片的百炼 tab，先附加到: {url}")
                print(f"       共 {len(bailian_candidates)} 个百炼 tab，请确保智能体列表已渲染")
            else:
                print("[ERR] 没有任何 tab 的 URL 含 'bailian'")
                print(f"     检测到的 tabs: {all_urls}")
                return
        try:
            driver.execute_script("window.focus();")
        except Exception:
            pass
        print(f"当前页面: {driver.current_url}")
        print("=" * 50)
        # 长轮询等卡片出现
        deadline = time.time() + 300
        found = False
        while time.time() < deadline:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, CARD_SEL)
                if els:
                    print(f"[OK] 检测到 {len(els)} 张卡片")
                    found = True
                    break
                probe = driver.execute_script("""
                    const cls = new Set();
                    document.querySelectorAll('[class]').forEach(e => {
                      (e.className.toString().match(/card__\\w+/g) || []).forEach(c => cls.add(c));
                    });
                    return [...cls];
                """) or []
                if probe:
                    print(f"  [{int(deadline - time.time())}s] URL={driver.current_url}")
                    print(f"        发现 card__ 类：{probe} (脚本期望 {CARD_SEL[1:]})")
                else:
                    print(f"  [{int(deadline - time.time())}s] URL={driver.current_url} (页面里无 card__ 类)")
            except Exception as e:
                print(f"  [WARN] {e}")
            time.sleep(5)
        if not found:
            print("[ERR] 5 分钟内未找到卡片")
            return
        time.sleep(2)

        # 设置筛选条件
        ensure_filters(driver)
        time.sleep(1)

        results, seen = [], set()
        # 断点续传：加载已抓数据
        if OUTPUT_FILE.exists():
            try:
                results = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
                seen = {r["app_id"] for r in results if r.get("app_id")}
                print(f"[INFO] 已加载 {len(results)} 条已抓数据，跳过这些")
            except Exception:
                results, seen = [], set()
        page_num = 1
        while True:
            print(f"\n[第 {page_num} 页]")
            time.sleep(1.5)
            metas = list_cards(driver)
            print(f"  本页 {len(metas)} 个卡片")

            for i, meta in enumerate(metas):
                if not meta["app_id"] or meta["app_id"] in seen:
                    continue
                seen.add(meta["app_id"])
                print(f"  [{i+1}/{len(metas)}] {meta['name']}  id={meta['app_id']}")

                try:
                    # 重新定位（避免 stale）
                    cards = driver.find_elements(By.CSS_SELECTOR, CARD_SEL)
                    if i >= len(cards):
                        print("    [WARN] 卡片索引越界，跳过")
                        results.append({**meta, "prompt": "[卡片索引越界]"})
                        OUTPUT_FILE.write_text(
                            json.dumps(results, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        continue

                    try:
                        cards[i].click()
                    except Exception as e:
                        print(f"    [WARN] 点击失败：{e}")
                        results.append({**meta, "prompt": f"[点击失败: {e}]"})
                        OUTPUT_FILE.write_text(
                            json.dumps(results, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        continue

                    try:
                        prompt = extract_prompt(driver, meta["app_id"], wait)
                    except TimeoutException:
                        prompt = "[未找到 .cm-editor，可能不是智能体类型]"
                        print("    [WARN] " + prompt)
                    except Exception as e:
                        prompt = f"[抓取异常: {e}]"
                        print(f"    [WARN] {prompt}")

                    results.append({**meta, "prompt": prompt})
                    OUTPUT_FILE.write_text(
                        json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception as e:
                    print(f"    [WARN] 单条处理异常: {e}")

                # 无论成功失败，都尽力回到列表页
                try:
                    click_back(driver)
                    # 等列表卡片回来，最多 15 秒；失败就强制 driver.get
                    try:
                        WebDriverWait(driver, 15).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SEL))
                        )
                    except TimeoutException:
                        print("    [WARN] 返回列表超时，强制刷新")
                        driver.get(driver.current_url)
                        WebDriverWait(driver, 30).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SEL))
                        )
                    time.sleep(1.5)
                except Exception as e:
                    print(f"    [ERR] 无法返回列表页: {e}")
                    return  # 列表页都回不去，无法继续

            if not goto_next_page(driver):
                print("\n[DONE] 没有下一页")
                break
            page_num += 1
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SEL)))
            time.sleep(1.5)
            # 翻页后重新检查筛选（防止被重置）
            ensure_filters(driver)
            if page_num > 10:
                print("[WARN] 超过 10 页，安全退出")
                break

        print(f"\n[OK] 共 {len(results)} 条 -> {OUTPUT_FILE.resolve()}")
        print(f"     单条快照在 {DEBUG_DIR.resolve()}")
        time.sleep(15)
    finally:
        # 附加模式下不要 quit（会关掉你的 Edge），只断开
        try:
            driver.command_executor._conn.clear()
        except Exception:
            pass


if __name__ == "__main__":
    main()
