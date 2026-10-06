# ProxyMatrix 安全指南与威胁模型 (Security Policy)

ProxyMatrix 从架构设计上将**凭据安全**与**防泄露**置于最高优先级。本文档阐述项目的核心安全模型与防护机制。

---

## 1. 威胁模型与安全边界

| 威胁场景 | 防护措施 |
|---|---|
| **机场订阅 Token 外泄** | 1. 订阅文件 `profiles/*.yaml` 强制 `0600` 属主读写权限；<br>2. 目录 `profiles/` 与 `local/` 默认写入 `.gitignore`；<br>3. 全量输出日志与错误信息通过 `redact_url` 处理（仅保留 `scheme://host/***`，去除 userinfo 与 path）。 |
| **控制台未授权访问 / CSRF** | 1. 服务端严格绑定环回地址 `127.0.0.1`；<br>2. 强校验 HTTP `Host` 头（拒绝任何外部伪造域名）；<br>3. 强校验 HTTP `Origin` 头防御跨站请求伪造；<br>4. 随机 256 位 Session Token Cookie 认证。 |
| **中间网络抓包 / 嗅探** | 1. 远程分发模式下强制开启 HTTPS (Let's Encrypt / ZeroSSL)；<br>2. 响应头强制注入 `Cache-Control: no-store`，禁止公共代理或客户端缓存凭据。 |
| **未授权扫描与探测** | 1. 分发服务要求 64 位不可猜测 Token 路径隔离 (`/<TOKEN>/`)；<br>2. 根路径及所有未匹配请求一律静默响应 404，不暴露服务特征。 |
| **Git 误提交敏感信息** | 1. 双层泄露审计扫描器 (`audit_leak.py`)：Tier 1 启发式通用规则 + Tier 2 外部私有黑名单；<br>2. Git pre-commit 钩子强制扫描暂存区，任何敏感命中立即阻断提交。 |

---

## 2. 敏感信息脱敏规范 (`redact_url`)

所有对外暴露或打印到终端的 URL 均必须经过 `proxymatrix.utils.redact.redact_url()` 处理：
- 格式规范：`scheme://hostname[:port]/***`
- 自动剔除：
  - 用户凭据（如 `https://user:password@host` 中的 `user:password`）；
  - 查询参数（如 `?token=...`, `?key=...`）；
  - 路径式 Token（如 `/link/<token>`, `/sub/<uuid>`, `/s/<hex>`）。

---

## 3. 提交前本地安全审计

在向公开或私有 Git 仓库推送任何代码前，必须执行：

```bash
# 扫描本地代码与 Git 元数据历史
python3 proxymatrix/utils/audit_leak.py --check-git
```

只有当终端输出 `[✓] PASS: No leaks detected.` 时方可推送。

---

## 4. 漏洞反馈

若在本项目中发现任何潜在的安全漏洞，请勿公开提交 Issue。请联系项目维护者：`veedou@users.noreply.github.com`。
