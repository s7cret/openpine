/** Actual component setup lifecycle; no browser layout or canvas pixels are simulated. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, h, nextTick, ref, ssrContextKey } from 'vue'
import Visualization from './TvParityVisualization.vue'

const api = vi.hoisted(() => ({ getTvParityChartData: vi.fn(), getTvParitySummaryCards: vi.fn(),
  getTvParityTopMismatches: vi.fn(), getTvParityDiagnosticsCallouts: vi.fn() }))
vi.mock('@/api/client', () => ({...api, tvParityReportUrl: () => '/report'}))
vi.mock('@/api/auth', () => ({downloadApiResource: vi.fn()}))
vi.mock('vue-i18n', () => ({useI18n: () => ({t:(key: string)=>key})}))
const renderer = createRenderer<any, any>({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  insert: () => {}, remove: () => {}, setText: () => {}, setElementText: () => {},
  parentNode: () => null, nextSibling: () => null, patchProp: () => {},
})
function deferred() {
  let resolve!: (v: any) => void, reject!: (e: Error) => void
  const promise = new Promise<any>((yes,no) => { resolve=yes; reject=no })
  return {resolve,reject,promise}
}
const flush = async () => { for(let i=0;i<20;i++) await Promise.resolve(); await nextTick() }
const chart = (id: string, value=1) => ({data:{run_id:id,series:[{kind:'openpine_equity',t:0,v:value}]}})
let unmount = () => {}
async function mount(id='A') {
  const run = ref(id)
  const component = {...Visualization,render:()=>null}
  const app = renderer.createApp({setup:()=>()=>h(component,{runId:run.value})})
  // Vitest's Node transform registers SFC module IDs in the SSR context.
  // Custom rendering still runs the real client setup and lifecycle hooks.
  app.provide(ssrContextKey, { modules: new Set<string>() })
  const vm = app.mount({})
  unmount=()=>{app.unmount();unmount=()=>{}}
  const state=(vm as any).$.subTree.component.setupState
  await flush()
  return {state,run}
}
beforeEach(()=>{
  vi.resetAllMocks()
  vi.stubGlobal('window',{addEventListener:vi.fn(),removeEventListener:vi.fn()})
  vi.stubGlobal('requestAnimationFrame',vi.fn(()=>1))
  vi.stubGlobal('cancelAnimationFrame',vi.fn())
  api.getTvParitySummaryCards.mockImplementation(async id=>({data:{run_id:id}}))
  api.getTvParityTopMismatches.mockResolvedValue({data:{total:100,items:[]}})
  api.getTvParityDiagnosticsCallouts.mockImplementation(async id=>({data:{run_id:id,callouts:[]}}))
})
afterEach(()=>{unmount();vi.unstubAllGlobals()})

describe('visualization loadAll selection identity',()=>{
  it('A -> B with a delayed A retains only B across all response fields',async()=>{
    const a=deferred(),b=deferred()
    api.getTvParityChartData.mockImplementation(id=>id==='A'?a.promise:b.promise)
    const {state,run}=await mount()
    run.value='B';await nextTick()
    b.resolve(chart('B',20));await flush()
    expect(state.chart.run_id).toBe('B')
    a.resolve(chart('A',10));await flush()
    expect(state.chart.run_id).toBe('B')
    expect(state.summary.run_id).toBe('B')
    expect(state.chartBounds.yMax).toBe(20)
    expect(state.loading).toBe(false)
    expect(state.error).toBe('')
  })
  it('late A rejection cannot change B error or loading state',async()=>{
    const a=deferred(),b=deferred()
    api.getTvParityChartData.mockImplementation(id=>id==='A'?a.promise:b.promise)
    const {state,run}=await mount()
    run.value='B';await nextTick()
    a.reject(Error('old failure'));await flush()
    expect(state.error).toBe('')
    expect(state.loading).toBe(true)
    b.resolve(chart('B'));await flush()
    expect(state.chart.run_id).toBe('B')
    expect(state.loading).toBe(false)
  })
  it('unmount prevents response writes and rendering work',async()=>{
    const pending=deferred();api.getTvParityChartData.mockReturnValue(pending.promise)
    const {state}=await mount()
    unmount();pending.resolve(chart('A'));await flush()
    expect(state.chart).toBeNull()
    expect(requestAnimationFrame).not.toHaveBeenCalled()
  })
  it('empty selection invalidates pending requests and clears prior data',async()=>{
    const pending=deferred();api.getTvParityChartData.mockReturnValue(pending.promise)
    const {state,run}=await mount()
    run.value='';await nextTick();pending.resolve(chart('A'));await flush()
    expect(state.chart).toBeNull()
    expect(state.summary).toBeNull()
    expect(state.loading).toBe(false)
  })
  it('same-run reloads are generation guarded and wrong-run payloads are rejected',async()=>{
    const old=deferred(),latest=deferred()
    api.getTvParityChartData.mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise)
    const {state}=await mount()
    const reload=state.loadAll()
    latest.resolve(chart('unexpected'));await reload
    expect(state.error).toContain('identity mismatch')
    old.resolve(chart('A'));await flush()
    expect(state.chart).toBeNull()
    expect(state.error).toContain('identity mismatch')
  })
  it('does not clamp out-of-viewport mismatches into boundary buckets',async()=>{
    api.getTvParityChartData.mockResolvedValue({data:{run_id:'A',series:[{kind:'tv_equity',t:10,v:1},{kind:'tv_equity',t:20,v:2}]}})
    api.getTvParityTopMismatches.mockResolvedValue({data:{total:100,items:[
      {bar_time:0,delta_net_profit_abs:1000},{bar_time:15,delta_net_profit_abs:2},
      {bar_time:30,delta_net_profit_abs:1000},{bar_time:16,delta_net_profit_abs:NaN},
    ]}})
    const {state}=await mount()
    expect(state.heatmapBuckets.reduce((n:number,b:any)=>n+b.count,0)).toBe(1)
    expect(state.sampledMismatches).toBe(true)
  })
})
