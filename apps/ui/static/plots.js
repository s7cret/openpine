"use strict";

// The runtime publishes colors as canonical #RRGGBBAA tokens. Null means
// hidden; do not replace it with the chart's fallback palette.
function drawPinePlot(ctx, series, start, end, x, y) {
    const style = series.style || "linebr";
    const width = Math.max(1, Math.min(64, series.linewidth ?? 1.5));
    const colorAt = i => series.colors ? series.colors[i] : series.color;
    const missing = v => v === null || v === undefined || !Number.isFinite(v);
    let previous = null;
    for (let i = start; i < end; i++) {
        const value = series.values[i], color = colorAt(i);
        if (missing(value)) {
            if (style === "linebr" || !series.style) previous = null;
            continue;
        }
        const point = {x: x(i), y: y(value)};
        if (color === null || color === undefined) { previous = point; continue; }
        ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = width;
        if (style === "columns" || style === "histogram") {
            const barWidth = style === "columns" ? Math.max(1, Math.abs(x(i + 1) - x(i)) * .8) : width;
            ctx.fillRect(point.x - barWidth / 2, Math.min(point.y, y(0)), barWidth, Math.abs(y(0) - point.y));
        } else if (style === "circles" || style === "cross") {
            ctx.beginPath();
            if (style === "circles") ctx.arc(point.x, point.y, width + 1, 0, 2 * Math.PI);
            else {
                ctx.moveTo(point.x - width - 1, point.y); ctx.lineTo(point.x + width + 1, point.y);
                ctx.moveTo(point.x, point.y - width - 1); ctx.lineTo(point.x, point.y + width + 1);
            }
            if (style === "circles") ctx.fill(); else ctx.stroke();
        } else if (previous !== null) {
            ctx.beginPath(); ctx.moveTo(previous.x, previous.y);
            if (style === "stepline") {
                const middle = (previous.x + point.x) / 2;
                ctx.lineTo(middle, previous.y); ctx.lineTo(middle, point.y);
            }
            ctx.lineTo(point.x, point.y);
            if (style === "area") {
                ctx.lineTo(point.x, y(0)); ctx.lineTo(previous.x, y(0)); ctx.closePath(); ctx.fill();
            } else ctx.stroke();
        }
        previous = point;
    }
    // Draw point markers after segments so the following bar's segment cannot
    // repaint the preceding marker with a different color.
    if (end - start <= 20 && series.kind !== "hline" && !["columns", "histogram", "circles", "cross"].includes(style)) {
        for (let i=start;i<end;i++) {
            const color=colorAt(i),value=series.values[i];
            if (missing(value)||color===null||color===undefined) continue;
            ctx.fillStyle=color;ctx.beginPath();ctx.arc(x(i),y(value),2.5,0,2*Math.PI);ctx.fill();
        }
    }
}

function drawPineLayers(ctx, series, start, end, x, y) {
    const outputs = new Map(series.filter(s => s.output_id).map(s => [s.output_id, s]));
    for (const layer of series.filter(s => s.kind === "fill" && !s.hiddenLine)) {
        const [a, b] = (layer.references || []).map(id => outputs.get(id));
        if (!a || !b) continue;
        let previous = null;
        for (let i=start;i<end;i++) {
            const av=a.values[i],bv=b.values[i];
            if (av===null||bv===null||!Number.isFinite(av)||!Number.isFinite(bv)) {
                if (!layer.fillgaps) previous=null;
                continue;
            }
            const point={x:x(i),a:y(av),b:y(bv)},color=layer.colors?.[i];
            if (previous && color!==null && color!==undefined) {
                ctx.fillStyle=color;ctx.beginPath();ctx.moveTo(previous.x,previous.a);
                ctx.lineTo(point.x,point.a);ctx.lineTo(point.x,point.b);
                ctx.lineTo(previous.x,previous.b);ctx.closePath();ctx.fill();
            }
            previous=point;
        }
    }
    for (const s of series) {
        if (s.kind==="fill"||s.hiddenLine||isSignalLayer(s)) continue;
        ctx.save();
        if (s.kind==="hline") ctx.setLineDash(s.style==="dashed"?[6,4]:s.style==="dotted"?[1,3]:[]);
        drawPinePlot(ctx,s,start,end,x,y);ctx.restore();
    }
}

function isSignalLayer(s) {return s.presentation_schema === "openpine.signal-layer.1";}

// Bar-aligned values are immutable. Only drawing applies offset. Range halos
// use indices relative to the requested window, including negative indices.
function signalPoints(s,start,end) {
    const result=[],offset=s.offset||0;
    for(let i=Math.max(0,start-offset);i<Math.min(s.values.length,end-offset);i++)
        result.push({index:i+offset,value:s.values[i],color:s.colors?.[i],textcolor:s.textcolors?.[i]});
    for(const p of s.halo||[])if(p.index+offset>=start&&p.index+offset<end)
        result.push({...p,index:p.index+offset});
    return result;
}

function drawPineBackgrounds(ctx,layers,start,end,x,top,bottom) {
    const width=Math.max(1,Math.abs(x(start+1)-x(start)));
    for(const s of layers)if(isSignalLayer(s)&&s.kind==="bgcolor"&&!s.hiddenLine)
        for(const p of signalPoints(s,start,end))if(p.color){ctx.fillStyle=p.color;ctx.fillRect(x(p.index)-width/2,top,width,bottom-top);}
}

function drawPineCandles(ctx,bars,layers,start,end,x,y) {
    const colors=new Map();
    for(const s of layers)if(isSignalLayer(s)&&s.kind==="barcolor"&&!s.hiddenLine)
        for(const p of signalPoints(s,start,end))if(p.color!==null&&p.color!==undefined)colors.set(p.index,p.color);
    const width=Math.max(1,Math.min(14,Math.abs(x(start+1)-x(start))*.65));
    for(let i=start;i<end;i++){
        const b=bars[i];if(![b?.open,b?.high,b?.low,b?.close].every(Number.isFinite))continue;
        ctx.strokeStyle=ctx.fillStyle=colors.get(i)||(b.close>=b.open?"#5de4c2":"#ff8e98");ctx.lineWidth=1;
        const xx=x(i);ctx.beginPath();ctx.moveTo(xx,y(b.high));ctx.lineTo(xx,y(b.low));ctx.stroke();
        ctx.fillRect(xx-width/2,Math.min(y(b.open),y(b.close)),width,Math.max(1,Math.abs(y(b.open)-y(b.close))));
    }
}

function drawSignalShape(ctx,style,xx,yy,r) {
    ctx.beginPath();
    if(style==="circle")ctx.arc(xx,yy,r,0,Math.PI*2);
    else if(style==="cross"||style==="xcross"){
        const d=style==="cross"?0:r;
        if(d){ctx.moveTo(xx-r,yy-r);ctx.lineTo(xx+r,yy+r);ctx.moveTo(xx-r,yy+r);ctx.lineTo(xx+r,yy-r);}
        else{ctx.moveTo(xx-r,yy);ctx.lineTo(xx+r,yy);ctx.moveTo(xx,yy-r);ctx.lineTo(xx,yy+r);}
        ctx.stroke();return;
    }else if(style==="diamond"){ctx.moveTo(xx,yy-r);ctx.lineTo(xx+r,yy);ctx.lineTo(xx,yy+r);ctx.lineTo(xx-r,yy);}
    else if(style==="square")ctx.rect(xx-r,yy-r,r*2,r*2);
    else if(style==="flag"){ctx.moveTo(xx-r,yy+r);ctx.lineTo(xx-r,yy-r);ctx.lineTo(xx+r,yy-r);ctx.lineTo(xx,yy);ctx.lineTo(xx-r,yy);}
    else {
        const up=style.endsWith("up"),d=up?-1:1;
        ctx.moveTo(xx,yy+d*r);ctx.lineTo(xx-r,yy-d*r);
        if(style.startsWith("arrow")){ctx.lineTo(xx-r*.35,yy-d*r);ctx.lineTo(xx-r*.35,yy-d*r*2);ctx.lineTo(xx+r*.35,yy-d*r*2);ctx.lineTo(xx+r*.35,yy-d*r);}
        else if(style.startsWith("label")){ctx.lineTo(xx-r,yy-d*r*2);ctx.lineTo(xx+r,yy-d*r*2);}
        ctx.lineTo(xx+r,yy-d*r);
    }
    ctx.closePath();ctx.fill();
}

function drawPineSignals(ctx,layers,bars,start,end,x,y,top,bottom,pricePane=false) {
    const radii={auto:4,tiny:2,small:4,normal:6,large:8,huge:10};
    for(const s of layers)if(isSignalLayer(s)&&s.kind.startsWith("plot")&&!s.hiddenLine){
        const r=radii[s.size]||4;
        for(const p of signalPoints(s,start,end)){
            if(p.value===null||p.value===undefined||!Number.isFinite(p.value))continue;
            const b=bars[p.index],xx=x(p.index),above=s.location==="abovebar"||s.location==="top";
            let yy;
            if(s.location==="absolute")yy=y(p.value);
            else if(pricePane&&s.location==="abovebar")yy=y(Number.isFinite(b?.high)?b.high:b?.close)-r-8;
            else if(pricePane&&s.location==="belowbar")yy=y(Number.isFinite(b?.low)?b.low:b?.close)+r+8;
            else yy=above?top+r+8:bottom-r-8;
            if(!Number.isFinite(yy))continue;
            ctx.save();ctx.lineWidth=1.5;
            if(p.color){ctx.fillStyle=ctx.strokeStyle=p.color;
                if(s.kind==="plotshape")drawSignalShape(ctx,s.style,xx,yy,r);
                else if(s.char){ctx.font=`${Math.max(10,r*3)}px system-ui`;ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(s.char,xx,yy);}}
            if(s.text&&p.textcolor){ctx.fillStyle=p.textcolor;ctx.font="12px system-ui";ctx.textAlign="center";ctx.textBaseline=above?"bottom":"top";
                const lines=s.text.split("\n");lines.forEach((line,j)=>ctx.fillText(line,xx,yy+(above?-1:1)*(r+5)+j*14-(above?(lines.length-1)*14:0)));}
            ctx.restore();
        }
    }
}
function drawPineLabels(ctx, labels, start, end, x, y, left, right, top, bottom) {
  ctx.save();
  ctx.font = "11px system-ui";
  ctx.textBaseline = "middle";
  for (const label of labels) {
    if (!Number.isInteger(label.x) || label.x < start || label.x >= end || !Number.isFinite(label.y)) continue;
    const px = x(label.x), py = y(label.y);
    ctx.fillStyle = "#3a6fd3";
    ctx.beginPath();
    ctx.arc(px, py, 3, 0, 2 * Math.PI);
    ctx.fill();
    if (!label.text) continue;
    const text = label.text.replaceAll("\n", " ").slice(0, 160);
    const width = Math.min(ctx.measureText(text).width + 12, right - left);
    if (width <= 12) continue;
    const bx = Math.max(left, Math.min(right - width, px - width / 2));
    const by = Math.max(top, Math.min(bottom - 18, py - 23));
    ctx.fillRect(bx, by, width, 18);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, bx + 6, by + 9, width - 12);
  }
  ctx.restore();
}
function pineLineSegments(lines, start, end, offset) {
  const segments = [];
  if (end <= start) return segments;
  for (const line of lines) {
    if (![line.x1, line.y1, line.x2, line.y2].every(Number.isFinite)) continue;
    let x1 = line.x1 - offset, x2 = line.x2 - offset, y1 = line.y1, y2 = line.y2;
    if (x2 < x1) [x1, y1, x2, y2] = [x2, y2, x1, y1];
    if (x2 < start || x1 >= end || x1 === x2 && y1 === y2) continue;
    if (x1 === x2) {
      segments.push([x1, y1, x2, y2]);
      continue;
    }
    const left = Math.max(start, x1), right = Math.min(end - 1, x2);
    const priceAt = x => {
      const t = (x - x1) / (x2 - x1);
      return Math.sign(y1) === Math.sign(y2) ? y1 + t * (y2 - y1) : (1 - t) * y1 + t * y2;
    };
    segments.push([left, priceAt(left), right, priceAt(right)]);
  }
  return segments;
}

function drawPineLines(ctx, segments, x, y) {
  if (!segments.length) return;
  ctx.save();
  ctx.strokeStyle = "#3a6fd3";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (const [x1, y1, x2, y2] of segments) {
    ctx.moveTo(x(x1), y(y1));
    ctx.lineTo(x(x2), y(y2));
  }
  ctx.stroke();
  ctx.restore();
}
