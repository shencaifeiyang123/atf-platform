"""百炼智能体导入模块。

通过 Selenium 附加到已运行的 Edge 浏览器（复用登录态），
从百炼平台批量抓取智能体名称、app_id 和 system prompt，
并批量导入到 platform 的 agent 数据库中。

依赖：
- Edge 浏览器已通过 --remote-debugging-port=9222 启动
- 用户已在百炼控制台登录
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# selenium 为可选依赖，未安装时给出友好提示
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.support.wait import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

LIST_URL = (
    "https://bailian.console.aliyun.com/cn-beijing/"
    "?tab=app&productCode=p_efm#/app-center"
)

CARD_SEL = ".card__nGdDV"
CM_EDITOR = ".cm-editor"
BACK_SEL = ".spark-icon-spark-leftArrow-line"
APP_ID_RE = re.compile(r"\b[0-9a-f]{32}\b")
SKIP_LINES = {"未发布", "已发布", "应用ID", "选用模型"}

# 绕过 CodeMirror 虚拟滚动提取提示词
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


@dataclass
class BailianAgent:
    """从百炼抓取到的智能体信息。"""
    name: str = ""
    app_id: str = ""
    prompt: str = ""
    error: str = ""


@dataclass
class ImportProgress:
    """导入进度状态。"""
    phase: str = "idle"          # idle | connecting | loading_list | fetching | done | error
    message: str = ""
    total: int = 0
    current: int = 0
    imported: int = 0
    skipped: int = 0
    errors: int = 0
    agents: list[dict[str, Any]] = field(default_factory=list)


def _parse_card_text(text: str) -> tuple[str, str]:
    """从卡片 .text 解析 name + app_id。"""
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


def _list_cards(driver) -> list[dict[str, str]]:
    """获取当前页所有卡片的 name 和 app_id。"""
    cards = driver.find_elements(By.CSS_SELECTOR, CARD_SEL)
    out = []
    for c in cards:
        try:
            name, app_id = _parse_card_text(c.text)
            out.append({"name": name, "app_id": app_id})
        except StaleElementReferenceException:
            pass
    return out


def _extract_prompt(driver, app_id: str) -> str:
    """进入详情页提取 CodeMirror 中的提示词。"""
    deadline = time.time() + 20
    found = False
    while time.time() < deadline:
        if driver.find_elements(By.CSS_SELECTOR, CM_EDITOR):
            found = True
            break
        time.sleep(1)
    if not found:
        return "[未找到 .cm-editor，可能不是智能体类型]"
    time.sleep(2.5)
    res = driver.execute_script(GET_PROMPT_JS)
    method = res.get("method", "?")
    text = res.get("text", "")
    print(f"    提示词来源={method} 长度={len(text)}")
    return text


def _click_back(driver) -> bool:
    """点击返回按钮回到列表页。"""
    btns = driver.find_elements(By.CSS_SELECTOR, BACK_SEL)
    if btns:
        try:
            driver.execute_script("arguments[0].closest('button,a,div').click()", btns[0])
            return True
        except Exception:
            btns[0].click()
            return True
    driver.back()
    return True


def _goto_next_page(driver) -> bool:
    """尝试翻页。"""
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


def _ensure_filters(driver) -> None:
    """确保筛选条件为「智能体」+「已发布」。"""
    try:
        is_agent = driver.execute_script("""
        const sel = document.querySelectorAll('.efm_ant-segmented-item-selected');
        for (const e of sel) if (e.offsetParent && (e.innerText || '').trim() === '智能体') return true;
        return false;
        """)
        if not is_agent:
            driver.execute_script("""
            const items = document.querySelectorAll('.efm_ant-segmented-item');
            for (const it of items) {
              if (it.offsetParent && (it.innerText || '').trim() === '智能体') {
                it.click(); return true;
              }
            }
            """)
            time.sleep(2)
    except Exception:
        pass

    time.sleep(1)
    try:
        current = driver.execute_script("""
        const selects = document.querySelectorAll('.efm_ant-select-selector, .efm_ant-select-selection-item');
        for (const s of selects) {
          const t = (s.innerText || '').trim();
          if (t === '已发布' || t === '全部' || t === '未发布') return t;
        }
        return null;
        """)
        if current == '已发布':
            return
        opened = driver.execute_script("""
        const selects = document.querySelectorAll('.efm_ant-select-selector');
        for (const s of selects) {
          const t = (s.innerText || '').trim();
          if (t === '全部' || t === '未发布' || t === '已发布') { s.click(); return true; }
        }
        return false;
        """)
        if not opened:
            return
        time.sleep(0.8)
        driver.execute_script("""
        const items = document.querySelectorAll('.efm_ant-select-item, .efm_ant-dropdown-menu-item, [role="option"]');
        for (const it of items) {
          if (!it.offsetParent) continue;
          const t = (it.innerText || '').trim();
          if (t === '已发布') { it.click(); return true; }
        }
        """)
        time.sleep(1.5)
    except Exception:
        pass


def _find_bailian_tab(driver) -> bool:
    """找到已登录的百炼 tab 且列表已渲染。"""
    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            url = driver.current_url or ""
            if "bailian" not in url.lower():
                continue
            if driver.find_elements(By.CSS_SELECTOR, CARD_SEL):
                return True
        except Exception:
            continue
    return False


def import_from_bailian(
    debug_port: int = 9222,
    max_pages: int = 10,
    api_key: str = "",
    progress_cb: Optional[Callable[[ImportProgress], None]] = None,
) -> ImportProgress:
    """执行百炼导入。

    Args:
        debug_port: Edge 远程调试端口
        max_pages: 最大爬取页数（安全限制）
        api_key: 百炼 API Key（用于导入后配置）
        progress_cb: 进度回调函数

    Returns:
        ImportProgress 包含导入结果
    """
    if not HAS_SELENIUM:
        prog = ImportProgress(
            phase="error",
            message="未安装 selenium，请执行: pip install selenium",
        )
        if progress_cb:
            progress_cb(prog)
        return prog

    prog = ImportProgress(phase="connecting", message="正在连接 Edge 浏览器...")
    if progress_cb:
        progress_cb(prog)

    try:
        opts = Options()
        opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
        driver = webdriver.Edge(options=opts)
    except Exception as e:
        prog.phase = "error"
        prog.message = f"无法连接到 Edge 调试端口 {debug_port}：{e}\n请先通过 --remote-debugging-port={debug_port} 启动 Edge"
        if progress_cb:
            progress_cb(prog)
        return prog

    wait = WebDriverWait(driver, 30)

    try:
        # 1. 找到百炼 tab
        prog.phase = "loading_list"
        prog.message = "正在定位百炼控制台..."
        if progress_cb:
            progress_cb(prog)

        if not _find_bailian_tab(driver):
            prog.phase = "error"
            prog.message = "未找到已登录的百炼页面，请先在 Edge 中打开 bailian.console.aliyun.com 并登录"
            if progress_cb:
                progress_cb(prog)
            return prog

        # 2. 等待卡片出现
        deadline = time.time() + 60
        found = False
        while time.time() < deadline:
            els = driver.find_elements(By.CSS_SELECTOR, CARD_SEL)
            if els:
                found = True
                break
            time.sleep(3)

        if not found:
            prog.phase = "error"
            prog.message = "60 秒内未检测到智能体卡片，请确认百炼列表页已渲染完成"
            if progress_cb:
                progress_cb(prog)
            return prog

        # 3. 设置筛选
        _ensure_filters(driver)
        time.sleep(1)

        # 4. 逐页抓取
        prog.phase = "fetching"
        prog.message = "开始抓取智能体..."
        seen: set[str] = set()
        results: list[BailianAgent] = []
        page_num = 0

        while page_num < max_pages:
            page_num += 1
            prog.message = f"正在处理第 {page_num} 页..."
            if progress_cb:
                progress_cb(prog)

            time.sleep(1.5)
            metas = _list_cards(driver)
            if page_num == 1:
                prog.total = len(metas)  # 首页估算总数

            for i, meta in enumerate(metas):
                if not meta["app_id"] or meta["app_id"] in seen:
                    prog.skipped += 1
                    continue
                seen.add(meta["app_id"])

                prog.current = len(seen)
                prog.message = f"正在抓取: {meta['name']}"
                if progress_cb:
                    progress_cb(prog)

                agent = BailianAgent(name=meta["name"], app_id=meta["app_id"])

                try:
                    cards = driver.find_elements(By.CSS_SELECTOR, CARD_SEL)
                    if i < len(cards):
                        cards[i].click()
                        time.sleep(2)
                        agent.prompt = _extract_prompt(driver, meta["app_id"])
                    else:
                        agent.error = "卡片索引越界"
                        prog.errors += 1
                except Exception as e:
                    agent.error = str(e)[:200]
                    prog.errors += 1

                results.append(agent)

                # 返回列表
                try:
                    _click_back(driver)
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SEL)))
                    time.sleep(1.5)
                except Exception:
                    prog.phase = "error"
                    prog.message = "返回列表页失败，终止导入"
                    if progress_cb:
                        progress_cb(prog)
                    return prog

            if not _goto_next_page(driver):
                break
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SEL)))
                time.sleep(1.5)
                _ensure_filters(driver)
            except Exception:
                break

        # 5. 导入到数据库
        prog.message = f"抓取完成，共 {len(results)} 个智能体，正在导入..."
        if progress_cb:
            progress_cb(prog)

        from . import store
        from .models import AgentUnderTest

        imported = 0
        skipped_db = 0
        imported_agents: list[dict[str, Any]] = []

        for ag in results:
            if ag.error:
                continue
            # 检查是否已存在
            existing = None
            for ea in store.list_agents():
                if ea.config.get("app_id") == ag.app_id:
                    existing = ea
                    break
            if existing:
                skipped_db += 1
                continue

            # 创建新 agent
            new_agent = AgentUnderTest(
                name=ag.name or f"百炼智能体-{ag.app_id[:8]}",
                description=f"从百炼平台自动导入 (app_id: {ag.app_id})",
                system_prompt=ag.prompt,
                industry="通用",
                adapter="bailian",
                config={
                    "api_key": api_key or "",
                    "app_id": ag.app_id,
                    "endpoint": "https://dashscope.aliyuncs.com",
                },
            )
            store.create_agent(new_agent)
            imported += 1
            imported_agents.append({
                "id": new_agent.id,
                "name": new_agent.name,
                "app_id": ag.app_id,
                "prompt_length": len(ag.prompt),
            })

        prog.phase = "done"
        prog.imported = imported
        prog.skipped = skipped_db
        prog.agents = imported_agents
        prog.message = f"导入完成！新增 {imported} 个智能体，跳过 {skipped_db} 个已存在的"
        if progress_cb:
            progress_cb(prog)

    finally:
        try:
            driver.command_executor._conn.clear()
        except Exception:
            pass

    return prog
