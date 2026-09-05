import { describe, expect, it } from 'vitest'
import { finiteEquityBounds, sampleEquityForDisplay } from './tvParitySeries'

describe('parity display-only bounds and sampling', () => {
  it('handles 300,000 points without spread/call-stack overflow and retains extrema/endpoints', () => {
    const rows = Array.from({length:300000}, (_,t) => Object.freeze({kind:'openpine_equity',t,v:t===154321?1e9:t}))
    const bounds = finiteEquityBounds(rows)!
    expect(bounds).toEqual({tMin:0,tMax:299999,yMin:0,yMax:1e9})
    const sampled = sampleEquityForDisplay(rows,'openpine_equity',bounds,320)
    expect(sampled.length).toBeLessThanOrEqual(1280)
    expect(sampled[0]).toBe(rows[0])
    expect(sampled[sampled.length-1]).toBe(rows[299999])
    expect(sampled).toContain(rows[154321])
    expect(rows.length).toBe(300000)
    expect(sampled.every((row,i)=>i===0 || row.t>=sampled[i-1]!.t)).toBe(true)
  })
  it('ignores missing, non-finite and non-equity values, but preserves actual zeros', () => {
    const rows=[{kind:'openpine_equity',t:0,v:0},{kind:'tv_equity',t:2,v:5},
      {kind:'tv_equity',t:Infinity,v:2},{kind:'tv_equity',t:4,v:NaN},
      {kind:'tv_equity',t:5},{kind:'tv_equity',t:1e100,v:2},{kind:'signal',t:6,v:1e9}]
    expect(finiteEquityBounds(rows)).toEqual({tMin:0,tMax:2,yMin:0,yMax:5})
    expect(finiteEquityBounds(rows.slice(2))).toBeNull()
  })
  it('samples each series independently and sorts unsorted input without mutation', () => {
    const rows=Object.freeze([{kind:'tv_equity',t:2,v:9},{kind:'openpine_equity',t:3,v:8},
      {kind:'tv_equity',t:1,v:4},{kind:'tv_equity',t:0,v:7}])
    const points=sampleEquityForDisplay(rows,'tv_equity',finiteEquityBounds(rows)!,1)
    expect(points.map(p=>p.t)).toEqual([0,1,2])
    expect(rows[0]!.t).toBe(2)
  })
  it.each([0,-1,NaN,Infinity])('bounds allocation for invalid pixel width %s', width=>{
    const rows=[{kind:'tv_equity',t:0,v:1},{kind:'tv_equity',t:0,v:2}]
    expect(sampleEquityForDisplay(rows,'tv_equity',finiteEquityBounds(rows)!,width)).toEqual(rows)
  })
  it('retains only the requested viewport and rejects invalid bounds',()=>{
    const rows=Array.from({length:50},(_,t)=>({kind:'tv_equity',t,v:t}))
    const bounds={tMin:10,tMax:20,yMin:10,yMax:20}
    expect(sampleEquityForDisplay(rows,'tv_equity',bounds,100)).toEqual(rows.slice(10,21))
    expect(sampleEquityForDisplay(rows,'tv_equity',{...bounds,tMin:21},100)).toEqual([])
  })
})
