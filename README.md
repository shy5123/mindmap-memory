# 记忆树 MemoryTree

[![Tests](https://github.com/shy5123/mindmap-memory/actions/workflows/test.yml/badge.svg)](https://github.com/shy5123/mindmap-memory/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**一棵会新陈代谢的记忆树**

让 AI 的记忆不再是无限膨胀的仓库，而是一片有呼吸、会随着四季更替生长落叶的森林。

`记忆树` × `MemoryTree` | MIT License | Python 3.8+

> A self-pruning memory tree for Hermes Agent — organizes knowledge into
> hierarchical topics, auto-decays stale memories, and protects what matters.
> Zero-dependency keyword matching out of the box; plug in BGE embeddings
> for 10× semantic accuracy.

> ⚡ **开箱即用**：克隆即跑，零配置关键词匹配。  
> 🧠 **三步激活语义检索**：`pip install sentence-transformers` → `export MEMORYTREE_EMBEDDING_MODEL=local` → 首次自动下载 BGE 模型。  
> 不配也能用——检索精度差 10 倍，但基本功能完整。

## 为什么做这个项目

Hermes 原生的记忆系统是一个扁平、只增不减的 .md 文件。任何说过的话都可能被记下，但永远不会被忘记。

这会导致三个严重问题：

1. **记忆膨胀** — 日久天长，里面积攒的无效信息会淹没真正重要的记忆。
2. **检索噪音** — AI 回忆时需要从大量无关文本中硬搜，结果慢且不精准。
3. **缺少认知结构** — 人类的记忆是分门别类、有逻辑层级的，而原系统只是一串列表。

记忆树解决了这些痛点：它把你的记忆组织成一棵会自己长新枝、自己落枯叶的树。

---

## 核心能力

**🌲 层级化记忆结构**
你的知识被自动分类、拆解成话题树（最多 6 层）。从此"苹果"是水果还是公司，系统心里有数。

**🕰️ 自动遗忘与增强**
每次被回想起来的记忆都会 +1 分；连续 7 天未被访问的记忆每周 -1 分，分数归零自动删除。记得多的自然沉淀为长期记忆，不再用的悄悄消亡。

**🛡️ 核心记忆保护**
你可以把最重要的记忆标记为"核心"，它们永不被衰减删除（至少保留 1 分）。即使父节点被遗忘，核心子节点也会自动提升为根节点，避免株连。

**🔍 逐层下钻检索**
检索时像人一样从大话题一层层深入，而不是全文漫无目的地匹配。省 Token，更精准。

**📦 全自动运转**
衰减检查在每次对话启动时自动执行，无需手动干预。记忆守护每天自动用嵌入模型重分类当天记忆，话题树越用越准。

**📋 旧记忆一键迁移**
原生 MEMORY.md 的旧数据可以自动迁入树形结构，原有文件备份保留，不会丢失。

**⚡ SQLite 高性能存储**
从单文件 JSON 升级为 SQLite 数据库。事务保证原子性，写入中途崩溃自动回滚。冷启动检索亚毫秒级完成。

**🔗 原生工具封装**
记忆操作已注册为 Hermes 原生工具，不再依赖外部脚本调用。通信延迟从数百毫秒降至毫秒级。

**🔄 守护同步机制**
原生 memory 工具和记忆树同时写入时，守护逻辑会自动发现原生工具的新条目，将其纳入记忆树，两个入口安全共存。

**🧠 可插拔语义引擎**
三种模型一键切换：`KeywordModel`（默认零依赖）、`LocalEmbeddingModel`（BGE 本地惰性加载）、`OpenAIEmbeddingModel`（任意兼容 API）。批量语义匹配一次编码全量对比。加载失败静默降级。

**🔎 宽搜兜底**
逐层下钻结果不足时自动切换到全叶子节点批量匹配，避免路径依赖导致漏检。

**🧪 完整测试覆盖**
26 项压力测试 + 12 项边界值测试（批量写入、事务回滚、SQL 注入防护、衰减准确性、节点上限淘汰等）。

---

## 语义模型

记忆树默认使用轻量级的关键词匹配（零依赖，开箱即用）。可选集成第三方语义模型以提升理解能力：

| 属性 | 详情 |
|------|------|
| 模型名称 | BAAI/bge-small-zh-v1.5 |
| 开发者 | 智源研究院 (Beijing Academy of Artificial Intelligence, BAAI) |
| 许可协议 | MIT License |
| 用途 | 中文语义相似度匹配 |
| 配置方式 | `pip install sentence-transformers` + 设置环境变量（详见安装章节） |

> ⚡ **强烈建议配置嵌入模型**。默认关键词匹配在"深度思考报错"这类跨语言/跨表述的查询中精度仅 0.048，启用后提升至 0.495（实测提升 10 倍）。不配也能用全部功能，只是检索精度受限。

**模型说明**：本项目语义匹配功能可选集成该模型。模型的版权归原作者（智源研究院）所有，我们根据其 MIT 许可协议进行使用和分发。

**伦理声明**：我们意识到 AI 模型可能存在偏见。本项目仅将该模型作为可选技术组件，用户需自行启用并评估其在特定应用场景下的公平性、可靠性与安全性。

---

## 与原装记忆系统的对比

| 维度 | Hermes 原装记忆 | 记忆树 MemoryTree |
|------|:---:|:---:|
| 结构 | 扁平线性列表 | 多叉树，话题层级可追溯 |
| 存储方式 | MEMORY.md 文本 | mindmap.db (SQLite) + 自动生成的 MEMORY.md 索引 |
| 生命周期 | 永存，只增不减 | 分数制：1–20 短期 / 21–40 长期 / 41+ 永久 |
| 检索方式 | 全文扫描，全量注入上下文 | 逐层下钻 + 索引摘要注入，大幅节省 Token |
| 资源占用 | 线性增长，无清理机制 | 上限 10,000 非核心节点，自动衰减清理 |
| 写入性能 | 追加式文本，极快 | SQLite 事务写入，500 节点约 200 条/秒 |
| 检索性能 | 全文扫描，记忆越多越慢 | 逐层下钻，冷加载亚毫秒级 |
| 数据安全 | 极简文本，几乎不损坏 | SQLite 事务原子性，崩溃自动回滚 |
| 维护成本 | 需要手工整理 | 全自动新陈代谢，零维护 |
| CRUD | add / replace / remove | add / search / replace / remove / recover + sync |
| 兼容性 | Hermes 内置 | Skill 形式加载，与原生 tool 共存 |
| 扩展性 | 无 | 可插拔语义模型接口 |

---

## 检索实测

查询："在会话过程中深度思考导致报错的问题" | 存储 9 条记忆 (2 条目标)

```
                     关键词匹配    BGE 嵌入(本项目)
thinking mode→HTTP400   0.048        0.495 ✅
深度思考→think:false    0.145        0.487 ✅
麻辣香锅                0.037        0.262

原系统:  1.6ms | 9条全返回 | 目标混在 Top2
记忆树:  712ms | 3条精准返回 | 2/2 目标命中
```

> 关键词分数均 < 0.20 阈值，无法命中。BGE 嵌入将语义相似度提升 10 倍。

---

## 原生工具 API

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `memory_tree_add` | 添加记忆，自动语义分类 | `content` |
| `memory_tree_search` | 逐层下钻检索 | `query` |
| `memory_tree_replace` | 按内容子串查找并替换 | `search_text`, `new_content` |
| `memory_tree_remove` | 删除记忆（含子树，软删除可恢复） | `search_text`, `force` |
| `memory_tree_sync` | 从 MEMORY.md 增量导入原生条目 | 无 |
| `memory_tree_recover` | 恢复被软删除的记忆 | `search_text` |

---

## 安装

**环境要求**：Hermes Agent 环境 + Python 3.8+  
**默认依赖**：零。纯标准库（`sqlite3`, `json`, `difflib`），克隆即用。

> 💡 不装任何额外依赖就能跑全部功能——只是检索用关键词匹配。装 BGE 嵌入后精度提升约 10 倍。

**可选：激活 BGE 语义嵌入（推荐）**：
```bash
pip install sentence-transformers
export MEMORYTREE_EMBEDDING_MODEL=local
# 首次自动下载模型 (~90MB)，之后纯本地运行
# 若网络受限，可手动下载模型文件到 ~/.cache/hermes/embeddings/BAAI/bge-small-zh-v1.5/
```

安装到 Hermes：
```bash
git clone <你的仓库地址>
cd mindmap-memory
bash install.sh
# 重启 Hermes，然后 /mindmap-memory 加载技能
```

---

## 使用

```bash
cd ~/.hermes/skills/custom/mindmap-memory

# 添加记忆
python3 mindmap_memory.py add "你需要记住的内容"

# 检索记忆
python3 mindmap_memory.py search "关键词"

# 替换记忆
python3 mindmap_memory.py replace "旧内容" "新内容"

# 删除记忆
python3 mindmap_memory.py remove "搜索文本"

# 恢复已删除的记忆
python3 mindmap_memory.py recover
python3 mindmap_memory.py recover "关键词"

# 从 MEMORY.md 同步原生记忆
python3 mindmap_memory.py sync

# 迁移旧 MEMORY.md 到记忆树
python3 mindmap_memory.py migrate

# 查看整棵记忆树
python3 mindmap_memory.py recall

# 记忆守护：用嵌入模型重分类当天记忆
python3 mindmap_memory.py consolidate

# 统计信息
python3 mindmap_memory.py stats

# 标记核心记忆
python3 mindmap_memory.py core <节点ID>

# 手动触发衰减（通常不需要）
python3 mindmap_memory.py decay
```

---

## 文件结构

```
mindmap-memory/
├── mindmap_memory.py      # 核心引擎 (2407 行)
├── SKILL.md               # Hermes Skill 描述
├── README.md              # 项目文档
├── LICENSE                # MIT 开源许可证
├── CONTRIBUTING.md        # 贡献指南
├── SECURITY.md            # 安全策略
├── install.sh             # 一键安装脚本
├── .gitignore
├── demo_mindmap.db        # 演示用种子数据库
├── .github/
│   ├── workflows/test.yml # CI 自动测试 (Python 3.9-3.12)
│   ├── ISSUE_TEMPLATE.md  # Bug 报告模板
│   └── PULL_REQUEST_TEMPLATE.md  # PR 模板
├── scripts/
│   ├── decay_worker.py    # 定时衰减工作脚本
│   ├── migrate.py         # 旧记忆迁移工具
│   ├── sync_native.py     # 原生记忆同步便捷脚本
│   ├── seed_demo.py       # 演示数据生成脚本
│   ├── stress_test.py     # 26 项压力测试
│   ├── edge_tests.py      # 12 项边界值测试
│   └── pre_release_tests.py  # 18 项发布前补充测试
└── tools/
    └── memory_tree_tool.py   # Hermes 原生工具注册

~/.hermes/memories/
├── mindmap.db             # SQLite 记忆数据库
├── mindmap.json.migrated  # JSON→SQLite 迁移后的备份
├── MEMORY.md              # 自动生成的索引（替换旧扁平格式）
└── decay_log/             # 遗忘日志（可后悔恢复）
```

---

## 设计决策

| 决策 | 取值 | 原因 |
|------|------|------|
| 树深度上限 | 6 层 | 超过 6 层的拆解极少被独立引用 |
| 分数区间 | 1–20 短期 / 21–40 长期 / 41+ 永久 | 约 40 周连续引用可升至永久 |
| 衰减周期 | 每周一次 | 太频繁浪费，太稀疏迟钝 |
| 加分规则 | 仅匹配节点 +1 | 避免全链加分导致遗忘机制失效 |
| 非核心节点上限 | 10,000 | 日均 3 条约需 9 年填满，衰减下更早稳态 |
| 语义匹配阈值 | 0.20 | 对短查询友好 |
| 存储后端 | SQLite | 替代单文件 JSON，事务安全 |

---

## 已知局限

**嵌入模型需要额外下载**：默认使用纯关键词匹配（零依赖）。BGE 嵌入模型首次使用需 `pip install sentence-transformers` + 下载约 90MB 模型文件。

**逐层检索存在路径依赖**：如果一条记忆被放在与查询词无关的话题路径下，常规下钻可能错过。已内置宽搜兜底：当下钻结果不足时自动批量匹配所有叶子节点，此问题已大幅缓解。

**偶尔遗忘潜在有用的信息**：长时间未引用的技术笔记可能被衰减删除。两条防线：(1) 标记为核心记忆即可永久保护；(2) 删除是软删除，可用 `recover` 命令随时恢复。并非真正丢失。

---

## 测试

```bash
# 26 项核心压力测试
python3 scripts/stress_test.py

# 12 项边界值测试
python3 scripts/edge_tests.py

# 18 项发布前补充测试（事务回滚、错误处理、防抖）
python3 scripts/pre_release_tests.py
```

测试覆盖：批量写入、层级结构、检索准确性与性能、访问加分、核心保护、衰减准确性、节点稳定性、语义匹配、深度限制、节点上限淘汰、数据库损坏恢复、迁移幂等、事务回滚、异常输入处理。

---

## 开源许可

MIT License — 完全开放，欢迎修改、使用和贡献。

---

## 致谢

本项目语义匹配功能可选集成 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) 模型，该模型由**智源研究院 (Beijing Academy of Artificial Intelligence, BAAI)** 开发并采用 MIT 协议开源，在此表示衷心感谢。
