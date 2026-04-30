# 贡献指南 / Contributing

感谢你的关注！以下是参与本项目的方式。

## 运行测试

```bash
# 完整测试套件（共 67 项）
python3 scripts/stress_test.py       # 26 项压力测试
python3 scripts/edge_tests.py        # 12 项边界值测试
python3 scripts/pre_release_tests.py # 18 项发布前补充测试
```

## 提交 PR 的流程

1. Fork 本仓库
2. 创建分支：`git checkout -b fix/xxx` 或 `feat/xxx`
3. 写代码 + 跑全部测试
4. 如果改了功能，同步更新 README.md 和 SKILL.md
5. 提交 PR，描述清楚改了什么、为什么改

## 代码风格

- Python 3.8+，标准库优先
- 函数和类写 docstring（中文即可）
- 用 `patch` 工具编辑文件，不要用 execute_code 的 read_file/write_file（会插入行号）

## 文档同步

本项目有三个文档需要保持一致：`mindmap_memory.py`（代码）、`README.md`（用户文档）、`SKILL.md`（AI 技能描述）。功能改动后三者必须同步更新。
