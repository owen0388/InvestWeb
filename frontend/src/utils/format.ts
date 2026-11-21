export function fmtPct(value?: number | null, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`
}

export function fmtPctPoint(value?: number | null, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
}

export function fmtNum(value?: number | null, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtMoney(value?: number | null): string {
  if (value === null || value === undefined) return '—'
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)} 万`
  return value.toFixed(0)
}

export function fmtDate(value?: string | null, withTime = false): string {
  if (!value) return '—'
  const text = value.replace('T', ' ')
  return withTime ? text.slice(0, 16) : text.slice(0, 10)
}

export function fmtTime(value?: string | null): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(11, 16) || value.slice(0, 16)
}

export function relativeTime(value?: string | null): string {
  if (!value) return '—'
  const ts = new Date(value.replace(' ', 'T')).getTime()
  if (Number.isNaN(ts)) return fmtDate(value, true)
  const diff = Date.now() - ts
  const min = Math.round(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hours = Math.round(min / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days} 天前`
  return fmtDate(value)
}

export function changeClass(value?: number | null): string {
  if (value === null || value === undefined) return 'muted'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'muted'
}

const DIRECTION_LABELS: Record<string, string> = {
  bullish: '偏多',
  bearish: '偏空',
  mixed: '方向冲突',
  unknown: '方向未知',
}

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  normal: { label: '正常', cls: 'tag-down' },
  delayed: { label: '延迟', cls: 'tag-warn' },
  missing: { label: '缺失', cls: 'tag-danger' },
  manual: { label: '人工维护', cls: 'tag-neutral' },
  restricted: { label: '受限', cls: 'tag-muted' },
  unverified: { label: '待核验', cls: 'tag-warn' },
  healthy: { label: '健康', cls: 'tag-down' },
  degraded: { label: '降级', cls: 'tag-warn' },
  error: { label: '异常', cls: 'tag-danger' },
  unconfigured: { label: '未配置', cls: 'tag-muted' },
  disabled: { label: '已停用', cls: 'tag-muted' },
  idle: { label: '待运行', cls: 'tag-neutral' },
  running: { label: '运行中', cls: 'tag-primary' },
  success: { label: '成功', cls: 'tag-down' },
  partial: { label: '部分成功', cls: 'tag-warn' },
  failed: { label: '失败', cls: 'tag-danger' },
  skipped: { label: '已跳过', cls: 'tag-muted' },
  sent: { label: '已发送', cls: 'tag-down' },
  pending: { label: '待发送', cls: 'tag-warn' },
}

export function statusLabel(code?: string | null): { label: string; cls: string } {
  if (!code) return { label: '未知', cls: 'tag-muted' }
  return STATUS_MAP[code] || { label: code, cls: 'tag-neutral' }
}

export function directionLabel(value?: string | null): string {
  return DIRECTION_LABELS[value || 'unknown'] || value || '未知'
}

export function directionClass(value?: string | null): string {
  if (value === 'bullish') return 'tag-up'
  if (value === 'bearish') return 'tag-down'
  if (value === 'mixed') return 'tag-warn'
  return 'tag-muted'
}

export function actionClass(action?: string | null): string {
  if (!action) return 'tag-muted'
  if (action === '考虑提高权重') return 'tag-up'
  if (action === '积极研究') return 'tag-primary'
  if (action === '维持配置' || action === '观察等待') return 'tag-neutral'
  if (action === '考虑降低权重') return 'tag-warn'
  if (action === '降低风险') return 'tag-down'
  return 'tag-muted'
}

export function regimeClass(state?: string | null): string {
  if (state === '风险偏好上升') return 'tag-up'
  if (state === '风险偏好下降') return 'tag-down'
  if (state === '极端事件风险') return 'tag-danger'
  if (state === '数据不足') return 'tag-warn'
  return 'tag-neutral'
}

export function importanceClass(value: number): string {
  if (value >= 85) return 'tag-danger'
  if (value >= 72) return 'tag-warn'
  if (value >= 55) return 'tag-primary'
  return 'tag-neutral'
}

export function moduleLabel(module?: string | null): string {
  const map: Record<string, string> = {
    equity_cn: '股票模型',
    fund: '基金模型',
    bond: '固收模型',
    gold: '黄金模型',
    commodity: '商品模型',
    reit: 'REITs 模型',
    cash: '现金模型',
    wealth: '资管模型',
    long_term: '长期资产模型',
    derivative: '衍生品模型',
    overseas: '跨境模型',
    generic: '通用模型',
  }
  return map[module || ''] || '通用模型'
}
