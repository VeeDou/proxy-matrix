# ProxyMatrix 部署指南 (Deployment Guide)

ProxyMatrix 支持两种核心分发模式与第三方边缘分发方案：

---

## 模式 A：纯本地桌面模式 (Local Desktop Mode, 零成本默认)

- **适用场景**：单机个人使用，直接配合本地 Clash Verge Rev 或 Shadowrocket 运行。
- **运行流程**：
  1. 执行 `python3 manage.py fetch` 拉取并缓存机场订阅节点；
  2. 执行 `python3 manage.py apply`（或双击 macOS 专属 `.command` 脚本）；
  3. 程序自动完成 Mihomo 内核语法校验、时间戳原子备份，并无缝写入客户端配置文件。

---

## 模式 B：自建 Docker / Caddy 模式 (标准私有分发)

- **适用场景**：多设备分发（Mac / iOS / Android / Windows），将编译好的客户端配置托管在私有 VPS。
- **安全架构**：
  - **Token 路径隔离**：配置仅挂载于 `/<TOKEN>/` 路径下，全站未授权路径一律静默响应 404；
  - **无缓存策略**：强制返回 `Cache-Control: no-store`，防止敏感凭据残留在中间网络；
  - **白名单同步**：仅同步 3 个客户端产物，严禁将整个 `dist/` 或中间数据库对外暴露。

### 部署步骤：
1. **生成 64 位防猜测 Token**：
   ```bash
   export TOKEN=$(openssl rand -hex 32)
   export DOMAIN="sub.yourdomain.com"
   ```
2. **启动 Caddy 服务**：
   ```bash
   cd deploy/docker
   cp ../caddy/Caddyfile.template ./Caddyfile
   docker compose up -d
   ```
3. **本地编译并原子同步产物**：
   ```bash
   export REMOTE_HOST="user@your-server-ip"
   export REMOTE_PATH="/var/www/profiles"
   export SECRET_SUBDIR="${TOKEN}"
   ./scripts/deploy.example.sh
   ```
4. **客户端订阅链接**：
   - Clash Verge Rev: `https://sub.yourdomain.com/<TOKEN>/Clash-Verge-Rev.yaml`
   - Shadowrocket (规则): `https://sub.yourdomain.com/<TOKEN>/shadowrocket.conf`
   - Shadowrocket (节点): `https://sub.yourdomain.com/<TOKEN>/shadowrocket.yaml`

---

## 模式 C：Cloudflare Worker 边缘分发指引 (文档预览)

如需利用 Cloudflare 边缘网络分发，请注意**安全信任边界**：
- **安全提示**：将包含机场节点密码与私有 Token 的配置文件托管到第三方云函数或边缘服务时，凭据明文将暂存于第三方平台。
- **推荐策略**：
  - 仅使用 Cloudflare Worker 作为**反向代理**透传私有 VPS，开启边缘缓存禁用 (`cache-control: no-store`)；
  - 严格校验 `URL Path` 中的 64 位 Token，未授权请求直接抛出 404；
  - 严禁在公开的 Git 代码或 Cloudflare 环境变量中硬编码机场真实订阅 URL。
