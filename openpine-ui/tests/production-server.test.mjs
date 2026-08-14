import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import http from 'node:http'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { createProductionServer } from '../tools/serve-production.mjs'

test('systemd unit is ordered and lifecycle-coupled to the deployed API unit', async () => {
  const unit = await readFile(new URL('../openpine-ui.service.template', import.meta.url), 'utf8')
  assert.match(unit, /^After=.*\bopenpine-api\.service\b/m)
  assert.match(unit, /^Requires=openpine-api\.service$/m)
  assert.match(unit, /^PartOf=openpine-api\.service$/m)
  assert.doesNotMatch(unit, /openpine-gateway\.service/)
})

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  return server.address().port
}

async function close(server) {
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
}

async function request(port, pathname, options = {}) {
  return await new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1',
      port,
      path: pathname,
      method: options.method ?? 'GET',
      headers: options.headers ?? {},
    }, (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(chunk))
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }))
    })
    req.once('error', reject)
    if (options.body) req.write(options.body)
    req.end()
  })
}

async function websocketHandshake(port, pathname, headers = {}) {
  return await new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: '127.0.0.1', port })
    let response = ''
    const timeout = setTimeout(() => {
      socket.destroy()
      resolve(response)
    }, 2_000)
    socket.once('error', reject)
    socket.once('connect', () => {
      const lines = [
        `GET ${pathname} HTTP/1.1`,
        `Host: 127.0.0.1:${port}`,
        'Connection: Upgrade',
        'Upgrade: websocket',
        'Sec-WebSocket-Version: 13',
        'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==',
        ...Object.entries(headers).map(([name, value]) => `${name}: ${value}`),
        '',
        '',
      ]
      socket.write(lines.join('\r\n'))
    })
    socket.on('data', (chunk) => {
      response += chunk.toString('utf8')
      if (response.includes('\r\n\r\n')) {
        clearTimeout(timeout)
        socket.end()
        resolve(response)
      }
    })
    socket.on('close', () => {
      clearTimeout(timeout)
      resolve(response)
    })
  })
}

test('serves immutable assets, SPA fallback, and browser security headers', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-static-'))
  const outside = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-outside-'))
  await writeFile(path.join(root, 'index.html'), '<main>openpine</main>')
  await writeFile(path.join(outside, 'secret.txt'), 'must-not-leak')
  await symlink(path.join(outside, 'secret.txt'), path.join(root, 'leak.txt'))
  const logs = []
  const server = createProductionServer({ staticRoot: root, apiTarget: 'http://127.0.0.1:9', logger: (event) => logs.push(event) })
  const port = await listen(server)
  try {
    const page = await request(port, '/strategies')
    assert.equal(page.status, 200)
    assert.match(page.body, /openpine/)
    assert.equal(page.headers['content-security-policy'], "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' ws: wss:; font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
    assert.equal(page.headers['x-content-type-options'], 'nosniff')
    assert.equal(page.headers['x-frame-options'], 'DENY')
    assert.equal(page.headers['referrer-policy'], 'no-referrer')
    assert.match(page.headers['x-request-id'], /^[0-9a-f-]{36}$/)
    assert.equal(logs.at(-1).client_ip, '127.0.0.1')
    assert.equal(logs.at(-1).path, '/strategies')
    const leaked = await request(port, '/leak.txt')
    assert.doesNotMatch(leaked.body, /must-not-leak/)
  } finally {
    await close(server)
    await rm(root, { recursive: true, force: true })
    await rm(outside, { recursive: true, force: true })
  }
})

test('streams API requests, preserves authorization, and never logs query values', async () => {
  let seen = null
  const backend = http.createServer((req, res) => {
    const chunks = []
    req.on('data', (chunk) => chunks.push(chunk))
    req.on('end', () => {
      seen = {
        authorization: req.headers.authorization,
        requestId: req.headers['x-request-id'],
        forwardedFor: req.headers['x-forwarded-for'],
        body: Buffer.concat(chunks).toString('utf8'),
        url: req.url,
      }
      res.writeHead(201, { 'content-type': 'application/json' })
      res.end('{"ok":true}')
    })
  })
  const backendPort = await listen(backend)
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-proxy-'))
  await writeFile(path.join(root, 'index.html'), 'ok')
  const logs = []
  const server = createProductionServer({
    staticRoot: root,
    apiTarget: `http://127.0.0.1:${backendPort}`,
    logger: (event) => logs.push(event),
  })
  assert.equal(server.headersTimeout, 15_000)
  assert.equal(server.requestTimeout, 300_000)
  assert.equal(server.keepAliveTimeout, 5_000)
  assert.equal(server.maxHeadersCount, 100)
  const port = await listen(server)
  try {
    const response = await request(port, '/api/backtest/runs/sensitive-run-id?secret=must-not-be-logged', {
      method: 'PATCH',
      headers: {
        authorization: 'Bearer local-token',
        'content-type': 'application/json',
        'user-agent': 'x'.repeat(500),
        'x-forwarded-for': '203.0.113.200',
      },
      body: '{"timezone":"UTC"}',
    })
    assert.equal(response.status, 201)
    assert.equal(response.body, '{"ok":true}')
    assert.equal(seen.authorization, 'Bearer local-token')
    assert.equal(seen.body, '{"timezone":"UTC"}')
    assert.equal(seen.url, '/api/backtest/runs/sensitive-run-id?secret=must-not-be-logged')
    assert.match(seen.requestId, /^[0-9a-f-]{36}$/)
    assert.equal(seen.forwardedFor, '127.0.0.1')
    assert.equal(logs.at(-1).path, '/api/*')
    assert.equal(logs.at(-1).user_agent.length, 256)
    assert.equal(JSON.stringify(logs.at(-1)).includes('must-not-be-logged'), false)
    assert.equal(JSON.stringify(logs.at(-1)).includes('sensitive-run-id'), false)
  } finally {
    await close(server)
    await close(backend)
    await rm(root, { recursive: true, force: true })
  }
})

test('destroys the upstream API request when the browser disconnects', async () => {
  let markStarted
  let markClosed
  const started = new Promise((resolve) => { markStarted = resolve })
  const closed = new Promise((resolve) => { markClosed = resolve })
  const backend = http.createServer((req) => {
    markStarted()
    req.once('close', () => markClosed(req.destroyed))
  })
  const backendPort = await listen(backend)
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-disconnect-'))
  await writeFile(path.join(root, 'index.html'), 'ok')
  const server = createProductionServer({
    staticRoot: root,
    apiTarget: `http://127.0.0.1:${backendPort}`,
    logger: () => {},
  })
  const port = await listen(server)

  try {
    const client = http.request({ hostname: '127.0.0.1', port, path: '/api/slow' })
    client.on('error', () => {})
    client.end()
    await started
    client.destroy()
    const upstreamDestroyed = await Promise.race([
      closed,
      new Promise((_, reject) => setTimeout(() => reject(new Error('upstream remained open')), 2_000)),
    ])
    assert.equal(upstreamDestroyed, true)
  } finally {
    await close(server)
    await close(backend)
    await rm(root, { recursive: true, force: true })
  }
})

test('rejects encoded traversal and non-GET static mutations', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-safe-'))
  await writeFile(path.join(root, 'index.html'), 'ok')
  const server = createProductionServer({ staticRoot: root, apiTarget: 'http://127.0.0.1:9', logger: () => {} })
  const port = await listen(server)
  try {
    const traversal = await request(port, '/%2e%2e/%2e%2e/etc/passwd')
    assert.equal(traversal.status, 403)
    const mutation = await request(port, '/dashboard', { method: 'POST' })
    assert.equal(mutation.status, 405)
    const absoluteForm = await request(port, 'http://attacker.invalid/api/private')
    assert.equal(absoluteForm.status, 400)
  } finally {
    await close(server)
    await rm(root, { recursive: true, force: true })
  }
})

test('proxies authenticated WebSocket upgrades and overwrites spoofed forwarded IP', async () => {
  let seen = null
  let backendSocket = null
  const backend = http.createServer()
  backend.on('upgrade', (req, socket) => {
    seen = req.headers
    backendSocket = socket
    socket.write([
      'HTTP/1.1 101 Switching Protocols',
      'Upgrade: websocket',
      'Connection: Upgrade',
      'Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=',
      '',
      '',
    ].join('\r\n'))
  })
  const backendPort = await listen(backend)
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-ws-'))
  await writeFile(path.join(root, 'index.html'), 'ok')
  const server = createProductionServer({
    staticRoot: root,
    apiTarget: `http://127.0.0.1:${backendPort}`,
    logger: () => {},
  })
  const port = await listen(server)
  try {
    const response = await websocketHandshake(port, '/api/ws/events', {
      Authorization: 'Bearer local-token',
      'X-Forwarded-For': '203.0.113.200',
    })
    assert.match(response, /^HTTP\/1\.1 101 /)
    assert.equal(seen.authorization, 'Bearer local-token')
    assert.equal(seen['x-forwarded-for'], '127.0.0.1')
  } finally {
    backendSocket?.destroy()
    await close(server)
    await close(backend)
    await rm(root, { recursive: true, force: true })
  }
})
