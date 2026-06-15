import { describe, it, expect } from 'vitest'
import { readPineFile, normalizePineFileName } from './pineFileUpload'

class FakeFile {
  name: string
  size: number
  contents: string
  constructor(contents: string, name: string) {
    this.contents = contents
    this.name = name
    this.size = contents.length
  }
  // Modern File API.
  async text(): Promise<string> {
    return this.contents
  }
}

describe('readPineFile', () => {
  it('returns the file text content', async () => {
    const file: any = new FakeFile('//@version=5\nstrategy("x")', 'foo.pine')
    const text = await readPineFile(file)
    expect(text).toBe('//@version=5\nstrategy("x")')
  })

  it('throws on empty file', async () => {
    const file: any = new FakeFile('', 'empty.pine')
    await expect(readPineFile(file)).rejects.toThrow(/empty/i)
  })

  it('throws when no file is given', async () => {
    await expect(readPineFile(null as any)).rejects.toThrow(/no file/i)
  })
})

describe('normalizePineFileName', () => {
  it('strips directory prefix and lowercases the extension', () => {
    expect(normalizePineFileName('Foo.PINE')).toBe('Foo.PINE')
  })

  it('appends .pine when missing', () => {
    expect(normalizePineFileName('my_strategy')).toBe('my_strategy.pine')
  })

  it('collapses whitespace to underscores', () => {
    expect(normalizePineFileName('  my new strategy  ')).toBe('my_new_strategy.pine')
  })

  it('strips Windows-style path', () => {
    expect(normalizePineFileName('C:\\Users\\me\\my.pine')).toBe('my.pine')
  })

  it('truncates very long names to 100 chars', () => {
    const long = 'a'.repeat(200) + '.pine'
    expect(normalizePineFileName(long).length).toBeLessThanOrEqual(100)
  })

  it('returns empty string for empty input', () => {
    expect(normalizePineFileName('')).toBe('')
  })
})
