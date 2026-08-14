import fs from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import ts from 'typescript'

const HTTP_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete'])

function cartesian(parts) {
  let values = ['']
  for (const alternatives of parts) {
    values = values.flatMap((prefix) => alternatives.map((value) => prefix + value))
  }
  return values
}

function evaluatePath(node) {
  if (ts.isStringLiteralLike(node)) return [node.text]
  if (ts.isIdentifier(node)) return [`{${node.text}}`]
  if (ts.isParenthesizedExpression(node)) return evaluatePath(node.expression)
  if (ts.isConditionalExpression(node)) {
    return [...evaluatePath(node.whenTrue), ...evaluatePath(node.whenFalse)]
  }
  if (ts.isTemplateExpression(node)) {
    const parts = [[node.head.text]]
    for (const span of node.templateSpans) {
      parts.push(evaluatePath(span.expression), [span.literal.text])
    }
    return cartesian(parts)
  }
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    return cartesian([evaluatePath(node.left), evaluatePath(node.right)])
  }
  if (
    ts.isCallExpression(node)
    && ts.isIdentifier(node.expression)
    && node.expression.text === 'apiPath'
  ) {
    return cartesian(node.arguments.map((argument, index) =>
      evaluatePath(argument).map((part) => index === 0 ? part.replace(/\/$/, '') : `/${part.replace(/^\//, '')}`),
    ))
  }
  throw new Error(`Unsupported API path expression: ${node.getText()}`)
}

export function normalizeContractPath(value) {
  const normalized = value.replace(/\{[^}]+\}/g, '{}').replace(/\/+$/, '')
  return normalized || '/'
}

export function collectAxiosOperations(sourceText, fileName = 'client.ts') {
  const source = ts.createSourceFile(fileName, sourceText, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const operations = []
  const visit = (node) => {
    if (
      ts.isCallExpression(node)
      && ts.isPropertyAccessExpression(node.expression)
      && ts.isIdentifier(node.expression.expression)
      && node.expression.expression.text === 'api'
      && HTTP_METHODS.has(node.expression.name.text)
    ) {
      if (node.arguments.length === 0) throw new Error(`API call has no path: ${node.getText(source)}`)
      const method = node.expression.name.text.toUpperCase()
      for (const apiPath of evaluatePath(node.arguments[0])) {
        operations.push({ method, path: normalizeContractPath(apiPath), source: node.getText(source) })
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  return operations
}

export function openApiOperations(schema) {
  const operations = new Set()
  for (const [apiPath, pathItem] of Object.entries(schema.paths ?? {})) {
    const clientPath = apiPath === '/api' ? '/' : apiPath.replace(/^\/api(?=\/)/, '')
    for (const method of Object.keys(pathItem)) {
      if (HTTP_METHODS.has(method)) operations.add(`${method.toUpperCase()} ${normalizeContractPath(clientPath)}`)
    }
  }
  return operations
}

export function compareContract(clientOperations, schema) {
  const available = openApiOperations(schema)
  const missing = clientOperations.filter(({ method, path: apiPath }) => !available.has(`${method} ${apiPath}`))
  return { available, missing }
}

export function compareBrowserContract(contract, schema) {
  const availableHttp = new Set()
  for (const [apiPath, pathItem] of Object.entries(schema.paths ?? {})) {
    for (const method of Object.keys(pathItem)) {
      if (HTTP_METHODS.has(method)) {
        availableHttp.add(`${method.toUpperCase()} ${normalizeContractPath(apiPath)}`)
      }
    }
  }
  const availableWebSocket = new Set(
    (schema['x-openpine-websocket-paths'] ?? []).map(normalizeContractPath),
  )
  const missingHttp = (contract.http ?? [])
    .map(({ method, path: apiPath }) => `${method.toUpperCase()} ${normalizeContractPath(apiPath)}`)
    .filter((operation) => !availableHttp.has(operation))
  const missingWebSocket = (contract.websocket ?? [])
    .map(normalizeContractPath)
    .filter((apiPath) => !availableWebSocket.has(apiPath))
  return { missingHttp, missingWebSocket }
}

async function main() {
  const [clientPath, schemaPath, browserContractPath] = process.argv.slice(2)
  if (!clientPath || !schemaPath) {
    throw new Error('usage: node tools/check-api-contract.mjs <client.ts> <openapi.json> [browser-contract.json]')
  }
  const [source, schemaText] = await Promise.all([
    fs.readFile(path.resolve(clientPath), 'utf8'),
    fs.readFile(path.resolve(schemaPath), 'utf8'),
  ])
  const clientOperations = collectAxiosOperations(source, clientPath)
  const schema = JSON.parse(schemaText)
  const { available, missing } = compareContract(clientOperations, schema)
  const supplemental = browserContractPath
    ? compareBrowserContract(JSON.parse(await fs.readFile(path.resolve(browserContractPath), 'utf8')), schema)
    : { missingHttp: [], missingWebSocket: [] }
  const uniqueClient = new Set(clientOperations.map(({ method, path: apiPath }) => `${method} ${apiPath}`))
  process.stdout.write(`${JSON.stringify({ client_operations: uniqueClient.size, openapi_operations: available.size, missing, supplemental }, null, 2)}\n`)
  if (missing.length || supplemental.missingHttp.length || supplemental.missingWebSocket.length) process.exitCode = 1
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname)) {
  await main()
}
