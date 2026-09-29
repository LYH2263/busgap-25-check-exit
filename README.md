# BusGap 公交串车检测

对比计划发车间隔与实际到站间隔，识别串车与大间隔，并给出调班建议。

技术栈：Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL / Vue 3 / TypeScript / Vite

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4600 |
| API | http://localhost:9600 |
| API 文档 | http://localhost:9600/docs |
| Postgres | localhost:5447 |

健康检查：`GET http://localhost:9600/api/health`

## 使用说明

1. 在「线路」查看运营线路与阈值阈值。
2. 在「班次」「到站」核对计划与实际到站时间。
3. 打开「串车报告」执行间隔判定。
4. 在「时间轴」观察到站分布，在「建议」查看调班提示。

## 开发与测试

```bash
docker compose exec api pytest -q
```

## 间隔对账 CLI

与「串车报告」「时间轴」「到站」三页同一口径的可重复、只读对账。源码分三层：

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 原材料抽取 | `app/services/recon_extract.py` | 只收集到站行、报告事件、时间轴行；只读直连库或读夹具；不依赖判读层 |
| 规则判读 | `app/services/recon_rules.py` | 只基于抽取产物判四项；定义退出码常量 |
| 命令入口 | `app/cli/reconcile.py` | 只选库或夹具并串联；不做进程探活 |

四项判读（严格不等号同 `classify_gap`）：①事件班次对能在到站命中；②串车 gap 严格 `< 串车阈`；③大间隔 gap 严格 `> 大间隔阈`；④时间轴点数 = 该站未取消到站数。任一失败整次非 0。

```bash
# 直连库（默认 B12 / 市民中心，只读，可重复执行）
docker compose exec api python -m app.cli.reconcile
# 夹具
docker compose exec api python -m app.cli.reconcile --fixture tests/fixtures/bunching_marked_normal.json
docker compose exec api python -m app.cli.reconcile --fixture tests/fixtures/arrival_missing_field.json
```

退出码：`0` 通过；`2`（常量甲 `EXIT_RECON_MISMATCH`）四项矛盾，如「实串车却标正常」；`3`（常量乙 `EXIT_MALFORMED`，≠甲）原材料缺字段并点名缺项。种子数据对账退出 0。
