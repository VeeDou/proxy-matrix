# ProxyMatrix 跨客户端规则转换与有损降级规范

由于 Clash Meta (Mihomo) 与 iOS Shadowrocket (小火箭) 采用不同的底层分流架构与规则集规范，ProxyMatrix 实现了双端规则编译器与严格的有损转换防护机制。

---

## 1. 核心差异矩阵

| 规则能力 / 特性 | Clash Meta (Mihomo) | Shadowrocket (小火箭) | ProxyMatrix 转换策略 |
|---|---|---|---|
| **GEOSITE 规则集** | 原生支持（集成路由数据库） | 不支持 | 常见域名展开为 `DOMAIN-SUFFIX`；丢弃项在报告中明确公示 |
| **国内域名直连 (cn)** | `GEOSITE,cn,DIRECT` | 不支持 GEOSITE | 追加 `DOMAIN-SUFFIX,cn,DIRECT` 并结合去 `no-resolve` 的 `GEOIP,CN` 联合承接 |
| **私有网段 (private)** | `GEOIP,private,DIRECT,no-resolve` | 不支持 `GEOIP,private` | 自动展开为 8 段具体的 RFC 1918 / 本地 IPv4/IPv6 `IP-CIDR` 规则 |
| **未命中规则终点** | `MATCH,@proxy` | `FINAL,PROXY` | 自动将逻辑组 `@proxy` 映射为 Shadowrocket 的 `FINAL` 目标组 |
| **UDP 不支持行为** | 策略组原生降级支持 | `udp-policy-not-supported-behaviour` | 严格设置为 `REJECT`，防止 WebRTC 与真实 IP 外泄 |

---

## 2. 关键防护机制详解

### 2.1 淘宝/京东等国内流量防误走海外代理
在 Shadowrocket 中，若 `GEOIP` 规则带有 `no-resolve` 标记，小火箭不会对域名请求触发本地 DNS 解析，导致不在预设清单中的海量国内网站（如 `taobao.com`, `jd.com`, `bilibili.com`）直接落入 `FINAL,PROXY` 并经过海外节点中转，引发严重的延迟与风控。

**ProxyMatrix 解决方案**：
1. 在规则链前部追加 `DOMAIN-SUFFIX,cn,DIRECT` 优先直连；
2. 编译器编译 `GEOIP,CN` 时**主动剥离 `no-resolve` 参数**，允许 Shadowrocket 调用本地 DNS（如 `223.5.5.5`）解析域名并匹配中国大陆 IP 网段，确保国内流量平滑直连。

### 2.2 私有与局域网网段展开
Shadowrocket 规则引擎无法识别 `GEOIP,private` 标签。ProxyMatrix 将其全量展开为：
- `IP-CIDR,127.0.0.0/8,DIRECT,no-resolve`
- `IP-CIDR,172.16.0.0/12,DIRECT,no-resolve`
- `IP-CIDR,192.168.0.0/16,DIRECT,no-resolve`
- `IP-CIDR,10.0.0.0/8,DIRECT,no-resolve`
- `IP-CIDR,100.64.0.0/10,DIRECT,no-resolve`
- `IP-CIDR6,fc00::/7,DIRECT,no-resolve`
- `IP-CIDR6,fe80::/10,DIRECT,no-resolve`
- `IP-CIDR6,::1/128,DIRECT,no-resolve`

---

## 3. 自动化有损转换报告 (`dist/lossy_conversion_report.md`)

每次执行 `python3 manage.py build` 时，系统均会自动比对输入与输出，并生成 Markdown 格式的审计报告，详列：
- 规则丢弃清单与安全原因；
- 规则转换映射清单与转换理由；
- 平台安全策略设定差异（如 DNS Fallback Filter、DoH 预解析策略等）。
