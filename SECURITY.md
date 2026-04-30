# 安全策略 / Security Policy

## 报告漏洞 / Reporting a Vulnerability

如果发现安全漏洞，请**不要**提交公开 Issue。

请发送邮件至：**shy5123@vip.qq.com**

我会在 48 小时内回复，并在修复完成后公开发布安全公告。

## 支持版本 / Supported Versions

| 版本 | 支持状态 |
|------|---------|
| v1.2.x | ✅ 活跃支持 |
| v1.1.x | ❌ 不再支持 |
| v1.0.x | ❌ 不再支持 |

## 安全检查清单

- 不在代码中硬编码 API Key 或 Token
- `.gitignore` 排除 `.env`、`*.db`、`decay_log/`
- 演示数据库 `demo_mindmap.db` 不包含真实用户数据
