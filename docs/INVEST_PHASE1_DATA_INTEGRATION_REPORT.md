# INVEST Phase 1 数据接入报告

> **执行日期**：2026-11-21
> **阶段目标**：将 InvestData 接入 InvestWeb，建立全市场行情数据基础
> **范围限定**：只做数据层，未开发新评分/排名/页面/AI
> **结论**：数据层已打通，115 个测试全绿，**未部署生产**，等待确认后再上线

---

## 〇、先说要紧的三件事

### 1. 发现并修复了一个会污染全部计算的 bug

InvestData 的 `/bars/daily` 按日期**升序**返回（从最早开始）。最初实现按"倒序"取第一页当最新，结果 `days=260` 拿到的是 **2021 年的 260 根数据**。

```
修复前：days=260 → 2021-11-22 ~ 2022-12-14（错）
修复后：days=260 → 2025-10-28 ~ 2026-11-20（对）
```

**如果没做单位核对环节，这个错误会一路带进所有指标、评分、回测，而且不会报错。**

### 2. 数据与现有库完全一致

同一只标的同一天的收盘价和成交量，InvestData 与 InvestWeb 现有库**分毫不差**：

```
sh600519 2026-11-20: 本地 close=1485.75 vol=6,722,940
                      InvestData close=1485.75 vol=6,722,940
```

### 3. 未部署生产，未改现有表

- 新增 2 张表，**没有动现有 28 张表的任何一个字段**
- 现有 103 个测试全部通过，无回归
- 生产环境（`/opt/investweb`）未做任何改动

---

## 一、实际修改文件

### 新增（8 个）

| 文件 | 作用 |
| --- | --- |
| `backend/app/datasource/__init__.py` | 统一导出 |
| `backend/app/datasource/investdata.py` | InvestData HTTP 客户端（`InvestDataClient`、`Bar`、`BarSeries`） |
| `backend/app/datasource/repository.py` | 统一行情访问层（`MarketRepository`） |
| `backend/app/models/market_models.py` | 新表：`market_instruments`、`market_sync_logs` |
| `backend/app/services/market_sync_service.py` | 标的同步服务 |
| `backend/tests/test_investdata.py` | 12 个接入层测试 |
| `docs/v2_probe/unit_check.json` | 单位核对抽样数据 |
| `docs/INVEST_PHASE1_DATA_INTEGRATION_REPORT.md` | 本报告 |

### 修改（2 个，均为增量）

| 文件 | 改动 | 影响 |
| --- | --- | --- |
| `backend/app/core/config.py` | 新增 `INVESTDATA_ENABLED` / `INVESTDATA_BASE_URL` / `INVESTDATA_TIMEOUT` | 默认 `ENABLED=0`，不开启时对现有系统零影响 |
| `backend/app/core/db.py` | `init_db()` 多导入一个模型模块 | 只会创建 2 张新表，已存在的表不受影响 |

**未改动**：任何服务模块、任何 API 路由、任何前端页面、任何现有表结构。

---

## 二、数据架构

```
上层（指标/评分/排名/页面）
        ↓ 只通过 MarketRepository
┌────────────────────────────────────┐
│ MarketRepository（统一访问层）      │
│  resolve / bars / close_series /    │
│  bars_batch / industries / ready    │
└──────────────┬─────────────────────┘
               ↓
┌──────────────────────────────────────┐
│ InvestDataClient（HTTP）              │
│  超时 60s / 分页 / 歧义拦截 / 质量标记 │
└──────────────┬───────────────────────┘
               ↓ HTTP
┌──────────────────────────────────────┐
│ InvestData（127.0.0.1:8100）          │
│  SQLite 3.0GB / 981 万行 raw          │
│  qfq·hfq 由 API 按复权因子实时派生      │
└──────────────────────────────────────┘
```

**本地库角色**：
- `market_instruments`（1 万条）：标的目录镜像，用于识别与索引
- `market_observations`（现有 9.4 万行）：**保留不动**，原有功能继续使用
- **不导入行情全量**：避免把 981 万行搬进本地库（用户明确要求）

---

## 三、接入方式：选 HTTP API

### 3.1 实测对比

| 方式 | 1000 只 × 260 天 | 全市场估算 | 复权支持 |
| --- | --- | --- | --- |
| **HTTP API**（服务器同机） | 14.4ms/只 | **约 2.3 分钟** | ✅ raw/qfq/hfq |
| SQLite 直读 | 2.0s（1000 只，SQL 聚合） | 约 20 秒 | ❌ **只有 raw** |

### 3.2 为什么没选更快的 SQLite

**关键发现：库里的 `daily_bars` 只有 `raw` 一种口径**（9,818,968 行全是 raw）。

```sql
select adjustment_type, count(*) from daily_bars group by 1;
→ raw  9,818,968
```

`qfq` / `hfq` 是 API 层按复权因子实时计算的派生视图。走 SQLite 就等于**要自己实现复权算法**——而文档明确警告"qfq 是动态的，除权后历史价整体平移"，自己实现极易算错。

而且 API 全市场 2.3 分钟**完全够用**（每日一次）。

**决定：API 为主。SQLite 留作未来瓶颈时的备选，不在本阶段实现。**

---

## 四、数据模型

### 4.1 `market_instruments`（新增）

```sql
instrument_id   TEXT UNIQUE     -- InvestData UUID，唯一识别依据
symbol          TEXT NOT NULL   -- 不是唯一键！
name            TEXT NULL       -- 可能为空（ETF 有 692 只无名）
instrument_type TEXT            -- stock/etf/index/industry_index/concept_index
market          TEXT            -- SH/SZ/BJ
exchange, status, sector_type, currency, listing_date, delisting_date
source, last_synced_at, created_at
UNIQUE(symbol, instrument_type, market)
```

### 4.2 `market_sync_logs`（新增）

每次同步一条记录：`total_fetched / inserted / updated / unchanged / by_type / status / error`。

### 4.3 唯一识别规则

| 场景 | 处理 |
| --- | --- |
| `000001` | **必须带 `instrument_type`**，否则抛 `AmbiguousSymbol` |
| 外部行情对齐 | 用 `instrument_id` |
| 与现有 `instrument_master` 关联 | 用 `symbol + instrument_type + market` |

实测：

```
resolve("000001")                        → AmbiguousSymbol（2 个候选）
resolve("000001", "stock")               → 平安银行 (SZ)
resolve("000001", "index")               → 上证指数 (SH)
```

---

## 五、标的同步结果

### 5.1 实际数量（与上游完全一致）

| 类型 | 数量 |
| --- | ---: |
| stock | 5,922 |
| etf | 2,876 |
| index | 801 |
| industry_index | 467 |
| concept_index | 459 |
| **合计** | **10,525** |

### 5.2 性能与幂等

```
首次同步：10,525 条，耗时 1.9 秒，inserted=10,525
二次同步：10,525 条，耗时 2.4 秒，inserted=0, updated=0, unchanged=10,525
```

**幂等验证通过**：重复执行不产生重复数据。

### 5.3 历史标的保留

同步**不删除**库中已存在但上游消失的标的。上游 `status` 字段原样保存（active/inactive 等），便于后续区分停牌、退市。

---

## 六、数据覆盖情况

| 项 | InvestData 实际 |
| --- | --- |
| 日线行数 | 9,818,968 |
| 时间范围 | 2021-11-22 ~ 2026-11-20（1,212 个交易日） |
| 复权口径 | raw（库里）；raw/qfq/hfq（API 可提供） |
| 板块成分关系 | 94,941 条 |
| 最新交易日 | 2026-11-20 |

**历史深度实测**：`days=260` 可取到 2025-10-28 ~ 2026-11-20；`days=1000` 可取到 2021 年起点。

---

## 七、数据质量情况

### 7.1 质量标记可用

每条 bar 带 `quality_status`（PASS/FAIL）与 `quality_flags`。客户端已暴露：

```python
series.clean_bars()   # 只返回 PASS 的
bar.is_clean          # 单条判断
```

**不静默过滤**：异常数据留在序列里，由调用方决定丢弃还是降权。

### 7.2 抽样结果

45 个抽样标的中，**全部返回 `quality_status=PASS`**，未发现异常标记。

**【尚未核验】**：全库 `quality_status` 的 FAIL 比例没有统计（需要全表扫描，本阶段未做）。

---

## 八、单位核对结果

### 8.1 抽样范围

| 类型 | 样本数 | 结果 |
| --- | ---: | --- |
| 股票 | 20 | amount/volume ÷ close = **1.003 ~ 1.060** |
| ETF | 5（主流） | **0.995 ~ 1.012** |
| 指数 | 5 | 0.004 ~ 0.023（**正常**：指数成交额是成分股之和，与点位不可比） |
| 行业板块 | 5 | 0.004 ~ 0.009（同上） |
| 概念板块 | 5 | 0.007 ~ 0.100（同上） |

### 8.2 结论

| 字段 | 单位 | 证据 |
| --- | --- | --- |
| `volume` | **股** | 股票/ETF 的 `amount/volume` 均接近收盘价 |
| `amount` | **元** | 同上，量级正确 |
| `turnover_rate` | **百分数**（0.54 = 0.54%） | 值域 0.3~0.5，与文档一致 |
| `close` | 元（个股/ETF）/ 点位（指数） | — |

### 8.3 与现有库交叉验证

| 标的 | 日期 | 本地 close / volume | InvestData close / volume | 一致 |
| --- | --- | --- | --- | --- |
| sh600519 | 2026-11-20 | 1485.75 / 6,722,940 | 1485.75 / 6,722,940 | ✅ |
| sz000858 | 2026-11-20 | 72.55 / 25,039,833 | 72.55 / 25,039,833 | ✅ |

### 8.4 三口径验证

```
600519 2026-11-20:  raw=1485.75  qfq=1485.75  hfq=12002.29
600519 2021-11-24:  raw=1941.00  qfq=1677.57  hfq=13551.90
```

早期待数据三口径明显不同，近期因无新除权 raw=qfq——**符合定义**。

---

## 九、性能测试结果

### 9.1 实测数据

| 场景 | 环境 | 结果 |
| --- | --- | --- |
| 单标的 260 根 | 服务器同机 | **48ms** |
| 100 只串行 | 服务器同机 | 1.44s（14.4ms/只） |
| 100 只串行 | 本机（SSH 隧道） | 5.8s（58ms/只） |
| 1000 只串行 | 本机（SSH 隧道） | 70.4s（70ms/只），成功 950/1000 |
| SQLite 直读 1000 只×260 天 | 服务器同机 | 2.01s |

### 9.2 全市场推算

| 环境 | 9,724 只估算 | 是否满足每日计算 |
| --- | --- | --- |
| 服务器同机（生产） | **约 2.3 分钟** | ✅ 满足 |
| 本机（开发，走隧道） | 约 11 分钟 | 可接受，但别频繁全量跑 |

### 9.3 容错验证

`bars_batch` 中**单只失败不影响整批**：1000 只里 50 只无数据（新股/停牌），其余正常返回，失败的返回空序列并记录原因。

### 9.4 内存

1000 只 × 260 天 = 23 万根 bar 全部加载，进程内存未出现异常增长（单次批量约 200MB 峰值）。

**【尚未核验】**：全市场 9,724 只一次载入的内存峰值未测（不建议一次全载，应分批）。

---

## 十、测试结果

### 10.1 新增测试（12 个）

| 测试 | 验证内容 |
| --- | --- |
| `test_all_five_types_are_available` | 5 个类别均可查询 |
| `test_same_symbol_different_type_is_not_confused` | 000001 歧义拦截 |
| `test_instrument_id_is_unique_key` | 同代码不同类型 ID 不同 |
| `test_bars_return_recent_data_not_oldest` | **回归本次修复的升序 bug** |
| `test_days_parameter_controls_length` | days 参数生效 |
| `test_three_adjustments_differ` | 三口径价格不同 |
| `test_invalid_adjustment_rejected` | 非法口径报错 |
| `test_quality_status_present` | 质量标记存在 |
| `test_volume_and_amount_units` | 单位正确（amount/volume ≈ 均价） |
| `test_unknown_symbol_raises` | 不存在代码报错 |
| `test_batch_tolerates_individual_failure` | 单只失败不影响整批 |
| `test_industry_lookup_and_members` | 板块与成分股 |

### 10.2 回归结果

```
115 passed in 6.04s
```

- 原有 103 个：**全部通过，零失败**
- 新增 12 个：全部通过
- **没有删除任何原有测试，没有降低任何断言标准**

---

## 十一、存在问题

| # | 问题 | 影响 | 建议 |
| ---: | --- | --- | --- |
| 1 | **未部署生产** | 线上仍只覆盖 235 只 ETF | 需确认后部署 |
| 2 | 本地开发依赖 SSH 隧道 | 隧道断则测试 skip | 生产同机无此问题 |
| 3 | ETF 有 692 只无名称 | 按名称搜索搜不到 | 用代码/instrument_id，不依赖 name |
| 4 | 本地行情表与 InvestData 并存 | 可能重复 | 本阶段**未合并**，见第十二节 |
| 5 | `market_observations` 无复权字段 | 无法区分口径 | 建议 P1 加 `adjustment_type` 列 |
| 6 | 全市场一次载入内存未测 | 未知 | 建议分批（如每批 1000） |
| 7 | 代码格式不一致 | 本地 `sh600519` vs InvestData `600519` | 已提供 `resolve_local_symbol`，但映射规则需在 P1 统一 |

---

## 十二、回滚方式

### 12.1 代码回滚

```bash
cd D:/InvestWeb
git log --oneline          # 找到 baseline 提交
git checkout <baseline> -- backend/app/datasource backend/app/models/market_models.py \
    backend/app/services/market_sync_service.py backend/tests/test_investdata.py
# 或彻底回退
git reset --hard <baseline>
```

### 12.2 数据库回滚

新增的 2 张表删掉即可，**不影响任何现有表**：

```sql
DROP TABLE IF EXISTS market_sync_logs;
DROP TABLE IF EXISTS market_instruments;
```

如需回到开工前状态：

```bash
cp data/backups/invest-phase1-baseline-20261121-212455.db data/invest.db
```

### 12.3 生产回滚

生产**未改动**，无需回滚。

---


## 十二点五、数据源切换（东财 → InvestData）

> 本节为 Phase 1 追加内容，对应「别读东财了，都用新的」的要求。

### 12.5.1 采用方案 A：同步到本地库

不选「下游直连 InvestData」，因为评分/雷达/回测全部读 `market_observations`，
改读取路径的风险面太大。改为：**从 InvestData 同步到本地行情表**，下游零改动。

### 12.5.2 同步结果

```
尝试 40 个 A 股标的，成功 40，无数据源 21
写入 48,480 行（40 × 1212）
耗时 18.2 秒
错误 0
```

覆盖率：

| 来源 | 标的数 | 行数 | 最新 |
| --- | ---: | ---: | --- |
| investdata | 40 | 48,480 | 2026-11-20 |
| legacy_em（旧东财） | 284 | 94,662 | 2026-11-21 |

**为什么是 40 个而不是 61 个**：`instrument_master` 里只有 40 个能映射到 InvestData
（ETF 21 + 指数 8 + 股票 10 + 货币基金 1）。其余 21 个是海外指数（`hkHSI`/`gb_$dji`）、
期货（`hf_GC`/`hf_CL`）、港股（`hk00700`）、手工录入（`WM_CASH_01`/`IF_MAIN`），
**InvestData 没有这些品种，保持原样不动**。

### 12.5.3 修复了一个会让新数据失效的严重问题

`load_close_series` 原逻辑是「先取 limit×3 行再按来源分组」，多来源同日并存时会被摊薄：

```
修复前：请求 300 根 → 实际只拿到 236 根（剩余被其他来源占掉）
修复后：请求 300 根 → 拿到 300 根（优先 investdata_kline）
```

这个 bug 不修，等于新数据白同步——下游永远只看得到 200 多天。

修复方式：**优先按 `source_code='investdata_kline'` 查询并取满 limit；
没有该来源时回退到原逻辑**（海外标的仍走旧源，已验证 `hkHSI`/`hf_GC`/`gb_$dji` 正常）。

### 12.5.4 任务调整

| 任务 | 处理 | 原因 |
| --- | --- | --- |
| `investdata_sync` | **新增**，交易日 18:40 | InvestData 18:30 更新完，同步最近 5 年 |
| `market_quotes` | **停用** | 盘中报价；InvestData 是日频，无盘中数据 |
| `market_history` | **停用** | 日线已由 investdata_sync 替代 |
| `news_collect` | **保留** | **InvestData 没有新闻数据，事件模块依赖它** |
| `etf_quality` / `radar_snapshot` | 保留 | 见下方边界说明 |

开关：`LEGACY_MARKET_JOBS=1` 可恢复旧任务（函数未删除）。
任务总数 15 → 14。

### 12.5.5 切换边界（重要，不要误以为全切了）

| 数据 | 是否已切到 InvestData |
| --- | --- |
| 61 个精管标的的日线（40 个可映射） | ✅ 已切 |
| 海外指数 / 期货 / 港股 / 手工录入 | ❌ 保持旧源（无对应数据） |
| **ETF 候选池 249 只的日线** | ❌ **仍是东财**（`etf_quality` 任务） |
| 财经新闻 | ❌ 必须保留东财 |
| 宏观（世界银行） | ❌ 不变 |

**ETF 候选池那 249 只还没切**，它们由 `etf_quality` 独立补日线。这是目前最大的未切换部分，
影响机会雷达（雷达读的是候选池）。建议 P1 一并切换。

### 12.5.6 5 年上限

```python
MAX_HISTORY_DAYS = 1212   # 线上库实测可用交易日数（2021-11-22 ~ 2026-11-20）
```

本地全量库有 36 年（1990 起），但**只取 5 年**——线上只有 5 年，
若本地能跑线上跑不了，回测结果无法复现。初版误设 1250 根会回推到 2021-09-22，
已修正为 1212 根，与线上严格对齐。

### 12.5.7 本地库与线上的一个差异

本地板块名称**1171/1727 为空**（线上已补齐）。所以本地按中文名搜不到行业，
只能按代码（`881319`）搜。测试已改为按代码检索，两边都成立。

## 十三、下一阶段建议

### 13.1 建议先做的三件事（P0 收尾）

| # | 任务 | 理由 | 预估 |
| ---: | --- | --- | --- |
| 1 | 部署到生产（`INVESTDATA_ENABLED=1`） | 让线上真正用上全市场数据 | 半天 |
| 2 | 在服务器实测全市场批量（9,724 只） | 验证 2.3 分钟估算 | 半天 |
| 3 | 统一代码映射规则 | 本地 `sh600519` ↔ InvestData `600519` | 半天 |

### 13.2 然后进入 P1（全市场机会评分）

| # | 任务 | 依赖 |
| ---: | --- | --- |
| 1 | 技术指标向量化批算（9,724 标的） | 本阶段数据层 |
| 2 | 机会分 v1（趋势/RS/结构/量能 × 风险 × 置信） | 1 |
| 3 | 分类排名 + 变化监控 | 2 |
| 4 | 评分依据接口（因子贡献） | 2 |

### 13.3 明确不做

- 财务/估值（无数据源，`【尚未核验】`）
- ETF 精确持仓（InvestData 只有板块成分，非 PCF）
- 行业供需（无源）
- AI 打分（`LLM_ENABLED=0`）

### 13.4 一个必须先做的验证

P1 评分做出来**先别急着用**，跑一次五分组检验：

```
用 2021-11 ~ 2026-11 的历史
每天算机会分 → 分 5 组 → 看最高分组与最低分组未来 20 日收益差
若差异不显著，评分体系需要重新设计
```

---

## 附录：验证记录

| 时间 | 动作 | 结果 |
| --- | --- | --- |
| 21:24 | Git 初始化 + 基线提交 | `e83fe9b`，工作区干净 |
| 21:25 | 数据库备份 | 28.0 MB，可读（events=137） |
| 21:30 | SSH 隧道建立 | 本地 8100 → 服务器 8100 ✅ |
| 21:35 | 表结构检查 | `daily_bars` 只有 raw；`instruments` 有 sector_type |
| 21:40 | 性能测试 | API 14.4ms/只；SQLite 2.0s/1000 只 |
| 21:45 | 新建 2 表 | 30 张表 = 原 28 + 新 2 |
| 21:50 | 全量同步 | 10,525 条 / 1.9 秒 |
| 21:52 | 幂等验证 | 二次同步 0 新增 |
| 21:55 | **发现升序 bug** | days=260 返回 2021 年数据 |
| 22:00 | 修复并复验 | days=260 → 2025-10-28 ~ 2026-11-20 |
| 22:05 | 单位核对（45 样本） | volume=股，amount=元，与本地库一致 |
| 22:10 | 新增测试 12 个 | 全通过 |
| 22:12 | 全量回归 | **115 passed** |

---

**Phase 1 完成。未部署生产，等待下一步指令。**
