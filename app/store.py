"""基于 SQLite 的极简存储层。

数据模型参考 TwinTalk：智能体 / 用例 / 任务 / 用例结果 四张表。
所有字段保持简单扁平，JSON 内容统一 TEXT 存储。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
import logging
from pathlib import Path
from typing import Any, Optional

from .config import settings
from .models import AgentUnderTest, CaseResult, Schedule, ScheduleSelector, ScheduleTrigger, Template, TestCase, TestRun

logger = logging.getLogger(__name__)

DB_PATH = settings.data_dir / "platform.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL + 配套 PRAGMA：在保证 crash safety 的前提下显著提速
    # synchronous=NORMAL 在 WAL 模式下是安全的（断电只会丢最近未 checkpoint 的事务）
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -64000")    # 64MB page cache（默认仅 ~2MB）
    conn.execute("PRAGMA mmap_size = 268435456")  # 256MB mmap，减少 read syscalls
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_db = _conn()


def now_ms() -> int:
    return int(time.time() * 1000)


def _ensure_column(table: str, col_name: str, col_def: str, *, index: str | None = None) -> None:
    """增量迁移：如果列不存在则添加；可选地创建索引。"""
    cols = {row["name"] for row in _db.execute(f"PRAGMA table_info({table})").fetchall()}
    if col_name not in cols:
        _db.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        if index:
            _db.execute(index)


def wal_checkpoint() -> dict[str, int]:
    """执行 WAL checkpoint 并返回 {busy, log_frames, checkpointed}。

    建议定时调用（如每小时一次），避免 WAL 文件无限增长。
    """
    try:
        row = _db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row is not None:
            return {
                "busy": row[0],
                "log_frames": row[1],
                "checkpointed": row[2],
            }
    except Exception:
        logger.exception("wal_checkpoint 失败")
    return {"busy": -1, "log_frames": 0, "checkpointed": 0}


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def init_db() -> None:
    _db.executescript(
        """
        CREATE TABLE IF NOT EXISTS templates (
          id TEXT PRIMARY KEY,
          type TEXT NOT NULL,            -- dimension / industry_rule / good_case / bad_case / system_prompt
          dimension TEXT DEFAULT '',     -- 关联维度 key（system_prompt 不填）
          industry TEXT DEFAULT '',      -- 仅 industry_rule 使用
          name TEXT NOT NULL,
          content TEXT NOT NULL,         -- Prompt 文本 / 规则文本 / 用例 JSON
          description TEXT DEFAULT '',
          is_active INTEGER NOT NULL DEFAULT 1,
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_templates_type ON templates(type);
        CREATE INDEX IF NOT EXISTS idx_templates_dim  ON templates(dimension);

        CREATE TABLE IF NOT EXISTS agents (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT DEFAULT '',
          system_prompt TEXT NOT NULL,
          industry TEXT DEFAULT '通用',
          adapter TEXT NOT NULL DEFAULT 'openai',
          config TEXT NOT NULL DEFAULT '{}',
          variables TEXT NOT NULL DEFAULT '{}',
          analysis TEXT NOT NULL DEFAULT '{}',
          analysis_at INTEGER,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS test_cases (
          id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          dimension TEXT NOT NULL,
          sub_type TEXT DEFAULT '',
          title TEXT DEFAULT '',
          turns TEXT NOT NULL,
          expectation TEXT DEFAULT '',
          pass_criteria TEXT DEFAULT '[]',
          weight INTEGER DEFAULT 3,
          opening_mode TEXT NOT NULL DEFAULT 'default',
          created_at INTEGER NOT NULL,
          FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cases_agent ON test_cases(agent_id);

        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          name TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending',
          total INTEGER DEFAULT 0,
          finished INTEGER DEFAULT 0,
          passed INTEGER DEFAULT 0,
          failed INTEGER DEFAULT 0,
          errors INTEGER DEFAULT 0,
          average_score REAL DEFAULT 0,
          summary TEXT DEFAULT '',
          error TEXT DEFAULT '',
          created_at INTEGER NOT NULL,
          started_at INTEGER,
          finished_at INTEGER,
          tokens_in INTEGER DEFAULT 0,
          tokens_out INTEGER DEFAULT 0,
          cost_usd REAL DEFAULT 0,
          FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS case_results (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL,
          case_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          transcript TEXT DEFAULT '[]',
          score REAL DEFAULT 0,
          passed INTEGER DEFAULT 0,
          reasons TEXT DEFAULT '[]',
          judge_comment TEXT DEFAULT '',
          error TEXT DEFAULT '',
          metrics TEXT DEFAULT '[]',
          judge_runs TEXT DEFAULT '[]',
          agreement REAL DEFAULT 1.0,
          token_usage TEXT DEFAULT '[]',
          created_at INTEGER NOT NULL,
          UNIQUE(run_id, case_id),
          FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_case_results_run ON case_results(run_id);

        -- 生成任务（动态 / PD / 维度驱动）状态。落库后服务重启不丢，
        -- 重启时一律把 running 状态改写为 error（孤儿 task）
        CREATE TABLE IF NOT EXISTS generation_jobs (
          id TEXT PRIMARY KEY,
          mode TEXT NOT NULL DEFAULT '',          -- dimension / prompt_debugger / dynamic
          agent_id TEXT NOT NULL,
          agent_name TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'running', -- running / done / error
          planned INTEGER NOT NULL DEFAULT 0,
          generated INTEGER NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT '',
          raw_text TEXT NOT NULL DEFAULT '',      -- 解析失败时保留模型原始输出
          params TEXT NOT NULL DEFAULT '{}',      -- 入参（dimensions / opening_style 等）
          analysis TEXT NOT NULL DEFAULT '',      -- 维度生成时的 analysis 缓存（JSON 字符串）
          started_at INTEGER NOT NULL,
          finished_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_genjobs_status ON generation_jobs(status, started_at DESC);

        -- 定时任务（一键启停 + 周期触发）
        CREATE TABLE IF NOT EXISTS schedules (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          agent_id TEXT NOT NULL,
          trigger TEXT NOT NULL,            -- JSON: {type, minutes/hour/minute/weekday}
          selector TEXT NOT NULL,           -- JSON: {mode, dimensions, ids}
          concurrency INTEGER NOT NULL DEFAULT 5,
          enabled INTEGER NOT NULL DEFAULT 1,
          on_overlap TEXT NOT NULL DEFAULT 'skip',
          next_run_at INTEGER,
          last_run_id TEXT NOT NULL DEFAULT '',
          last_run_at INTEGER,
          last_run_status TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_schedules_due
          ON schedules(enabled, next_run_at);
        """
    )
    # ---- 增量迁移（老库补列）----
    _ensure_column("agents", "variables", "variables TEXT NOT NULL DEFAULT '{}'")
    _ensure_column("agents", "analysis", "analysis TEXT NOT NULL DEFAULT '{}'")
    _ensure_column("agents", "analysis_at", "analysis_at INTEGER")

    _ensure_column("test_cases", "opening_mode", "opening_mode TEXT NOT NULL DEFAULT 'default'")
    _ensure_column("test_cases", "batch_id", "batch_id TEXT NOT NULL DEFAULT ''",
                   index="CREATE INDEX IF NOT EXISTS idx_cases_batch ON test_cases(batch_id)")
    _ensure_column("test_cases", "batch_label", "batch_label TEXT NOT NULL DEFAULT ''")
    _ensure_column("test_cases", "dialogue_mode", "dialogue_mode TEXT NOT NULL DEFAULT 'scripted'")
    _ensure_column("test_cases", "user_persona", "user_persona TEXT NOT NULL DEFAULT ''")
    _ensure_column("test_cases", "user_goal", "user_goal TEXT NOT NULL DEFAULT ''")
    _ensure_column("test_cases", "max_turns", "max_turns INTEGER NOT NULL DEFAULT 6")
    _ensure_column("test_cases", "termination_keywords", "termination_keywords TEXT NOT NULL DEFAULT '[]'")

    _ensure_column("runs", "tokens_in", "tokens_in INTEGER DEFAULT 0")
    _ensure_column("runs", "tokens_out", "tokens_out INTEGER DEFAULT 0")
    _ensure_column("runs", "cost_usd", "cost_usd REAL DEFAULT 0")
    _ensure_column("runs", "schedule_id", "schedule_id TEXT NOT NULL DEFAULT ''",
                   index="CREATE INDEX IF NOT EXISTS idx_runs_schedule ON runs(schedule_id, created_at DESC)")

    _ensure_column("generation_jobs", "tokens_in", "tokens_in INTEGER DEFAULT 0")
    _ensure_column("generation_jobs", "tokens_out", "tokens_out INTEGER DEFAULT 0")
    _ensure_column("generation_jobs", "cost_usd", "cost_usd REAL DEFAULT 0")

    _ensure_column("case_results", "metrics", "metrics TEXT DEFAULT '[]'")
    _ensure_column("case_results", "judge_runs", "judge_runs TEXT DEFAULT '[]'")
    _ensure_column("case_results", "agreement", "agreement REAL DEFAULT 1.0")
    _ensure_column("case_results", "token_usage", "token_usage TEXT DEFAULT '[]'")

    # 确保 (run_id, case_id) 唯一，支持 ON CONFLICT upsert
    _db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_run_case ON case_results(run_id, case_id)")

    # ---- Tier 1 新增索引 ----
    # runs(agent_id, created_at DESC)：list_runs(agent_id) 的最近一次 / 列表查询
    _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_runs_agent_created "
        "ON runs(agent_id, created_at DESC)"
    )
    # test_cases(agent_id, dimension)：agents_overview 的维度分布聚合
    _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cases_agent_dim "
        "ON test_cases(agent_id, dimension)"
    )
    # case_results(case_id)：反查某用例的历史结果 / JOIN 时回表
    _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cr_case ON case_results(case_id)"
    )


# ---------- agents ----------

def create_agent(a: AgentUnderTest) -> AgentUnderTest:
    a.id = a.id or new_id("ag_")
    a.created_at = a.updated_at = now_ms()
    _db.execute(
        "INSERT INTO agents(id,name,description,system_prompt,industry,adapter,config,variables,analysis,analysis_at,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (a.id, a.name, a.description, a.system_prompt, a.industry, a.adapter,
         json.dumps(a.config, ensure_ascii=False),
         json.dumps(a.variables, ensure_ascii=False),
         json.dumps(a.analysis or {}, ensure_ascii=False),
         a.analysis_at,
         a.created_at, a.updated_at),
    )
    return a


def update_agent(a: AgentUnderTest) -> AgentUnderTest:
    a.updated_at = now_ms()
    _db.execute(
        "UPDATE agents SET name=?,description=?,system_prompt=?,industry=?,adapter=?,config=?,variables=?,"
        "analysis=?,analysis_at=?,updated_at=? WHERE id=?",
        (a.name, a.description, a.system_prompt, a.industry, a.adapter,
         json.dumps(a.config, ensure_ascii=False),
         json.dumps(a.variables, ensure_ascii=False),
         json.dumps(a.analysis or {}, ensure_ascii=False),
         a.analysis_at,
         a.updated_at, a.id),
    )
    return a


def update_agent_analysis(agent_id: str, analysis: dict[str, Any]) -> None:
    """单独保存分析结果，避免覆盖其他字段。"""
    _db.execute(
        "UPDATE agents SET analysis=?, analysis_at=?, updated_at=? WHERE id=?",
        (json.dumps(analysis or {}, ensure_ascii=False), now_ms(), now_ms(), agent_id),
    )


def list_agents() -> list[AgentUnderTest]:
    rows = _db.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
    return [_row_to_agent(r) for r in rows]


def get_agent(agent_id: str) -> Optional[AgentUnderTest]:
    r = _db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    return _row_to_agent(r) if r else None


def delete_agent(agent_id: str) -> None:
    _db.execute("DELETE FROM agents WHERE id=?", (agent_id,))


def _row_to_agent(r: sqlite3.Row) -> AgentUnderTest:
    # 兼容老库：variables / analysis / analysis_at 列可能不存在
    def _safe(col: str, default: Any = None) -> Any:
        try:
            return r[col]
        except (IndexError, KeyError):
            return default
    try:
        variables = json.loads(_safe("variables", "{}") or "{}")
    except Exception:
        variables = {}
    try:
        analysis = json.loads(_safe("analysis", "{}") or "{}")
    except Exception:
        analysis = {}
    return AgentUnderTest(
        id=r["id"], name=r["name"], description=r["description"],
        system_prompt=r["system_prompt"], industry=r["industry"],
        adapter=r["adapter"], config=json.loads(r["config"] or "{}"),
        variables=variables,
        analysis=analysis,
        analysis_at=_safe("analysis_at", None),
        created_at=r["created_at"], updated_at=r["updated_at"],
    )


# ---------- test cases ----------

def save_cases(
    cases: list[TestCase],
    batch_id: str = "",
    batch_label: str = "",
) -> list[TestCase]:
    """保存一批用例。

    - batch_id / batch_label：标识本批用例属于哪一次生成（或手动创建）。
      若调用方未提供，则自动生成一个 batch_id 用于把本次入库的用例聚到一起；
      手动创建（单条）若也未传，会得到一个独立的 batch_id（可在前端归类到「手动创建」）。
    - 入参 case 自身的 batch_id/batch_label 优先级高于外层参数（用于前端编辑后的少量场景）。
    """
    ts = now_ms()
    auto_batch = batch_id or new_id("bat_")
    _db.execute("BEGIN")
    try:
        for c in cases:
            c.id = c.id or new_id("tc_")
            c.created_at = ts
            # 单条用例若有自己的 batch_id 则保留；否则用本次入库的批次
            cb_id = c.batch_id or auto_batch
            cb_label = c.batch_label or batch_label
            c.batch_id = cb_id
            c.batch_label = cb_label
            _db.execute(
                "INSERT INTO test_cases(id,agent_id,dimension,sub_type,title,turns,expectation,pass_criteria,"
                "weight,opening_mode,batch_id,batch_label,created_at,"
                "dialogue_mode,user_persona,user_goal,max_turns,termination_keywords)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (c.id, c.agent_id, c.dimension, c.sub_type, c.title,
                 json.dumps([t.model_dump() for t in c.turns], ensure_ascii=False),
                 c.expectation, json.dumps(c.pass_criteria, ensure_ascii=False),
                 c.weight, (c.opening_mode or "default"),
                 cb_id, cb_label, c.created_at,
                 (c.dialogue_mode or "scripted"),
                 c.user_persona or "", c.user_goal or "",
                 int(c.max_turns or 6),
                 json.dumps(list(c.termination_keywords or []), ensure_ascii=False)),
            )
        _db.execute("COMMIT")
    except Exception:
        _db.execute("ROLLBACK")
        raise
    return cases


def list_cases(agent_id: str) -> list[TestCase]:
    rows = _db.execute(
        "SELECT * FROM test_cases WHERE agent_id=? ORDER BY created_at DESC", (agent_id,)
    ).fetchall()
    return [_row_to_case(r) for r in rows]


def get_case(case_id: str) -> Optional[TestCase]:
    r = _db.execute("SELECT * FROM test_cases WHERE id=?", (case_id,)).fetchone()
    return _row_to_case(r) if r else None


def delete_case(case_id: str) -> None:
    _db.execute("DELETE FROM test_cases WHERE id=?", (case_id,))


def update_case(case_id: str, fields: dict[str, Any]) -> Optional[TestCase]:
    """部分更新单条用例。允许的字段见 allowed 集合（含动态对话扩展字段）。"""
    if not fields:
        return get_case(case_id)
    allowed = {
        "dimension", "sub_type", "title", "turns", "expectation",
        "pass_criteria", "weight", "opening_mode",
        # 动态对话模式扩展
        "dialogue_mode", "user_persona", "user_goal", "max_turns", "termination_keywords",
    }
    sets: list[str] = []
    args: list[Any] = []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "turns":
            # 可以是 [TestTurn] 或 [{role, content}]
            normalized = []
            for t in v:
                if hasattr(t, "model_dump"):
                    normalized.append(t.model_dump())
                elif isinstance(t, dict):
                    normalized.append({"role": "user", "content": str(t.get("content", ""))})
                else:
                    normalized.append({"role": "user", "content": str(t)})
            sets.append("turns=?")
            args.append(json.dumps(normalized, ensure_ascii=False))
        elif k == "pass_criteria":
            sets.append("pass_criteria=?")
            args.append(json.dumps(list(v), ensure_ascii=False))
        elif k == "termination_keywords":
            sets.append("termination_keywords=?")
            args.append(json.dumps(list(v), ensure_ascii=False))
        elif k == "max_turns":
            sets.append("max_turns=?")
            args.append(int(v or 6))
        else:
            sets.append(f"{k}=?")
            args.append(v)
    if not sets:
        return get_case(case_id)
    args.append(case_id)
    _db.execute(f"UPDATE test_cases SET {','.join(sets)} WHERE id=?", args)
    return get_case(case_id)


def _row_to_case(r: sqlite3.Row) -> TestCase:
    # 兼容老库：opening_mode / batch_id / batch_label / 动态对话相关列可能不存在
    def _safe(col: str, default: str = "") -> str:
        try:
            return r[col] or default
        except (IndexError, KeyError):
            return default

    def _safe_int(col: str, default: int) -> int:
        try:
            v = r[col]
            return int(v) if v is not None else default
        except (IndexError, KeyError, TypeError, ValueError):
            return default

    try:
        term_kw_raw = _safe("termination_keywords", "[]")
        termination_keywords = json.loads(term_kw_raw) if term_kw_raw else []
        if not isinstance(termination_keywords, list):
            termination_keywords = []
    except Exception:
        termination_keywords = []

    return TestCase(
        id=r["id"], agent_id=r["agent_id"], dimension=r["dimension"],
        sub_type=r["sub_type"], title=r["title"],
        opening_mode=_safe("opening_mode", "default"),
        turns=json.loads(r["turns"] or "[]"),
        expectation=r["expectation"],
        pass_criteria=json.loads(r["pass_criteria"] or "[]"),
        weight=r["weight"],
        dialogue_mode=_safe("dialogue_mode", "scripted") or "scripted",
        user_persona=_safe("user_persona", ""),
        user_goal=_safe("user_goal", ""),
        max_turns=_safe_int("max_turns", 6),
        termination_keywords=termination_keywords,
        batch_id=_safe("batch_id", ""),
        batch_label=_safe("batch_label", ""),
        created_at=r["created_at"],
    )


def list_batches(agent_id: str) -> list[dict[str, Any]]:
    """列出某智能体下所有批次（按创建时间倒序）。
    
    返回：[{batch_id, batch_label, count, created_at}, ...]
    - batch_id 为空的归为「未分组」
    """
    rows = _db.execute(
        """
        SELECT batch_id, batch_label, COUNT(*) AS count, MIN(created_at) AS created_at
        FROM test_cases
        WHERE agent_id=?
        GROUP BY batch_id, batch_label
        ORDER BY created_at DESC
        """,
        (agent_id,),
    ).fetchall()
    return [
        {
            "batch_id": r["batch_id"] or "",
            "batch_label": r["batch_label"] or "未分组",
            "count": r["count"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ---------- runs ----------

def create_run(r: TestRun) -> TestRun:
    r.id = r.id or new_id("run_")
    r.created_at = now_ms()
    _db.execute(
        "INSERT INTO runs(id,agent_id,name,status,total,finished,passed,failed,errors,average_score,summary,error,created_at,tokens_in,tokens_out,cost_usd,schedule_id)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (r.id, r.agent_id, r.name, r.status, r.total, r.finished, r.passed, r.failed,
         r.errors, r.average_score, r.summary, r.error, r.created_at, r.tokens_in, r.tokens_out, r.cost_usd, r.schedule_id or ""),
    )
    return r


def update_run(r: TestRun) -> None:
    _db.execute(
        "UPDATE runs SET status=?,total=?,finished=?,passed=?,failed=?,errors=?,average_score=?,"
        "summary=?,error=?,started_at=?,finished_at=?,tokens_in=?,tokens_out=?,cost_usd=? WHERE id=?",
        (r.status, r.total, r.finished, r.passed, r.failed, r.errors, r.average_score,
         r.summary, r.error, r.started_at, r.finished_at, r.tokens_in, r.tokens_out, r.cost_usd, r.id),
    )


def get_run(run_id: str) -> Optional[TestRun]:
    r = _db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return _row_to_run(r) if r else None


def list_runs(agent_id: Optional[str] = None, schedule_id: Optional[str] = None) -> list[TestRun]:
    """列出测试任务；可按 agent_id 或 schedule_id 过滤。"""
    sql = "SELECT * FROM runs WHERE 1=1"
    args: list[Any] = []
    if agent_id:
        sql += " AND agent_id=?"
        args.append(agent_id)
    if schedule_id:
        sql += " AND schedule_id=?"
        args.append(schedule_id)
    sql += " ORDER BY created_at DESC"
    rows = _db.execute(sql, args).fetchall()
    return [_row_to_run(r) for r in rows]


def delete_run(run_id: str) -> None:
    """删除测试任务及其全部用例结果。
    
    case_results 表已声明 ON DELETE CASCADE，配合 PRAGMA foreign_keys=ON 会自动级联清理。
    """
    _db.execute("DELETE FROM runs WHERE id=?", (run_id,))


def _row_to_run(r: sqlite3.Row) -> TestRun:
    def _safe_int(col: str, default: int = 0) -> int:
        try:
            v = r[col]
            return int(v) if v is not None else default
        except (IndexError, KeyError, TypeError, ValueError):
            return default
    def _safe_float(col: str, default: float = 0.0) -> float:
        try:
            v = r[col]
            return float(v) if v is not None else default
        except (IndexError, KeyError, TypeError, ValueError):
            return default
    def _safe_str(col: str, default: str = "") -> str:
        try:
            v = r[col]
            return v if v is not None else default
        except (IndexError, KeyError):
            return default
    return TestRun(
        id=r["id"], agent_id=r["agent_id"], name=r["name"], status=r["status"],
        total=r["total"], finished=r["finished"], passed=r["passed"],
        failed=r["failed"], errors=r["errors"], average_score=r["average_score"],
        summary=r["summary"], error=r["error"],
        created_at=r["created_at"], started_at=r["started_at"], finished_at=r["finished_at"],
        tokens_in=_safe_int("tokens_in", 0),
        tokens_out=_safe_int("tokens_out", 0),
        cost_usd=_safe_float("cost_usd", 0.0),
        schedule_id=_safe_str("schedule_id", ""),
    )


# ---------- 聚合查询（避免 N+1） ----------

def agents_overview_data() -> list[dict[str, Any]]:
    """一次性拉取 agents_overview 所需的全部数据。

    避开了之前 main.py 里 agent × (list_cases + list_runs) 的 N+1：
      - 用例分布：1 条 GROUP BY 拿到 (agent_id, dimension, count)
      - 最近一次 run：1 条窗口聚合（每个 agent 取 max(created_at)）
      - agents 主表：1 条 SELECT
    总查询数 = 3，与 agent 数无关。
    """
    agents = _db.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()

    # 用例分布
    dim_rows = _db.execute(
        "SELECT agent_id, dimension, COUNT(*) AS cnt "
        "FROM test_cases GROUP BY agent_id, dimension"
    ).fetchall()
    dim_map: dict[str, dict[str, int]] = {}
    total_map: dict[str, int] = {}
    for r in dim_rows:
        aid = r["agent_id"]
        dim_map.setdefault(aid, {})[r["dimension"]] = r["cnt"]
        total_map[aid] = total_map.get(aid, 0) + r["cnt"]

    # 每个 agent 的最近一次 run（用相关子查询拿到 created_at 最大那条的全部字段）
    last_run_rows = _db.execute(
        """
        SELECT r.* FROM runs r
        WHERE r.created_at = (
            SELECT MAX(r2.created_at) FROM runs r2 WHERE r2.agent_id = r.agent_id
        )
        """
    ).fetchall()
    last_run_map: dict[str, sqlite3.Row] = {row["agent_id"]: row for row in last_run_rows}

    out: list[dict[str, Any]] = []
    for a in agents:
        aid = a["id"]
        # 兼容老库：analysis 列可能不存在
        try:
            analysis_raw = a["analysis"] or "{}"
        except (IndexError, KeyError):
            analysis_raw = "{}"
        try:
            has_analysis = bool(json.loads(analysis_raw))
        except Exception:
            has_analysis = False
        try:
            analysis_at = a["analysis_at"]
        except (IndexError, KeyError):
            analysis_at = None
        lr = last_run_map.get(aid)
        out.append({
            "id": aid,
            "name": a["name"],
            "description": a["description"],
            "industry": a["industry"],
            "adapter": a["adapter"],
            "analysis_at": analysis_at,
            "has_analysis": has_analysis,
            "total_cases": total_map.get(aid, 0),
            "by_dimension": dim_map.get(aid, {}),
            "last_run": (
                {
                    "id": lr["id"],
                    "name": lr["name"],
                    "status": lr["status"],
                    "average_score": lr["average_score"],
                    "passed": lr["passed"],
                    "failed": lr["failed"],
                    "errors": lr["errors"],
                    "total": lr["total"],
                    "created_at": lr["created_at"],
                }
                if lr else None
            ),
        })
    return out


def list_case_results_with_meta(run_id: str) -> list[dict[str, Any]]:
    """LEFT JOIN 一次拿到 case_results + 关联的 case 元信息（dimension/sub_type/title/weight）。

    替代 main.py 中 list_case_results + 逐条 get_case 的 1+N 查询。
    返回的 dict 结构与原来 _results_with_case_meta 保持一致，前端无感。
    """
    rows = _db.execute(
        """
        SELECT
            cr.case_id, cr.status, cr.transcript, cr.score, cr.passed,
            cr.reasons, cr.judge_comment, cr.error,
            cr.metrics, cr.judge_runs, cr.agreement,
            tc.dimension AS tc_dimension,
            tc.sub_type  AS tc_sub_type,
            tc.title     AS tc_title,
            tc.weight    AS tc_weight
        FROM case_results cr
        LEFT JOIN test_cases tc ON tc.id = cr.case_id
        WHERE cr.run_id = ?
        ORDER BY cr.created_at ASC
        """,
        (run_id,),
    ).fetchall()

    from .models import JudgeRun, MetricScore  # 局部 import 避开循环依赖

    out: list[dict[str, Any]] = []
    for r in rows:
        # —— case_results 反序列化 ——（与 list_case_results 同款兼容逻辑）
        def _safe(col: str, default: Any = None) -> Any:
            try:
                return r[col]
            except (IndexError, KeyError):
                return default

        try:
            metrics_raw = json.loads(_safe("metrics", "[]") or "[]")
        except Exception:
            metrics_raw = []
        try:
            judge_runs_raw = json.loads(_safe("judge_runs", "[]") or "[]")
        except Exception:
            judge_runs_raw = []

        metrics = [MetricScore(**m).model_dump() for m in metrics_raw] if isinstance(metrics_raw, list) else []
        judge_runs = [JudgeRun(**j).model_dump() for j in judge_runs_raw] if isinstance(judge_runs_raw, list) else []

        try:
            agreement_v = r["agreement"]
            agreement = float(agreement_v) if agreement_v is not None else 1.0
        except (IndexError, KeyError, TypeError, ValueError):
            agreement = 1.0

        out.append({
            "case_id": r["case_id"],
            "status": r["status"],
            "transcript": json.loads(r["transcript"] or "[]"),
            "score": r["score"],
            "passed": bool(r["passed"]),
            "reasons": json.loads(r["reasons"] or "[]"),
            "judge_comment": r["judge_comment"] or "",
            "error": r["error"] or "",
            "metrics": metrics,
            "judge_runs": judge_runs,
            "agreement": agreement,
            # 关联的 case 元信息（case 已删则为空字符串）
            "dimension": r["tc_dimension"] or "",
            "sub_type": r["tc_sub_type"] or "",
            "title": r["tc_title"] or "",
            "weight": r["tc_weight"] if r["tc_weight"] is not None else 0,
        })
    return out


# ---------- case results ----------

def save_case_result(run_id: str, cr: CaseResult) -> None:
    _db.execute(
        "INSERT INTO case_results(id,run_id,case_id,status,transcript,score,passed,reasons,judge_comment,error,"
        "metrics,judge_runs,agreement,token_usage,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(run_id,case_id) DO UPDATE SET"
        " status=excluded.status,transcript=excluded.transcript,score=excluded.score,"
        " passed=excluded.passed,reasons=excluded.reasons,judge_comment=excluded.judge_comment,"
        " error=excluded.error,metrics=excluded.metrics,judge_runs=excluded.judge_runs,"
        " agreement=excluded.agreement,token_usage=excluded.token_usage",
        (new_id("cr_"), run_id, cr.case_id, cr.status,
         json.dumps(cr.transcript, ensure_ascii=False), cr.score,
         1 if cr.passed else 0, json.dumps(cr.reasons, ensure_ascii=False),
         cr.judge_comment, cr.error,
         json.dumps([m.model_dump() for m in cr.metrics], ensure_ascii=False),
         json.dumps([j.model_dump() for j in cr.judge_runs], ensure_ascii=False),
         cr.agreement,
         json.dumps([u.model_dump() for u in cr.token_usage], ensure_ascii=False),
         now_ms()),
    )


def list_case_results(run_id: str) -> list[CaseResult]:
    rows = _db.execute(
        "SELECT * FROM case_results WHERE run_id=? ORDER BY created_at ASC", (run_id,)
    ).fetchall()
    results = []
    for r in rows:
        # 兼容老数据：新列可能不存在
        def _safe_col(col: str, default: str = "[]") -> str:
            try:
                return r[col] or default
            except (IndexError, KeyError):
                return default

        def _safe_float(col: str, default: float = 1.0) -> float:
            try:
                v = r[col]
                return float(v) if v is not None else default
            except (IndexError, KeyError, TypeError, ValueError):
                return default

        from .models import JudgeRun, MetricScore, TokenUsage
        metrics_raw = json.loads(_safe_col("metrics", "[]"))
        judge_runs_raw = json.loads(_safe_col("judge_runs", "[]"))
        token_usage_raw = json.loads(_safe_col("token_usage", "[]"))

        metrics = [MetricScore(**m) for m in metrics_raw] if isinstance(metrics_raw, list) else []
        judge_runs = [JudgeRun(**j) for j in judge_runs_raw] if isinstance(judge_runs_raw, list) else []
        token_usage = [TokenUsage(**u) for u in token_usage_raw] if isinstance(token_usage_raw, list) else []

        results.append(CaseResult(
            case_id=r["case_id"], status=r["status"],
            transcript=json.loads(r["transcript"] or "[]"),
            score=r["score"], passed=bool(r["passed"]),
            reasons=json.loads(r["reasons"] or "[]"),
            judge_comment=r["judge_comment"], error=r["error"],
            metrics=metrics,
            judge_runs=judge_runs,
            agreement=_safe_float("agreement", 1.0),
            token_usage=token_usage,
        ))
    return results


# ---------- generation_jobs（生成任务持久化） ----------

def _row_to_genjob(r: sqlite3.Row) -> dict[str, Any]:
    """SQLite Row → 与原 _GEN_JOBS 字典字段对齐的 dict。"""
    try:
        params = json.loads(r["params"] or "{}")
        if not isinstance(params, dict): params = {}
    except Exception:
        params = {}
    try:
        analysis_raw = r["analysis"] or ""
        analysis = json.loads(analysis_raw) if analysis_raw else None
    except Exception:
        analysis = None
    # 安全读取 token/cost 字段（老库可能没有）
    cols = {k for k in r.keys()}
    return {
        "id": r["id"],
        "mode": r["mode"] or "",
        "agent_id": r["agent_id"],
        "agent_name": r["agent_name"] or "",
        "status": r["status"],
        "planned": int(r["planned"] or 0),
        "generated": int(r["generated"] or 0),
        "error": r["error"] or "",
        "raw_text": r["raw_text"] or "",
        "params": params,
        "analysis": analysis,
        "started_at": int(r["started_at"]),
        "finished_at": int(r["finished_at"]) if r["finished_at"] is not None else None,
        "tokens_in": int(r["tokens_in"]) if "tokens_in" in cols and r["tokens_in"] is not None else 0,
        "tokens_out": int(r["tokens_out"]) if "tokens_out" in cols and r["tokens_out"] is not None else 0,
        "cost_usd": float(r["cost_usd"]) if "cost_usd" in cols and r["cost_usd"] is not None else 0.0,
    }


def create_gen_job(
    *,
    job_id: str,
    mode: str,
    agent_id: str,
    agent_name: str,
    planned: int,
    params: dict[str, Any],
) -> None:
    _db.execute(
        "INSERT INTO generation_jobs(id, mode, agent_id, agent_name, status, planned, "
        "generated, error, raw_text, params, analysis, started_at, finished_at) "
        "VALUES(?,?,?,?, 'running', ?, 0, '', '', ?, '', ?, NULL)",
        (job_id, mode, agent_id, agent_name, planned,
         json.dumps(params or {}, ensure_ascii=False), now_ms()),
    )


def update_gen_job(job_id: str, fields: dict[str, Any]) -> None:
    """部分更新：generated / status / error / raw_text / analysis / finished_at / tokens_in / tokens_out / cost_usd。"""
    if not fields: return
    allowed = {"generated", "status", "error", "raw_text", "analysis", "finished_at", "tokens_in", "tokens_out", "cost_usd"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed: continue
        if k == "analysis" and v is not None and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k} = ?")
        args.append(v)
    if not sets: return
    args.append(job_id)
    _db.execute(f"UPDATE generation_jobs SET {', '.join(sets)} WHERE id = ?", args)


def get_gen_job(job_id: str) -> Optional[dict[str, Any]]:
    r = _db.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_genjob(r) if r else None


def list_gen_jobs(active_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM generation_jobs"
    if active_only:
        sql += " WHERE status = 'running'"
    sql += " ORDER BY started_at DESC"
    return [_row_to_genjob(r) for r in _db.execute(sql).fetchall()]


def cleanup_gen_jobs(max_age_ms: int) -> int:
    """删掉已完成且超龄的 job。返回删除数量。"""
    cutoff = now_ms() - max_age_ms
    cur = _db.execute(
        "DELETE FROM generation_jobs WHERE finished_at IS NOT NULL AND finished_at < ?",
        (cutoff,),
    )
    return cur.rowcount or 0


def mark_stale_running_jobs_as_error() -> int:
    """启动时调用：上次进程残留的 running 任务一律改判为 error。"""
    now = now_ms()
    cur = _db.execute(
        "UPDATE generation_jobs SET status = 'error', "
        "error = COALESCE(NULLIF(error,''), '服务重启时该任务仍在运行，已标记为失败'), "
        "finished_at = ? WHERE status = 'running'",
        (now,),
    )
    return cur.rowcount or 0


# ---------- templates ----------

def _row_to_template(r: sqlite3.Row) -> Template:
    return Template(
        id=r["id"],
        type=r["type"],
        dimension=r["dimension"] or "",
        industry=r["industry"] or "",
        name=r["name"],
        content=r["content"],
        description=r["description"] or "",
        is_active=bool(r["is_active"]),
        sort_order=r["sort_order"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def create_template(t: Template) -> Template:
    t.id = t.id or new_id("tpl_")
    t.created_at = t.updated_at = now_ms()
    _db.execute(
        "INSERT INTO templates(id,type,dimension,industry,name,content,description,is_active,sort_order,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (t.id, t.type, t.dimension or "", t.industry or "", t.name, t.content,
         t.description or "", 1 if t.is_active else 0, t.sort_order,
         t.created_at, t.updated_at),
    )
    return t


def update_template(template_id: str, fields: dict[str, Any]) -> Optional[Template]:
    """部分更新；只更新提供的字段。"""
    if not fields:
        return get_template(template_id)
    allowed = {"type", "dimension", "industry", "name", "content", "description",
               "is_active", "sort_order"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        col = k
        if k == "is_active":
            v = 1 if v else 0
        sets.append(f"{col}=?")
        args.append(v)
    if not sets:
        return get_template(template_id)
    sets.append("updated_at=?")
    args.append(now_ms())
    args.append(template_id)
    _db.execute(f"UPDATE templates SET {','.join(sets)} WHERE id=?", args)
    return get_template(template_id)


def get_template(template_id: str) -> Optional[Template]:
    r = _db.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
    return _row_to_template(r) if r else None


def list_templates(
    type_: Optional[str] = None,
    dimension: Optional[str] = None,
    industry: Optional[str] = None,
    active_only: bool = False,
) -> list[Template]:
    sql = "SELECT * FROM templates WHERE 1=1"
    args: list[Any] = []
    if type_:
        sql += " AND type=?"
        args.append(type_)
    if dimension is not None:
        sql += " AND dimension=?"
        args.append(dimension)
    if industry is not None:
        sql += " AND industry=?"
        args.append(industry)
    if active_only:
        sql += " AND is_active=1"
    sql += " ORDER BY sort_order ASC, created_at ASC"
    rows = _db.execute(sql, args).fetchall()
    return [_row_to_template(r) for r in rows]


def delete_template(template_id: str) -> None:
    _db.execute("DELETE FROM templates WHERE id=?", (template_id,))


def count_templates() -> int:
    r = _db.execute("SELECT COUNT(*) AS c FROM templates").fetchone()
    return int(r["c"]) if r else 0


# ---------- schedules（定时任务） ----------

def _row_to_schedule(r: sqlite3.Row) -> Schedule:
    try:
        trigger = json.loads(r["trigger"] or "{}")
    except Exception:
        trigger = {}
    try:
        selector = json.loads(r["selector"] or "{}")
    except Exception:
        selector = {}
    return Schedule(
        id=r["id"],
        name=r["name"],
        agent_id=r["agent_id"],
        trigger=ScheduleTrigger(**trigger) if trigger else ScheduleTrigger(type="daily"),
        selector=ScheduleSelector(**selector) if selector else ScheduleSelector(),
        concurrency=int(r["concurrency"] or 5),
        enabled=bool(r["enabled"]),
        on_overlap=r["on_overlap"] or "skip",
        next_run_at=r["next_run_at"],
        last_run_id=r["last_run_id"] or "",
        last_run_at=r["last_run_at"],
        last_run_status=r["last_run_status"] or "",
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def create_schedule(s: Schedule) -> Schedule:
    s.id = s.id or new_id("sch_")
    s.created_at = s.updated_at = now_ms()
    _db.execute(
        "INSERT INTO schedules(id,name,agent_id,trigger,selector,concurrency,enabled,on_overlap,"
        "next_run_at,last_run_id,last_run_at,last_run_status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            s.id, s.name, s.agent_id,
            json.dumps(s.trigger.model_dump(), ensure_ascii=False),
            json.dumps(s.selector.model_dump(), ensure_ascii=False),
            int(s.concurrency or 5),
            1 if s.enabled else 0,
            s.on_overlap or "skip",
            s.next_run_at,
            s.last_run_id or "",
            s.last_run_at,
            s.last_run_status or "",
            s.created_at, s.updated_at,
        ),
    )
    return s


def update_schedule(schedule_id: str, fields: dict[str, Any]) -> Optional[Schedule]:
    """部分更新；trigger / selector 接受 dict 或 Pydantic 模型。"""
    if not fields:
        return get_schedule(schedule_id)
    allowed = {
        "name", "agent_id", "trigger", "selector", "concurrency", "enabled",
        "on_overlap", "next_run_at", "last_run_id", "last_run_at", "last_run_status",
    }
    sets: list[str] = []
    args: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("trigger", "selector"):
            if v is None:
                continue
            if hasattr(v, "model_dump"):
                v = v.model_dump()
            v = json.dumps(v, ensure_ascii=False)
        elif k == "enabled":
            v = 1 if v else 0
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return get_schedule(schedule_id)
    sets.append("updated_at=?")
    args.append(now_ms())
    args.append(schedule_id)
    _db.execute(f"UPDATE schedules SET {','.join(sets)} WHERE id=?", args)
    return get_schedule(schedule_id)


def get_schedule(schedule_id: str) -> Optional[Schedule]:
    r = _db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    return _row_to_schedule(r) if r else None


def list_schedules() -> list[Schedule]:
    rows = _db.execute(
        "SELECT * FROM schedules ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_schedule(r) for r in rows]


def list_schedules_due(now_at: int) -> list[Schedule]:
    """返回到点该跑的启用任务（next_run_at <= now_at）。"""
    rows = _db.execute(
        "SELECT * FROM schedules WHERE enabled=1 AND next_run_at IS NOT NULL "
        "AND next_run_at <= ? ORDER BY next_run_at ASC",
        (now_at,),
    ).fetchall()
    return [_row_to_schedule(r) for r in rows]


def delete_schedule(schedule_id: str) -> None:
    _db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


init_db()
# 启动收尾：上次进程残留的 running 生成任务标为 error，避免前端拿到永远停在 running 的孤儿
try:
    _stale = mark_stale_running_jobs_as_error()
    if _stale:
        logger.info("启动时发现 %d 个孤儿生成任务，已标记为 error", _stale)
except Exception:
    logger.exception("mark_stale_running_jobs_as_error 失败")

