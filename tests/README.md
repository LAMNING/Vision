# 测试说明

这个仓库原本没有任何自动化测试，`tests/` 是补上的第一套。
全部用例不需要浏览器、不需要模型文件（ONNX 不在仓库里），跑一遍约 3~4 秒。

```bash
python -m pytest            # 全部
python -m pytest -q tests/test_api_task_review_flow.py   # 单个文件
```

`tests/conftest.py` 在导入 app **之前**把环境变量指向系统临时目录里的独立库与存储
（`IMAGELABEL_DATABASE_URL` / `IMAGELABEL_STORAGE_URI` / 固定密钥 / 关掉两个后台
worker / 放开登录限流），所以既能跑在 CI 上，也**不会碰仓库里的 `test.db` 与 `state/`**。
每个用例开始前清空业务表（保留单行的 `platform_setting`），用例之间互不影响。

| 文件 | 守的是什么 |
| --- | --- |
| `test_api_image_progress.py` | 图像 tab 每行状态：`ProjectImageRead.stage` 的聚合优先级（可编辑 > 已通过 > 已退回 > 可审核 > 已标注 > 失败 > 待标注）、`?stage=` 筛选、非法 stage 报 400、分页不受筛选影响 |
| `test_api_task_review_flow.py` | 任务列表的 `复核 x / y` 计数（`TaskListItem.review_approved` / `review_rejected`）、逐图全部通过后任务自动收口成 `approved`、混合结论仍留人工裁决、驳回必须写原因、`review_required=False` 标完即通过 |
| `test_api_project_stats.py` | 项目统计的聚合口径：各分组之和必须等于 `annotations_current`（防「隐式笛卡尔积把每条统计放大」回归）、历史版本不计入 |
| `test_auto_annotation_units.py` | 纯函数：跨类目 IoU ≥ 0.8 / 同类目 ≥ 0.6 去重、手写 `en_name` 优先、留空才翻译、`translate_style` 校验 |
| `test_migrations_columns.py` | 老库补列：`create_all()` 不会给已有表加列，`ensure_columns()` 必须补 `task.review_required` 且幂等 |
| `test_frontend_static.py` | 前端静态约定：id 不重复、页签与面板一一对应、三主题分层（澄白=基础层，不得有 `pure-white` 覆盖块）、`[hidden]` 显式 `display:none`、标题「中文在上英文小字在下」、`mini-button` 四档语义色、脚本依赖顺序、`node --check` |

已知缺陷用 `pytest.mark.xfail(strict=True)` 记录（例如 `web/projects.js:810` 仍写已删除的
`#projectDesc`）。修好之后该用例会变成 XPASS 并让测试失败，提醒把标记删掉。
