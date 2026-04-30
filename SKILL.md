---
name: mindmap-memory
description: "记忆树（MemoryTree）— 一棵会新陈代谢的记忆树"
version: 1.2.1
author: Hermes Agent + User
license: MIT
metadata:
  hermes:
    tags: [memory, mindmap, hierarchical, decay, retrieval, auto-trigger]
    homepage: https://github.com/NousResearch/hermes-agent
---

# 记忆树（MemoryTree）

## 概述

将 Hermes 的扁平记忆升级为**层级化记忆树结构**。记忆按话题组织成多叉树（最多 6 层），
上层节点存标题，叶子节点存完整内容。自带基于时间的遗忘衰减机制和核心记忆保护。

数据存储使用 **SQLite**（`mindmap.db`），首次运行自动从旧 `mindmap.json` 迁移。

## 核心特性

- **树形层级**：话题→子话题→...→叶子内容，最多 6 层
- **自动分类**：新增记忆时自动语义匹配已有话题，找不到则新建
- **智能检索**：逐层下钻，分数优先+最近访问优先
- **遗忘机制**：每周衰减扫描，7天未访问 -1 分，分数归零自动删除
- **核心保护**：可标记 `is_core=true`，永不被衰减删除
- **迁移兼容**：首次运行自动将旧 MEMORY.md 迁移为树形结构
- **遗忘日志**：删除的内容写入日志，可后悔恢复
- **自动触发**：对话中自然提及记忆关键词时静默后台执行，无需手动命令
- **守护同步**：每次写 MEMORY.md 前自动扫描原生 memory 工具的新增条目并纳入
- **原生 API**：注册为 Hermes 一等公民工具 `memory_tree_add/search/sync/replace/remove/recover`，不再依赖 terminal()
- **完整 CRUD**：支持 add / search / replace / remove，与原 memory 工具对等
- **可插拔模型**：默认关键词匹配（零依赖），可选配置 BGE 嵌入模型提升语义理解（强烈建议）
- **可选语义模型**：BAAI/bge-small-zh-v1.5（智源研究院，MIT），需手动配置
  - `LocalEmbeddingModel`：本地惰性加载，纯 CPU 推理，零 API 调用
  - `OpenAIEmbeddingModel`：兼容任意 OpenAI `/v1/embeddings` 端点（DeepSeek、SiliconFlow 等）
  - `batch_similarity`：批量余弦相似度匹配，一次编码查询向量，批量对比所有候选节点
- **宽搜兜底**：逐层下钻不足时自动切换到全叶子节点批量匹配，避免路径依赖导致漏检
- **健康检查**：嵌入模型暴露 `health_status()` 和 `healthy` 属性，加载失败静默降级为关键词匹配
- **完整测试体系**：26 项压力测试（`scripts/stress_test.py`）+ 12 项边界值测试（`scripts/edge_tests.py`），覆盖批量写入、事务回滚、衰减准确性、节点上限淘汰、SQL 注入防护等

## 分数区间

| 分数范围 | 类别     | 说明         |
|---------|---------|-------------|
| 1-20    | 短期记忆 | 容易被遗忘    |
| 21-40   | 长期记忆 | 较稳定       |
| 41+     | 永久记忆 | 极难被遗忘    |

---

## 自动触发规则（静默后台）

以下规则在此技能加载后自动生效。Agent 检测到触发词时必须后台执行对应操作，
**不打断对话、不展示命令、不向用户确认**。

### 存储触发词 → 自动 add

**触发关键词**：记住、别忘了、记一下、记着、存一下、存起来、mark、save this

**执行**：提取触发词后面的信息内容，调用原生工具 `memory_tree_add`：
```
memory_tree_add(content="<提取的内容>")
```
或后台 CLI：
```
cd ~/.hermes/skills/custom/mindmap-memory && python3 mindmap_memory.py add "<提取的内容>"
```
然后在回复中自然提一句"已记下"（一句话带过，不展开）。

### 检索触发词 → 自动 search

**触发关键词**：之前说过、上次提到、还记得、回忆一下、之前聊过、查一下记忆、你记得、之前那个、以前说过、recall

**执行**：提取检索关键词，调用原生工具 `memory_tree_search`：
```
memory_tree_search(query="<检索关键词>")
```
或后台 CLI：
```
cd ~/.hermes/skills/custom/mindmap-memory && python3 mindmap_memory.py search "<检索关键词>"
```
将检索到的记忆内容融入回复，自然引用，不说"根据记忆系统"这类元描述。

### 约束

- 静默执行：用 terminal() 后台运行，不向用户展示命令
- 自然融入：回复中不提"记忆系统""检索结果"等元描述
- 不打断：用户问别的事时优先回答问题，记忆操作独立进行
- 去重：完全相同的内容静默跳过
- 失败静默：执行失败不告知用户

---

## 手动使用方法

```bash
# 加载技能
/mindmap-memory

# CLI 命令（手动使用）
cd ~/.hermes/skills/custom/mindmap-memory
python3 mindmap_memory.py add "记住的内容"  # 添加记忆
python3 mindmap_memory.py search "查询"     # 检索记忆
python3 mindmap_memory.py replace "旧内容" "新内容"  # 替换记忆
python3 mindmap_memory.py remove "搜索文本"         # 删除记忆
python3 mindmap_memory.py recover ["关键词"]          # 恢复已删除的记忆
python3 mindmap_memory.py sync             # 从 MEMORY.md 增量导入
python3 mindmap_memory.py recall           # 查看完整记忆树
python3 mindmap_memory.py decay            # 手动触发衰减
python3 mindmap_memory.py consolidate      # 记忆守护：用嵌入模型重分类
python3 mindmap_memory.py migrate          # 迁移旧记忆
python3 mindmap_memory.py core <id>        # 切换核心记忆标记
python3 mindmap_memory.py stats            # 统计信息
```

## 原生工具 API

技能加载后，以下 Hermes 原生工具自动可用（不再需要 terminal() 执行脚本）：

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `memory_tree_add` | 添加记忆，自动分类 | `content` (string) |
| `memory_tree_search` | 检索记忆，逐层下钻 | `query` (string) |
| `memory_tree_replace` | 替换记忆内容 | `search_text`, `new_content` |
| `memory_tree_remove` | 删除记忆（含子树，软删除可恢复） | `search_text`, `force` (bool) |
| `memory_tree_sync` | 从 MEMORY.md 增量导入 | 无 |
| `memory_tree_recover` | 恢复被软删除的记忆 | `search_text` |

## 语义模型配置

默认使用**关键词匹配**（零依赖，纯 Python）。如需更好的语义理解，
可配置嵌入模型：

```bash
# 本地模型 — 推荐：零API、零延迟、零费用
export MEMORYTREE_EMBEDDING_MODEL=local:BAAI/bge-small-zh-v1.5
# 中文场景最佳：BAAI/bge-small-zh-v1.5 (100MB)
# 英文轻量：all-MiniLM-L6-v2 (80MB)

# 使用 OpenAI 嵌入（需要 API Key）
export MEMORYTREE_EMBEDDING_MODEL=openai:text-embedding-3-small
export MEMORYTREE_EMBEDDING_API_KEY=sk-your-key

# 使用第三方兼容 API（DeepSeek, SiliconFlow 等）
export MEMORYTREE_EMBEDDING_MODEL=openai:pro-embedding-2025
export MEMORYTREE_EMBEDDING_API_KEY=sk-your-key
export MEMORYTREE_EMBEDDING_API_BASE=https://api.deepseek.com/v1

# 恢复默认关键词匹配
unset MEMORYTREE_EMBEDDING_MODEL
# 或显式指定
export MEMORYTREE_EMBEDDING_MODEL=keyword
```

配置后，"买电脑" 能正确匹配到 "苹果MacBook"，解决简单关键词无法处理的同义/相关语义。

### 受限网络环境安装本地模型

当 HuggingFace 不可达时，通过 ModelScope 下载：

```bash
# 安装 sentence-transformers（需先确保 transformers<5.0, torch>=2.2）
python3.11 -m pip install sentence-transformers --break-system-packages

# 通过 ModelScope 下载 BAAI/bge-small-zh-v1.5
MODEL_DIR=~/.cache/hermes/embeddings/BAAI/bge-small-zh-v1.5
API="https://modelscope.cn/api/v1/models/BAAI/bge-small-zh-v1.5/repo?Revision=master&FilePath="
mkdir -p "$MODEL_DIR/1_Pooling" "$MODEL_DIR/2_Normalize"

for f in config.json tokenizer.json tokenizer_config.json vocab.txt \
         modules.json sentence_bert_config.json special_tokens_map.json \
         pytorch_model.bin 1_Pooling/config.json 2_Normalize/config.json; do
  curl -sL --max-time 60 -o "$MODEL_DIR/$f" "$API$f"
done

# 修复 Pooling 配置（旧版字段名兼容）
python3 -c "
import json; from pathlib import Path
p = Path('$MODEL_DIR/1_Pooling/config.json')
cfg = json.loads(p.read_text())
if 'word_embedding_dimension' in cfg:
    cfg['embedding_dimension'] = cfg.pop('word_embedding_dimension')
    p.write_text(json.dumps(cfg))
"

# 启用
export MEMORYTREE_EMBEDDING_MODEL=local:BAAI/bge-small-zh-v1.5
```

**已知坑位**：
- PyTorch 2.2 + transformers≥5.0 不兼容 → 降级 `transformers==4.44.0`
- ModelScope 的 `2_Normalize/config.json` 可能为空，创建空 `{}` 即可
- 首次加载约 1.3s，之后编码速度 100-130 it/s (CPU)

## 开发注意事项

修改 mindmap_memory.py 时的关键坑位：

1. **绝对不要用 execute_code 的 read_file/write_file 编辑 .py 文件** — 
   它们会在每行前插入行号，导致文件损坏。
   ✅ 正确做法：用 `patch` 工具直接修改。
   ❌ 错误做法：`execute_code` 里调 `read_file` + `write_file`。

2. **斜杠命令是 `/mindmap-memory` 不是 `/skill mindmap-memory`** —
   `/skill` 会被前缀匹配到 `/skills`（hub管理），然后 `mindmap-memory` 被当成子命令报 "Unknown action"。
   所有文档、CLI 帮助、注释里的引用必须用 `/mindmap-memory`。

3. **修改 MemoryTree 时自动同步更新 README.md 和 SKILL.md** —
   新增功能、修改命令、变更设计决策后，主动更新两个文档。
   用户说过"今天不再赘述"→ 以后每次改代码后自动更新文档，不用等提醒。

4. **原生工具注册位置**: `~/.hermes/hermes-agent/tools/memory_tree_tool.py`，
   通过 `registry.register()` 注册，toolset 名为 `"memorytree"`（不能叫 `"memory"`——会和 toolsets.py 静态定义冲突导致工具不可见），
   无需修改 toolsets.py。
   下次 Hermes 进程重启自动发现。

5. **GitHub 发布清单**：先隐私审计 → 创建 demo 种子脚本 → 更新 .gitignore → 确认 14 个文件（无 .db 泄露、无 API key） → git push + tag。

6. **`deleted` 标志未持久化到 SQLite（v1.2.1 已修复）** —
   修复内容：schema 加 `deleted INTEGER DEFAULT 0` 和 `deleted_at TEXT DEFAULT ''` 列；
   `load()` 读取这两个字段，`save()` 写入；旧 DB 自动 `ALTER TABLE` 补充列；
   `_log_decay` 调用处的 `actually_removed` 从 `n.id not in self.nodes` 改为 `n.deleted`。
   修复后 remove/decay 的软删除正确跨会话持久化，`recover` 可正常恢复。

## 文件结构

```
~/.hermes/memories/
├── mindmap.db            # SQLite 记忆数据库（自动从 mindmap.json 迁移）
├── mindmap.json.migrated # 旧 JSON 文件备份（迁移后重命名）
├── MEMORY.md             # 自动生成的轻量索引（替换旧扁平记忆）
├── USER.md               # 用户档案（不变）
└── decay_log/            # 遗忘日志目录
    └── YYYY-MM-DD.json   # 每日删除记录
```
