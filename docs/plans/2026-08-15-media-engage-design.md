# 首页图片 CMS + 互动统计设计

**日期：** 2026-08-15  
**状态：** 已批准（头脑风暴 §1–§4）  
**仓库：** `fries-global-api`（主实现）+ `fries-global-website`（读图、埋点、Admin UI）  
**前置：** 分支 `cursor/site-user-login`（站点用户登录已落地；本设计不回退文件型用户存储）

## 1. 背景与目标

官网首页各区域图片目前写死在 `public/`。需要：

1. **管理员在后台上传/替换各区域图片**，官网生效并可恢复默认。
2. **了解用户在各区域停留多久、喜欢点哪里**（前端埋点 → API → Admin 汇总）。

站点用户登录（弹窗、资料、后台用户管理）已由补丁落地，不在本期重做。

### 目标

- SQLite 存图片槽位元数据；`media/site/` 存文件
- 公开读图 + Admin CRUD
- `POST /api/engage` 接收区块停留与点击；Admin 查看排行
- 与现有 visit/security 文件分析、站点用户会话隔离共存

### 非目标

- 像素级热力图、第三方分析（GA/Plausible）
- 把管理员/站点用户迁到 SQLite
- 改营销页视觉 class / SCSS 规范
- 分类 sprite 拆成单帧编辑（本期整图替换）

## 2. 总体架构

```
浏览器
  ├─ 营销页：读公开图片配置 + 埋点上报 engage
  └─ Admin：管图 / 看互动统计（需 fries_admin_session）

fries-global-website (:3000)
  rewrite → fries-global-api (:3001)

fries-global-api
  ├─ authx（管理员 + 站点用户，.private JSON）— 已有
  ├─ analytics / forensics（访问与安全，文件）— 已有
  ├─ apps/media/   — 新增：SQLite 槽位 + media/site/
  └─ apps/engage/  — 新增：停留/点击事件 + 汇总
```

| 存储 | 用途 |
|------|------|
| `.private/site-users.json` 等 | 站点用户（保持） |
| `.private/` 分析文件 | visit / presence / security（保持） |
| SQLite | 图片槽位元数据、engage 事件 |
| `media/site/` | 自定义首页图（gitignore，部署需持久化） |

## 3. 图片槽位与 API

### 槽位

| slotKey | 用途 | 默认回退 |
|---------|------|----------|
| `hero.backdrop` | Hero 背景 | `/hero-port-global.png` |
| `brand.symbol` | 品牌标 | `/brand-symbol.png` |
| `categories.mascot_sheet` | 分类精灵图 | `/category-mascots.png` |
| `services.case_a` | 服务案例 A | `/case-industrial-tech.png` |
| `services.case_b` | 服务案例 B | `/case-manufacturing.png` |
| `cta.visual` | CTA 配图（可选） | 无则组件不强制显示 |

无自定义图时 Web 使用 `public/` 默认路径，页面不空白。

### 模型

`SiteImageSlot`：`slot_key`（唯一）、`file_path`、`content_type`、`width`/`height`（可选）、`updated_at`、`updated_by`。

文件路径约定：`media/site/<slot_key_with_underscores>.<ext>`。

### 接口

| 路径 | 方法 | 鉴权 | 行为 |
|------|------|------|------|
| `/api/site-images` | GET | 公开 | `{ slots: { [slotKey]: { url, updatedAt } } }` |
| `/api/site-images/<slotKey>` | GET | 公开 | 返回图片字节；缺失 404 |
| `/admin/api/site-images` | GET | Admin | 列表含说明与是否已自定义 |
| `/admin/api/site-images/<slotKey>` | POST | Admin | multipart 上传替换 |
| `/admin/api/site-images/<slotKey>/reset` | POST | Admin | 删除自定义，恢复默认 |

### 上传约束

- PNG / JPEG / WebP（魔数校验）
- 体积 ≤ **2MB**
- 边长 ≤ **4096**（读头，不整图解码）
- Admin Cookie + 同源校验；不全局放大 `DATA_UPLOAD_MAX_MEMORY_SIZE` 误伤 ingest

## 4. 互动统计（停留 + 点击）

### 区块枚举 `sectionId`

`hero` | `categories` | `services` | `process` | `why-us` | `global-network` | `cta` | `footer` | `nav`

### Web 采集

| 事件 | 触发 | 字段 |
|------|------|------|
| `section_view` | 进入视口 ≥50% | `sectionId`, `enteredAt` |
| `section_leave` | 离开视口 / 隐藏 / 卸载 | `sectionId`, `durationMs`（单次封顶 30min） |
| `click` | 主内容点击委托 | `sectionId`, `targetId`, 可选截断 `href` |

批量队列 + `sendBeacon` / keepalive；约 5–10s 或页面隐藏时冲刷。不采集坐标热力与表单内容。

### 接口

| 路径 | 方法 | 鉴权 | 行为 |
|------|------|------|------|
| `/api/engage` | POST | 公开（限流） | `{ events: [...] }`；校验后写库；**204** |
| `/admin/api/engagement` | GET | Admin | 汇总 JSON |

### 存储与保留

`EngageEvent`：`at`, `kind`, `section_id`, `target_id`, `duration_ms`, `path`, `client_key`（粗粒度）。  
滚动保留约 **90 天**。

### 汇总形状

```json
{
  "generatedAt": "...",
  "window": "24h",
  "sections": [
    { "sectionId": "hero", "views": 120, "avgDwellMs": 8400, "totalDwellMs": 1008000 }
  ],
  "topClicks": [
    { "sectionId": "cta", "targetId": "primary-cta", "count": 45 }
  ]
}
```

与现有 `/admin/api/summary`（访问/安全）并列，不替换。

## 5. 错误处理与安全

| 场景 | 码 |
|------|-----|
| engage 非法 / 缺字段 | 400（整批拒绝） |
| engage 体 > 64KB | 413 |
| 图片违规 | 400 / 413（`invalid` / `too-large`） |
| Admin 无会话 | 401 |
| Admin 跨站 | 403 |
| 公开缺图 | 404（Web 回退默认） |
| engage 限流 | 429 + `Retry-After` |

- 站点用户 Cookie **不能**访问 media/engagement 管理写读接口
- CORS 保持默认同源反代

## 6. 测试与里程碑

### 测试（pytest，TDD）

- media：上传 → 公开 GET → reset；拒超大/假图；无 Cookie 401
- engage：合法 204；非法 section 400；汇总聚合；用户 Cookie 不可读 Admin engagement
- 回归：站点用户与现有 analytics 用例保持绿色

### 里程碑

1. API：SQLite 启用 + `apps/media`
2. API：`apps/engage` 写入与汇总
3. Web：首页读槽位 + Admin 图片页
4. Web：埋点 + Admin 互动面板
5. 双仓 README / 联调验收

## 7. 成功标准

- Admin 可为各槽位上传/重置图片，官网显示自定义或默认
- Admin 可见各区停留与点击排行
- 现有登录、访问统计、取证行为不回归
- `media/` 与密钥不进 git
