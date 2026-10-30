# Vision Studio 前端

原生 HTML / CSS / JavaScript 前端，不需要安装依赖。

## 打开方式

用 VS Code 打开本文件夹，再用实时预览插件打开 `login.html`。默认**同源**：
后端用本目录托管前端，`python run.py` 后访问 `http://127.0.0.1:8000/login.html`。

要单独起静态服务调样式，就在 `sdk.js` 之前插一行
`<script>window.__VS_BASE__ = "http://127.0.0.1:8000";</script>` 指回后端。
业务代码全部通过 `sdk.js` 请求，不直接调用 `fetch`。

## 文件职责

- `login.html` / `login.css` / `auth.js`：登录、注册与进入动画。
- `index.html` / `styles.css`：个人 / 团队 / 企业三个空间的项目列表、项目页、平台管理、资料和统一弹窗界面；`styles.css` 保留「澄白」主题原样。
- `theme.js` / `themes.css`：双主题切换、侧栏收起状态与「晴蓝」Material 3 外观层。
- `app.js`：上传、分析、图像列表、详情和删除。
- `team.js`：小组、成员、小组成果和聊天客户端。
- `settings.js`：设置弹窗的三节（个人资料与头像 / 外观与主题 / 账号安全）。侧栏「个人与账号」和「设置」打开的是同一个弹窗，各自定位到对应一节。
- `admin.js`：平台管理页（概览、账号与平台角色、重置密码、小组 / 项目、审计、存储与模型）。
- `projects.js`：三个空间的项目列表、项目页六个 tab（概览 / 图像 / 任务 / 成员 / 类目 / 设置）、新建任务对话框、空间设置抽屉。
- `annotate.js`：人工标注工作台（左选图 / 中画框 / 右选类目），负责拉取任务图片、编辑 bbox 并以整组覆盖的方式保存，最后「完成标注 / 重新提交 / 重新排队」。
- `notifications.js`：通知中心（左栏「通知」+ 右侧抽屉）。轮询未读数显示徽标，按 `resource_type` 跳转：`task` 打开任务所在项目，`invitation` 行内直接「接受 / 拒绝」。
- `core.js`：后端地址、SDK 实例、错误翻译和公共工具。
- `sdk.js`：FastAPI 接口封装。

## 当前能力边界

图片、分析任务、小组、企业空间、账号头像与展示名都已接上本仓库后端。只剩小组介绍字段没有
（前端已不展示，补了也不会用到）；缺少接口时前端会明确禁用，不会保存假数据。
平台管理页只对 `platform_role=admin` 显示入口。

已有动画被保留。修改功能时优先复用现有组件，避免新增系统 `alert` / `confirm`，危险操作统一使用红色确认卡片。

## 外观主题

- **经典**（默认）：紧凑工作台。复用 Material 组件层，项目页的「图像」tab 排成「上传区 + 识别方案 / 分析状态 + 图像列表」，动效更短。
- **晴蓝**：宽松 Material 3 方向，桌面端使用常驻且可收起的左侧导航，窄屏使用底部导航。
- **澄白**：保留重构前的极简界面与悬浮工具轨。

进入「个人资料与账号 → 外观与主题」即可切换。选择保存在浏览器本地，只改变界面外观，不会修改图片、任务或小组数据；登录页也会沿用上次选择。已经是旧版本的用户，如果手动选过主题，升级后不会被默认主题覆盖。

### 三个主题相关的约定（改样式前先看）

1. **判断主题只认 `data-vision-theme`，不要读 `data-theme`。**
   经典主题复用晴蓝的整套 Material 组件层，所以 `data-theme` 在经典下被写成 `"clear-sky"`（它只是「基线样式别名」）。真实主题挂在 `data-vision-theme` 上，JS 里统一用 `VSTheme.get()`。
2. **`styles.css` 是两套主题共用的基础层，`themes.css` 才是主题层。**
   晴蓝与经典的规则都必须带 `html[data-theme="clear-sky"]` / `html[data-vision-theme="classic"]` 前缀，否则改动会漏到澄白上。
3. **版式与动效参数集中在 `themes.css` 末尾的「全站统一层」。**
   内容列宽（`--layout-content`，1600px 以上自动加档到 1560）、标题阶梯（`--fs-display` / `--fs-page`）、动效时长（`--motion-fast` / `--motion-base` / `--motion-slow`）都在那里改一处、三套主题同时生效。

> 注意：`styles.css` 顶部有 `* { transition: none }` 的全站重置，想给某个元素加过渡**必须写 `transition` 简写**，只写 `transition-duration` 是没有效果的。

---

## 给后端同学：联调前需要知道的 6 条

### 1. 怎么跑起来

不能双击 `file://` 打开（会被 CORS 拦住），要起一个静态服务：

```bash
# 在本文件夹里执行
python -m http.server 8125 --bind 127.0.0.1
# 浏览器打开 http://127.0.0.1:8125/login.html
```

后端地址只在 `core.js` 的 `BASE` 一处（`window.__VS_BASE__ || location.origin`）。

### 2. 前端是「按能力探测」的，不会因为缺接口报错

开页会调用 `VS.probe()`：拿 `/auth/me` 的字段和 `/groups` 的字段判断后端支持什么。
缺 `display_name` 就隐藏展示名称，缺 `description` 就禁用小组介绍输入框，缺聊天接口就禁用输入框并写明原因。
所以**新增字段后前端会自动出现对应入口，不用改前端代码**。

### 3. 目前缺的接口（前端已写好，等后端补）

| 用途 | 接口 | 字段 |
|---|---|---|
| 小组介绍（可选） | `Group` 增加可写 `description` | 字符串。前端已不展示，补了也不会用到 |

展示名称与头像**已经可用**：`AccountRead` 带 `display_name` / `has_avatar`，
头像走 `POST｜DELETE /auth/me/avatar` 与 `GET /accounts/{id}/avatar`。
`VS.probe()` 探测到这两个字段后，资料弹窗的展示名输入框和头像区会自动出现。

小组聊天已经从前端**整块删除**（后端一直没有消息接口，占位反而造成「功能好像坏了」的观感），不用再补。

企业空间的接口见下面第 7 条，那是一整套新契约。

### 4. 两个字段名不一致，别踩

- 项目图片上传：`POST /projects/{id}/images`，字段名 **`files`（复数，单次最多 20 张）**
- 小组图库上传：`POST /groups/{id}/images`，字段名 **`file`（单数，一次一张）**

`/groups/{id}/images` 的 **列表** 返回的是**裸数组**（不是 `{items,total}` 分页对象），前端按数组用。

### 5. 任务状态决定图片能不能删

终态只有 `approved` / `cancelled`。模型跑完（`auto_annotated`）**仍然算活动任务**，此时 `DELETE /images/{id}` 返回 409。
前端的处理是：先查关联任务 → 点一次「删除图片」变成「确认删除」→ 确认后取消任务再删图。
所以 409 是预期行为，不是前端 bug。

### 6. `listProjectTasks` 的返回顺序不是契约

前端按 **id 取最大** 当作最新任务，不依赖数组顺序。如果后端改了排序，前端不会受影响；但请不要改成「id 小的在前」以外还省略 id。


---

### 7. 企业空间：后端已实现，前端按它对齐

**角色**：`owner`（所有者）> `admin`（管理员）> `reviewer`（审核员）> `member`（成员）> `guest`（只读访客）。

三处和早期草案不同，以本仓库后端为准：

1. **图库、日志是分页对象** `{items, total, offset, limit}`，不是裸数组。
2. **公告不单独占接口**：读 `enterprise.notice_text`，改走 `PATCH /enterprises/{id}` 的
   `notice_text`（传 null 撤下）。
3. **`EnterpriseImage.id` 是关联行 id**，取图 / 看详情要用它指向的 `image_id`。

接口都在 `/enterprises` 下：

| 用途 | 接口 |
|---|---|
| 我的企业 / 创建 | `GET｜POST /enterprises` |
| 详情 / 改 / 解散 | `GET｜PATCH｜DELETE /enterprises/{id}` |
| 凭码加入 | `POST /enterprises/join`（body `{code, message}`） |
| 待审申请 / 审批 | `GET /{id}/requests`，`POST /{id}/requests/{accountId}/approve｜reject` |
| 成员 / 邀请 / 改角色 / 移除 / 退出 | `GET｜POST /{id}/members`，`PATCH｜DELETE /{id}/members/{accountId}`，`POST /{id}/leave` |
| 图库 / 上传 / 删图 | `GET｜POST /{id}/images`（上传字段 `files`，最多 20 张），`DELETE /{id}/images/{imageId}` |
| 待审 / 审批 | `GET /{id}/review`，`POST /{id}/images/{imageId}/review`（body `{decision, note}`） |
| 用量 / 排名 | `GET /{id}/usage`，`GET /{id}/ranking` |
| 日志 / 日报 / 心跳 | `GET /{id}/logs`，`GET /{id}/report?date=YYYY-MM-DD`，`POST /{id}/heartbeat` |

实现约定：

1. **在线状态不落库**：45 秒内有心跳且没开隐身即为在线，前端每 20 秒打一次心跳。
2. **图片审核**：`review_status` 取 `pending` / `approved` / `rejected`；企业开了
   `auto_approve_staff` 时，管理员及以上上传的图直接 `approved`。
3. **配额超限返回 409**，detail 是「企业图片配额已满：12/10」，前端可直接显示。
4. **企业图仍存在 `Image` 表**（落在企业背后的隐藏组里），`GET /images/{id}/content`
   已放行企业成员，否则企业图库全是空白格子。

---

### 8. 平台管理页

`admin.js`，入口是侧栏「平台管理」，**只有 `platform_role === "admin"` 才显示**；
进页后还会再调一次 `/auth/me` 校验，非管理员直接显示「需要平台管理员权限」。

五个 tab 对应已有的 `/admin/*` 接口：

| tab | 用到的接口 |
|---|---|
| 概览 | `/admin/stats`、`/admin/storage-policy`、`/admin/model-config` |
| 账号 | `/admin/accounts`、`PATCH .../role`、`POST .../reset-password` |
| 小组与项目 | `/admin/groups`、`/admin/projects` |
| 审计日志 | `/admin/audit-logs`（按 action 前缀过滤） |
| 存储与模型 | `GET｜PUT /admin/storage-policy`、`GET｜PUT /admin/model-config` |

约定：列表每页 50 条，翻页只改 `offset`；自己的平台角色下拉是禁用的（后端也拒绝改自己）；
模型配置留空表示回到环境变量，保存后立即生效。

---

### 9. 空间与项目

三个空间页都只列项目，进入项目才是工作区。项目就是后端的 `Project`，三种空间只是它挂的 `group`：

- 个人空间 = `personal` 组；团队 = `team` 组；企业 = 企业背后的 `org` 组（`EnterpriseListItem.group_id`）。
- 建项目：`POST /projects`，body `{group_id, name, description}`；空间 owner/admin 或企业 admin+ 才能建，创建者成为项目 manager。
- 列表：`GET /projects?group_id=...&group_type=...`（`personal` / `team` / `org`）。
- 企业成员访问企业项目由后端 `ProjectAccess` 识别：企业 owner/admin 可管，reviewer 可审，member 可传，guest 只读。
- 项目页六个 tab：概览（`/projects/{id}/stats`，顶部「下一步」按项目状态给出下一步动作）、图像（图片与上传）、任务（筛选 + 新建任务对话框）、成员、类目（识别类目，自动标注的 prompt 来源与人工标注的色板）、设置（改名/归档）。
- 任务列表要走 `GET /projects/{id}/tasks`，返回 `TaskListItem`：在 `TaskRead` 基础上多了 `image_total` / `image_annotated`，列表才能直接显示「已标注 3 / 12」而不用逐条拉详情。
- 看图页「在标注台打开」靠 `GET /projects/{id}/images/{image_id}/tasks`：一张图可以同时属于多个任务，所以要问后端这张图落在哪个任务、对应哪条 `task_image`、以及能不能改；没有任何可编辑任务时按钮保持隐藏。
- 空间级功能（小组成员与图库、企业审核/公告/日报）在右上角「空间设置」抽屉里，直接复用 team.js / enterprise.js 的原有 DOM。
- 「识别方案」对话框同时管两件事：把填写的目标同步成项目类目（新增 / 修改 / 删除），以及置信度阈值 `box_threshold`（滑块 0.05–0.95，默认 0.30，`PATCH /projects/{id}/auto-config`）。上传后分析走的 `syncAnalysisConfig()` **不传**阈值，避免上传动作悄悄改掉老项目的设置；只有在这个对话框里按「保存识别方案」才会写。
- 目标是一条一条编辑的（`#targetRows` 里每行 `中文名 | 英文提示词 | ×`）：中文名只用于界面显示，英文提示词才是喂给模型的检测口径，留空则保存时由后端按中文名自动翻译（`create_category` / `update_category` 里 `en_name` 传空串即触发翻译）。保存时按行比对——没有 `id` 的新建，改过的 PATCH，从列表里删掉的 DELETE；中文名重复在提交前就拦下。
- 阈值的作用点在推理之后：一次推理用所有类目里最小的那个阈值取框，再按每个类目的 `threshold_override` / 全局值过滤，最后做跨类目去重（IoU ≥ 0.8，同类目 IoU ≥ 0.6）。所以调高阈值只会少框，不会变快。
