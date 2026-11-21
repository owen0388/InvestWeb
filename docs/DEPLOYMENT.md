# 部署说明

## 1. Windows 本机（开发 / 日常使用）

```powershell
cd D:\InvestWeb
.\start.ps1          # 后端 8000 + 前端 5173
.\start.ps1 -Prod    # 构建前端，单端口 8000
```

- 依赖：Python 3.11+、Node.js 18+。
- 数据库：`data/invest.db`（自动创建）。
- 日志：后端控制台输出；任务错误写入 `job_runs` / `data_source_runs`。

**重要**：定时任务随 API 进程运行。关闭窗口 / 停止服务后任务停止，**浏览器关闭不影响任务**；电脑关机或服务停止则不会采集。

## 2. Docker

```bash
copy .env.example .env
docker compose up -d --build
# 访问 http://127.0.0.1:8000
```

- 数据卷 `./data:/app/data`，容器重建不丢库。
- 容器内时区 `Asia/Shanghai`，调度按该时区执行。
- 健康检查访问 `/api/health`，失败 3 次会标记 unhealthy（不会自动重启，除非配置 restart policy）。
- 单容器单进程：**不要**用多 worker（`--workers > 1`）运行，否则调度器会重复启动。需要横向扩展时，应把调度器拆为独立进程（见下）。

## 3. 切换 PostgreSQL

1. 安装 `psycopg[binary]`（或 `psycopg2-binary`）并加入 `requirements.txt`。
2. 修改 `.env`：

```env
DATABASE_URL=postgresql+psycopg://invest:password@127.0.0.1:5432/invest
```

3. 启动后自动建表；如需迁移历史数据，先导出 SQLite 再逐表导入。

## 4. 云端长期运行

要让系统在个人电脑关机后继续采集与分析：

1. 部署到一台常开的服务器 / 云主机 / NAS，或使用 Docker。
2. 使用 systemd / supervisor / Docker restart policy 保证进程退出后自动重启。
3. 若使用多实例，将调度器与 API 分离：
   - API 实例设置 `SCHEDULER_ENABLED=0`；
   - 单独运行一个调度进程（调用 `app.jobs.scheduler.start_scheduler()` 的脚本）。
4. 配置 `WEBHOOK_URL` 或后续微信通知，把提醒推到手机。

示例 systemd：

```ini
[Unit]
Description=Invest Intelligence
After=network.target

[Service]
WorkingDirectory=/opt/invest
ExecStart=/opt/invest/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend
Restart=always
Environment=TZ=Asia/Shanghai

[Install]
WantedBy=multi-user.target
```

## 5. 配置检查清单

| 项 | 检查方式 |
| --- | --- |
| 数据库可写 | `/api/health` 返回 counts 且非 0 |
| 调度器 | `/api/jobs` → `running: true`，任务有 `next_run_at` |
| 行情 | 「数据源管理」→ 新浪行情状态 healthy，最近成功时间在数分钟内 |
| 新闻 | 有 `raw_documents` 且事件数量增长 |
| 宏观 | 世界银行状态 healthy，宏观页有数据 |
| AI（可选） | 设置页显示已启用，预算未耗尽 |
| 通知 | 阈值设置合理，Webhook 收到测试消息 |

## 6. 常见启动问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ModuleNotFoundError` | 依赖未装进虚拟环境 | 用 `.venv/Scripts/python -m pip install -r backend/requirements.txt` |
| 前端 404 / API 跨域 | 前端 5173 未代理 | 确认 `vite.config.ts` 的 proxy 指向 8000，或使用 `-Prod` 单端口 |
| 页面全为“缺失” | 未采集 | 按「宏观 → 日线 → 行情 → 新闻 → 评分」顺序触发 |
| 行情返回空 | 新浪接口需要 Referer 或临时限流 | 稍后重试；查看 `data_source_runs.error` |
| AI 一直“未配置” | `.env` 未填写或 `LLM_ENABLED=0` | 修改 `.env` 后重启后端 |
