# ProxyMatrix 快速入门 (Quickstart Guide)

只需 5 分钟，即可配置并跑通多端代理矩阵。

---

## 步骤 1：初始化工作区

在项目根目录下执行：

```bash
python3 manage.py init
```

该命令会：
- 自动创建隔离目录 `profiles/`（权限 `0700`）、`dist/`、`local/` 等；
- 初始化订阅模板 `subscriptions/urls.json` 与规则模板 `rules/sites.json`（权限严格锁定 `0600`）；
- 自动为 Git 仓库启用本地提交防泄露拦截钩子（`git config core.hooksPath .githooks`）；
- 生成随机 64 位防猜测 Token 并安全保存于 `.state/deploy_token`（权限 `0600`）供远程部署使用。

---

## 步骤 2：填入机场订阅

打开 `subscriptions/urls.json`（或不想被 Git 跟踪的 `local/urls.json`）：

```json
{
  "主力机场": "https://sub.airport1.com/api/v1/client/subscribe?token=EXAMPLE_TOKEN_AAA",
  "备用机场": "https://sub.airport2.com/link/EXAMPLE_TOKEN_BBB?clash=1"
}
```

支持常见的 Clash YAML 格式订阅与 Base64 节点链接（SS、VMess、Trojan、VLESS、Hysteria2）。

---

## 步骤 3：拉取并构建

```bash
# 1. 拉取节点并缓存到本地 profiles/ (严格权限 0600)
python3 manage.py fetch

# 2. 编译各客户端配置文件
python3 manage.py build
```

编译输出位于 `dist/` 目录：
- `dist/Clash-Verge-Rev.yaml`：Clash Verge Rev 完整订阅配置
- `dist/shadowrocket.conf`：Shadowrocket INI 路由与规则分流文件
- `dist/shadowrocket.yaml`：Shadowrocket 节点代理池定义
- `dist/lossy_conversion_report.md`：规则有损转换与安全保障报告

---

## 步骤 4：客户端导入

### 方案 A：macOS 本地 Clash Verge Rev 一键应用
```bash
python3 manage.py apply
```
（或直接双击根目录下的 `更新配置.command`）

程序会自动备份旧配置、校验语法并原子替换。

### 方案 B：iOS Shadowrocket 导入
将 `dist/` 下的 `shadowrocket.conf` 与 `shadowrocket.yaml` 部署至私有服务器（详见 [deploy/README.md](../deploy/README.md)）或通过局域网隔空投送至手机：
1. 在 Shadowrocket「配置」中添加远程配置链接；
2. 在「服务器节点」中添加节点订阅链接；
3. 全局路由选择「配置」模式即可。
