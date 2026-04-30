# 更新日志 / Changelog

## v1.2.2 (2026-04-30)

**开源标配**

- 新增 `LICENSE`（MIT）
- 新增 `CONTRIBUTING.md` 贡献指南
- 新增 `SECURITY.md` 安全策略
- 新增 `.github/ISSUE_TEMPLATE.md` Bug 报告模板
- 新增 `.github/PULL_REQUEST_TEMPLATE.md` PR 模板
- 新增 `.github/workflows/test.yml` GitHub Actions CI（Python 3.9-3.12，自动跑全部测试）
- 新增 `install.sh` 一键安装脚本（自动部署 skill + 原生工具）
- 新增 `scripts/pre_release_tests.py` 18 项发布前补充测试
- `.gitignore` 增强：新增 `*.db` `decay_log/` `.env` 隐私保护
- `README.md` 顶部加英文简介 + CI / License 徽章

## v1.2.1 (2026-04-30)

**关键修复**

- 修复 `deleted` 标志未持久化到 SQLite 的 bug。
  之前 `_remove_node_cascade` 只在内存中标记软删除，`save()` 不写 `deleted`/`deleted_at` 列，
  导致 `remove`/`decay` 后下次 `load()` 节点死而复生，`recover` 永远找不到可恢复节点。
  修复：SQLite schema 加列，`save()`/`load()` 读写，旧 DB 自动 `ALTER TABLE` 兼容。
- 修复 `_log_decay` 的 `actually_removed` 判断逻辑（`n.id not in self.nodes` → `n.deleted`），
  遗忘日志从空恢复为实际记录。
- 修复原生工具 `toolset` 名冲突：`"memory"` → `"memorytree"`，避开 `toolsets.py` 静态定义。
- `memory_tree_tool.py` 补全 `memory_tree_recover` 工具注册。

**新增**

- `recover` CLI 命令：列出 + 恢复软删除记忆
- `consolidate` CLI 命令：记忆守护，用嵌入模型重分类当天记忆
- `remove` 改为软删除（`deleted=True`），可通过 `recover` 恢复

**文档**

- `SKILL.md` / `README.md` 补全 `recover` `consolidate` 命令列表
- `README.md` 行数 2057 → 2407

## v1.2.0 (2026-04-29)

**原生工具 API**

- 注册为 Hermes 一等公民工具：`memory_tree_add` `search` `sync` `replace` `remove` `recover`
- 不再依赖 `terminal()` 执行脚本，直接函数调用，延迟降至毫秒级
- `memory_tree_tool.py` 自动发现，Hermes 重启生效

**语义引擎**

- 可插拔语义模型接口：`KeywordModel`（默认零依赖）、`LocalEmbeddingModel`（BGE 本地推理）、
  `OpenAIEmbeddingModel`（兼容任意 API）
- `batch_similarity` 批量余弦相似度匹配
- 宽搜兜底：逐层下钻不足时自动全叶子节点批量匹配
- 健康检查：嵌入模型加载失败静默降级为关键词匹配

**测试**

- `scripts/stress_test.py` 26 项压力测试（批量写入、衰减准确、节点上限淘汰、SQL 注入防护等）
- `scripts/edge_tests.py` 12 项边界值测试（事务回滚、异常输入、深层嵌套、损坏恢复等）

**其他**

- 守护同步：每次写 `MEMORY.md` 前自动扫描原生 `memory` 工具的新增条目并纳入
- 自动触发规则：对话中提及"记住/别忘了/查一下记忆"等关键词时静默后台执行

## v1.1.0 (2026-04-28)

**SQLite 迁移**

- 存储后端从单文件 JSON 升级为 SQLite（`mindmap.db`）
- 事务保证原子性：写入中途崩溃自动回滚，数据库不损坏
- 首次运行自动从旧 `mindmap.json` 迁移，旧文件备份为 `.json.migrated`
- 冷启动检索亚毫秒级

**遗忘衰减机制**

- 每周自动衰减扫描：7 天未访问 -1 分，分数归零自动删除
- 核心记忆保护：`is_core=true` 永不被衰减删除，最低保留 1 分
- 核心子节点防株连：父节点被删时核心子节点自动提升为根节点

**节点上限**

- 非核心节点上限 10,000，超出时淘汰最低分节点
- 遗忘日志：删除的内容写入 `decay_log/YYYY-MM-DD.json`，可后悔恢复

**其他**

- 层级化记忆树结构（最多 6 层深度）
- 智能话题提取 + 自动分类
- 逐层下钻检索（分数优先 + 最近访问优先）
- `recall` 完整记忆树查看
- `core` 核心记忆标记切换
- `stats` 统计信息
- `migrate` 旧 MEMORY.md 迁移工具

## v1.0.0 (2026-04-27)

**首次发布**

- 将 Hermes 扁平记忆升级为层级化记忆树结构
- 基于 JSON 文件存储
- 核心命令：`add` `search` `replace` `remove`
- 基本关键词匹配检索
- Hermes Skill 形式加载
