const BASE = ''

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(message: string, status: number, detail: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

function buildQuery(params?: Record<string, unknown>): string {
  if (!params) return ''
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.append(key, String(value))
  })
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

async function handle<T>(resp: Response): Promise<T> {
  const text = await resp.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!resp.ok) {
    const detail = data?.detail ?? data
    const message = typeof detail === 'string' ? detail : detail?.message || `请求失败（${resp.status}）`
    throw new ApiError(message, resp.status, detail)
  }
  return data as T
}

export async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const resp = await fetch(`${BASE}${path}${buildQuery(params)}`, { headers: { Accept: 'application/json' } })
  return handle<T>(resp)
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return handle<T>(resp)
}

export async function put<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return handle<T>(resp)
}

export async function patch<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return handle<T>(resp)
}

export async function del<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  return handle<T>(resp)
}

export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(`${BASE}${path}`, { method: 'POST', body: form })
  return handle<T>(resp)
}
