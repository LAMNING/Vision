# Vison

图片标注平台。后端 FastAPI，前端是同源托管的原生 HTML/CSS/JavaScript（无构建步骤）。
一个进程就能同时提供 API 和页面。

```
python run.py        →  http://127.0.0.1:8000/login.html   前端
                        http://127.0.0.1:8000/docs          API 文档（OpenAPI）
```

## 安装与启动

```powershell
pip install -r requirements.txt
python run.py                      # 默认 127.0.0.1:8000，开热重载
python run.py -H 0.0.0.0           # 允许局域网内其他机器访问
python run.py --no-reload          # 关热重载（生产/计划任务）
```

`run.py` 的参数：`-H/--host`、`-p/--port`、`--workers`、`--no-reload`、`--log-level`，
也可以用环境变量 `HOST` / `PORT` / `RELOAD` / `WORKERS` / `LOG_LEVEL` 控制（命令行优先）。

## 数据库

默认 `sqlite:///./test.db`，启动时自动建表。仓库里**不带** alembic 迁移脚本，此时
`app/migrations.py` 会退回 `create_all()`：只建缺失的表，**不会给已有表补列或补索引**，
并打一条 WARNING 说明这次退化了。把 `alembic/` 目录和 `alembic.ini` 放回仓库根目录，
就会自动改走 `stamp`（老库只写版本号）→ `upgrade head`。

换库改 `IMAGELABEL_DATABASE_URL`（生产建议 PostgreSQL，SQLite 只适合本地/单机演示）。

## 第一个管理员

`/admin/*` 全部挂在管理员守卫后面，注册接口只会给 `user` 角色，所以**首个管理员
只能从命令行创建**（没有这个入口，全新部署永远进不去管理端）：

```powershell
python -m app.cli.make_admin alice --create        # 建账号并设为管理员，密码交互输入
python -m app.cli.make_admin alice                 # 账号已存在时只提权（幂等）
python -m app.cli.make_admin alice --reset-password  # 顺带重置密码并作废旧 token
```

命令会先跑一遍建表逻辑，空库也能直接执行；数据库地址与 `.env` 和服务完全一致。

## 配置

所有配置走环境变量（前缀 `IMAGELABEL_`）。仓库根的 `.env` 会被自动加载
（`app/env.py`，**真实环境变量优先**），所以 CLI 和服务看到的配置始终一致。
首次部署先 `cp .env.example .env`（Windows：`Copy-Item .env.example .env`），
至少填一个固定的 `IMAGELABEL_SECRET_KEY`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IMAGELABEL_DATABASE_URL` | `sqlite:///./test.db` | 数据库地址 |
| `IMAGELABEL_SECRET_KEY` | 随机临时值 | JWT 签名密钥。不配就是每次重启换密钥、所有 token 失效，生产必须配 |
| `IMAGELABEL_STORAGE_URI` | `file://./state/storage` | 图片存储根：`file://`、`oss://`、`s3://`、`cos://`、`ftp://`、`sftp://`、`dav://` |
| `IMAGELABEL_CORS_ORIGINS` | `*` | 逗号分隔的允许来源 |
| `IMAGELABEL_LOG_LEVEL` / `IMAGELABEL_LOG_JSON` | `INFO` / 关 | 日志级别；置 `1` 输出单行 JSON |
| `IMAGELABEL_AUTO_MIGRATE` | `1` | 置 `0` 跳过 alembic、强制 `create_all()` |
| `IMAGELABEL_AUTO_WORKER` / `IMAGELABEL_AUTO_WORKER_INTERVAL` | `1` / `2.0` | 自动标注 worker 开关与轮询间隔（秒） |
| `IMAGELABEL_AUTO_TIMEOUT_SECONDS` | `180` | 单图推理超时 |
| `IMAGELABEL_MAX_IMAGE_PIXELS` | `40000000` | 推理前的像素上限（防 OOM） |
| `IMAGELABEL_WORKER_LEASE_SECONDS` | `600` | 自动标注任务租约，超时任务自动回收重投 |
| `IMAGELABEL_REMINDER_WORKER` / `IMAGELABEL_REMINDER_INTERVAL` | `1` / `300` | 逾期提醒 worker 开关与间隔（秒） |
| `IMAGELABEL_SIGNIN_LIMIT` / `IMAGELABEL_SIGNIN_WINDOW` | `10` / `60` | 登录限流（次数 / 窗口秒数） |
| `IMAGELABEL_SQLITE_BUSY_TIMEOUT_MS` | `30000` | SQLite 写锁忙等超时 |
| `IMAGELABEL_DB_POOL_SIZE` / `IMAGELABEL_DB_MAX_OVERFLOW` | `10` / `20` | 非 SQLite 连接池 |
| `IMAGELABEL_LLMDET_ONNX` / `_DIR` / `_PRECISION` / `_DEVICE` | — / — / `fp16` / `cpu` | 检测模型路径、精度、设备（`cpu`/`cuda`/`auto`） |
| `IMAGELABEL_LLMDET_SIZE` / `_MAX_SIZE` | `800` / `1333` | 推理输入短边 / 长边上限 |
| `IMAGELABEL_MARIAN_DIR` / `_PRECISION` | — / `fp32` | 翻译模型（类目中文名 → 英文提示词）；不配则类目要手填英文 |
| `LLMDET_ORT_GPU_DIR` | `.cuda` | CUDA/cuDNN DLL 目录（`_DEVICE=cuda` 时需要） |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `OSS_SECURITY_TOKEN` | — | 对象存储凭据（`AWS_*` 同义） |

`.env` 已被 `.gitignore` 忽略，别把真实密钥提交进仓库。

## 目录

| 路径 | 内容 |
|---|---|
| `app/` | FastAPI 应用：`routers/` 路由、`schemas/` 出入参、`data/` 模型与 CRUD、`services/` 业务（自动标注 worker、存储、通知）、`cli/` 命令行工具、`utils/` 工具 |
| `web/` | 前端：`login.html` 登录、`index.html` 空间列表 + 项目页 + 平台管理，`core.js` 负责接口封装，`sdk.js` 是可单独复用的 SDK |
| `state/` | 默认本地存储根（图片文件），首次上传时自动创建 |

## 接口

运行中的实例自带 `/docs`（Swagger UI）和 `/redoc`，这是接口的唯一权威说明。
前端 `web/sdk.js` 覆盖了全部接口，业务字段用 snake_case，SDK 参数用 camelCase。

认证是 Bearer token（登录后放请求头，前端存 `localStorage`），不是 cookie。
`<img>` 不能带请求头，所以图片统一 `fetch` 成 blob 再 `URL.createObjectURL`。

## 前端开发

- 前端默认**同源**：`web/core.js` 里 `BASE = window.__VS_BASE__ || location.origin`，
  后端地址变了不用改代码；要临时指向别的后端，就在 `sdk.js` 之前写
  `<script>window.__VS_BASE__ = "http://host:port";</script>`。
- 想把前端单独跑起来（例如 `python -m http.server 8125`）调样式，后端要给
  `IMAGELABEL_CORS_ORIGINS=http://127.0.0.1:8125`，否则跨域会被拦。
- 前端**按接口探测能力**：小组介绍这类后端还没提供的字段，对应面板会自动禁用并说明原因；
  展示名称、头像、企业空间均已实现，探测到就自动显示入口。
- 平台管理有独立页面：侧栏「平台管理」只对 `platform_role=admin` 可见（页面自身再校验一次），
  含账号与平台角色、重置密码、小组 / 项目、审计日志、存储策略与模型配置。
