import crypto from 'node:crypto'
import { execFile } from 'node:child_process'
import { chmod, cp, lstat, mkdir, open, readFile, readdir, realpath, rm, stat, unlink } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

const CANDIDATE_FILES = Object.freeze([
  ['openpine-ui.service.template', 'openpine-ui.service.template'],
  ['run-production.sh', 'run-production.sh'],
  ['tools/serve-production.mjs', 'tools/serve-production.mjs'],
])

async function assertFile(filePath, label) {
  const value = await stat(filePath).catch(() => null)
  if (!value?.isFile()) throw new Error(`${label} is missing: ${filePath}`)
}

async function collectTree(root, relative = '') {
  const directory = path.join(root, relative)
  const entries = await readdir(directory, {
    withFileTypes: true,
  })
  const files = []
  const directories = []
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const child = path.join(relative, entry.name)
    if (entry.isDirectory()) {
      directories.push(child)
      const nested = await collectTree(root, child)
      directories.push(...nested.directories)
      files.push(...nested.files)
    } else if (entry.isFile()) files.push(child)
    else throw new Error(`candidate contains unsupported filesystem entry: ${child}`)
  }
  return { directories, files }
}

async function sha256(filePath) {
  return crypto.createHash('sha256').update(await readFile(filePath)).digest('hex')
}

async function makeReadOnly(root, files, directories) {
  for (const relativePath of files) {
    const mode = relativePath === 'run-production.sh' ? 0o555 : 0o444
    await chmod(path.join(root, relativePath), mode)
  }
  for (const relativePath of [...directories, '.'].sort((a, b) => b.length - a.length)) {
    await chmod(path.join(root, relativePath), 0o555)
  }
}

async function existingEntry(filePath) {
  try {
    return await lstat(filePath)
  } catch (error) {
    if (error?.code === 'ENOENT') return null
    throw error
  }
}

async function removeCandidateTree(root) {
  const value = await existingEntry(root)
  if (!value) return
  if (value.isSymbolicLink()) {
    await unlink(root)
    return
  }
  if (value.isDirectory()) {
    await chmod(root, 0o700)
    for (const entry of await readdir(root)) {
      await removeCandidateTree(path.join(root, entry))
    }
  } else {
    await chmod(root, 0o600)
  }
  await rm(root, { recursive: true, force: true })
}

async function projectedCanonicalTarget(requestedTarget) {
  let ancestor = path.dirname(requestedTarget)
  const missingParents = []
  while (!(await existingEntry(ancestor))) {
    missingParents.unshift(path.basename(ancestor))
    const parent = path.dirname(ancestor)
    if (parent === ancestor) throw new Error(`candidate parent is unavailable: ${ancestor}`)
    ancestor = parent
  }
  const canonicalAncestor = await realpath(ancestor)
  return path.join(canonicalAncestor, ...missingParents, path.basename(requestedTarget))
}

function assertOutsideSource(source, target) {
  if (target === source || target.startsWith(`${source}${path.sep}`)) {
    throw new Error('candidate destination must be outside the source tree')
  }
}

export async function publishNoReplace(temporary, target) {
  if (process.platform !== 'linux') {
    throw new Error('atomic no-replace candidate publication requires Linux')
  }
  await execFileAsync(
    '/usr/bin/mv',
    ['--no-clobber', '--no-copy', '--no-target-directory', temporary, target],
    { maxBuffer: 64 * 1024 },
  )
  if (await existingEntry(temporary)) {
    throw new Error(`candidate destination already exists: ${target}`)
  }
}

export async function packageProductionCandidate({ sourceRoot, destination }) {
  const source = await realpath(path.resolve(sourceRoot))
  const requestedTarget = path.resolve(destination)
  assertOutsideSource(source, await projectedCanonicalTarget(requestedTarget))
  await assertFile(path.join(source, 'dist/index.html'), 'built UI entrypoint')
  for (const [sourcePath] of CANDIDATE_FILES) {
    await assertFile(path.join(source, sourcePath), 'production runtime file')
  }

  await mkdir(path.dirname(requestedTarget), { recursive: true })
  const canonicalParent = await realpath(path.dirname(requestedTarget))
  const target = path.join(canonicalParent, path.basename(requestedTarget))
  assertOutsideSource(source, target)
  if (await existingEntry(target)) {
    throw new Error(`candidate destination already exists: ${target}`)
  }

  const temporary = `${target}.tmp-${process.pid}-${crypto.randomBytes(6).toString('hex')}`
  try {
    await mkdir(temporary, { mode: 0o700 })
    await cp(path.join(source, 'dist'), path.join(temporary, 'dist'), {
      recursive: true,
      errorOnExist: true,
      force: false,
    })
    for (const [sourcePath, targetPath] of CANDIDATE_FILES) {
      const output = path.join(temporary, targetPath)
      await mkdir(path.dirname(output), { recursive: true })
      await cp(path.join(source, sourcePath), output, {
        errorOnExist: true,
        force: false,
      })
    }

    const { directories, files: packagedFiles } = await collectTree(temporary)
    const files = {}
    for (const relativePath of packagedFiles) {
      files[relativePath] = await sha256(path.join(temporary, relativePath))
    }
    const manifest = {
      schema: 'openpine.ui-candidate.v1',
      directories,
      files,
    }
    const manifestHandle = await open(path.join(temporary, 'manifest.json'), 'wx', 0o444)
    try {
      await manifestHandle.writeFile(`${JSON.stringify(manifest, null, 2)}\n`)
    } finally {
      await manifestHandle.close()
    }
    await makeReadOnly(temporary, [...packagedFiles, 'manifest.json'], directories)
    await publishNoReplace(temporary, target)
    return manifest
  } catch (error) {
    await removeCandidateTree(temporary)
    throw error
  }
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null
if (invokedPath === fileURLToPath(import.meta.url)) {
  const destination = process.argv[2]
  if (!destination) throw new Error('usage: node tools/package-production.mjs <destination>')
  const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const manifest = await packageProductionCandidate({ sourceRoot, destination })
  process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`)
}
