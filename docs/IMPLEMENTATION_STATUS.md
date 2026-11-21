# 实施状态

> 最后更新：2026-11-05 14:00（本机实测）
> 运行环境：Windows 11 / Python 3.13.12 / Node 22.22.2 / SQLite

## 1. 阶段进度（对照任务书第十七章）

| 阶段 | 目标 | 状态 | 说明 |
| --- | --- | --- | --- |
| 0 | 检查工作区与环境 | ✅ 完成 | 工作区仅有任务书；确认 Python/Node/网络可用 |
| 1 | 工程骨架与资产目录 | ✅ 完成 | FastAPI + SQLAlchemy + 27 张表；12 个一级类别 / 36 节点 / 61 个登记品种；Vue 3 前端 11 页 |
| 2 | 数据采集 | ✅ 完成（真实数据） | 新浪行情、东方财富日线与新闻、世界银行宏观、基金净值；日线自动回退 + 熔断；FRED / Tushare / AKShare / GDELT 以状态呈现 |
| 3 | AI 事件分析 | ⚠️ 规则引擎完成，**AI 实际调用 0 次** | 去重 → 聚类 → 分类 → 重要性 → 证据 → 传导 → 资产关联 → 版本快照全部可跑；模型适配层、缓存、预算、用量统计已实现，但未提供密钥，`model_usage` 表为 0 条 |
| 4 | 信号引擎 | ✅ 完成 | 12 类模块配置、6 类特征、数据质量门槛、7 种建议动作、风险状态 6 维加权；ETF 质量分 v0 + 机会雷达每日 0–10 条 |
| 5 | 组合配置 | ✅ 完成 | 投资者配置、持仓 CRUD + CSV 导入、战术倾斜、有界单纯形投影、换手上限、风险分析、版本历史 |
| 6 | 历史验证 | ✅ 完成 | 五策略对比、分年表现、参数敏感性；修复权重不漂移 bug 后重跑 |
| 7 | 可靠性与部署 | ✅ 完成 | 每日备份（sqlite backup API，保留 7 份）、失败告警推送、企业微信格式适配 |

## 2. 实际运行数据（2026-11-05 14:00）

| 项目 | 数量 |
| --- | --- |
| 数据库表 | 27（新增 `etf_universe`、`trade_journal`） |
| 资产类别（含二级） | 36（一级 12，REIT 已排除） |
| 登记品种 | 61（REIT 3 只已移除） |
| 行情观测 | 76,519 条（含候选池日线约 5.2 万条） |
| 宏观观测 | 90 条（中国/美国/全球 × 5 指标 × 6 年） |
| 原始新闻 | 169 条 |
| 事件 | 91 条 |
| 事件证据 | 99 条（全部带原文链接） |
| 事件→资产影响 | 381 条 |
| 资产信号 | 51 条 |
| ETF 候选池 | 1,680 只登记 / 254 只候选（收盘后）· 223 只（盘中） |
| ETF 质量分 | 候选池 100% 覆盖（4 项指标） |
| 机会雷达 | 每日 0–10 条；2026-11-05 输出 6 条（消费2/红利2/债券2） |
| 交易日志 | 0 条（结构已就绪，等你真实录入） |
| 目标权重版本 | 7 版 |
| 通知 | 19 条（站内，已去重） |
| **LLM 调用** | **0 次**（未配置密钥） |
| 测试 | 97 passed |

## 3. 文件清单

### 后端（`backend/`）

```text
app/main.py                         FastAPI 入口、CORS、SPA 托管、异常处理
app/core/config.py                  环境变量配置
app/core/db.py                      SQLite/PostgreSQL 引擎、会话、建表
app/core/timeutil.py                中国时区与宽松时间解析
app/core/jsonutil.py                NaN/Inf 清洗
app/models/entities.py              25 张表模型
app/providers/base.py               适配器接口、HTTP 重试/退避/限频
app/providers/sina_quote.py         新浪行情（GBK 解码、数据类型判定）
app/providers/eastmoney.py          东方财富/腾讯/新浪日线（自动回退 + 熔断）、东方财富新闻
app/providers/macro.py              世界银行（并发抓取）、FRED
app/providers/optional.py           GDELT / Tushare / AKShare（状态降级）
app/providers/registry.py           适配器注册表
app/services/config_service.py      YAML 配置加载
app/services/seed_service.py        目录/示例组合初始化（幂等）
app/services/collector_service.py   采集编排、运行记录、幂等写入
app/services/news_pipeline.py       新闻清洗、哈希去重、入库
app/services/event_engine.py        聚类、分类、重要性、证据、传导、版本
app/services/market_data.py         序列读取与指标函数
app/services/regime_service.py      风险状态 6 维加权
app/services/scoring_service.py     资产评分（模块化权重、缺失处理）
app/services/allocation_service.py  战术倾斜 + 上下界投影 + 风险分析
app/services/backtest_service.py    回测（防前视、成本、样本内外）
app/services/notification_service.py 站内/Webhook 通知、每日简报
app/services/audit.py               审计日志
app/services/*                      汇总：11 个服务模块
app/ai/llm.py                       OpenAI 兼容适配层、成本估算
app/ai/event_analyst.py             结构化事件分析、缓存、预算、升级策略
app/jobs/scheduler.py               11 个定时任务、任务记录、调度状态
app/api/*.py                        6 个路由模块 + 请求模型 + 分页工具
tests/                              31 个测试
```

### 前端（`frontend/src/`）

```text
api/client.ts, api/endpoints.ts     统一请求与全部接口封装
components/                         EChart、SectionCard、SectionBlock
layouts/AppLayout.vue               侧边导航 + 顶部状态栏
pages/OverviewPage.vue              市场总览
pages/OpportunitiesPage.vue         全市场机会雷达
pages/EventsPage.vue                新闻与事件中心（列表 + 证据链抽屉）
pages/AssetsPage.vue                全品类资产目录
pages/AssetDetailPage.vue           单品分析
pages/AllocationPage.vue            资产配置（配置/持仓/风险三个标签页）
pages/MacroPage.vue                 宏观与风险
pages/BacktestPage.vue              策略回测
pages/SourcesPage.vue               数据源管理
pages/SettingsPage.vue              系统设置
styles/main.css                     设计系统（浅色、金融研究工作台风格）
```

### 配置与文档

```text
config/asset_classes.yaml           12 类 / 36 节点
config/instruments.yaml             64 个品种
config/data_sources.yaml            11 个数据源
config/signal_rules.yaml            事件分类、评分权重、风险状态、AI 升级条件
config/risk_limits.yaml             组合约束、示例基准、风险偏好预设
docs/ARCHITECTURE.md                架构与算法说明
docs/DATA_SOURCES.md                数据源实测与权限
docs/DATABASE.md                    表结构与迁移建议
docs/DEPLOYMENT.md                  本机/Docker/云端部署
docs/KNOWN_LIMITATIONS.md           已知限制
docs/TEST_REPORT.md                 测试报告
docs/screenshots/                   10 个页面实际截图
```

## 4. 关键实现决策

1. **数据真实性优先于覆盖率**：没有 60 条以上价格序列就不打分；没有估值数据就把“估值”列入缺失项，不写死 PE 阈值。
2. **采集回退链**：东方财富日线在短时间内高频访问后会直接断开连接（实测 43 个请求全部失败），因此实现 `东方财富 → 腾讯 → 新浪` 回退，并把真实来源写入 `market_observations.source_code`。一次运行内连续失败 2 次即熔断该来源，避免整轮被拖慢。
3. **二级类别用层级映射归并**，不用字符串截断（`CN_EQ_INDEX` 按 `_` 截断会变成 `CN`，曾导致配置页拿不到股票评分）。
4. **约束求解用投影而非归一化**：简单归一化会破坏上下限，因此用有界单纯形对偶二分，并在不可行/换手冲突时明确告警。
5. **回测只使用价量信号**：宏观 vintage 数据未接入，若把当前宏观数据回填历史会造成前视偏差，因此回测策略只含趋势/动量/波动。
6. **AI 不参与数字**：评分、权重、风险指标全部由可测试代码产生；AI 只做事件结构化，未配置时明确显示。

## 5. 对照原始任务书的差距（2026-11-05）

按任务书章节逐项核对，只列**未完成或有实质缺口**的部分。

| 任务书章节 | 要求 | 现状 | 缺口 |
| --- | --- | --- | --- |
| 十一、机会雷达 | 研究卡片 20 项字段 | ✅ 20 项齐全（含标题/首次发现/驱动/风险/相反证据/建议动作/权重区间/环比） | 无 |
| 十、历史回测 | 12 项能力 | ✅ 12 项（五策略对比 + 分年 + 单参数敏感性） | 无 |
| 六、AI 模型 | 结构化输出、可回溯、Token 可查 | 代码完整，`model_usage` = **0 条** | **从未真实调用**，只有单测验证了容错分支 |
| 五 / V4 §4 | 事件 → ETF 映射、信息差 5 形态 | ✅ 两级映射 + 3 类可算因子 | 预期分歧/跨市场时差无数据，**显式声明不计算** |
| 十二、前端 | 10 个页面连接真实接口 | 13 页，新增机会雷达（卡片）与交易日志 | 无 |
| 十五、通知 | 站内 / 邮件 / 微信 | 站内可用；企业微信格式已适配 + 测试接口 | 需你填 `WEBHOOK_URL` 才能真发；邮件未做 |
| 十七、阶段 7 | 监控告警、备份 | `health_check` 任务每 10 分钟跑 | 无外部告警通道；备份是手动 `cp`，无定时任务 |

### 优先级建议（按"能否提高胜率"排序）

| 顺序 | 事项 | 理由 |
| --- | --- | --- |
| 1 | 回测四策略对比 + 阶段表现 | 目前**没有任何证据**证明雷达信号优于持有不动；不做这个，前面所有评分都无法证伪 |
| 2 | 雷达研究卡片补全（8 → 21 项） | 现在只输出"分数 + 一句理由"，不足以支撑一次真实决策 |
| 3 | 事件 → ETF 映射 + 信息差 | 这是"信息差系统"与普通趋势系统的分水岭，目前完全空白 |
| 4 | AI 接入（需密钥） | 代码就绪，缺 `LLM_API_KEY`；无密钥时规则引擎可独立工作 |
| 5 | 前端补雷达页、通知配置、监控告警 | 体验与运维，放最后 |

## 6. 本机运行

```powershell
cd D:\InvestWeb
.\start.ps1          # 前端 5173 + 后端 8000
.\start.ps1 -Prod    # 构建后单端口 8000
```

当前已有一个实例运行在 `http://127.0.0.1:8000`（单端口模式，前端已构建）。

首次数据初始化顺序：宏观 → 日线历史 → 行情 → 新闻 → 评分 → 目标权重 → 回测。
