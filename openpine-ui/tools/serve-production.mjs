import crypto from 'node:crypto'
import fs from 'node:fs'
import { realpath, stat } from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SECURITY_HEADERS = Object.freeze({
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' ws: wss:; font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
  'Referrer-Policy': 'no-referrer',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
})

const MIME_TYPES = Object.freeze({
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
})

const HOP_BY_HOP_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
])

function clientIp(request) {
  const address = request.socket.remoteAddress ?? 'unknown'
  return address.startsWith('::ffff:') ? address.slice(7) : address
}

function requestId(request) {
  const supplied = request.headers['x-request-id']
  if (typeof supplied === 'string' && /^[A-Za-z0-9._-]{1,128}$/.test(supplied)) return supplied
  return crypto.randomUUID()
}

function originFormUrl(rawUrl, base) {
  const value = rawUrl ?? '/'
  if (!value.startsWith('/') || value.startsWith('//')) {
    throw new TypeError('request target must use origin-form')
  }
  return new URL(value, base)
}

function auditPath(pathname) {
  if (pathname.startsWith('/api/')) return '/api/*'
  if (pathname.startsWith('/assets/')) return '/assets/*'
  return pathname
}

function auditUserAgent(request) {
  const value = request.headers['user-agent']
  return typeof value === 'string' ? value.replace(/[\r\n]/g, ' ').slice(0, 256) : null
}

function applySecurityHeaders(response, id) {
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) response.setHeader(name, value)
  response.setHeader('X-Request-ID', id)
}

function rawPathHasTraversal(rawUrl) {
  const rawPath = rawUrl.split('?', 1)[0]
  try {
    return rawPath.split('/').some((segment) => decodeURIComponent(segment) === '..')
  } catch {
    return true
  }
}

function safeStaticPath(staticRoot, requestPath) {
  let decoded
  try {
    decoded = decodeURIComponent(requestPath)
  } catch {
    return null
  }
  const relative = decoded.replace(/^\/+/, '')
  const candidate = path.resolve(staticRoot, relative || 'index.html')
  const root = path.resolve(staticRoot)
  if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) return null
  return candidate
}

async function staticFileFor(staticRoot, pathname) {
  const root = path.resolve(staticRoot)
  let canonicalRoot
  try {
    canonicalRoot = await realpath(root)
  } catch {
    return null
  }
  const candidate = safeStaticPath(staticRoot, pathname)
  if (candidate) {
    try {
      const canonicalCandidate = await realpath(candidate)
      if (
        canonicalCandidate !== canonicalRoot
        && !canonicalCandidate.startsWith(`${canonicalRoot}${path.sep}`)
      ) return null
      const info = await stat(canonicalCandidate)
      if (info.isFile()) return canonicalCandidate
    } catch {
      // SPA fallback below.
    }
  }
  const fallback = path.join(root, 'index.html')
  try {
    const canonicalFallback = await realpath(fallback)
    if (
      canonicalFallback !== canonicalRoot
      && !canonicalFallback.startsWith(`${canonicalRoot}${path.sep}`)
    ) return null
    const info = await stat(canonicalFallback)
    return info.isFile() ? canonicalFallback : null
  } catch {
    return null
  }
}

function proxyRequest(request, response, { apiTarget, id, ip }) {
  const target = originFormUrl(request.url, apiTarget)
  const headers = {}
  for (const [name, value] of Object.entries(request.headers)) {
    if (!HOP_BY_HOP_HEADERS.has(name.toLowerCase()) && value !== undefined) headers[name] = value
  }
  headers.host = target.host
  headers['x-request-id'] = id
  headers['x-forwarded-host'] = request.headers.host ?? ''
  headers['x-forwarded-proto'] = 'http'
  headers['x-forwarded-for'] = ip

  const upstream = http.request({
    protocol: target.protocol,
    hostname: target.hostname,
    port: target.port,
    method: request.method,
    path: `${target.pathname}${target.search}`,
    headers,
  }, (upstreamResponse) => {
    response.statusCode = upstreamResponse.statusCode ?? 502
    for (const [name, value] of Object.entries(upstreamResponse.headers)) {
      if (!HOP_BY_HOP_HEADERS.has(name.toLowerCase()) && value !== undefined) response.setHeader(name, value)
    }
    applySecurityHeaders(response, id)
    const abortDownstream = () => {
      if (!response.destroyed) response.destroy()
    }
    upstreamResponse.once('aborted', abortDownstream)
    upstreamResponse.once('error', abortDownstream)
    upstreamResponse.pipe(response)
  })
  request.once('aborted', () => upstream.destroy())
  response.once('close', () => {
    if (!response.writableEnded) upstream.destroy()
  })
  upstream.once('error', (error) => {
    if (response.destroyed) return
    if (response.headersSent) {
      response.destroy(error)
      return
    }
    response.statusCode = 502
    response.setHeader('Content-Type', 'application/json; charset=utf-8')
    applySecurityHeaders(response, id)
    response.end(JSON.stringify({ detail: 'OpenPine API is unavailable', request_id: id }))
  })
  upstream.setTimeout(300_000, () => upstream.destroy(new Error('OpenPine API request timed out')))
  request.pipe(upstream)
}

function proxyWebSocket(request, clientSocket, head, { apiTarget, id, ip, onStatus }) {
  const safeProtocol = 'openpine.events.v1'
  const credentialPrefix = 'openpine.bearer.b64.'
  const offeredProtocols = String(request.headers['sec-websocket-protocol'] ?? '')
    .split(',')
    .map(value => value.trim())
    .filter(Boolean)
  const credentialProtocols = offeredProtocols.filter(value => value.startsWith(credentialPrefix))
  const validProtocolShape = (
    offeredProtocols.filter(value => value === safeProtocol).length === 1
    && credentialProtocols.length <= 1
    && offeredProtocols.every(value => value === safeProtocol || value.startsWith(credentialPrefix))
  )
  if (!validProtocolShape) {
    onStatus(400)
    clientSocket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
    return
  }

  const target = originFormUrl(request.url, apiTarget)
  const headers = {}
  for (const [name, value] of Object.entries(request.headers)) {
    const normalized = name.toLowerCase()
    if (
      value !== undefined
      && !['host', 'proxy-authorization', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-request-id'].includes(normalized)
    ) headers[name] = value
  }
  headers.host = target.host
  headers.connection = 'Upgrade'
  headers.upgrade = 'websocket'
  headers['x-request-id'] = id
  headers['x-forwarded-host'] = request.headers.host ?? ''
  headers['x-forwarded-proto'] = 'http'
  headers['x-forwarded-for'] = ip

  const upstream = http.request({
    protocol: target.protocol,
    hostname: target.hostname,
    port: target.port,
    method: 'GET',
    path: `${target.pathname}${target.search}`,
    headers,
  })
  let upgradedSocket = null
  let clientClosed = false
  let terminalStatusSelected = false
  const selectTerminalStatus = (value) => {
    if (terminalStatusSelected) return
    terminalStatusSelected = true
    onStatus(value)
  }
  const pendingClientData = head.length ? [Buffer.from(head)] : []
  let pendingClientBytes = head.length
  const maxPendingClientBytes = 64 * 1024
  const closeUpstream = () => {
    clientClosed = true
    if (upgradedSocket) upgradedSocket.destroy()
    else {
      selectTerminalStatus(499)
      upstream.socket?.destroy()
      upstream.destroy()
    }
  }
  const bufferClientData = (chunk) => {
    pendingClientBytes += chunk.length
    if (pendingClientBytes > maxPendingClientBytes) {
      clientSocket.destroy(new Error('OpenPine WebSocket pre-upgrade buffer exceeded'))
      closeUpstream()
      return
    }
    pendingClientData.push(Buffer.from(chunk))
  }
  clientSocket.once('end', closeUpstream)
  clientSocket.prependOnceListener('close', closeUpstream)
  clientSocket.once('error', closeUpstream)
  clientSocket.on('data', bufferClientData)
  clientSocket.resume()
  upstream.once('upgrade', (upstreamResponse, upstreamSocket, upstreamHead) => {
    clientSocket.pause()
    clientSocket.removeListener('data', bufferClientData)
    upstreamSocket.once('error', () => clientSocket.destroy())
    upstreamSocket.once('close', () => clientSocket.destroy())
    if (clientClosed || clientSocket.destroyed) {
      upstreamSocket.destroy()
      return
    }
    const selectedProtocol = upstreamResponse.headers['sec-websocket-protocol']
    if (selectedProtocol !== safeProtocol) {
      selectTerminalStatus(502)
      upstreamSocket.destroy()
      clientSocket.end('HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
      return
    }
    upgradedSocket = upstreamSocket
    upstream.setTimeout(0)
    selectTerminalStatus(101)
    const responseHeaders = []
    for (let index = 0; index < upstreamResponse.rawHeaders.length; index += 2) {
      if (upstreamResponse.rawHeaders[index].toLowerCase() === 'sec-websocket-protocol') continue
      responseHeaders.push(`${upstreamResponse.rawHeaders[index]}: ${upstreamResponse.rawHeaders[index + 1]}`)
    }
    responseHeaders.push(`Sec-WebSocket-Protocol: ${safeProtocol}`)
    clientSocket.write([
      `HTTP/1.1 ${upstreamResponse.statusCode ?? 101} ${upstreamResponse.statusMessage ?? 'Switching Protocols'}`,
      ...responseHeaders,
      '',
      '',
    ].join('\r\n'))
    for (const chunk of pendingClientData) upstreamSocket.write(chunk)
    if (upstreamHead.length) clientSocket.write(upstreamHead)
    upstreamSocket.pipe(clientSocket)
    clientSocket.pipe(upstreamSocket)
    clientSocket.resume()
  })
  upstream.once('response', (upstreamResponse) => {
    upstreamResponse.once('error', () => clientSocket.destroy())
    if (clientClosed || clientSocket.destroyed) {
      upstreamResponse.destroy()
      return
    }
    selectTerminalStatus(upstreamResponse.statusCode ?? 502)
    clientSocket.write(`HTTP/1.1 ${upstreamResponse.statusCode ?? 502} ${upstreamResponse.statusMessage ?? 'Bad Gateway'}\r\nConnection: close\r\n\r\n`)
    upstreamResponse.pipe(clientSocket)
  })
  upstream.once('error', () => {
    selectTerminalStatus(502)
    if (!clientSocket.destroyed && clientSocket.writable) {
      clientSocket.end('HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
    }
  })
  upstream.setTimeout(15_000, () => upstream.destroy(new Error('OpenPine WebSocket handshake timed out')))
  upstream.end()
}

export function createProductionServer({ staticRoot, apiTarget, logger = console.log }) {
  const root = path.resolve(staticRoot)
  const server = http.createServer(async (request, response) => {
    const started = process.hrtime.bigint()
    const id = requestId(request)
    const ip = clientIp(request)
    let pathname = '/'
    try {
      pathname = originFormUrl(request.url, 'http://openpine.local').pathname
    } catch {
      response.statusCode = 400
    }

    response.once('finish', () => {
      const durationMs = Number(process.hrtime.bigint() - started) / 1_000_000
      logger({
        event: 'openpine_ui_access',
        timestamp: new Date().toISOString(),
        request_id: id,
        client_ip: ip,
        method: request.method ?? 'GET',
        path: auditPath(pathname),
        status: response.statusCode,
        duration_ms: Number(durationMs.toFixed(3)),
        user_agent: auditUserAgent(request),
      })
    })

    if (response.statusCode === 400 || rawPathHasTraversal(request.url ?? '/')) {
      response.statusCode = response.statusCode === 400 ? 400 : 403
      applySecurityHeaders(response, id)
      response.end()
      return
    }

    if (pathname === '/health' || pathname.startsWith('/api/')) {
      proxyRequest(request, response, { apiTarget, id, ip })
      return
    }

    if (!['GET', 'HEAD'].includes(request.method ?? 'GET')) {
      response.statusCode = 405
      response.setHeader('Allow', 'GET, HEAD')
      applySecurityHeaders(response, id)
      response.end()
      return
    }

    const filePath = await staticFileFor(root, pathname)
    if (!filePath) {
      response.statusCode = 404
      applySecurityHeaders(response, id)
      response.end()
      return
    }
    response.statusCode = 200
    response.setHeader('Content-Type', MIME_TYPES[path.extname(filePath).toLowerCase()] ?? 'application/octet-stream')
    response.setHeader(
      'Cache-Control',
      filePath.endsWith('index.html') ? 'no-cache' : (pathname.startsWith('/assets/') ? 'public, max-age=31536000, immutable' : 'public, max-age=3600'),
    )
    applySecurityHeaders(response, id)
    if (request.method === 'HEAD') {
      response.end()
      return
    }
    const stream = fs.createReadStream(filePath)
    stream.once('error', () => {
      if (!response.headersSent) response.statusCode = 500
      response.end()
    })
    stream.pipe(response)
  })
  server.on('upgrade', (request, socket, head) => {
    const started = process.hrtime.bigint()
    const id = requestId(request)
    const ip = clientIp(request)
    let pathname = '<invalid>'
    let status = 500
    socket.once('close', () => {
      const durationMs = Number(process.hrtime.bigint() - started) / 1_000_000
      logger({
        event: 'openpine_ui_access',
        timestamp: new Date().toISOString(),
        request_id: id,
        client_ip: ip,
        method: 'WEBSOCKET',
        path: auditPath(pathname),
        status,
        duration_ms: Number(durationMs.toFixed(3)),
        user_agent: auditUserAgent(request),
      })
    })
    try {
      pathname = originFormUrl(request.url, 'http://openpine.local').pathname
    } catch {
      status = 400
      socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
      return
    }
    if (!pathname.startsWith('/api/')) {
      status = 404
      socket.end('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n')
      return
    }
    status = 502
    proxyWebSocket(request, socket, head, {
      apiTarget,
      id,
      ip,
      onStatus: value => { status = value },
    })
  })
  server.headersTimeout = 15_000
  server.requestTimeout = 300_000
  server.keepAliveTimeout = 5_000
  server.maxHeadersCount = 100
  return server
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null
if (invokedPath === fileURLToPath(import.meta.url)) {
  const staticRoot = process.env.OPENPINE_UI_STATIC_ROOT
  if (!staticRoot) throw new Error('OPENPINE_UI_STATIC_ROOT is required')
  const indexPath = path.join(path.resolve(staticRoot), 'index.html')
  if (!fs.existsSync(indexPath)) throw new Error(`OpenPine UI index is missing: ${indexPath}`)
  const host = process.env.OPENPINE_UI_HOST ?? '0.0.0.0'
  const port = Number.parseInt(process.env.OPENPINE_UI_PORT ?? '1888', 10)
  const apiTarget = process.env.OPENPINE_API_TARGET ?? 'http://127.0.0.1:8080'
  const logger = (event) => process.stdout.write(`${JSON.stringify(event)}\n`)
  const server = createProductionServer({ staticRoot, apiTarget, logger })
  server.listen(port, host, () => logger({
    event: 'openpine_ui_started',
    timestamp: new Date().toISOString(),
    host,
    port,
    static_root: path.resolve(staticRoot),
    api_target: apiTarget,
  }))
}
