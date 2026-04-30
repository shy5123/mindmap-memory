#!/usr/bin/env python3
"""记忆树演示数据生成器。

生成示例记忆树，清除个人数据后用作文档演示。
运行: python3 scripts/seed_demo.py
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from mindmap_memory import MindMapStore
import sqlite3

DEMO_DB = SCRIPT_DIR / "demo_mindmap.db"

def seed():
    # 如果已有 demo 数据，询问
    if DEMO_DB.exists():
        DEMO_DB.unlink()

    store = MindMapStore(data_path=DEMO_DB)

    # ── 技术开发 ──
    store.add_memory("Python/异步编程: asyncio 协程在 3.11 版本引入了 TaskGroup 新特性，简化了并发任务管理")
    store.add_memory("Python/异步编程: FastAPI 使用 Starlette 作为底层框架，原生支持 async/await")
    store.add_memory("Python/包管理: 推荐使用 uv 替代 pip，速度提升 10-100 倍")
    store.add_memory("Rust/异步: tokio runtime 通过 spawn 实现轻量级并发任务调度")
    store.add_memory("Rust/所有权: 所有权系统在编译期保证内存安全，无需垃圾回收")
    store.add_memory("Docker/部署: Docker Compose v2 使用 `docker compose` 命令（无连字符）替代旧版 docker-compose")
    store.add_memory("Git/工作流: 推荐使用 conventional commits 规范: feat/fix/docs/chore/refactor")
    store.add_memory("Git/技巧: git stash push -m 'message' 可以给暂存命名，方便后续查找")

    # ── 生活日常 ──
    store.add_memory("咖啡/偏好: 冰美式是首选，偶尔点冷萃。星巴克的豆子偏酸，更喜欢本地精品咖啡店的浅烘豆")
    store.add_memory("咖啡/习惯: 每天上午一杯，下午尽量不喝以免影响睡眠")
    store.add_memory("阅读/书单: 正在读《设计数据密集型应用》(DDIA)，推荐给所有后端开发者")
    store.add_memory("阅读/书单: 上个月读完了《Rust 程序设计》，对所有权系统理解加深很多")
    store.add_memory("健身/计划: 每周二四六去健身房，有氧+力量结合。最近加入了 HIIT 训练，效果显著")

    # ── 项目信息 ──
    store.add_memory("项目/记忆树: 一个会新陈代谢的记忆系统，用话题树组织 AI 的记忆，自动衰减清理")
    store.add_memory("项目/记忆树: 后端存储使用 SQLite，检索支持 BGE 嵌入模型和关键词双模式")
    store.add_memory("项目/记忆树: 分数区间: 1-20 短期记忆 / 21-40 长期记忆 / 41+ 永久记忆")

    # 标记核心记忆
    for node_id, node in store.nodes.items():
        if "记忆树" in node.topic and "分数区间" in (node.content or ""):
            store.set_core(node_id, True)
            break

    store.write_index_to_md()
    print(f"✅ 演示数据已生成: {DEMO_DB}")
    print(f"   节点总数: {len(store.nodes)}")
    print(f"   根话题: {len(store.root_ids)}")
    stats = store.stats()
    print(f"   短期记忆: {stats.get('短期记忆(1-20)', 0)}")
    print(f"   长期记忆: {stats.get('长期记忆(21-40)', 0)}")
    print(f"   核心记忆: {stats.get('核心记忆', 0)}")

if __name__ == "__main__":
    seed()
