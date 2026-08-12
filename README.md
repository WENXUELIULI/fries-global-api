# fries-global-api

薯条出海官网后端 API（Hono）。默认监听 **3001** 端口（可用环境变量 `PORT` 覆盖）。

前端仓库（`fries-global-website`）在开发与生产中会将同源的 `/api/*`、`/admin/api/*` 转发到本服务。

## 本地开发（双仓库）

请**先启动本 API**，再启动 Web：

```powershell
cd E:\Project\fries-global-api
npm install
npm run admin:init   # 仅首次：生成 .private/admin-auth.json 与 ADMIN_ACCESS.txt
npm run dev          # http://127.0.0.1:3001
```

再在另一个终端：

```powershell
cd E:\Project\fries-global-website
npm install
npm run dev          # http://127.0.0.1:3000
```

## 健康检查

```powershell
curl http://127.0.0.1:3001/health
# {"ok":true}
```

## 路由

| 路径 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 存活探测 |
| `/api/presence` | GET | 访客在线心跳（204） |
| `/api/ingest` | POST | 分析事件上报 |
| `/admin/api/login` | POST | 管理员登录（303 + 会话 Cookie） |
| `/admin/api/logout` | POST | 管理员登出（303） |
| `/admin/api/session` | GET | 会话校验（204 或 401） |
| `/admin/api/summary` | GET | 仪表盘汇总 JSON |
| `/admin/api/export` | GET | CSV 导出 |
| `/admin/api/forensics` | GET | 取证证据下载 |

## 管理员鉴权（经 Web 反向代理时）

登录/登出使用 `validSameOriginRequest`：`Origin` 的主机名须与 `x-forwarded-host` 或 `host` 一致。  
当 Next.js 将管理端 API 转发到本服务时，Web 层必须转发**对外主机名**（例如 `127.0.0.1:3000`）到 `host` / `x-forwarded-host`，而不是内部 API 主机（`127.0.0.1:3001`）。

## 密钥

全部凭据与加密密钥存放在 `.private/`（已 gitignore）。  
切勿将 `ADMIN_ACCESS.txt` 或 `.private/` 提交到公开的 Web 仓库。
