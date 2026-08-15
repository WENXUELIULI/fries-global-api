# 首页图片 CMS + 互动统计 Implementation Plan

> **For implementer:** 全程 TDD。先写失败测试 → 确认红灯 → 最小实现 → 绿灯 → 提交。  
> **设计依据：** `docs/plans/2026-08-15-media-engage-design.md`

**Goal:** 在 `fries-global-api` 落地 SQLite 首页图片槽位与 engage 停留/点击汇总；再在 `fries-global-website` 接读图、Admin 管图、埋点与互动面板。

**Architecture:** 新增 `apps/media`（槽位元数据 + `media/site/` 文件）与 `apps/engage`（事件表 + Admin 汇总）。公开 GET 读图/配置；Admin Cookie 写图；`POST /api/engage` 收批量事件。站点用户与 visit 文件分析保持不动。

**Tech Stack:** Django 5、DRF、SQLite、pytest、pytest-django；Web 侧 Next.js（后续任务）。

**工作分支：** 在 `cursor/site-user-login` 上继续（或再开 `cursor/media-engage` 基于该分支）。

---

## 文件结构（API 目标）

```
apps/media/
  models.py          # SiteImageSlot
  slots.py           # 槽位常量与默认 public 路径映射
  validators.py      # 魔数、2MB、4096 边长
  views.py
  apps.py
apps/engage/
  models.py          # EngageEvent
  sections.py        # sectionId 枚举
  views.py
  summary.py         # 24h/7d 聚合
  apps.py
config/settings.py   # INSTALLED_APPS、MEDIA_ROOT
config/urls.py
media/site/          # gitignore，运行时创建
tests/test_media_slots.py
tests/test_engage.py
```

---

### Task 1: 启用业务 SQLite 应用骨架 + MEDIA_ROOT

**Files:**
- Modify: `config/settings.py`
- Create: `apps/media/__init__.py`, `apps/media/apps.py`, `apps/media/models.py`（空模型壳可后补）
- Modify: `.gitignore`（`/media/`）
- Test: `tests/test_media_slots.py`（先写「health 仍 200」或「settings 含 MEDIA_ROOT」的轻量断言也可并入 Task 2）

**Step 1:** 在 settings 增加：

```python
INSTALLED_APPS = [
    # ...existing...
    "apps.media",
    "apps.engage",  # 可 Task 4 再加；若提前加需空 AppConfig
]
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"
```

**Step 2:** `.gitignore` 增加 `/media/`（保留目录可用 `media/site/.gitkeep` 若不忽略 keep 文件则忽略 `media/site/*` 例外 keep）。

**Step 3:** `manage.py migrate`（后续模型迁移在 Task 2）。

**Step 4:** Commit `chore: 配置 MEDIA_ROOT 与 media/engage 应用注册`

---

### Task 2: SiteImageSlot 模型 + 槽位常量（TDD）

**Files:**
- Create: `apps/media/slots.py`, `apps/media/models.py`
- Create: `apps/media/migrations/0001_*.py`
- Test: `tests/test_media_slots.py`

**Step 1: 失败测试**

```python
import pytest
from apps.media.slots import SLOT_KEYS, default_public_path

@pytest.mark.django_db
def test_slot_keys_include_hero():
    assert "hero.backdrop" in SLOT_KEYS
    assert default_public_path("hero.backdrop") == "/hero-port-global.png"
```

**Step 2:** RED → 实现 `SLOT_KEYS` 与 `default_public_path` 映射（见设计文档表）。

**Step 3:** 模型：

```python
class SiteImageSlot(models.Model):
    slot_key = models.CharField(max_length=64, unique=True)
    file_path = models.CharField(max_length=512)
    content_type = models.CharField(max_length=64)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=80, blank=True, default="")
```

**Step 4:** `makemigrations media && migrate` → GREEN → Commit `feat: 定义首页图片槽位常量与 SiteImageSlot 模型`

---

### Task 3: 图片校验器（魔数 / 体积 / 边长）

**Files:**
- Create: `apps/media/validators.py`
- Test: `tests/test_media_slots.py`

**Step 1:** 测试：合法小 PNG 通过；超过 2MB → `too-large`；非图片 → `invalid`；声明尺寸 >4096 → `too-large`（可复用 authx 头像尺寸解析思路，常量改为 `IMAGE_MAX_BYTES = 2*1024*1024`, `IMAGE_MAX_EDGE = 4096`）。

**Step 2:** RED → 实现 → GREEN → Commit `feat: 校验首页上传图类型体积与边长`

---

### Task 4: 公开与 Admin 图片 API（TDD）

**Files:**
- Create: `apps/media/views.py`
- Modify: `config/urls.py`
- Test: `tests/test_media_slots.py`

**接口行为（设计）：**

| 路径 | 期望 |
|------|------|
| `GET /api/site-images` | 200，含 slots；无自定义时 url 可为 null 或省略 |
| `POST /admin/api/site-images/hero.backdrop` 无 Cookie | 401 |
| `POST` 带 Admin Cookie + PNG | 200/204，随后 `GET /api/site-images/hero.backdrop` 200 且 Content-Type 正确 |
| `POST .../reset` | 删除记录与文件；公开 GET 404 |

**Step 1:** 写上述失败测试（复用 `isolated_private` + `create_admin_session`）。

**Step 2:** 实现 views：上传写入 `MEDIA_ROOT/site/`，Admin 同源校验可复用 `valid_same_origin_request`（若 POST multipart）。

**Step 3:** GREEN → Commit `feat: 实现首页图片公开读取与 Admin 上传重置`

---

### Task 5: EngageEvent 模型 + section 枚举

**Files:**
- Create: `apps/engage/sections.py`, `apps/engage/models.py`, migrations
- Test: `tests/test_engage.py`

**Step 1:**

```python
from apps.engage.sections import SECTION_IDS

def test_section_ids():
    assert "hero" in SECTION_IDS
    assert "cta" in SECTION_IDS
```

**Step 2:** 模型字段：`at`, `kind`, `section_id`, `target_id`, `duration_ms`, `path`, `client_key`（索引：`at`, `kind`, `section_id`）。

**Step 3:** migrate → Commit `feat: 定义 engage 区块枚举与事件模型`

---

### Task 6: POST /api/engage（TDD）

**Files:**
- Create: `apps/engage/views.py`（Ingest）
- Modify: `config/urls.py`
- Test: `tests/test_engage.py`

**Step 1:**

```python
@pytest.mark.django_db
def test_engage_accepts_batch(client):
    r = client.post(
        "/api/engage",
        data={"events": [
            {"kind": "section_leave", "sectionId": "hero", "durationMs": 1200},
            {"kind": "click", "sectionId": "cta", "targetId": "primary-cta"},
        ]},
        content_type="application/json",
    )
    assert r.status_code == 204

@pytest.mark.django_db
def test_engage_rejects_bad_section(client):
    r = client.post(
        "/api/engage",
        data={"events": [{"kind": "click", "sectionId": "nope", "targetId": "x"}]},
        content_type="application/json",
    )
    assert r.status_code == 400
```

**Step 2:** 实现：整批校验；`durationMs` 服务端封顶 `30 * 60 * 1000`；体长 >64KB → 413；可选简单内存限流 → 429。

**Step 3:** Commit `feat: 接收区块停留与点击 engage 事件`

---

### Task 7: GET /admin/api/engagement 汇总（TDD）

**Files:**
- Create: `apps/engage/summary.py`
- Modify: `apps/engage/views.py`, `config/urls.py`
- Test: `tests/test_engage.py`

**Step 1:** 插入若干 leave/click → Admin GET → `sections` 含 avg/total dwell，`topClicks` 排序；无 Cookie 401；仅 `USER_COOKIE` 401。

**Step 2:** 实现 24h 窗口（query `?window=7d` 可选后期）。

**Step 3:** Commit `feat: 提供 Admin 互动统计汇总接口`

---

### Task 8: API README + 保留清理命令（可选轻量）

**Files:**
- Modify: `README.md`（路由表）
- Create: `apps/engage/management/commands/purge_engage.py`（删除 90 天前事件）或文档注明日后 cron

**Step 1:** 更新路由说明与 `media/` 部署注意。

**Step 2:** `pytest -q` 全绿（含站点用户 38+ 新用例）。

**Step 3:** Commit `docs: 补充 media 与 engage 路由说明`

---

### Task 9: Web — 读槽位图 + Admin 图片页

**Files（website）：**
- Create: `lib/site-images.ts`, `app/admin/images/page.tsx`, `components/admin-site-images.tsx`
- Modify: `components/hero.tsx` / `services.tsx` / `brand-mark.tsx` / categories 样式数据源；`components/admin-shell.tsx` 导航
- Test: Vitest 轻量（slot URL 拼接 / 回退逻辑）

**约束：** 尽量少改 class；用 CSS 变量或 inline `style`/`next/image` src 切换 URL，避免大改 SCSS。

**Step:** TDD 回退逻辑 → 接 `GET /api/site-images` → Admin 上传表单 POST admin API → Commit（web 仓）。

---

### Task 10: Web — 埋点 + Admin 互动面板

**Files（website）：**
- Create: `components/engage-tracker.tsx`, `lib/engage.ts`
- Modify: 首页各 section 加 `data-engage-section` / 关键 CTA `data-engage`
- Create: `app/admin/engagement/page.tsx` 或并入 dashboard
- Modify: `proxy.ts` 若需放行（engage 经 rewrite 即可）

**Step:** 队列 + beacon → 本地可 mock；Admin 拉 `/admin/api/engagement` 展示表格。

**Commit（web）：** `feat: 首页互动埋点与后台停留点击排行`

---

### Task 11: 双仓联调验收

- API `runserver :3001` + Web `dev :3000`
- 上传 hero 图 → 刷新首页可见
- 浏览滚动点击 → Admin 互动有数据
- 站点用户仍不能打开管理汇总
- `pytest -q` 与 `npm test` 全绿

---

## Spec Coverage

| 设计项 | Task |
|--------|------|
| SQLite + MEDIA_ROOT | 1–2 |
| 槽位与校验 | 2–3 |
| 图片 API | 4 |
| engage 写入 | 5–6 |
| engagement 汇总 | 7 |
| Web 管图/埋点 | 9–10 |
| 联调 | 11 |
| 站点用户不回退 | 全任务避免改坏 authx 文件存储 |

---

## Execution Handoff

计划已保存：`docs/plans/2026-08-15-media-engage.md`。

**两种执行方式：**

1. **Subagent-driven（推荐）** — 本会话按 Task 派生子代理实现 + 两阶段审查  
2. **手动执行** — 你按任务清单逐步自己做  

选 **1** 或 **2** 后开始实现（从 Task 1 起，仍先红灯再写代码）。
