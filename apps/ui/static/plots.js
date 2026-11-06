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
    ctx.font = custom ? pineLabelFont(8.25, label) : "11px system-ui";
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

function pineLabelFont(points, label) {
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
  ctx.font = pineLabelFont(points, label);
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
    ctx.stroke();
  }
  ctx.restore();
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
      if (segments.length) strokes.push({segments, color: row.line_color});
    }
  }
  return {fills, strokes};
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
    if (fill === null && (border === null || !intersects && !extended)) continue;
    const bounds = intersects ? {
      left: Math.max(left, start), right: right >= end ? end - 1 : right,
      leftCut: left < start, rightCut: right >= end
    } : null;
    rects.push({bounds, top, bottom, fill, border, extendLeft, extendRight,
      width: box.border_width ?? 1, style: box.border_style ?? "solid"});
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
    if (rect.border == null) continue;
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
