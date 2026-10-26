# Vision Studio 前端

原生 HTML / CSS / JavaScript 前端，不需要安装依赖。

## 打开方式

用 VS Code 打开本文件夹，再用实时预览插件打开 `login.html`。当前唯一后端是：

`http://10.11.82.221:8000`

地址统一配置在 `core.js`；业务代码全部通过 `sdk.js` 请求，不直接调用 `fetch`。

## 文件职责

- `login.html` / `login.css` / `auth.js`：登录、注册与进入动画。
- `index.html` / `styles.css`：工作台、小组、资料和统一弹窗界面。
- `app.js`：上传、分析、图像列表、详情和删除。
- `team.js`：小组、成员、小组成果和聊天客户端。
- `settings.js`：个人资料与账号安全。
- `core.js`：后端地址、SDK 实例、错误翻译和公共工具。
- `sdk.js`：FastAPI 接口封装。

## 当前能力边界

远端已支持图片、分析任务、小组、成员及小组图片。远端暂未提供聊天接口和小组介绍字段；前端已准备对应界面与 SDK 方法，缺少接口时会明确禁用，不会保存假数据。个人简介功能不在当前产品范围内。

已有动画被保留。修改功能时优先复用现有组件，避免新增系统 `alert` / `confirm`，危险操作统一使用红色确认卡片。

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
| 小组聊天（读） | `GET /groups/{id}/messages?after_id=&limit=` | 每条至少 `id`、`group_id`、`account_id`、`content`、`created_at`，可附 `account` |
| 小组聊天（写） | `POST /groups/{id}/messages` | body `{ "content": "..." }` |
| 小组介绍 | `Group` 增加可写 `description` | 字符串 |
| 展示名称（可选） | `Account` 增加 `display_name` | 字符串 |

聊天现在是「接口缺失 → 输入框禁用 + 说明文案」，没有用 localStorage 造假数据。补上接口后前端会自动启用。

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

