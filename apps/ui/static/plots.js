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
    if (end - start <= 20 && !["columns", "histogram", "circles", "cross"].includes(style)) {
        for (let i=start;i<end;i++) {
            const color=colorAt(i),value=series.values[i];
            if (missing(value)||color===null||color===undefined) continue;
            ctx.fillStyle=color;ctx.beginPath();ctx.arc(x(i),y(value),2.5,0,2*Math.PI);ctx.fill();
        }
    }
}
