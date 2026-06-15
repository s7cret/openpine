/**
 * Helpers for handling user-uploaded .pine files in the PineFiles page
 * and the Strategies create form.  Plain DOM logic only — no third-party
 * deps.  Extracted from inline component handlers so we can unit-test
 * them in jsdom (the components themselves are still .vue).
 */

/**
 * Read a single File (browser File API) as UTF-8 text.
 *
 * @param file the file the user picked via <input type="file">
 * @returns the file's text content
 * @throws when the file is empty or cannot be read
 */
export async function readPineFile(file: File): Promise<string> {
  if (!file) {
    throw new Error('No file selected')
  }
  if (file.size === 0) {
    throw new Error(`File "${file.name || 'pine'}" is empty`)
  }
  if (typeof file.text === 'function') {
    return await file.text()
  }
  // Fallback for older environments (jsdom, IE) — should not be hit in
  // production but keeps the helper testable in unit tests.
  return await new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error ?? new Error('FileReader failed'))
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.readAsText(file)
  })
}

/**
 * Normalize a user-typed filename into a sane Pine source leaf name.
 *  - strips any directory prefix (browsers give us the basename on the
 *    File object, but be defensive)
 *  - if no extension is present, appends ".pine"
 *  - collapses whitespace to underscores
 *  - truncates to 100 chars (the OpenPine API rejects longer names)
 */
export function normalizePineFileName(raw: string): string {
  const base = (raw || '').replace(/\\/g, '/').split('/').pop() || ''
  const cleaned = base.trim().replace(/\s+/g, '_')
  if (!cleaned) return ''
  const hasPine = cleaned.toLowerCase().endsWith('.pine')
  // Truncate to 100 chars TOTAL (including ".pine" suffix when present)
  const max = 100
  if (cleaned.length > max) {
    const cut = hasPine ? max - 5 : max
    const truncated = cleaned.slice(0, cut).replace(/\.pine$/i, '')
    return `${truncated}.pine`
  }
  return hasPine ? cleaned : `${cleaned}.pine`
}
