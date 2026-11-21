# 百度云部署记录（180.76.54.211）

> 部署日期：2026-11-19
> 系统：Ubuntu 24.04.4 LTS / 2 核 / 3.3G 内存 / 79G 磁盘

## 访问方式

```
https://invest.mengrengjiu.com/     ← 正式地址（HTTPS，对外公开）
http://180.76.54.211/               ← 备用，http 直连（无证书，仅排查用）
```

**已关闭访问认证**（`AUTH_PASSWORD` 留空）。公开前核对过：持仓只有「示例组合」、
`trade_journal` 为 0 条、`system_settings` 不含密钥。认证代码保留，填入密码即可重新启用。

## 域名与证书

| 域名 | 指向 | 服务 | 证书 |
| --- | --- | --- | --- |
| invest.mengrengjiu.com | 8000 | InvestWeb | Let's Encrypt，至 2027-02-17 |
| www.mengrengjiu.com / 裸域 | 3000 | FaceSeek | TrustAsia，至 2027-02-17 |
| 180.76.54.211 | 8000 | InvestWeb | 无（IP 不能签证书） |

**分流原理**：同一个 IP 上多站点靠 HTTP 的 `Host` 头与 TLS 的 `SNI` 区分，
nginx 按 `server_name` 匹配。实测：

```
Host: www.mengrengjiu.com     → FaceSeek 接口 200 / InvestWeb 接口 404
Host: invest.mengrengjiu.com  → FaceSeek 接口 404 / InvestWeb 接口 200
```

**证书是按域名签发的**：FaceSeek 那张只含 `www.mengrengjiu.com` 与裸域，
子域名需要单独证书，因此为 invest 另签了一张 Let's Encrypt（免费）。

自动续期：`certbot.timer`（enabled + active），续期后经
`/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` 自动重载 nginx。

## 与 FaceSeek 的隔离

服务器上原有 FaceSeek（mengrengjiu.com，PM2 + node:3000 + nginx:80/443）。
本次部署**没有修改它的任何文件**，只做了新增：

| 新增内容 | 路径 |
| --- | --- |
| 应用目录 | `/opt/investweb`（264M） |
| systemd 服务 | `/etc/systemd/system/investweb.service` |
| nginx 站点 | `/etc/nginx/sites-available/investweb` + `sites-enabled/investweb`（软链） |
| 防火墙规则 | `ufw allow 8000/tcp`（备注 investweb） |
| 日志 | `/var/log/investweb.log` |

隔离方式：nginx 按 `server_name 180.76.54.211` 精确匹配 IP 访问 → 反代到 127.0.0.1:8000；
域名请求仍落到 FaceSeek 的 server 块，互不干扰。

**旁证**：部署后 `https://www.mengrengjiu.com/` 与 `/api/v1/health` 均为 200。

## 服务管理

```bash
systemctl status investweb      # 状态
systemctl restart investweb     # 重启（改了代码后）
systemctl stop investweb        # 停止
journalctl -u investweb -n 50   # 或 tail -f /var/log/investweb.log
```

代码更新后重启即可；前端已构建在 `frontend/dist`，由后端静态托管。

## 配置

`/opt/investweb/.env`（权限 600）：

```
APP_ENV=prod
DATABASE_URL=sqlite:////opt/investweb/data/invest.db
AUTH_USER=admin
AUTH_PASSWORD=****
SCHEDULER_ENABLED=1
TIMEZONE=Asia/Shanghai
```

改密码后 `systemctl restart investweb` 生效。

## 定时任务

15 个任务随服务启动（见 `app/jobs/scheduler.py`），关键时间点：

```
每 10 分钟     行情快照
每 30 分钟     新闻采集
交易日 16:15   ETF 候选池
交易日 16:35   候选池日线与质量分
交易日 16:45   雷达快照
每日   02:30   数据库备份（data/backups，保留 7 份）
```

## 回滚

```bash
systemctl disable --now investweb
rm -f /etc/nginx/sites-enabled/investweb
systemctl reload nginx
ufw delete allow 8000/tcp
# 数据与代码仍保留在 /opt/investweb，确认无用后再删
```

## 移动端适配

一套代码响应式，不做两套前端。要点（`frontend/src/styles/main.css`）：

| 项 | 做法 |
| --- | --- |
| 导航 | ≤900px 侧栏变抽屉（汉堡按钮 + 遮罩），路由切换自动收起 |
| 多栏布局 | `.grid[style*="grid-template-columns"]` 统一压成单列——各页面把栏宽写在内联样式里，优先级高于类，媒体查询管不到 |
| KPI 卡片 | ≤640px 改两列，避免四个卡片占满四屏 |
| 表格 | `.table-wrap` 内部横向滚动，不撑破页面 |
| 溢出防护 | `.grid > *, .split > * { min-width: 0 }`，否则 grid 子项会被宽表格撑开导致整页横向溢出 |
| 顶栏 | 允许换行 + 文本省略，避免「市场状态」撑破视口 |

**测量方法**：`node scripts/screenshot-mobile.js <url> <out> 390 844`
用 CDP 的 `Emulation.setDeviceMetricsOverride` 模拟真机视口，并打印 `innerWidth/scrollWidth`。
不要用 `chrome --headless --window-size=390`：Windows 上有约 485px 最小窗口宽度，
截出来会显示「页面右侧被裁」的假象，容易误判成布局溢出。

## 已知限制

1. **完全公开，任何人都能调用写接口**，包括「更新数据」（`POST /api/refresh`）。
   已有并发锁（重复触发返回 409），但外部仍可反复触发采集。若担心被滥用，
   可对非 GET 请求加来源限制。
2. 行情数据随定时任务更新（已验证 2026-11-19 正常采集）；ETF 候选池需等交易日 16:15，
   否则雷达用的是上一交易日的池子。
3. 内存 3.3G 中当前占用约 900M，候选池全量刷新（620 次详情请求）期间会上升到 1.5G 左右，可接受。
4. 单进程 uvicorn，无 multi-worker。SQLite 写入是串行的，多 worker 反而会锁冲突。
