# Vision Studio 前端

原生 HTML / CSS / JavaScript 前端，不需要安装依赖。

## 打开方式

用 VS Code 打开本文件夹，再用实时预览插件打开 `login.html`。当前唯一后端是：

`http://10.11.82.221:8000`

地址统一配置在 `core.js`；业务代码全部通过 `sdk.js` 请求，不直接调用 `fetch`。

## 文件职责

- `login.html` / `login.css` / `auth.js`：登录、注册与进入动画。
- `index.html` / `styles.css`：工作台、小组、资料和统一弹窗界面；`styles.css` 保留「澄白」主题原样。
- `theme.js` / `themes.css`：双主题切换、侧栏收起状态与「晴蓝」Material 3 外观层。
- `app.js`：上传、分析、图像列表、详情和删除。
- `team.js`：小组、成员、小组成果和聊天客户端。
- `settings.js`：个人资料与账号安全。
- `core.js`：后端地址、SDK 实例、错误翻译和公共工具。
- `sdk.js`：FastAPI 接口封装。

## 当前能力边界

远端已支持图片、分析任务、小组、成员及小组图片。远端暂未提供聊天接口和小组介绍字段；前端已准备对应界面与 SDK 方法，缺少接口时会明确禁用，不会保存假数据。个人简介功能不在当前产品范围内。

已有动画被保留。修改功能时优先复用现有组件，避免新增系统 `alert` / `confirm`，危险操作统一使用红色确认卡片。

## 外观主题

- **经典**（默认）：紧凑工作台。复用 Material 组件层，但把首页排成「上传区 + 识别方案 / 分析状态 + 最近图像」，动效更短。
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

后端地址只在 `core.js` 第 5 行的 `BASE` 一处，改地址只改这一行。

### 2. 前端是「按能力探测」的，不会因为缺接口报错

开页会调用 `VS.probe()`：拿 `/auth/me` 的字段和 `/groups` 的字段判断后端支持什么。
缺 `display_name` 就隐藏展示名称，缺 `description` 就禁用小组介绍输入框，缺聊天接口就禁用输入框并写明原因。
所以**新增字段后前端会自动出现对应入口，不用改前端代码**。

### 3. 目前缺的接口（前端已写好，等后端补）

| 用途 | 接口 | 字段 |
|---|---|---|
| 展示名称（可选） | `Account` 增加 `display_name` | 字符串 |
| 小组介绍（可选） | `Group` 增加可写 `description` | 字符串。前端已不展示，补了也不会用到 |

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

### 7. 企业空间：一整套新接口（前端已完成，后端未实现）

前端已经做完，**后端只要照下面实现，界面零改动就能通**。没实现时前端不会报错：企业页显示「当前后端还没有企业接口」，创建/加入按钮自动禁用，Logo 旁的企业栏不出现。

**角色**：`owner`（所有者）> `admin`（管理员）> `reviewer`（审核员）> `member`（成员）> `guest`（只读访客）

| 用途 | 接口 | 说明 |
|---|---|---|
| 我的企业 | `GET /enterprises` | 数组，每项在 `Enterprise` 基础上附 `my_role`、`member_count`、`image_count`、`used_bytes`、`review_pending`、`pending_count` |
| 创建 | `POST /enterprises` | body `{name, email}`；邮箱域名不得是免费邮箱；返回带 `code` 的企业对象 |
| 详情 / 改 / 解散 | `GET｜PATCH｜DELETE /enterprises/{id}` | 解散仅 owner |
| 凭码申请 | `POST /enterprises/join` | body `{code, message}`；返回 `{status: "pending"｜"active", enterprise}` |
| 待审申请 | `GET /enterprises/{id}/requests` | 数组 `{account_id, account, message, applied_at}` |
| 审批 | `POST /enterprises/{id}/requests/{accountId}/approve` · `.../reject` | admin 及以上 |
| 成员 | `GET｜POST /enterprises/{id}/members` | GET 每项附 `online`、`invisible`、`upload_count`；POST body `{username, role}` 为邀请 |
| 改角色 / 移除 | `PATCH｜DELETE /enterprises/{id}/members/{accountId}` | PATCH 只传 `{invisible}` 表示本人改隐身 |
| 退出 | `POST /enterprises/{id}/leave` | owner 不能退出（409） |
| 图库 | `GET｜POST /enterprises/{id}/images` | POST 用 **`files`（复数）**，返回数组 |
| 删图 | `DELETE /enterprises/{id}/images/{imageId}` | 管理员以上或本人 |
| 用量 | `GET /enterprises/{id}/usage` | `{image_count, used_bytes, quota_images, member_count, quota_members}` |
| 待审列表 | `GET /enterprises/{id}/review` | 图库里 `review_status === "pending"` 的那些 |
| 审批图片 | `POST /enterprises/{id}/images/{imageId}/review` | body `{decision: "approve"｜"reject", note}`，reviewer 及以上 |
| 上传排名 | `GET /enterprises/{id}/ranking` | 数组 `{account_id, username, role, count, rank}`，按 count 降序 |
| 公告 | `GET｜PUT /enterprises/{id}/notice` | body `{text}`，admin 及以上 |
| 操作留痕 | `GET /enterprises/{id}/logs` | 数组 `{id, actor_account_id, actor_name, action, detail, created_at}`，新在前 |
| 日报 | `GET /enterprises/{id}/report?date=YYYY-MM-DD` | `{date, uploads, approved, rejected, pending, pass_rate, active_members, by_member[]}` |
| 心跳 | `POST /enterprises/{id}/heartbeat` | body `{invisible}`；返回 `{online, invisible, server_time}` |

**三条实现约定**：

1. **在线状态不由数据库存**，按 `last_seen_at` 算：45 秒内有心跳且没开隐身即为在线。前端每 20 秒打一次心跳。
2. **图片审核**：`EnterpriseImage.review_status` 取值 `pending` / `approved` / `rejected`。若企业开了 `auto_approve_staff`，管理员及以上上传的直接 `approved`。
3. **配额超限返回 409**，detail 里带已用量，前端直接显示这句话。

**图片内容复用现有端点**：`GET /images/{imageId}/content` 要能取到企业图，否则企业图库全是空白格子。
