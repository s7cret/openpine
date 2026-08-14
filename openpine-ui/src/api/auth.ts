export const API_TOKEN_STORAGE_KEY = 'openpine.api.bearer-token'

type UnauthorizedListener = () => void
const unauthorizedListeners = new Set<UnauthorizedListener>()
let pendingUnauthorized = false

function browserSessionStorage(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

export function getApiToken(): string {
  return browserSessionStorage()?.getItem(API_TOKEN_STORAGE_KEY)?.trim() ?? ''
}

export function setApiToken(token: string): void {
  const storage = browserSessionStorage()
  if (!storage) return
  const value = token.trim()
  if (value) storage.setItem(API_TOKEN_STORAGE_KEY, value)
  else storage.removeItem(API_TOKEN_STORAGE_KEY)
}

export function clearApiToken(): void {
  browserSessionStorage()?.removeItem(API_TOKEN_STORAGE_KEY)
}

export function getApiWebSocketProtocols(): string[] {
  const token = getApiToken()
  if (!token) return []
  const bytes = new TextEncoder().encode(token)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  const encoded = btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return [`openpine.bearer.b64.${encoded}`, 'openpine.events.v1']
}

export function subscribeUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener)
  if (pendingUnauthorized) {
    pendingUnauthorized = false
    queueMicrotask(() => {
      if (unauthorizedListeners.has(listener)) listener()
    })
  }
  return () => unauthorizedListeners.delete(listener)
}

export function notifyUnauthorized(): void {
  if (!unauthorizedListeners.size) {
    pendingUnauthorized = true
    return
  }
  for (const listener of unauthorizedListeners) listener()
}

export function applyApiAuth<T extends { headers?: any }>(config: T): T {
  const token = getApiToken()
  if (!token) return config
  if (config.headers?.set instanceof Function) {
    config.headers.set('Authorization', `Bearer ${token}`)
  } else {
    config.headers = { ...(config.headers ?? {}), Authorization: `Bearer ${token}` }
  }
  return config
}

function responseMessage(response: Response): string {
  return `API request failed (${response.status}${response.statusText ? ` ${response.statusText}` : ''})`
}

function shouldAttachApiToken(input: RequestInfo | URL): boolean {
  const raw = input instanceof Request ? input.url : String(input)
  if (typeof location === 'undefined') {
    return !/^[a-z][a-z0-9+.-]*:/i.test(raw) && !raw.startsWith('//')
  }
  try {
    return new URL(raw, location.href).origin === location.origin
  } catch {
    return false
  }
}

export async function authenticatedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = getApiToken()
  const attachToken = shouldAttachApiToken(input)
  if (token && attachToken) {
    headers.set('Authorization', `Bearer ${token}`)
  } else if (!attachToken) {
    headers.delete('Authorization')
  }
  const response = await fetch(input, { ...init, headers })
  if (response.status === 401) notifyUnauthorized()
  if (!response.ok) throw new Error(responseMessage(response))
  return response
}

type DownloadOptions = {
  filename?: string
  newTab?: boolean
}

function contentDispositionFilename(response: Response): string {
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try { return decodeURIComponent(encoded) } catch { return encoded }
  }
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? ''
}

export async function downloadApiResource(url: string, options: DownloadOptions = {}): Promise<void> {
  const response = await authenticatedFetch(url)
  const blobUrl = URL.createObjectURL(await response.blob())
  const filename = options.filename || contentDispositionFilename(response)
  try {
    if (options.newTab) {
      const opened = window.open(blobUrl, '_blank', 'noopener,noreferrer')
      if (opened) return
    }
    const anchor = document.createElement('a')
    anchor.href = blobUrl
    if (filename) anchor.download = filename
    anchor.rel = 'noopener'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000)
  }
}
