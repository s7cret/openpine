import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import { chmod, copyFile, lstat, mkdir, mkdtemp, readFile, readdir, rm, stat, symlink, unlink } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'

import { packageProductionCandidate, publishNoReplace } from '../tools/package-production.mjs'

const UI_ROOT = path.resolve(import.meta.dirname, '..')

async function makeWritable(root) {
  const value = await lstat(root).catch(() => null)
  if (!value) return
  if (value.isSymbolicLink()) {
    await unlink(root)
    return
  }
  if (value.isDirectory()) {
    await chmod(root, 0o700)
    for (const entry of await readdir(root)) await makeWritable(path.join(root, entry))
  } else {
    await chmod(root, 0o600)
  }
}

async function createSourceFixture(root) {
  await mkdir(path.join(root, 'dist'), { recursive: true })
  await mkdir(path.join(root, 'tools'), { recursive: true })
  await copyFile(path.join(UI_ROOT, 'dist/index.html'), path.join(root, 'dist/index.html'))
  for (const relativePath of [
    'openpine-ui.service.template',
    'run-production.sh',
    'tools/serve-production.mjs',
  ]) {
    await copyFile(path.join(UI_ROOT, relativePath), path.join(root, relativePath))
  }
}

async function listCandidateTree(root, relative = '') {
  const files = []
  const directories = []
  for (const entry of await readdir(path.join(root, relative), { withFileTypes: true })) {
    const child = path.join(relative, entry.name)
    if (entry.isDirectory()) {
      directories.push(child)
      const nested = await listCandidateTree(root, child)
      directories.push(...nested.directories)
      files.push(...nested.files)
    } else if (entry.isFile()) files.push(child)
    else throw new Error(`unexpected entry in test candidate: ${child}`)
  }
  return { directories: directories.sort(), files: files.sort() }
}

async function sha256(filePath) {
  return crypto.createHash('sha256').update(await readFile(filePath)).digest('hex')
}

test('packages a self-contained immutable UI candidate with verified manifest', async () => {
  const destination = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-candidate-'))
  await rm(destination, { recursive: true, force: true })

  try {
    const manifest = await packageProductionCandidate({
      sourceRoot: UI_ROOT,
      destination,
    })

    const expected = [
      'dist/index.html',
      'openpine-ui.service.template',
      'run-production.sh',
      'tools/serve-production.mjs',
    ]
    for (const relativePath of expected) {
      assert.equal((await stat(path.join(destination, relativePath))).isFile(), true)
    }

    const tree = await listCandidateTree(destination)
    const packagedFiles = tree.files.filter((relativePath) => relativePath !== 'manifest.json')
    assert.deepEqual(Object.keys(manifest.files).sort(), packagedFiles)
    assert.deepEqual([...manifest.directories].sort(), tree.directories)
    for (const relativePath of packagedFiles) {
      assert.equal(manifest.files[relativePath], await sha256(path.join(destination, relativePath)))
      const expectedMode = relativePath === 'run-production.sh' ? 0o555 : 0o444
      assert.equal((await stat(path.join(destination, relativePath))).mode & 0o777, expectedMode)
    }
    assert.equal((await stat(path.join(destination, 'manifest.json'))).mode & 0o777, 0o444)
    assert.equal((await stat(destination)).mode & 0o777, 0o555)
    for (const relativePath of manifest.directories) {
      assert.equal((await stat(path.join(destination, relativePath))).mode & 0o777, 0o555)
    }
    assert.deepEqual(
      JSON.parse(await readFile(path.join(destination, 'manifest.json'), 'utf8')),
      manifest,
    )
    assert.equal(manifest.schema, 'openpine.ui-candidate.v1')
  } finally {
    await makeWritable(destination)
    await rm(destination, { recursive: true, force: true })
  }
})

test('refuses to replace an existing empty destination', async () => {
  const destination = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-existing-'))
  try {
    await assert.rejects(
      packageProductionCandidate({ sourceRoot: UI_ROOT, destination }),
      /already exists/,
    )
    assert.deepEqual(await readdir(destination), [])
  } finally {
    await makeWritable(destination)
    await rm(destination, { recursive: true, force: true })
  }
})

test('rejects a destination whose canonical parent escapes into the source tree', async () => {
  const sandbox = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-containment-'))
  const sourceRoot = path.join(sandbox, 'source')
  const parentLink = path.join(sandbox, 'source-link')
  await createSourceFixture(sourceRoot)
  await symlink(sourceRoot, parentLink, 'dir')
  try {
    await assert.rejects(
      packageProductionCandidate({
        sourceRoot,
        destination: path.join(parentLink, 'candidate'),
      }),
      /outside the source tree/,
    )
    await assert.rejects(stat(path.join(sourceRoot, 'candidate')))
  } finally {
    await makeWritable(sandbox)
    await rm(sandbox, { recursive: true, force: true })
  }
})

test('preserves a raced-in destination and cleans a read-only temporary candidate', async () => {
  const sandbox = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-race-'))
  const sourceRoot = path.join(sandbox, 'source')
  const destination = path.join(sandbox, 'candidate')
  await createSourceFixture(sourceRoot)
  try {
    const packaging = packageProductionCandidate({ sourceRoot, destination })
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const entries = await readdir(sandbox)
      if (entries.some((entry) => entry.startsWith('candidate.tmp-'))) break
      await delay(1)
    }
    await mkdir(destination)
    await assert.rejects(packaging, /already exists/)
    assert.deepEqual(await readdir(destination), [])
    assert.equal(
      (await readdir(sandbox)).some(
        (entry) => entry.startsWith('candidate.tmp-') || entry.endsWith('.openpine-package.lock'),
      ),
      false,
    )
  } finally {
    await makeWritable(sandbox)
    await rm(sandbox, { recursive: true, force: true })
  }
})

test('rejects symlinks copied from dist and leaves no partial destination', async () => {
  const sandbox = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-symlink-'))
  const sourceRoot = path.join(sandbox, 'source')
  const destination = path.join(sandbox, 'candidate')
  await createSourceFixture(sourceRoot)
  await symlink('/etc/passwd', path.join(sourceRoot, 'dist/leak.txt'))
  try {
    await assert.rejects(
      packageProductionCandidate({ sourceRoot, destination }),
      /unsupported filesystem entry/,
    )
    await assert.rejects(stat(destination))
    assert.equal(
      (await readdir(sandbox)).some(
        (entry) => entry.startsWith('candidate.tmp-') || entry.endsWith('.openpine-package.lock'),
      ),
      false,
    )
  } finally {
    await makeWritable(sandbox)
    await rm(sandbox, { recursive: true, force: true })
  }
})

test('the publish primitive never replaces an existing empty directory', async () => {
  const sandbox = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-noreplace-'))
  const temporary = path.join(sandbox, 'temporary')
  const destination = path.join(sandbox, 'destination')
  await mkdir(temporary)
  await mkdir(destination)
  await copyFile(path.join(UI_ROOT, 'dist/index.html'), path.join(temporary, 'index.html'))
  const destinationInode = (await stat(destination)).ino
  try {
    await assert.rejects(publishNoReplace(temporary, destination), /already exists/)
    assert.equal((await stat(destination)).ino, destinationInode)
    assert.equal((await stat(path.join(temporary, 'index.html'))).isFile(), true)
  } finally {
    await makeWritable(sandbox)
    await rm(sandbox, { recursive: true, force: true })
  }
})

test('rejects an in-source missing parent without creating it', async () => {
  const sandbox = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-parent-side-effect-'))
  const sourceRoot = path.join(sandbox, 'source')
  const forbiddenParent = path.join(sourceRoot, 'new-parent')
  await createSourceFixture(sourceRoot)
  try {
    await assert.rejects(
      packageProductionCandidate({
        sourceRoot,
        destination: path.join(forbiddenParent, 'candidate'),
      }),
      /outside the source tree/,
    )
    await assert.rejects(stat(forbiddenParent))
  } finally {
    await makeWritable(sandbox)
    await rm(sandbox, { recursive: true, force: true })
  }
})

test('records and makes empty candidate directories read-only', async () => {
  const sandbox = await mkdtemp(path.join(os.tmpdir(), 'openpine-ui-empty-dirs-'))
  const sourceRoot = path.join(sandbox, 'source')
  const destination = path.join(sandbox, 'candidate')
  await createSourceFixture(sourceRoot)
  await mkdir(path.join(sourceRoot, 'dist/empty/nested'), { recursive: true })
  try {
    const manifest = await packageProductionCandidate({ sourceRoot, destination })
    assert.ok(manifest.directories.includes('dist/empty'))
    assert.ok(manifest.directories.includes('dist/empty/nested'))
    assert.equal((await stat(path.join(destination, 'dist/empty'))).mode & 0o777, 0o555)
    assert.equal((await stat(path.join(destination, 'dist/empty/nested'))).mode & 0o777, 0o555)
  } finally {
    await makeWritable(sandbox)
    await rm(sandbox, { recursive: true, force: true })
  }
})
