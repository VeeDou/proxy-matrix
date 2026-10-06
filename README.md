# ProxyMatrix (代理矩阵)

> **多端代理配置编排与跨客户端规则编译器**
> 
> 纯 Python 3 标准库实现（零外部依赖），支持 Python 3.9 ~ 3.13。一键生成 Clash Verge Rev (Mihomo) 与 Shadowrocket (小火箭) 生产级路由与策略组配置。

[![CI](https://github.com/VeeDou/proxy-matrix/actions/workflows/ci.yml/badge.svg)](https://github.com/VeeDou/proxy-matrix/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 核心特性

- **纯标准库实现 (Zero Dependencies)**：无需 `pip install`，100% 依托 Python 3 标准库，杜绝第三方依赖链安全风险。
- **双端规则协同编译 (Dual-Client Compilers)**：
  - **Clash Verge Rev (Mihomo 内核)**：支持现代化 GEOSITE/GEOIP 规则集、DoH 安全加密 DNS、子策略组按地区智能正则分流；
  - **Shadowrocket (iOS 小火箭)**：支持标准 INI 配置、RFC 1918 私有地址自动展开、国内直连保护与有损转换报告。
- **本地优先与极度隐私 (Local-First & Privacy First)**：
  - 机场订阅凭据与私有节点仅保存在本地隔离目录 `profiles/` 与 `local/`（权限严格锁定 `0700` / `0600`）；
  - 全流程错误与 URL 强脱敏（`redact_url` 仅保留 `scheme://host/***`，自动抹除账号密码与路径式 Token）；
  - 内置两级泄露审计器 (`audit_leak.py`) 与 Git 提交钩子。
- **开箱即用图形控制台 (Web Console)**：
  - 内置基于 Python `http.server` 的环回管理控制台（默认 `http://127.0.0.1:8787`）；
  - 严格的主机头检查、Origin CSRF 防护与 Session Token 认证。
- **灵活分发模式 (Flexible Deployment)**：
  - **模式 A (本地桌面)**：macOS 双击专属 `.command` 脚本即可热更新或导入配置；跨平台命令行 `python3 manage.py apply`；
  - **模式 B (自建 Docker + Caddy)**：提供一键式 Docker Compose 与 Caddyfile 模板，支持 64 位防猜测 Token 路径隔离与静默 404；
  - **模式 C (Cloudflare Worker 边缘预览)**：详见部署文档的架构指引与信任边界说明。

---

## 架构概览

```text
               ┌──────────────────────────────────────────────┐
               │         用户订阅源与自定义分流配置           │
               │   subscriptions/urls.json / rules/sites.json │
               └──────────────────────┬───────────────────────┘
                                      │
                         [ manage.py fetch / init ]
                                      │
                                      ▼
               ┌──────────────────────────────────────────────┐
               │           节点解析与订阅隔离缓存             │
               │  SS / VMess / Trojan / VLESS / Hysteria2     │
               │  本地 profiles/*.yaml (文件权限 0600)        │
               └──────────────────────┬───────────────────────┘
                                      │
                         [ manage.py build / check ]
                                      │
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
   ┌───────────────────────────┐             ┌───────────────────────────┐
   │    Clash / Mihomo 编译器  │             │   Shadowrocket 规则编译器 │
   │   (proxymatrix.targets)   │             │   (proxymatrix.targets)   │
   ├───────────────────────────┤             ├───────────────────────────┤
   │ • 智能国家地区节点分组    │             │ • RFC 1918 私有网段展开   │
   │ • 权威 DoH 预解析分流     │             │ • 移除 no-resolve 本地解析│
   │ • GEOSITE/GEOIP 规则编译  │             │ • DOMAIN-SUFFIX,cn 直连   │
   └─────────────┬─────────────┘             └─────────────┬─────────────┘
                 │                                         │
                 ▼                                         ▼
   dist/Clash-Verge-Rev.yaml                     dist/shadowrocket.conf
                                                 dist/shadowrocket.yaml
                                                 dist/lossy_conversion_report.md
```

---

## 快速上手

### 1. 环境初始化
```bash
git clone https://github.com/VeeDou/proxy-matrix.git
cd proxy-matrix

# 初始化环境与配置模板
python3 manage.py init
```

### 2. 配置机场订阅
编辑 `subscriptions/urls.json`（或私有目录 `local/urls.json`）：
```json
{
  "主力机场": "https://sub.airport.com/api/v1/client/subscribe?token=your_token",
  "备用机场": "https://sub.backup.com/link/your_token?clash=1"
}
```

### 3. 下载订阅并构建配置
```bash
# 1. 下载节点并缓存 (单向防泄露脱敏输出)
python3 manage.py fetch

# 2. 编译所有客户端产物
python3 manage.py build

# 3. (可选) 校验内核语法并直接应用到本地 Clash Verge Rev
python3 manage.py apply
```

### 4. 启动图形化管理控制台
```bash
python3 manage.py console
```
浏览器将自动打开 `http://127.0.0.1:8787`，提供可视化节点测试、规则编辑与一键发布能力。

---

## 有损转换与多端规则兼容性说明

由于 Shadowrocket (iOS) 与 Clash Meta (Mihomo) 在规则引擎能力上的差异，ProxyMatrix 在转换过程中实施严格的安全兜底：

1. **GEOSITE 规则降级与直连兜底**：
   Shadowrocket 不支持 Mihomo 内置的 `GEOSITE` 数据库。ProxyMatrix 会将特定关键规则（如 Google、Telegram 等）转换为明确的 `DOMAIN-SUFFIX` 规则；针对国内域名（`GEOSITE,cn`），系统会自动追加 `DOMAIN-SUFFIX,cn,DIRECT`，并在随后的 `GEOIP,CN,DIRECT` 规则中**主动移除 `no-resolve`**，促使客户端调用本地 DNS 解析域名，从而彻底杜绝淘宝、京东、B站等国内流量误走海外代理。
2. **私有地址展开**：
   Shadowrocket 规则中不支持 `GEOIP,private`。编译器会自动将其展开为 RFC 1918 及本地 IPv4/IPv6 共 8 段明确的 `IP-CIDR` 规则（如 `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7` 等）。
3. **有损转换报告**：
   每次构建时，系统都会在 `dist/lossy_conversion_report.md` 生成详细的降级审计清单，供运维人员比对。

---

## 部署与分发

- **模式 A (本地单机模式)**：无需任何服务器，直接使用生成的 `dist/Clash-Verge-Rev.yaml`。
- **模式 B (自建 Docker + Caddy)**：通过受保护的 Token 子路径对外提供订阅服务，详见 [deploy/README.md](deploy/README.md)。

---

## 安全与开源规范

- **零敏感信息承诺**：仓库内置两级泄露审计器，在提交代码前自动校验：
  ```bash
  python3 proxymatrix/utils/audit_leak.py --check-git
  ```
- **权限最小化**：所有写入的配置与节点缓存默认限制为当前系统用户独占读写（`0600` / `0700`）。
- **开源许可证**：本项目采用 [MIT License](LICENSE) 开源。
