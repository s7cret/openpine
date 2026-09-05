/** Real Vue setup/watch/unmount lifecycle with an in-memory renderer, not a browser test. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, nextTick } from 'vue'
import TvParity from './TvParity.vue'

const api = vi.hoisted(() => ({
  getDataMetadata: vi.fn(), getStrategies: vi.fn(), deleteTvParityRun: vi.fn(),
  getTvParityRun: vi.fn(), getTvParitySummaryCards: vi.fn(), listTvParityRuns: vi.fn(),
  previewTvParityCandles: vi.fn(), runTvParity: vi.fn(),
}))
vi.mock('@/api/client', () => ({ ...api, tvParityArtifactUrl: () => '/artifact' }))
vi.mock('@/api/auth', () => ({ downloadApiResource: vi.fn() }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('@/components/TvParityVisualization.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/MtfSeriesEditor.vue', () => ({ default: { render: () => null } }))

type Element = { parent: Element | null, children: Element[], text?: string }
const node = (text?: string): Element => ({ parent: null, children: [], text })
function remove(child: Element) {
  if (child.parent) child.parent.children = child.parent.children.filter(item => item !== child)
  child.parent = null
}
const renderer = createRenderer<Element, Element>({
  createElement: () => node(), createText: text => node(text), createComment: text => node(text),
  setText: (el, text) => { el.text = text }, setElementText: (el, text) => { el.text = text; el.children = [] },
  parentNode: el => el.parent,
  nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] ?? null,
  insert: (el, parent, anchor) => {
    remove(el)
    el.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(el)
    else parent.children.splice(index, 0, el)
  }, remove, patchProp: () => {},
})
function deferred<T = any>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); await nextTick() }
const entry = (id: string) => ({ run_id: id, status: 'completed' })
const response = (id: string, status = 'completed') => ({ data: {
  run_id: id, status, locked_period: { from_time: id === 'A' ? 10 : 20, to_time: 30 },
}})
let unmount: () => void
async function mount() {
  // Keep actual setup/watch/unmount; DOM directives and visual layout are not under test.
  const app = renderer.createApp({ ...TvParity, render: () => null })
  const vm = app.mount(node())
  unmount = () => app.unmount()
  await flush()
  return (vm as any).$.setupState
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  vi.stubGlobal('window', { confirm: () => true })
  api.getStrategies.mockResolvedValue({ data: { items: [{ strategy_id: 'S', name: 'S', symbol: 'SOLUSDT', timeframe: '1m', exchange: 'binance', market_type: 'spot' }] } })
  api.getDataMetadata.mockResolvedValue({ data: { exchanges: [{ id: 'binance', label: 'Binance', market_types: [{ id: 'spot', label: 'Spot' }] }] } })
  api.listTvParityRuns.mockResolvedValue({ data: { items: [], total: 0 } })
  api.getTvParitySummaryCards.mockResolvedValue({ data: {} })
  api.deleteTvParityRun.mockResolvedValue({ data: {} })
})
afterEach(() => { unmount?.(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('TV parity late response guards', () => {
  it('keeps the latest selected report and locked period when requests resolve in reverse order', async () => {
    const state = await mount(), a = deferred(), b = deferred()
    api.getTvParityRun.mockImplementationOnce(() => a.promise).mockImplementationOnce(() => b.promise)
    const first = state.loadHistoryEntry(entry('A'))
    const second = state.loadHistoryEntry(entry('B'))
    b.resolve(response('B')); await second
    a.resolve(response('A')); await first
    expect(state.result.run_id).toBe('B')
    expect(state.lockedPeriod.from_time).toBe(20)
    expect(state.status).toBe('')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ignores a completed old poll after a different history selection', async () => {
    const state = await mount(), oldPoll = deferred()
    api.getTvParityRun.mockResolvedValueOnce(response('A', 'running'))
      .mockImplementationOnce(() => oldPoll.promise).mockResolvedValueOnce(response('B'))
    await state.loadHistoryEntry(entry('A'))
    expect(api.getTvParityRun).toHaveBeenCalledTimes(2)
    await state.loadHistoryEntry(entry('B'))
    oldPoll.resolve(response('A')); await flush()
    expect(state.result.run_id).toBe('B')
    expect(api.listTvParityRuns).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not apply a late request after unmount', async () => {
    const state = await mount(), pending = deferred()
    api.getTvParityRun.mockImplementation(() => pending.promise)
    const load = state.loadHistoryEntry(entry('A'))
    unmount()
    pending.resolve(response('A')); await load
    expect(state.result).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not overwrite history selection with a late POST and prevents duplicate submissions', async () => {
    const state = await mount(), post = deferred()
    state.selectedStrategyId = 'S'
    state.form = { ...state.form, source: 'exchange_data', compareFromTime: '0', compareToTime: '100' }
    api.runTvParity.mockImplementation(() => post.promise)
    const run = state.queueRun()
    await state.queueRun()
    expect(api.runTvParity).toHaveBeenCalledTimes(1)
    api.getTvParityRun.mockResolvedValue(response('B'))
    await state.loadHistoryEntry(entry('B'))
    post.resolve(response('A', 'queued')); await run; await flush()
    expect(state.result.run_id).toBe('B')
    expect(state.runLoading).toBe(false)
    expect(api.listTvParityRuns).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('discards preview results when its input file changes', async () => {
    const state = await mount(), pending = deferred()
    state.selectedStrategyId = 'S'
    state.candlesFile = new File(['time,open,high,low,close'], 'first.csv')
    api.previewTvParityCandles.mockImplementation(() => pending.promise)
    const preview = state.previewCandles()
    state.candlesFile = new File(['other'], 'second.csv')
    pending.resolve({ data: { valid_bars: 10, from_time: 1, to_time: 2 } }); await preview
    expect(state.preview).toBeNull()
    expect(state.lockedPeriod).toBeNull()
    expect(state.loading).toBe(false)
  })

  it('cannot resurrect a run deleted while its detail request was in flight', async () => {
    const state = await mount(), pending = deferred()
    api.getTvParityRun.mockImplementation(() => pending.promise)
    const load = state.loadHistoryEntry(entry('A'))
    await state.deleteHistoryEntry(entry('A'))
    pending.resolve(response('A')); await load
    expect(state.result).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('treats US-spelled canceled as terminal without repeated requests', async () => {
    const state = await mount()
    api.getTvParityRun.mockResolvedValue(response('A', 'canceled'))
    await state.loadHistoryEntry(entry('A'))
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.getTvParityRun).toHaveBeenCalledTimes(1)
    expect(state.result.status).toBe('canceled')
  })

  it('rejects a response for another run rather than replacing the selection', async () => {
    const state = await mount()
    api.getTvParityRun.mockResolvedValue(response('B'))
    await state.loadHistoryEntry(entry('A'))
    expect(state.result).toBeNull()
    expect(state.status).toBe('tvParity.history.loadFailed')
  })

  it('retries summary data after a failure instead of caching an empty failure forever', async () => {
    const state = await mount()
    api.getTvParitySummaryCards.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce({data:{status:'match'}})
    await state.loadSummary('A'); await state.loadSummary('A')
    expect(api.getTvParitySummaryCards).toHaveBeenCalledTimes(2)
    expect(state.summaryOf('A')).toEqual({status:'match'})
  })

  it('a late list response cannot undo a newer history refresh', async () => {
    const state = await mount(), a = deferred(), b = deferred()
    api.listTvParityRuns.mockImplementationOnce(() => a.promise).mockImplementationOnce(() => b.promise)
    const first = state.fetchHistory(), last = state.fetchHistory()
    b.resolve({data:{items:[entry('B')],total:1}}); await last
    a.resolve({data:{items:[entry('A')],total:1}}); await first
    expect(state.history.map((r: any) => r.run_id)).toEqual(['B'])
    expect(state.historyLoading).toBe(false)
  })
  it('late CSV preview cannot overwrite the newly selected report period', async () => {
    const state = await mount(), pending = deferred()
    state.selectedStrategyId = 'S'
    state.candlesFile = new File(['csv'], 'first.csv')
    api.previewTvParityCandles.mockImplementation(() => pending.promise)
    const preview = state.previewCandles()
    api.getTvParityRun.mockResolvedValue(response('B'))
    await state.loadHistoryEntry(entry('B'))
    pending.resolve({data:{valid_bars:10,from_time:1,to_time:2}}); await preview
    expect(state.result.run_id).toBe('B')
    expect(state.lockedPeriod.from_time).toBe(20)
    expect(state.loading).toBe(false)
  })

})
