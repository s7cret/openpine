"use strict";

// The runtime publishes colors as canonical #RRGGBBAA tokens. Null means
// hidden; do not replace it with the chart's fallback palette.
function drawPinePlot(ctx, series, start, end, x, y, bounds) {
    const style = series.style || "linebr";
    const width = Math.max(1, Math.min(64, series.linewidth ?? 1.5));
    const colorAt = i => series.colors ? series.colors[i] : series.color;
    const missing = v => v === null || v === undefined || !Number.isFinite(v);
    const markers = style === "circles" || style === "cross";
    const joined = markers && series.join;
    const area = style === "area" || style === "areabr";
    const step = ["stepline", "steplinebr", "stepline_diamond"].includes(style);
    const breaks = ["linebr", "areabr", "steplinebr"].includes(style) || !series.style || joined;
    const histY = () => {
        const base = y(series.histbase ?? 0);
        return style === "areabr" && bounds ? Math.max(bounds.top,Math.min(bounds.bottom,base)) : base;
    };
    if (series.linestyle && ["line","linebr","stepline","stepline_diamond","area"].includes(style)) {
        ctx.setLineDash(series.linestyle === "dashed" ? [6,4] : series.linestyle === "dotted" ? [1,3] : []);
    }
    let previous = Number.isFinite(series.edgeValue)
        ? {x:x(start-1),y:y(series.edgeValue),value:series.edgeValue} : null;
    for (let i = start; i < end; i++) {
        const value = series.values[i], color = colorAt(i);
        if (missing(value)) {
            if (breaks) previous = null;
            continue;
        }
        const point = {x: x(i), y: y(value), value};
        if (color === null || color === undefined) { previous = joined ? null : point; continue; }
        ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = width;
        if (style === "columns" || style === "histogram") {
            const barWidth = style === "columns" ? Math.max(1, Math.abs(x(i + 1) - x(i)) * .8) : width;
            const base = histY();
            ctx.fillRect(point.x - barWidth / 2, Math.min(point.y, base), barWidth, Math.abs(base - point.y));
        } else if (markers) {
            if (joined && previous) {
                ctx.lineWidth=1;
                ctx.beginPath();ctx.moveTo(previous.x,previous.y);ctx.lineTo(point.x,point.y);ctx.stroke();
                ctx.lineWidth=width;
            }
            ctx.beginPath();
            if (style === "circles") ctx.arc(point.x, point.y, width + 1, 0, 2 * Math.PI);
            else {
                ctx.moveTo(point.x - width - 1, point.y); ctx.lineTo(point.x + width + 1, point.y);
                ctx.moveTo(point.x, point.y - width - 1); ctx.lineTo(point.x, point.y + width + 1);
            }
            if (style === "circles") ctx.fill(); else ctx.stroke();
        } else if (previous !== null) {
            ctx.beginPath(); ctx.moveTo(previous.x, previous.y);
            if (step) {
                const middle = (previous.x + point.x) / 2;
                ctx.lineTo(middle, previous.y); ctx.lineTo(middle, point.y);
            }
            ctx.lineTo(point.x, point.y);
            if (area) {
                const base = histY();
                ctx.lineTo(point.x,base);ctx.lineTo(previous.x,base);ctx.closePath();ctx.fill();
                if (style === "area" && series.linestyle) {
                    ctx.beginPath();ctx.moveTo(previous.x,previous.y);ctx.lineTo(point.x,point.y);ctx.stroke();
                }
            } else ctx.stroke();
            if (style === "stepline_diamond" && previous.value !== value) {
                drawSignalShape(ctx,"diamond",point.x,point.y,width+1);
            }
        }
        previous = point;
    }
    // Draw point markers after segments so the following bar's segment cannot
    // repaint the preceding marker with a different color.
    if (end - start <= 20 && series.kind !== "hline" &&
        !["columns","histogram","circles","cross","areabr","steplinebr","stepline_diamond"].includes(style)) {
        for (let i=start;i<end;i++) {
            const color=colorAt(i),value=series.values[i];
            if (missing(value)||color===null||color===undefined) continue;
            ctx.fillStyle=color;ctx.beginPath();ctx.arc(x(i),y(value),2.5,0,2*Math.PI);ctx.fill();
        }
    }
    if (series.trackPoint && end > start) {
        const {value,color}=series.trackPoint, height=y(value);
        ctx.strokeStyle=color;ctx.lineWidth=1;ctx.setLineDash([1,3]);
        ctx.beginPath();ctx.moveTo(bounds?.left??x(start),height);
        ctx.lineTo(bounds?.right??x(end-1),height);ctx.stroke();
    }
}

function pineDisplays(layer, destination) {
    return Array.isArray(layer.display) ? layer.display.includes(destination) : layer.display !== "none";
}

function isPineLayerHidden(layer) {
    return layer.hiddenLine || !pineDisplays(layer,"pane");
}

function pineValuePlots(layers, version) {
    if (version < 5 || version > 6 || !Number.isInteger(version)) return [];
    return layers.filter(layer => !layer.hiddenLine && (layer.kind === "plot" ||
        (!layer.kind && (!layer.presentation_schema || layer.presentation_schema === "openpine.plot-presentation.1"))));
}

function pineAxisPlots(plots, overlay, price) {
    return plots.filter(layer => pineDisplays(layer,"price_scale") && layer.show_last!==0 &&
        Boolean(overlay || layer.force_overlay)===price);
}

function drawPinePriceLabels(ctx, layers, x, top, bottom, low, high, y, format) {
    const spacing=16, capacity=Math.max(0,Math.floor((bottom-top)/spacing));
    const labels=layers.map(layer=>({layer,value:Object.hasOwn(layer,"last")?layer.last:layer.values.at(-1)}))
        .filter(label=>Number.isFinite(label.value))
        .map(label=>({...label,at:y(Math.min(high,Math.max(low,label.value)))}))
        .sort((a,b)=>a.at-b.at).slice(0,capacity);
    if (!labels.length) return;
    for (let i=0;i<labels.length;i++)
        labels[i].at=Math.max(top+spacing/2+i*spacing,Math.min(bottom-spacing/2,labels[i].at),
            i===0?top:labels[i-1].at+spacing);
    for (let i=labels.length-1;i>=0;i--)
        labels[i].at=Math.min(labels[i].at,i===labels.length-1?bottom-spacing/2:labels[i+1].at-spacing);
    ctx.save();ctx.textAlign="left";ctx.textBaseline="middle";
    for (const {layer,value,at} of labels) {
        ctx.fillStyle="#17243a";ctx.fillRect(x,at-7,80,14);
        ctx.fillStyle=layer.color||"#c8d7ed";ctx.fillRect(x,at-7,3,14);
        ctx.fillStyle="#f3f6ff";ctx.fillText(format(value),x+6,at,71);
    }
    ctx.restore();
}

const pineCursorBindings = new WeakMap();
const pineValueWindows = new WeakMap();

function updatePineValueSurfaces(status, panel, plots, bars, range, canvases, centered, format) {
    if (!status || !panel) return;
    let state = pineValueWindows.get(panel);
    if (!state) {
        state={time:null};
        pineValueWindows.set(panel,state);
    }
    const [start,end]=range;
    const groups=[plots.filter(layer=>pineDisplays(layer,"status_line")),
                  plots.filter(layer=>pineDisplays(layer,"data_window"))];
    state.render=()=>{
        let index=bars.findIndex((bar,i)=>i>=start&&i<end&&bar.time===state.time);
        if (index<0) index=end-1;
        const value=layer=>format(index<(layer.firstSignalIndex??-Infinity)?null:layer.values[index]);
        const title=layer=>layer.title||layer.output_id;
        status.hidden=!groups[0].length;
        panel.hidden=!groups[1].length;
        status.replaceChildren(...groups[0].map(layer=>{
            const row=document.createElement("span");
            row.textContent=`${title(layer)} ${value(layer)}`;
            return row;
        }));
        const stamp=panel.querySelector("time");
        const time=bars[index]?.time;
        stamp.dateTime=stamp.textContent=Number.isFinite(time)?new Date(time).toISOString():"";
        const list=panel.querySelector("dl");
        list.replaceChildren();
        for (const layer of groups[1]) {
            const name=document.createElement("dt"), sample=document.createElement("dd");
            name.textContent=title(layer);sample.textContent=value(layer);list.append(name,sample);
        }
    };
    for (const canvas of canvases) {
        if (!canvas) continue;
        if (!pineCursorBindings.has(canvas)) {
            canvas.addEventListener("pointermove",event=>pineCursorBindings.get(canvas).move(event));
            canvas.addEventListener("pointerleave",()=>pineCursorBindings.get(canvas).clear());
        }
        pineCursorBindings.set(canvas,{
            move(event) {
                if (!groups.some(group=>group.length) || end<=start) return;
                const {left,width}=canvas.getBoundingClientRect(), inset=centered?8:12;
                const position=Math.max(0,Math.min(1,(event.clientX-left-inset)/Math.max(1,width-inset-85)));
                const index=start+Math.min(end-start-1,centered?Math.floor(position*(end-start)):
                    Math.round(position*(end-start-1)));
                const time=bars[index]?.time;
                if (time===state.time) return;
                state.time=time;state.render();
            },
            clear() {if(state.time!==null){state.time=null;state.render();}}
        });
    }
    state.render();
}

function pineTrackPoint(layer) {
    if (!layer.trackprice || isPineLayerHidden(layer) || layer.show_last===0) return null;
    const value=Object.hasOwn(layer,"last") ? layer.last : layer.values.at(-1);
    const color=Object.hasOwn(layer,"last_color") ? layer.last_color : layer.colors?.at(-1);
    return Number.isFinite(value) && color && !color.endsWith("00") ? {value,color} : null;
}

function projectPinePlots(series,start,end,origin=0) {
    return series.map(layer=>{
        const diamond=layer.style==="stepline_diamond";
        if (isSignalLayer(layer) || !diamond && !Object.hasOwn(layer,"offset")) return layer;
        const first=diamond && origin+start>0 ? start-1 : start;
        const values=Array(end).fill(null),colors=Array(end).fill(null);
        let edgeValue=null;
        for (const point of signalPoints(layer,first,end)) {
            if (point.index===start-1) edgeValue=point.value;
            else {
                values[point.index]=point.value;
                colors[point.index]=point.color;
            }
        }
        return {...layer,values,colors,edgeValue,trackPoint:pineTrackPoint(layer)};
    });
}

function pinePlotHasBaseline(layer) {
    return pineDisplays(layer,"pane") && ["histogram","columns","area"].includes(layer.style)
        && (!Object.hasOwn(layer,"offset") || layer.values.some(Number.isFinite));
}

function relativeLevel(level, low, high) {
    const span=high-low;
    const ratio=Number.isFinite(span) ? (level-low)/span : (level/2-low/2)/(high/2-low/2);
    return Math.max(0,Math.min(1,ratio));
}

function gradientColor(bottom, top, fraction) {
    if (fraction<=0) return bottom;
    if (fraction>=1) return top;
    const channels=[1,3,5,7].map(i=>{
        const a=parseInt(bottom.slice(i,i+2),16), b=parseInt(top.slice(i,i+2),16);
        return a+(b-a)*fraction;
    });
    const [r,g,b,a]=channels;
    return `rgba(${r},${g},${b},${a/255})`;
}

function hasPineGradientStops(sample) {
    return sample && Number.isFinite(sample.top_value) && Number.isFinite(sample.bottom_value)
        && (sample.top_color || sample.bottom_color);
}

function verticalFillPaint(ctx, sample, low, high, y) {
    if (!hasPineGradientStops(sample) || low>=high) return null;
    let {top_value:top,bottom_value:bottom,top_color:topColor,bottom_color:bottomColor}=sample;
    if (top<bottom) {
        [top,bottom]=[bottom,top];
        [topColor,bottomColor]=[bottomColor,topColor];
    }
    topColor??=bottomColor.slice(0,7)+"00";
    bottomColor??=topColor.slice(0,7)+"00";
    if (high<=bottom) return bottomColor;
    if (low>=top) return topColor;
    const gradient=ctx.createLinearGradient(0,y(low),0,y(high));
    if (top===bottom) {
        const edge=relativeLevel(top,low,high);
        gradient.addColorStop(0,bottomColor);
        gradient.addColorStop(edge,bottomColor);
        gradient.addColorStop(edge,topColor);
        gradient.addColorStop(1,topColor);
    } else {
        const colorAt=price=>gradientColor(bottomColor,topColor,relativeLevel(price,bottom,top));
        gradient.addColorStop(0,colorAt(low));
        if (bottom>low) gradient.addColorStop(relativeLevel(bottom,low,high),bottomColor);
        if (top<high) gradient.addColorStop(relativeLevel(top,low,high),topColor);
        gradient.addColorStop(1,colorAt(high));
    }
    return gradient;
}

function paintFillSegment(ctx, previous, point, paint, gradient) {
    ctx.fillStyle=paint;ctx.beginPath();ctx.moveTo(previous.x,previous.a);
    const before=previous.a-previous.b, after=point.a-point.b;
    if (gradient && before*after<0) {
        const t=before/(before-after);
        const x=previous.x+(point.x-previous.x)*t, y=previous.a+(point.a-previous.a)*t;
        ctx.lineTo(x,y);ctx.lineTo(previous.x,previous.b);ctx.closePath();
        ctx.moveTo(x,y);ctx.lineTo(point.x,point.b);ctx.lineTo(point.x,point.a);
    } else {
        ctx.lineTo(point.x,point.a);ctx.lineTo(point.x,point.b);ctx.lineTo(previous.x,previous.b);
    }
    ctx.closePath();ctx.fill();
}

function clipPineFillBoundary(vertices, level, keepAbove) {
    const inside=price=>keepAbove ? price>=level : price<=level;
    const clipped=[];
    let previous=vertices.at(-1);
    for (const point of vertices) {
        if (inside(previous[1])!==inside(point[1])) {
            const [left,from]=previous,[right,to]=point;
            const fraction=from<to ? relativeLevel(level,from,to) : 1-relativeLevel(level,to,from);
            clipped.push([left+(right-left)*fraction,level]);
        }
        if (inside(point[1])) clipped.push(point);
        previous=point;
    }
    return clipped;
}

function paintClippedPineFill(ctx, previous, point, paint, x, y, {low,high}) {
    const left=x(previous.index), right=x(point.index);
    const polygon=[[left,previous.a],[right,point.a],[right,point.b],[left,previous.b]];
    const vertices=clipPineFillBoundary(clipPineFillBoundary(polygon,low,true),high,false);
    if (vertices.length<3) return;
    ctx.fillStyle=paint;ctx.beginPath();ctx.moveTo(vertices[0][0],y(vertices[0][1]));
    for (const [px,price] of vertices.slice(1)) ctx.lineTo(px,y(price));
    ctx.closePath();ctx.fill();
}

function* pineFillMasks(series) {
    const outputs = new Map(series.filter(s => s.output_id).map(s => [s.output_id, s]));
    for (const layer of series.filter(s => s.kind === "fill" && !isPineLayerHidden(s))) {
        const [a, b] = (layer.references || []).map(id => outputs.get(id));
        if (a && b) yield {layer, a, b};
    }
}

function* pineFillMaskSegments(layer, a, b, start, end) {
    let previous=null;
    for (let i=Math.max(start,layer.firstSignalIndex??-Infinity);i<end;i++) {
        const av=a.values[i],bv=b.values[i];
        if (!Number.isFinite(av)||!Number.isFinite(bv)) {
            if (!layer.fillgaps) previous=null;
            continue;
        }
        const point={index:i,a:av,b:bv};
        if (previous) yield {previous,point};
        previous=point;
    }
}

// A visible fill can establish the bounds of an otherwise empty pane.
// Only the painted mask contributes here, including with hidden endpoints.
function* pineFillScaleValues(series,start,end) {
    const visible=color=>color && !(color.length===9 && color.endsWith("00"));
    for (const {layer,a,b} of pineFillMasks(series)) {
        if (pineDisplays(a,"pane") && pineDisplays(b,"pane")) continue;
        const gradient=layer.presentation_schema==="openpine.gradient-fill.1";
        for (const {previous,point} of pineFillMaskSegments(layer,a,b,start,end)) {
            if (previous.a===previous.b && point.a===point.b) continue;
            if (gradient) {
                const sample=layer.gradients?.[point.index];
                if (!hasPineGradientStops(sample) || !visible(sample.top_color) && !visible(sample.bottom_color)) continue;
            } else if (!visible(layer.colors?.[point.index])) continue;
            yield previous.a;yield previous.b;yield point.a;yield point.b;
        }
    }
}

function drawPineLayers(ctx, series, start, end, x, y, bounds) {
    const project=point=>({x:x(point.index),a:y(point.a),b:y(point.b)});
    for (const {layer,a,b} of pineFillMasks(series)) {
        const gradient=layer.presentation_schema==="openpine.gradient-fill.1";
        let lastStops=null,lastPaint=null;
        for (const {previous,point} of pineFillMaskSegments(layer,a,b,start,end)) {
            let paint=layer.colors?.[point.index];
            if (gradient) {
                const sample=layer.gradients?.[point.index];
                if (!sample || !lastStops || sample.top_value!==lastStops.top_value ||
                    sample.bottom_value!==lastStops.bottom_value || sample.top_color!==lastStops.top_color ||
                    sample.bottom_color!==lastStops.bottom_color) {
                    lastPaint=verticalFillPaint(ctx,sample,bounds.low,bounds.high,y);
                    lastStops=sample;
                }
                paint=lastPaint;
            }
            if (paint===null || paint===undefined) continue;
            const left=project(previous),right=project(point);
            // Canvas coordinates lose pixel precision above the 24-bit float mantissa.
            // Hidden endpoints may lie far outside the displayed price range.
            if (bounds && [left.a,left.b,right.a,right.b].some(value=>!(Math.abs(value)<2**24)))
                paintClippedPineFill(ctx,previous,point,paint,x,y,bounds);
            else paintFillSegment(ctx,left,right,paint,gradient);
        }
    }
    for (const s of series) {
        if (s.kind==="fill"||isPineLayerHidden(s)||isSignalLayer(s)) continue;
        ctx.save();
        if (s.kind==="hline") ctx.setLineDash(s.style==="dashed"?[6,4]:s.style==="dotted"?[1,3]:[]);
        if (isPineOHLC(s)) drawPineOHLC(ctx,s,start,end,x,y);
        else drawPinePlot(ctx,s,start,end,x,y,bounds);
        ctx.restore();
    }
}

function isSignalLayer(s) {return s.presentation_schema === "openpine.signal-layer.1";}

function splitPineOutputs(layers, overlay) {
  const price = [], pane = [], locations = new Map();
  const destination = layer => overlay || layer.force_overlay === true || layer.kind === "barcolor" ? price : pane;
  for (const layer of layers) {
    if (layer.kind !== "fill" && layer.output_id) locations.set(layer.output_id, destination(layer));
  }
  for (const layer of layers) {
    if (layer.kind !== "fill") { destination(layer).push(layer); continue; }
    const [a, b] = (layer.references || []).map(id => locations.get(id));
    if (a && a === b) a.push(layer);
  }
  const visible = group => {
    const needed = new Set(group.filter(s => s.kind === "fill" && !isPineLayerHidden(s)).flatMap(s => s.references));
    return group.filter(s => !isPineLayerHidden(s) || needed.has(s.output_id));
  };
  return {price: visible(price), pane: visible(pane)};
}

function firstSignalIndex(layer, windowStart, total) {
    return Number.isInteger(layer.show_last) && Number.isInteger(total)
        ? Math.max(0,total-layer.show_last)-windowStart : -Infinity;
}

// The selection and offset operate on source indices. Halo indices are
// relative to the same window, including when its prefix lies off-screen.
function signalPoints(s,start,end) {
    const result=[],offset=s.offset||0,first=s.firstSignalIndex??-Infinity;
    for(let i=Math.max(0,start-offset,first);i<Math.min(s.values.length,end-offset);i++)
        result.push({index:i+offset,value:s.values[i],color:s.colors?.[i],textcolor:s.textcolors?.[i]});
    for(const p of s.halo||[])if(p.index>=first&&p.index+offset>=start&&p.index+offset<end)
        result.push({...p,index:p.index+offset});
    return result;
}

function drawPineBackgrounds(ctx,layers,start,end,x,top,bottom) {
    const width=Math.max(1,Math.abs(x(start+1)-x(start)));
    for(const s of layers)if(isSignalLayer(s)&&s.kind==="bgcolor"&&!isPineLayerHidden(s))
        for(const p of signalPoints(s,start,end))if(p.color){ctx.fillStyle=p.color;ctx.fillRect(x(p.index)-width/2,top,width,bottom-top);}
}

function isPineOHLC(layer) {
    return layer.presentation_schema === "openpine.ohlc-layer.1";
}

function* pineOHLCPrices(layer,start,end) {
    for (let i=Math.max(start,layer.firstSignalIndex??-Infinity);i<end;i++) {
        const bar=layer.bars?.[i];
        if (bar) { yield bar.low; yield bar.high; }
    }
}

function paintPineCandle(ctx,bar,xx,y,width,body,wick,border) {
    ctx.lineWidth=1;
    if (wick) {
        ctx.strokeStyle=wick;
        ctx.beginPath();ctx.moveTo(xx,y(bar.high));ctx.lineTo(xx,y(bar.low));ctx.stroke();
    }
    const left=xx-width/2,top=Math.min(y(bar.open),y(bar.close));
    const height=Math.max(1,Math.abs(y(bar.open)-y(bar.close)));
    if (body) { ctx.fillStyle=body;ctx.fillRect(left,top,width,height); }
    if (border) { ctx.strokeStyle=border;ctx.strokeRect(left,top,width,height); }
}

function drawPineOHLC(ctx,layer,start,end,x,y) {
    const width=Math.max(1,Math.min(14,Math.abs(x(start+1)-x(start))*.65));
    for (let i=Math.max(start,layer.firstSignalIndex??-Infinity);i<end;i++) {
        const bar=layer.bars?.[i];
        if (!bar) continue;
        const xx=x(i);
        if (layer.kind==="plotcandle") {
            paintPineCandle(ctx,bar,xx,y,width,bar.color,bar.wickcolor,bar.bordercolor);
        } else if (bar.color) {
            ctx.strokeStyle=bar.color;ctx.lineWidth=1;
            ctx.beginPath();ctx.moveTo(xx,y(bar.high));ctx.lineTo(xx,y(bar.low));
            ctx.moveTo(xx-width/2,y(bar.open));ctx.lineTo(xx,y(bar.open));
            ctx.moveTo(xx,y(bar.close));ctx.lineTo(xx+width/2,y(bar.close));ctx.stroke();
        }
    }
}

function drawPineCandles(ctx,bars,layers,start,end,x,y) {
    const colors=new Map();
    for(const s of layers)if(isSignalLayer(s)&&s.kind==="barcolor"&&!isPineLayerHidden(s))
        for(const p of signalPoints(s,start,end))if(p.color!==null&&p.color!==undefined)colors.set(p.index,p.color);
    const width=Math.max(1,Math.min(14,Math.abs(x(start+1)-x(start))*.65));
    for(let i=start;i<end;i++){
        const b=bars[i];if(![b?.open,b?.high,b?.low,b?.close].every(Number.isFinite))continue;
        const color=colors.get(i)||(b.close>=b.open?"#5de4c2":"#ff8e98");
        paintPineCandle(ctx,b,x(i),y,width,color,color,null);
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

function pineArrowHeight(layer,value,paneHeight) {
    const relative = Math.min(1,Math.abs(value)/layer.max_abs);
    const height = layer.minheight+(layer.maxheight-layer.minheight)*relative;
    return Math.min(height,Math.max(1,paneHeight/3-8));
}

function pineArrowPadding(layers,start,end,paneHeight) {
    let padding=0;
    for(const layer of layers)if(isSignalLayer(layer)&&layer.kind==="plotarrow"&&!isPineLayerHidden(layer)&&layer.max_abs>0){
        for(const point of signalPoints(layer,start,end))
            if(point.color&&point.value)padding=Math.max(padding,pineArrowHeight(layer,point.value,paneHeight)+6);
    }
    return padding;
}

function drawPineArrow(ctx,layer,point,bar,x,y,top,bottom,pricePane) {
    if(!point.color||!point.value||!(layer.max_abs>0))return;
    const up=point.value>0, direction=up?1:-1;
    const height=pineArrowHeight(layer,point.value,bottom-top);
    const barPrice=up?bar?.low:bar?.high;
    const tip=pricePane ? y(Number.isFinite(barPrice)?barPrice:bar?.close)+direction*6
        : up ? bottom-6-height : top+6+height;
    if(!Number.isFinite(tip))return;
    const head=Math.min(6,height*.55);
    ctx.save();ctx.lineWidth=1.5;ctx.strokeStyle=point.color;ctx.beginPath();
    ctx.moveTo(x,tip+direction*height);ctx.lineTo(x,tip);
    ctx.moveTo(x-head*.7,tip+direction*head);ctx.lineTo(x,tip);ctx.lineTo(x+head*.7,tip+direction*head);
    ctx.stroke();ctx.restore();
}

function drawPineSignals(ctx,layers,bars,start,end,x,y,top,bottom,pricePane=false) {
    const radii={auto:4,tiny:2,small:4,normal:6,large:8,huge:10};
    for(const s of layers)if(isSignalLayer(s)&&s.kind.startsWith("plot")&&!isPineLayerHidden(s)){
        const r=radii[s.size]||4;
        for(const p of signalPoints(s,start,end)){
            if(p.value===null||p.value===undefined||!Number.isFinite(p.value))continue;
            const b=bars[p.index],xx=x(p.index);
            if(s.kind==="plotarrow"){
                drawPineArrow(ctx,s,p,b,xx,y,top,bottom,pricePane);
                continue;
            }
            const above=s.location==="abovebar"||s.location==="top";
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
function splitPineDrawings(records, overlay) {
  const price = [], pane = [];
  for (const record of records) {
    (overlay || record.force_overlay === true ? price : pane).push(record);
  }
  return {price, pane};
}

function pineLabelUsesBar(label) {
  return label.yloc === "abovebar" || label.yloc === "belowbar";
}

function pineLabelWindow(labels, bars, offset) {
  const result = [];
  for (const label of labels) {
    const globalX = drawingX(label, "x");
    if (!Number.isFinite(globalX)) continue;
    const x = globalX - offset;
    if (x < 0 || x >= bars.length) continue;
    let y = label.y;
    if (pineLabelUsesBar(label)) {
      const bar = bars[x];
      if (!bar || label.xloc === "bar_time" && label.x !== bar.time) continue;
      const extreme = label.yloc === "abovebar" ? bar.high : bar.low;
      y = Number.isFinite(extreme) ? extreme : bar.close;
    }
    if (Number.isFinite(y)) result.push({...label, x, y});
  }
  return result;
}

function pineLabelAnchorY(yloc, py, pricePane, top, bottom, roomAbove, roomBelow) {
  const gap = 6;
  if (yloc === "abovebar") return pricePane ? py - roomBelow - gap : top + roomAbove + gap;
  if (yloc === "belowbar") return pricePane ? py + roomAbove + gap : bottom - roomBelow - gap;
  return py;
}

function drawPineLabels(ctx, labels, start, end, x, y, left, right, top, bottom, pricePane = true) {
  const hits = [];
  if (right <= left || bottom <= top) return hits;
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top, right - left, bottom - top);
  ctx.clip();
  ctx.textBaseline = "middle";
  for (const label of labels) {
    if (!Number.isFinite(label.x) || label.x < start || label.x >= end || !Number.isFinite(label.y)) continue;
    const px = x(label.x);
    const py = pricePane || !pineLabelUsesBar(label) ? y(label.y) : 0;
    if (!Number.isFinite(px) || !Number.isFinite(py)) continue;
    const path = new Path2D();
    hits.push({path, tooltip: label.tooltip ?? "", left, right, top, bottom});
    if (label.style !== undefined) {
      drawStyledPineLabel(ctx, path, label, px, py, left, right, top, bottom, pricePane);
      continue;
    }
    const custom = label.textalign != null ||
      label.text_font_family === "monospace" || Boolean(label.text_formatting);
    ctx.font = custom ? pineDrawingFont(8.25, label) : "11px system-ui";
    ctx.textAlign = custom ? label.textalign ?? "center" : "left";
    const lines = custom ? label.text.split("\n") : [label.text.replaceAll("\n", " ").slice(0, 160)];
    const lineHeight = custom ? 14 : 18, padding = custom ? 3 : 0;
    const height = lines.length * lineHeight + 2 * padding;
    const below = label.yloc === "belowbar";
    const reach = height + 5;
    const anchor = pineLabelAnchorY(label.yloc, py, pricePane, top, bottom, below ? 3 : reach, below ? reach : 3);
    ctx.fillStyle = "#3a6fd3";
    path.arc(px, anchor, 3, 0, 2 * Math.PI);
    ctx.fill(path);
    if (!label.text) continue;
    const width = Math.min(Math.max(...lines.map(line => ctx.measureText(line).width)) + 12, right - left);
    if (width <= 12) continue;
    const bx = Math.max(left, Math.min(right - width, px - width / 2));
    const by = Math.max(top, Math.min(bottom - height, anchor + (below ? 5 : -reach)));
    ctx.fillRect(bx, by, width, height);
    path.rect(bx, by, width, height);
    ctx.fillStyle = "#fff";
    paintPineLabelText(ctx, null, lines, bx + 6, by + padding, width - 12, lineHeight, top, bottom);
  }
  ctx.restore();
  return hits;
}

const PINE_LABEL_POINTS = {auto: 12, tiny: 7, small: 10, normal: 12, large: 18, huge: 24};

function pineDrawingFont(points, label) {
  const format = label.text_formatting ?? 0;
  const family = label.text_font_family === "monospace" ? "monospace" : "system-ui";
  return `${format & 2 ? "italic " : ""}${format & 1 ? "bold " : ""}${points}pt ${family}`;
}

function pineLabelInk(color) {
  return color && !(color.length === 9 && color.endsWith("00"));
}

function paintPineLabelText(ctx, path, lines, left, top, width, lineHeight, clipTop, clipBottom) {
  const x = ctx.textAlign === "left" ? left : ctx.textAlign === "right" ? left + width : left + width / 2;
  const firstY = top + lineHeight / 2;
  const firstLine = Math.max(0, Math.floor((clipTop - firstY) / lineHeight));
  const endLine = Math.min(lines.length, 1 + Math.ceil((clipBottom - firstY) / lineHeight));
  for (let i = firstLine; i < endLine; i++) {
    const line = lines[i], y = firstY + i * lineHeight;
    ctx.fillText(line, x, y, width);
    if (path && line) {
      const m = ctx.measureText(line), scale = m.width > width ? width / m.width : 1;
      path.rect(x - m.actualBoundingBoxLeft * scale, y - m.actualBoundingBoxAscent,
        (m.actualBoundingBoxLeft + m.actualBoundingBoxRight) * scale,
        m.actualBoundingBoxAscent + m.actualBoundingBoxDescent);
    }
  }
}

function drawStyledPineLabel(ctx, path, label, px, py, left, right, top, bottom, pricePane) {
  const {text, color, textcolor, style, size, yloc} = label;
  const padding = 5, pointer = 6;
  const maxTextWidth = right - left - 2 * padding;
  if (maxTextWidth <= 0 || bottom <= top) return;
  const points = Math.min(64, typeof size === "number" ? size || PINE_LABEL_POINTS.auto : PINE_LABEL_POINTS[size]);
  const lineHeight = Math.ceil(points * 1.6);
  const lines = text.split("\n");
  ctx.font = pineDrawingFont(points, label);
  ctx.textAlign = label.textalign ?? "center";
  const width = Math.min(right - left, lines.reduce((max, line) => Math.max(max, ctx.measureText(line).width), 0) + 2 * padding);
  const height = lineHeight * lines.length + 2 * padding;
  const roomAbove = style === "up" ? 0 : style === "down" ? height + pointer : height / 2;
  const roomBelow = style === "down" ? 0 : style === "up" ? height + pointer : height / 2;
  py = pineLabelAnchorY(yloc, py, pricePane, top, bottom, roomAbove, roomBelow);
  const bx = style === "left" ? px + pointer : style === "right" ? px - pointer - width : px - width / 2;
  const by = style === "up" ? py + pointer : style === "down" ? py - pointer - height : py - height / 2;
  const cx = bx + width / 2, cy = by + height / 2;
  const shoulderX = Math.min(pointer, width / 2), shoulderY = Math.min(pointer, height / 2);
  const body = style !== "none" && pineLabelInk(color);
  if (body) {
    ctx.fillStyle = color;
    path.moveTo(bx, by);
    if (style === "up") {
      path.lineTo(cx - shoulderX, by); path.lineTo(px, py); path.lineTo(cx + shoulderX, by);
    }
    path.lineTo(bx + width, by);
    if (style === "right") {
      path.lineTo(bx + width, cy - shoulderY); path.lineTo(px, py); path.lineTo(bx + width, cy + shoulderY);
    }
    path.lineTo(bx + width, by + height);
    if (style === "down") {
      path.lineTo(cx + shoulderX, by + height); path.lineTo(px, py); path.lineTo(cx - shoulderX, by + height);
    }
    path.lineTo(bx, by + height);
    if (style === "left") {
      path.lineTo(bx, cy + shoulderY); path.lineTo(px, py); path.lineTo(bx, cy - shoulderY);
    }
    path.closePath();
    ctx.fill(path);
  }
  if (pineLabelInk(textcolor) && text) {
    ctx.fillStyle = textcolor;
    paintPineLabelText(ctx, body ? null : path, lines, bx + padding, by + padding,
      width - 2 * padding, lineHeight, top, bottom);
  }
}

const pineLabelHovers = new WeakMap();

function bindPineLabelHover(canvas, tooltip, fallback = () => "") {
  const state = {tooltip, fallback, hits: [], pointer: null};
  pineLabelHovers.set(canvas, state);
  canvas.addEventListener("pointermove", event => {
    state.pointer = canvas.hasPointerCapture(event.pointerId) ? null : [event.clientX, event.clientY];
    refreshPineLabelHover(canvas, state);
  });
  for (const event of ["pointerleave", "gotpointercapture", "pointercancel"]) canvas.addEventListener(event, () => {
    state.pointer = null;
    refreshPineLabelHover(canvas, state);
  });
}

function publishPineLabelHits(canvas, hits) {
  const state = pineLabelHovers.get(canvas);
  if (!state) return;
  state.hits = hits;
  if (state.pointer) refreshPineLabelHover(canvas, state);
}

function refreshPineLabelHover(canvas, {tooltip, fallback, hits, pointer}) {
  let text = "";
  if (pointer) {
    const bounds = canvas.getBoundingClientRect(), x = pointer[0] - bounds.left, y = pointer[1] - bounds.top;
    if (x >= 0 && y >= 0 && x < bounds.width && y < bounds.height) {
      const ctx = canvas.getContext("2d"), scale = ctx.getTransform();
      const hit = hits.findLast(hit => x >= hit.left && x < hit.right && y >= hit.top && y < hit.bottom &&
        ctx.isPointInPath(hit.path, scale.a * x + scale.e, scale.d * y + scale.f));
      text = hit?.tooltip || fallback(x);
    }
  }
  tooltip.textContent = text;
  tooltip.hidden = !text;
}

function drawingX(record, key) {
  return record.xloc === "bar_time" ? record["chart_" + key] : record[key];
}

function pineLineProjection(line, start, end, offset) {
  const first = drawingX(line, "x1"), second = drawingX(line, "x2");
  if (end <= start || ![first, line.y1, second, line.y2].every(Number.isFinite)) return null;
  let x1 = first - offset, x2 = second - offset, y1 = line.y1, y2 = line.y2;
  if (x2 < x1) [x1, y1, x2, y2] = [x2, y2, x1, y1];
  if (x1 === x2) {
    return x1 >= start && x1 < end && y1 !== y2 ? {left: x1, right: x2, y1, y2} : null;
  }
  const left = ["left", "both"].includes(line.extend) ? start : Math.max(start, x1);
  const right = ["right", "both"].includes(line.extend) ? end - 1 : Math.min(end - 1, x2);
  if (left > right) return null;
  function priceAt(x) {
    if (x === x1 || y1 === y2) return y1;
    if (x === x2) return y2;
    const t = (x - x1) / (x2 - x1);
    if (t < 0 || t > 1) {
      const scale = Math.max(Math.abs(y1), Math.abs(y2));
      const a = y1 / scale, b = y2 / scale;
      return (a + t * (b - a)) * scale;
    }
    return Math.sign(y1) === Math.sign(y2) ? y1 + t * (y2 - y1) : (1 - t) * y1 + t * y2;
  }
  return {left, right, priceAt};
}

function pineLineSegments(lines, start, end, offset) {
  return lines.map(line => pineLineSegment(pineLineProjection(line, start, end, offset))).filter(Boolean);
}

function pineLineSegment(projection) {
  if (!projection) return null;
  const {left, right, priceAt} = projection;
  const y1 = priceAt ? priceAt(left) : projection.y1;
  const y2 = priceAt ? priceAt(right) : projection.y2;
  return Number.isFinite(y1) && Number.isFinite(y2) ? [left, y1, right, y2] : null;
}

function pineLineStrokes(lines, start, end, offset) {
  const strokes = [];
  for (const line of lines) {
    const {color = "#2196F3FF", style = "solid", width = 1} = line;
    if (color === null || color.length === 9 && color.endsWith("00")) continue;
    const projection = pineLineProjection(line, start, end, offset);
    const segment = pineLineSegment(projection);
    if (!segment) continue;
    const arrows = [];
    if (style.startsWith("arrow_")) {
      let left = [drawingX(line, "x1") - offset, line.y1];
      let right = [drawingX(line, "x2") - offset, line.y2];
      if (left[0] > right[0]) [left, right] = [right, left];
      const visible = ([anchor]) => projection.left <= anchor && anchor <= projection.right;
      if ((style === "arrow_left" || style === "arrow_both") && visible(left)) arrows.push([left, right]);
      if ((style === "arrow_right" || style === "arrow_both") && visible(right)) arrows.push([right, left]);
    }
    strokes.push({color, style, width, segment, arrows});
  }
  return strokes;
}

function pineLineFillPolygons(fills, lines, start, end, offset) {
  if (!fills.length) return [];
  const projected = new Map(lines.map(line => [line.id, pineLineProjection(line, start, end, offset)]));
  const polygons = [];
  for (const fill of fills) {
    const a = projected.get(fill.line1), b = projected.get(fill.line2);
    if (fill.color == null || !a?.priceAt || !b?.priceAt) continue;
    const left = Math.max(a.left, b.left), right = Math.min(a.right, b.right);
    if (left >= right) continue;
    const al = a.priceAt(left), ar = a.priceAt(right), bl = b.priceAt(left), br = b.priceAt(right);
    if (![al, ar, bl, br].every(Number.isFinite) || al === bl && ar === br) continue;
    if (al !== bl && ar !== br && (al < bl) !== (ar < br)) {
      // Split a crossing into two simple polygons. Normalize to keep differences finite.
      let dl = Math.abs(al - bl), dr = Math.abs(ar - br);
      if (!Number.isFinite(dl) || !Number.isFinite(dr)) {
        const scale = Math.max(Math.abs(al), Math.abs(ar), Math.abs(bl), Math.abs(br));
        dl = Math.abs(al / scale - bl / scale);
        dr = Math.abs(ar / scale - br / scale);
      }
      const length = Math.max(dl, dr);
      const fraction = (dl / length) / (dl / length + dr / length);
      const crossX = left + (right - left) * fraction;
      const crossY = a.priceAt(crossX);
      if (!Number.isFinite(crossY)) continue;
      const crossing = [crossX, crossY];
      polygons.push({color: fill.color, points: [[left, al], crossing, [left, bl]]},
                    {color: fill.color, points: [crossing, [right, ar], [right, br]]});
    } else {
      polygons.push({color: fill.color, points: [[left, al], [right, ar], [right, br], [left, bl]]});
    }
  }
  return polygons;
}

function drawPineLineFills(ctx, polygons, x, y) {
  if (!polygons.length) return;
  ctx.save();
  for (const {color, points} of polygons) {
    ctx.fillStyle = color;
    ctx.beginPath();
    const [[firstX, firstY], ...rest] = points;
    ctx.moveTo(x(firstX), y(firstY));
    for (const [px, py] of rest) ctx.lineTo(x(px), y(py));
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
}

function drawPineLines(ctx, segments, x, y, color, width) {
  if (!segments.length) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  for (const [x1, y1, x2, y2] of segments) {
    ctx.moveTo(x(x1), y(y1));
    ctx.lineTo(x(x2), y(y2));
  }
  ctx.stroke();
  ctx.restore();
}

function drawPineLineStrokes(ctx, strokes, x, y) {
  if (!strokes.length) return;
  ctx.save();
  for (const {color, style, width, segment: [x1, y1, x2, y2], arrows} of strokes) {
    applyPineStroke(ctx, color, style, width);
    ctx.beginPath();
    ctx.moveTo(x(x1), y(y1));
    ctx.lineTo(x(x2), y(y2));
    appendPineArrows(ctx, arrows, x, y, width);
    ctx.stroke();
  }
  ctx.restore();
}

function appendPineArrows(ctx, arrows, x, y, width) {
  for (const [[tipX, tipY], [otherX, otherY]] of arrows) {
    const px = x(tipX), py = y(tipY);
    const dx = x(otherX) - px, dy = y(otherY) - py;
    const length = Math.hypot(dx, dy);
    if (!Number.isFinite(length) || length === 0) continue;
    const size = Math.min(Math.max(6, width * 3), 24, length);
    const alongX = size * (dx / length), alongY = size * (dy / length);
    ctx.moveTo(px + alongX - alongY / 2, py + alongY + alongX / 2);
    ctx.lineTo(px, py);
    ctx.lineTo(px + alongX + alongY / 2, py + alongY - alongX / 2);
  }
}

function applyPineStroke(ctx, color, style, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = style === "dotted" ? "round" : "butt";
  ctx.setLineDash(style === "dashed" ? [width * 4, width * 3] : style === "dotted" ? [1, width * 2] : []);
}

function clipPineRing(points, x, keep) {
  const result = [];
  let prior = points.at(-1);
  for (const point of points) {
    const inside = keep(point[0], x), priorInside = keep(prior[0], x);
    if (inside !== priorInside) {
      const t = (x - prior[0]) / (point[0] - prior[0]);
      const a = prior[1], b = point[1];
      result.push([x, Math.sign(a) === Math.sign(b) ? a + t * (b - a) : (1 - t) * a + t * b]);
    }
    if (inside) result.push(point);
    prior = point;
  }
  return result;
}

function pinePolylineGeometry(polylines, start, end, offset) {
  const fills = [], strokes = [];
  if (end <= start) return {fills, strokes};
  for (const row of polylines) {
    const vertices = row.xloc === "bar_time" ? row.chart_vertices : row.vertices;
    if (!vertices?.length) continue;
    const points = vertices.map(([x, y]) => [Number.isFinite(x) ? x - offset : null, y]);
    if (row.closed && row.fill_color && points.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y))) {
      const left = clipPineRing(points, start, (a, b) => a >= b);
      const clipped = clipPineRing(left, end - 1, (a, b) => a <= b);
      if (clipped.length >= 3) fills.push({points: clipped, color: row.fill_color});
    }
    if (row.line_color) {
      const edges = [];
      for (let i = 0; i < points.length - (row.closed ? 0 : 1); i++) {
        const [x1, y1] = points[i], [x2, y2] = points[(i + 1) % points.length];
        edges.push({x1, y1, x2, y2});
      }
      const segments = pineLineSegments(edges, start, end, 0);
      if (!segments.length) continue;
      const stroke = {segments, color: row.line_color};
      if (row.line_style !== undefined) {
        const arrows = [], style = row.line_style;
        if (!row.closed) {
          const add = (point, neighbor) => {
            if (point[0] !== null && neighbor[0] !== null && point[0] >= start && point[0] <= end - 1)
              arrows.push([point, neighbor]);
          };
          if (style === "arrow_left" || style === "arrow_both") add(points[0], points[1]);
          if (style === "arrow_right" || style === "arrow_both") add(points.at(-1), points.at(-2));
        }
        Object.assign(stroke, {style, width: row.line_width, arrows});
      }
      strokes.push(stroke);
    }
  }
  return {fills, strokes};
}

function drawPinePolylineStrokes(ctx, strokes, x, y) {
  for (const {segments, color, style, width, arrows} of strokes) {
    if (style === undefined) { drawPineLines(ctx, segments, x, y, color, 1); continue; }
    ctx.save();
    applyPineStroke(ctx, color, style, width);
    ctx.beginPath();
    for (const [x1, y1, x2, y2] of segments) {
      ctx.moveTo(x(x1), y(y1));
      ctx.lineTo(x(x2), y(y2));
    }
    appendPineArrows(ctx, arrows, x, y, width);
    ctx.stroke();
    ctx.restore();
  }
}

function* pinePolylinePrices({fills, strokes}) {
  for (const {points} of fills) for (const [, price] of points) yield price;
  for (const {segments} of strokes) for (const [, y1, , y2] of segments) {
    yield y1;
    yield y2;
  }
}

function pineBoxRects(boxes, start, end, offset) {
  const rects = [];
  if (end <= start) return rects;
  const visibleColor = color => color != null && !(color.length === 9 && color.endsWith("00"));
  for (const box of boxes) {
    const first = drawingX(box, "left"), second = drawingX(box, "right");
    if (![first, box.top, second, box.bottom].every(Number.isFinite)) continue;
    const left = Math.min(first, second) - offset, right = Math.max(first, second) - offset;
    const top = Math.max(box.top, box.bottom), bottom = Math.min(box.top, box.bottom);
    if (left === right && top === bottom) continue;
    const intersects = right >= start && left < end;
    const fill = intersects && visibleColor(box.bgcolor) ? box.bgcolor : null;
    const border = visibleColor(box.border_color) ? box.border_color : null;
    const extendLeft = left < right && (box.extend === "left" || box.extend === "both");
    const extendRight = left < right && (box.extend === "right" || box.extend === "both");
    const extended = extendLeft && right >= start || extendRight && left < end;
    const hasText = intersects && left < right && bottom < top && box.text && visibleColor(box.text_color);
    if (!hasText && fill === null && (border === null || !intersects && !extended)) continue;
    const bounds = intersects ? {
      left: Math.max(left, start), right: right >= end ? end - 1 : right,
      leftCut: left < start, rightCut: right >= end
    } : null;
    const rect = {bounds, top, bottom, fill, border, extendLeft, extendRight,
      width: box.border_width ?? 1, style: box.border_style ?? "solid"};
    if (hasText) rect.text = {left, right, box};
    rects.push(rect);
  }
  return rects;
}

function drawPineBoxes(ctx, rects, x, y, paneLeft, paneRight) {
  if (!rects.length) return;
  ctx.save();
  for (const rect of rects) {
    const {bounds} = rect;
    const left = !bounds || bounds.leftCut ? paneLeft : x(bounds.left);
    const right = !bounds || bounds.rightCut ? paneRight : x(bounds.right);
    const top = y(rect.top), bottom = y(rect.bottom);
    if (rect.fill != null && right > left && bottom > top) {
      ctx.fillStyle = rect.fill;
      ctx.fillRect(left, top, right - left, bottom - top);
    }
    if (rect.border != null) {
      applyPineStroke(ctx, rect.border, rect.style, rect.width);
      const start = rect.extendLeft ? paneLeft : left, end = rect.extendRight ? paneRight : right;
      ctx.beginPath();
      if (end > start) {
        ctx.moveTo(start, top); ctx.lineTo(end, top);
        if (bottom > top) {ctx.moveTo(start, bottom); ctx.lineTo(end, bottom);}
      }
      if (bounds && bottom > top) {
        if (!bounds.leftCut) {ctx.moveTo(left, top); ctx.lineTo(left, bottom);}
        if (!bounds.rightCut && (right > left || bounds.leftCut)) {ctx.moveTo(right, top); ctx.lineTo(right, bottom);}
      }
      ctx.stroke();
    }
    if (rect.text) drawPineBoxText(ctx, rect.text.box, x(rect.text.left), x(rect.text.right), top, bottom);
  }
  ctx.restore();
}

const PINE_BOX_POINTS = {auto: 12, tiny: 8, small: 10, normal: 14, large: 20, huge: 36};
const pineBoxTextGraphemes = new Intl.Segmenter(undefined, {granularity: "grapheme"});

function wrapPineBoxText(ctx, text, width) {
  const lines = [];
  const fits = line => ctx.measureText(line).width <= width;
  for (const paragraph of text.split(/\r\n?|\n/)) {
    if (fits(paragraph)) {
      lines.push(paragraph);
      continue;
    }
    let line = "";
    for (const word of paragraph.match(/\S+/gu) ?? []) {
      const joined = line ? line + " " + word : word;
      if (fits(joined)) {
        line = joined;
        continue;
      }
      if (line) lines.push(line);
      if (fits(word)) {
        line = word;
        continue;
      }
      line = "";
      for (const {segment} of pineBoxTextGraphemes.segment(word)) {
        if (line && !fits(line + segment)) {
          lines.push(line);
          line = "";
        }
        line += segment;
      }
    }
    lines.push(line);
  }
  return lines;
}

function drawPineBoxText(ctx, box, left, right, top, bottom) {
  const width = right - left, height = bottom - top;
  if (width <= 0 || height <= 0) return;
  const size = box.text_size;
  const points = Math.min(64, (typeof size === "number" ? size : PINE_BOX_POINTS[size]) || PINE_BOX_POINTS.auto);
  const lineHeight = points * 1.6;
  const wrap = box.text_wrap === "auto";
  ctx.save();
  ctx.font = pineDrawingFont(points, box);
  ctx.textAlign = box.text_halign;
  ctx.textBaseline = "middle";
  ctx.fillStyle = box.text_color;
  const lines = wrap ? wrapPineBoxText(ctx, box.text, width) : [box.text.replace(/\r\n?|\n/g, " ")];
  const anchorX = box.text_halign === "left" ? left : box.text_halign === "right" ? right : left + width / 2;
  const freeHeight = Math.max(0, height - lines.length * lineHeight);
  const inset = box.text_valign === "top" ? 0 : box.text_valign === "bottom" ? freeHeight : freeHeight / 2;
  if (wrap) {
    ctx.beginPath();
    ctx.rect(left, top, width, height);
    ctx.clip();
  }
  for (let i = 0; i < lines.length && inset + i * lineHeight < height; i++) {
    ctx.fillText(lines[i], anchorX, top + inset + (i + .5) * lineHeight);
  }
  ctx.restore();
}

function renderPineTables(layer, tables) {
  if (!layer) return;
  const rendered = [];
  for (const record of tables) {
    if (!record.cells.length) continue;
    const table = document.createElement("table");
    table.dataset.position = record.position;
    table.setAttribute("aria-label", "Таблица Pine");
    table.style.backgroundColor = record.bgcolor ?? "transparent";
    const rowNumbers = [...new Set(record.cells.map(cell => cell.row))];
    const columns = [...new Set(record.cells.map(cell => cell.column))].sort((a, b) => a - b);
    const cells = new Map(record.cells.map(cell => [cell.row * record.columns + cell.column, cell]));
    const body = table.createTBody();
    for (const row of rowNumbers) {
      const tr = body.insertRow();
      for (const column of columns) {
        const td = tr.insertCell();
        const cell = cells.get(row * record.columns + column);
        if (!cell) continue;
        td.textContent = cell.text;
        td.style.color = cell.text_color ?? "transparent";
        td.style.backgroundColor = cell.bgcolor ?? "transparent";
      }
    }
    rendered.push(table);
  }
  layer.replaceChildren(...rendered);
}
