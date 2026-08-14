import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import test from 'node:test'

import {
  collectAxiosOperations,
  compareBrowserContract,
  compareContract,
} from '../tools/check-api-contract.mjs'

test('collects literal and apiPath axios operations with normalized parameters', () => {
  const operations = collectAxiosOperations(`
    const a = () => api.get('/items')
    const b = (itemId: string) => api.delete(apiPath('/items', itemId))
  `)
  assert.deepEqual(
    operations.map(({ method, path }) => ({ method, path })),
    [
      { method: 'GET', path: '/items' },
      { method: 'DELETE', path: '/items/{}' },
    ],
  )
})

test('checks declared download and WebSocket routes instead of ignoring them', () => {
  const browserContract = {
    http: [{ method: 'GET', path: '/api/files/{file_id}/download' }],
    websocket: ['/api/events'],
  }
  const missing = compareBrowserContract(browserContract, { paths: {} })
  assert.deepEqual(missing.missingHttp, ['GET /api/files/{}/download'])
  assert.deepEqual(missing.missingWebSocket, ['/api/events'])

  const completeSchema = {
    paths: { '/api/files/{file_id}/download': { get: {} } },
    'x-openpine-websocket-paths': ['/api/events'],
  }
  assert.deepEqual(compareBrowserContract(browserContract, completeSchema), {
    missingHttp: [],
    missingWebSocket: [],
  })
})

test('current axios client has no method/path drift from generated OpenAPI', async () => {
  const schemaPath = process.env.OPENPINE_OPENAPI_SCHEMA ?? '/tmp/openpine-openapi-current.json'
  const [source, schema, browserContract] = await Promise.all([
    fs.readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8'),
    fs.readFile(schemaPath, 'utf8'),
    fs.readFile(new URL('../src/api/browser-contract.json', import.meta.url), 'utf8'),
  ])
  const operations = collectAxiosOperations(source)
  const parsedSchema = JSON.parse(schema)
  const result = compareContract(operations, parsedSchema)
  const supplemental = compareBrowserContract(JSON.parse(browserContract), parsedSchema)
  assert.ok(operations.length >= 50, `expected broad client coverage, got ${operations.length}`)
  assert.deepEqual(result.missing, [])
  assert.deepEqual(supplemental, { missingHttp: [], missingWebSocket: [] })
})
