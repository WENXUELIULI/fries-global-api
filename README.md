# fries-global-api

薯条出海官网后端 API（**Django + Django REST framework**）。默认监听 **3001** 端口。

前端仓库（`fries-global-website`）在开发与生产中会将同源的 `/api/*`、`/admin/api/*` 转发到本服务。

> Hono/Node 实现已归档在 Git tag `legacy-hono`，可随时检出回滚。

## 本地开发（双仓库）

请**先启动本 API**，再启动 Web：

```powershell
cd E:\Project\fries-global-api
py -3 -m venv .venv          # 仅首次
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\.venv\Scripts\python scripts\init_admin.py   # 仅首次：生成 .private/ 与 ADMIN_ACCESS.txt
.\.venv\Scripts\python manage.py runserver 127.0.0.1:3001
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

## 测试（TDD）

本仓采用 **先写失败测试 → 再写最小实现**。测试框架为 pytest + pytest-django：

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m pytest -q --lf   # 只重跑上次失败
```

新增或修改接口/存储逻辑时，先在 `tests/` 写用例并看到失败，再改 `apps/` 或 `config/`。

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
| `/api/auth/login` | POST | 站点登录；管理员账号返回 `{ role: admin, next: /admin }` |
| `/api/auth/logout` | POST | 站点登出 |
| `/api/auth/register` | POST | 公开注册（限流、蜜罐；可被后台关闭） |
| `/api/auth/session` | GET | 站点会话与资料（200 JSON 或 401） |
| `/api/auth/settings` | GET | `{ registrationEnabled }` |
| `/api/auth/profile` | GET / POST | 读取或更新个人资料（头像 jpeg/png/webp ≤256KB 且边长 ≤1024px；姓名/密码/手机/邮箱/地址） |
| `/api/auth/avatar/<id>` | GET | 用户头像 |
| `/admin/api/users` | GET / POST | 列出或创建站点用户（需管理员会话） |
| `/admin/api/users/settings` | POST | 开启或禁止公开注册 |
| `/admin/api/users/<id>` | POST | 更新站点用户（启用/密码/备注） |
| `/admin/api/users/<id>/delete` | POST | 删除站点用户 |

## 管理员鉴权（经 Web 反向代理时）

登录/登出使用同源校验：`Origin` 的主机名须与 `x-forwarded-host` 或 `host` 一致。  
当 Next.js 将管理端 API 转发到本服务时，Web 层必须转发**对外主机名**（例如 `127.0.0.1:3000`）到 `host` / `x-forwarded-host`，而不是内部 API 主机（`127.0.0.1:3001`）。

管理员会话 Cookie：`fries_admin_session`（HttpOnly，SameSite=Strict，Path=/admin，约 4 小时）。  
站点用户会话 Cookie：`fries_user_session`（HttpOnly，SameSite=Strict，Path=/，约 4 小时）。官网可公开注册，同一来源 10 分钟内不能重复注册；管理员可在后台关闭公开注册。

## 密钥与存储

全部凭据与加密密钥存放在 `.private/`（已 gitignore）。业务数据不入库，仅用 SQLite 满足 Django 默认配置。  
切勿将 `ADMIN_ACCESS.txt` 或 `.private/` 提交到公开的 Web 仓库。

可选环境变量见 `.env.example`：`DJANGO_SECRET_KEY`、`DJANGO_DEBUG`、`DJANGO_ALLOWED_HOSTS`。

## 生产启动（反向代理后）

本地开发继续用 `runserver`。部署到服务器时关闭 DEBUG，并设置密钥与主机：

```powershell
# Windows（waitress）
$env:DJANGO_DEBUG="false"
$env:DJANGO_SECRET_KEY="请换成足够长的随机串"
$env:DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost,your.domain"
.\.venv\Scripts\python -m waitress --listen=127.0.0.1:3001 config.wsgi:application
```

```bash
# Linux（gunicorn）
export DJANGO_DEBUG=false
export DJANGO_SECRET_KEY='请换成足够长的随机串'
export DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,your.domain'
.venv/bin/gunicorn -b 127.0.0.1:3001 config.wsgi:application
```

对外仍建议由 Nginx / Caddy / Next 同源反代暴露，浏览器不直连 3001。
