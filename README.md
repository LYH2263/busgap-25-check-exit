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

可重复执行的只读对账，核对「到站表 / 串车报告事件 / 时间轴」三处是否同一口径。
源码按依赖单向拆为三层（`backend/app/reconcile/`）：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 原材料 | `model.py` | 到站行、报告事件行、时间轴原始点的数据结构 |
| 抽取 | `extract.py` | 只收集到站、报告事件、时间轴原始行，**禁止导入判读层**（AST 测试固化） |
| 判读 | `rules.py` | 只基于抽取出的原材料做四项核对，不碰库/夹具 |
| 入口 | `cli.py` | 只选数据来源（库或夹具）并串联，无判读逻辑 |

四项核对（任一失败整次非 0，不允许只警告仍退出 0）：

1. **PAIR_HIT** —— 报告事件班次对必须在到站表命中（同站、未取消）；
2. **BUNCH_GAP** —— 串车 ⇔ 间隔**严格小于**串车阈（双向，治「实串车却标正常」）；
3. **LARGE_GAP** —— 大间隔 ⇔ 间隔**严格大于**大间隔阈（双向）；
4. **TIMELINE_COUNT** —— 时间轴点数 == 该站未取消到站数。

退出码常量：`0` 通过；`10`（甲 `EXIT_CONTRADICTION`）展示与规则矛盾；
`20`（乙 `EXIT_MISSING_FIELD`，≠ 甲）原材料缺字段，并点名缺项。
缺字段行不参与配对，不会被误判成串车矛盾（落乙不落甲）。全程只读、不做进程探活。

```bash
# 对运营库（默认 B12 / 市民中心）
docker compose exec api python -m app.reconcile.cli
# 对夹具
docker compose exec api python -m app.reconcile.cli \
  --fixture tests/fixtures/seed_b12.json
# 夹具链路仅用标准库，无 SQLAlchemy 环境也可在 backend/ 下运行
PYTHONPATH=. python3 -m app.reconcile.cli --fixture tests/fixtures/<名称>.json
```

| 夹具 | 场景 | 退出码 |
| --- | --- | --- |
| `seed_b12.json` | 种子数据镜像（B12 / 市民中心），报告与引擎逐项一致 | `0` |
| `bunching_labeled_normal.json` | 实串车（2′<3）却标正常 | `10`（甲） |
| `arrival_missing_field.json` | T02 市民中心到站缺 `actual_arrive` | `20`（乙，点名缺项） |
| `timeline_includes_cancelled.json` | 时间轴仍画已取消的 T02 | `10`（甲，TIMELINE_COUNT） |

