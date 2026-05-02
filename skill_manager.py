#!/usr/bin/env python3
"""
Skill Manager — 技能管理注册表
=================================

AI 的技能就像记忆一样，长时间不用就会遗忘。
Skill Manager 管理所有 Hermes Skill 的生命周期：

  - 每次调用 +1 分
  - 7 天未用每周 -1 分
  - score ≤ 0 → 移出活跃列表（不再自动注入上下文）
  - 核心 skill 永不移除（score 不低于 3）

与记忆树的关系：照搬了 MindMapStore 的衰减逻辑，但更轻量。

数据文件: ~/.hermes/skills/skill_registry.db (SQLite)
纯标准库，零外部依赖。Python 3.8+。
"""

import json
import os
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("skill_manager")

# ---------------------------------------------------------------------------
# 常量（照搬记忆树的设计）
# ---------------------------------------------------------------------------
NEW_SKILL_SCORE = 1           # 新注册技能初始分
ACCESS_SCORE_INCREMENT = 1    # 每次调用加分
DECAY_AMOUNT = 1              # 每周期衰减量
DECAY_INTERVAL_DAYS = 7       # 衰减间隔（天）— skill 7天不调用就扣分
CORE_MIN_SCORE = 3            # 核心 skill 最低分数
INACTIVE_THRESHOLD = 0        # ≤0 时移出活跃列表
MAX_REGISTERED = 500          # 最大注册数

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def _get_skills_dir() -> Path:
    """获取技能管理目录。"""
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    return Path(hermes_home) / "skills"


def _get_db_path() -> Path:
    """获取注册表数据库路径。"""
    return _get_skills_dir() / "skill_registry.db"


# ---------------------------------------------------------------------------
# 数据库初始化
# ---------------------------------------------------------------------------

def _init_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """初始化（或连接）skill_registry.db。"""
    path = db_path or _get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            name        TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            category    TEXT NOT NULL DEFAULT '',
            score       INTEGER NOT NULL DEFAULT 1,
            is_core     INTEGER NOT NULL DEFAULT 0,
            is_active   INTEGER NOT NULL DEFAULT 1,
            last_access TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT '',
            updated_at  TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# 核心类
# ---------------------------------------------------------------------------

class SkillRegistry:
    """Skill 注册表管理器。

    用法:
        reg = SkillRegistry()
        reg.register("auto-model-router", "路由决策", "custom")
        reg.score("auto-model-router")           # 调用时加分
        active = reg.get_active()                 # 获取活跃列表
        reg.decay_if_needed()                     # 每周衰减
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or _get_db_path()
        self._conn = _init_db(db_path)
        self._ensure_last_decay_table()

    def _ensure_last_decay_table(self):
        """确保 metadata 表存在，记录上次衰减时间。"""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(self, name: str, description: str = "",
                 category: str = "", as_core: bool = False) -> bool:
        """注册一个新 skill。

        如果已存在：更新描述/分类，不重置分数。
        如果是新的：初始分为 NEW_SKILL_SCORE。

        Returns:
            True 表示新增，False 表示已存在（更新描述/分类）
        """
        now = _now_iso()
        existing = self._conn.execute(
            "SELECT name FROM skills WHERE name = ?", (name,)
        ).fetchone()

        if existing:
            self._conn.execute("""
                UPDATE skills SET
                    description = CASE WHEN ? != '' THEN ? ELSE description END,
                    category = CASE WHEN ? != '' THEN ? ELSE category END,
                    updated_at = ?
                WHERE name = ?
            """, (description, description, category, category, now, name))
            self._conn.commit()
            return False  # 已存在

        self._conn.execute("""
            INSERT INTO skills (name, description, category, score, is_core,
                                is_active, last_access, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (name, description, category,
              CORE_MIN_SCORE if as_core else NEW_SKILL_SCORE,
              1 if as_core else 0,
              now, now, now))
        self._conn.commit()
        logger.info("注册 skill: %s (core=%s)", name, as_core)
        return True

    def register_batch(self, skills: List[Tuple[str, str, str]]) -> int:
        """批量注册 skill，自动跳过已存在的。

        Args:
            skills: [(name, description, category), ...]

        Returns:
            新增数量
        """
        count = 0
        for name, desc, cat in skills:
            if self.register(name, desc, cat):
                count += 1
        return count

    # ------------------------------------------------------------------
    # 分数
    # ------------------------------------------------------------------

    def score(self, name: str, increment: int = ACCESS_SCORE_INCREMENT) -> Optional[int]:
        """记录一次 skill 调用，加分。

        如果是新 skill（未注册），自动注册并加分。
        如果已被标记为 inactive，重新激活。

        Returns:
            更新后的分数，或 None（skill 不存在）
        """
        now = _now_iso()
        existing = self._conn.execute(
            "SELECT name, is_active FROM skills WHERE name = ?", (name,)
        ).fetchone()

        if not existing:
            self.register(name)
            existing_score = NEW_SKILL_SCORE
            self._conn.execute("""
                UPDATE skills SET score = score + ?, last_access = ?, is_active = 1
                WHERE name = ?
            """, (increment, now, name))
        else:
            self._conn.execute("""
                UPDATE skills SET score = score + ?, last_access = ?,
                    is_active = 1, updated_at = ?
                WHERE name = ?
            """, (increment, now, now, name))

        self._conn.commit()
        row = self._conn.execute(
            "SELECT score FROM skills WHERE name = ?", (name,)
        ).fetchone()
        return row["score"] if row else None

    # ------------------------------------------------------------------
    # 核心标记
    # ------------------------------------------------------------------

    def promote_core(self, name: str) -> bool:
        """标记为 core skill，永不移除。自动设置 score 到 CORE_MIN_SCORE。"""
        row = self._conn.execute(
            "SELECT is_core, score FROM skills WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return False
        if row["is_core"]:
            logger.info("skill '%s' 已是核心", name)
            return True
        now = _now_iso()
        new_score = max(CORE_MIN_SCORE, row["score"])
        self._conn.execute("""
            UPDATE skills SET is_core = 1, score = ?, is_active = 1, updated_at = ?
            WHERE name = ?
        """, (new_score, now, name))
        self._conn.commit()
        logger.info("提升核心 skill: %s (score=%d)", name, new_score)
        return True

    def demote_core(self, name: str) -> bool:
        """取消核心标记。"""
        row = self._conn.execute(
            "SELECT is_core FROM skills WHERE name = ?", (name,)
        ).fetchone()
        if not row or not row["is_core"]:
            return False
        now = _now_iso()
        self._conn.execute("""
            UPDATE skills SET is_core = 0, updated_at = ? WHERE name = ?
        """, (now, name))
        self._conn.commit()
        logger.info("取消核心 skill: %s", name)
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_active(self) -> List[Dict]:
        """获取活跃 skill 列表（分数 > 0 或 核心），按分数降序排列。

        Returns:
            [{"name", "description", "category", "score", "is_core"}, ...]
        """
        rows = self._conn.execute("""
            SELECT name, description, category, score, is_core
            FROM skills
            WHERE is_active = 1 AND (score > 0 OR is_core = 1)
            ORDER BY is_core DESC, score DESC, name ASC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_inactive(self) -> List[Dict]:
        """获取 inactive skill 列表（被遗忘的）。"""
        rows = self._conn.execute("""
            SELECT name, description, category, score, is_core, last_access
            FROM skills
            WHERE is_active = 0 OR score <= 0
            ORDER BY score ASC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_all(self) -> List[Dict]:
        """获取所有已注册 skill。"""
        rows = self._conn.execute("""
            SELECT name, description, category, score, is_core, is_active,
                   last_access, created_at, updated_at
            FROM skills
            ORDER BY is_core DESC, score DESC, name ASC
        """).fetchall()
        return [dict(r) for r in rows]

    def get(self, name: str) -> Optional[Dict]:
        """获取单个 skill 的详情。"""
        row = self._conn.execute(
            "SELECT * FROM skills WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def stat(self) -> Dict:
        """获取注册表统计。"""
        total = self._conn.execute(
            "SELECT COUNT(*) as c FROM skills"
        ).fetchone()["c"]
        active = self._conn.execute(
            "SELECT COUNT(*) as c FROM skills WHERE is_active = 1 AND (score > 0 OR is_core = 1)"
        ).fetchone()["c"]
        core = self._conn.execute(
            "SELECT COUNT(*) as c FROM skills WHERE is_core = 1"
        ).fetchone()["c"]
        inactive = self._conn.execute(
            "SELECT COUNT(*) as c FROM skills WHERE is_active = 0 OR score <= 0"
        ).fetchone()["c"]
        last_decay = self._conn.execute(
            "SELECT value FROM metadata WHERE key = 'last_decay'"
        ).fetchone()
        return {
            "total": total,
            "active": active,
            "core": core,
            "inactive": inactive,
            "last_decay": last_decay["value"] if last_decay else None,
        }

    # ------------------------------------------------------------------
    # 衰减（照搬记忆树逻辑）
    # ------------------------------------------------------------------

    def _should_decay(self) -> bool:
        """检查是否需要执行衰减。"""
        row = self._conn.execute(
            "SELECT value FROM metadata WHERE key = 'last_decay'"
        ).fetchone()
        if not row:
            return True
        try:
            last = datetime.fromisoformat(row["value"].replace("Z", "+00:00"))
            if last.tzinfo:
                last = last.replace(tzinfo=None)
            return (datetime.now() - last).days >= DECAY_INTERVAL_DAYS
        except (ValueError, TypeError):
            return True

    def decay_if_needed(self, force: bool = False) -> Dict:
        """如果需要，执行衰减扫描。

        衰减规则:
          - 非核心 skill，last_access 距今 > 7 天，score -= 1
          - 核心 skill 不低于 CORE_MIN_SCORE (3)
          - score ≤ 0 → 标记 is_active = 0（移出活跃列表）

        Returns:
            {"decayed": N, "deactivated": [name, ...], "core_protected": [name, ...]}
        """
        if not force and not self._should_decay():
            logger.debug("距离上次衰减不足 %d 天，跳过", DECAY_INTERVAL_DAYS)
            return {"decayed": 0, "deactivated": [], "core_protected": []}

        logger.info("开始每周 skill 衰减扫描...")
        now_iso = _now_iso()
        now = datetime.now()
        cutoff = now - timedelta(days=DECAY_INTERVAL_DAYS)

        decayed = 0
        deactivated: List[str] = []
        protected: List[str] = []

        rows = self._conn.execute("""
            SELECT name, score, is_core, last_access
            FROM skills
            WHERE is_active = 1
        """).fetchall()

        for row in rows:
            try:
                last = datetime.fromisoformat(
                    row["last_access"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue

            if (now - last).days < DECAY_INTERVAL_DAYS:
                continue  # 无需衰减

            if row["is_core"]:
                # 核心保护：不低于 CORE_MIN_SCORE
                if row["score"] > CORE_MIN_SCORE:
                    new_score = max(CORE_MIN_SCORE, row["score"] - DECAY_AMOUNT)
                    self._conn.execute(
                        "UPDATE skills SET score = ?, updated_at = ? WHERE name = ?",
                        (new_score, now_iso, row["name"])
                    )
                    decayed += 1
                elif row["score"] < CORE_MIN_SCORE:
                    self._conn.execute(
                        "UPDATE skills SET score = ?, updated_at = ? WHERE name = ?",
                        (CORE_MIN_SCORE, now_iso, row["name"])
                    )
                protected.append(row["name"])
            else:
                new_score = row["score"] - DECAY_AMOUNT
                if new_score <= INACTIVE_THRESHOLD:
                    self._conn.execute("""
                        UPDATE skills SET score = ?, is_active = 0, updated_at = ?
                        WHERE name = ?
                    """, (new_score, now_iso, row["name"]))
                    deactivated.append(row["name"])
                    logger.info("skill '%s' 已移出活跃列表 (score=%d)", row["name"], new_score)
                else:
                    self._conn.execute(
                        "UPDATE skills SET score = ?, updated_at = ? WHERE name = ?",
                        (new_score, now_iso, row["name"])
                    )
                decayed += 1

        # 记录本次衰减时间
        self._conn.execute("""
            INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_decay', ?)
        """, (now_iso,))
        self._conn.commit()

        result = {
            "decayed": decayed,
            "deactivated": deactivated,
            "core_protected": protected,
        }
        logger.info("衰减完成: %d 个 skill 衰减, %d 个移出, %d 个核心保护",
                     decayed, len(deactivated), len(protected))
        return result

    # ------------------------------------------------------------------
    # 维护
    # ------------------------------------------------------------------

    def reset_score(self, name: str, score: int = NEW_SKILL_SCORE) -> bool:
        """重置单个 skill 的分数并激活。"""
        row = self._conn.execute(
            "SELECT name FROM skills WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return False
        now = _now_iso()
        self._conn.execute("""
            UPDATE skills SET score = ?, is_active = 1, updated_at = ?
            WHERE name = ?
        """, (score, now, name))
        self._conn.commit()
        return True

    def remove(self, name: str) -> bool:
        """从注册表中移除一个 skill。"""
        row = self._conn.execute(
            "SELECT name FROM skills WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return False
        self._conn.execute("DELETE FROM skills WHERE name = ?", (name,))
        self._conn.commit()
        return True

    def close(self):
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _cli_register(args: List[str], reg: SkillRegistry):
    """python3 skill_manager.py register <name> [--desc ...] [--cat ...] [--core]"""
    if not args:
        print("用法: skill_manager.py register <name> [--desc 描述] [--cat 分类] [--core]")
        return
    name = args[0]
    desc = ""
    cat = ""
    core = False
    i = 1
    while i < len(args):
        if args[i] == "--desc" and i + 1 < len(args):
            desc = args[i + 1]
            i += 2
        elif args[i] == "--cat" and i + 1 < len(args):
            cat = args[i + 1]
            i += 2
        elif args[i] == "--core":
            core = True
            i += 1
        else:
            i += 1
    new = reg.register(name, desc, cat, core)
    print(f"{'✅ 新增' if new else '✅ 已更新'}: {name} (score={reg.get(name)['score']})")


def _cli_score(args: List[str], reg: SkillRegistry):
    """python3 skill_manager.py score <name> [increment]"""
    if not args:
        print("用法: skill_manager.py score <name> [加分值]")
        return
    inc = int(args[1]) if len(args) > 1 else ACCESS_SCORE_INCREMENT
    s = reg.score(args[0], inc)
    if s is not None:
        print(f"✅ {args[0]}: score={s}")
    else:
        print(f"❌ 未找到: {args[0]}")


def _cli_list(reg: SkillRegistry):
    rows = reg.get_active()
    if not rows:
        print("📭 没有活跃 skill")
        return
    print(f"📋 活跃 Skill ({len(rows)} 个):")
    for r in rows:
        core = "⭐" if r["is_core"] else "  "
        print(f"  {core} {r['name']:40s} score={r['score']:3d}  [{r.get('category','')}]")
        if r["description"]:
            print(f"     {r['description']}")


def _cli_inactive(reg: SkillRegistry):
    rows = reg.get_inactive()
    if not rows:
        print("📭 没有 inactive skill")
        return
    print(f"💤 已遗忘的 Skill ({len(rows)} 个):")
    for r in rows:
        print(f"  {r['name']:40s} score={r['score']:3d}")


def _cli_stat(reg: SkillRegistry):
    s = reg.stat()
    print(f"📊 Skill 注册表统计")
    print(f"  总注册: {s['total']}")
    print(f"  活跃:   {s['active']}")
    print(f"  核心:   {s['core']}")
    print(f"  遗忘:   {s['inactive']}")
    print(f"  上次衰减: {s['last_decay'] or '从未衰减'}")


def _cli_decay(args: List[str], reg: SkillRegistry):
    force = "--force" in args
    r = reg.decay_if_needed(force=force)
    print(f"🔄 衰减结果: {r['decayed']} 个衰减, "
          f"{len(r['deactivated'])} 个移出, "
          f"{len(r['core_protected'])} 个核心保护")
    if r['deactivated']:
        print(f"  移出列表: {', '.join(r['deactivated'])}")
    if r['core_protected']:
        print(f"  核心保护: {', '.join(r['core_protected'])}")


def _cli_core(args: List[str], reg: SkillRegistry):
    """skill_manager.py core <name> / uncore <name>"""
    if not args:
        print("用法: skill_manager.py core <name> | uncore <name>")
        return
    if args[0] == "uncore" and len(args) > 1:
        ok = reg.demote_core(args[1])
    else:
        ok = reg.promote_core(args[0])
    print(f"{'✅' if ok else '❌'} 操作完成")


def _cli_remove(args: List[str], reg: SkillRegistry):
    if not args:
        print("用法: skill_manager.py remove <name>")
        return
    ok = reg.remove(args[0])
    print(f"{'✅' if ok else '❌'} 已移除: {args[0]}")


def _cli_get(args: List[str], reg: SkillRegistry):
    if not args:
        print("用法: skill_manager.py get <name>")
        return
    r = reg.get(args[0])
    if r:
        print(f"  name:        {r['name']}")
        print(f"  description: {r['description']}")
        print(f"  category:    {r['category']}")
        print(f"  score:       {r['score']}")
        print(f"  is_core:     {r['is_core']}")
        print(f"  is_active:   {r['is_active']}")
        print(f"  last_access: {r['last_access']}")
    else:
        print(f"❌ 未找到: {args[0]}")


def main():
    import sys
    args = sys.argv[1:]
    if not args:
        print("用法: skill_manager.py <command> [参数...]")
        print()
        print("命令:")
        print("  register <name> [--desc 描述] [--cat 分类] [--core]  注册 new skill")
        print("  score <name> [加分值]         记录一次调用")
        print("  core <name>                   标记为核心")
        print("  uncore <name>                 取消核心")
        print("  list                          活跃列表")
        print("  inactive                      遗忘列表")
        print("  stat                          统计信息")
        print("  decay [--force]               执行衰减")
        print("  get <name>                    详查")
        print("  remove <name>                 移除")
        return

    cmd = args[0]
    rest = args[1:] if len(args) > 1 else []

    with SkillRegistry() as reg:
        if cmd == "register":
            _cli_register(rest, reg)
        elif cmd == "score":
            _cli_score(rest, reg)
        elif cmd == "list":
            _cli_list(reg)
        elif cmd == "inactive":
            _cli_inactive(reg)
        elif cmd == "decay":
            _cli_decay(rest, reg)
        elif cmd == "stat":
            _cli_stat(reg)
        elif cmd == "core":
            _cli_core(rest, reg)
        elif cmd == "remove":
            _cli_remove(rest, reg)
        elif cmd == "get":
            _cli_get(rest, reg)
        else:
            print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
