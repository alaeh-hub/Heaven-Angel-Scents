// Chart.js defaults and chart helpers (legends, KPI tiles, formatters)
// (Part of the app's main script; loaded in order by base.html.)

function sortMovementTypes(types) {
    function rank(t) {
        var i = MOVEMENT_TYPE_ORDER.indexOf(t);
        return i === -1 ? MOVEMENT_TYPE_ORDER.length : i;
    }
    return types.slice().sort(function (a, b) { return rank(a) - rank(b); });
}

function titleCaseLabel(text) {
    return String(text).toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
}

// HTML legend for a Chart.js chart, rendered into `container`. Chart.js's
// canvas legend wraps into ragged centred rows on a phone and eats into
// the plot's fixed height; this one is a real grid (see .chart-legend in
// style.css) under the canvas. Each chip still toggles its dataset on and
// off, same as clicking the built-in legend.
function renderChartLegend(chart, container) {
    if (!container) return;
    container.innerHTML = '';
    chart.data.datasets.forEach(function (ds, i) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'chart-legend-item';
        btn.setAttribute('aria-pressed', 'true');
        var swatch = document.createElement('span');
        swatch.className = 'chart-legend-swatch';
        swatch.style.background = ds.borderColor;
        var label = document.createElement('span');
        label.className = 'chart-legend-label';
        label.textContent = ds.label;
        btn.appendChild(swatch);
        btn.appendChild(label);
        btn.addEventListener('click', function () {
            var visible = chart.isDatasetVisible(i);
            chart.setDatasetVisibility(i, !visible);
            btn.setAttribute('aria-pressed', String(!visible));
            chart.update();
        });
        container.appendChild(btn);
    });
}

// ---- Dashboard chart kit (Boss-Insights style) -------------------------
// Shared by the admin and branch Reports pages: compact peso formatting,
// SVG sparklines for KPI tiles, up/down delta chips, a donut with its total
// in the middle plus a "name / amount / share" list, and one set of Chart.js
// defaults so every graph reads as one system.
function compactPeso(value) {
    var n = Number(value) || 0;
    var abs = Math.abs(n);
    var sign = n < 0 ? '-' : '';
    if (abs < 1000) return sign + '₱' + abs.toLocaleString('en-PH', { maximumFractionDigits: abs % 1 ? 2 : 0 });
    var f = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 });
    return sign + '₱' + f.format(abs);
}

function fullPeso(value) {
    return '₱' + Number(value || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pctChange(current, previous) {
    if (!previous) return null;
    return ((current - previous) / Math.abs(previous)) * 100;
}

// Chart.js cannot parse every modern CSS colour (oklch, color-mix), so each
// theme token is resolved to plain sRGB through a 1px canvas first.
var chartColorCtx = null;
function chartRgb(value) {
    if (!chartColorCtx) {
        var cv = document.createElement('canvas');
        cv.width = cv.height = 1;
        chartColorCtx = cv.getContext('2d', { willReadFrequently: true });
    }
    chartColorCtx.clearRect(0, 0, 1, 1);
    chartColorCtx.fillStyle = '#000';
    chartColorCtx.fillStyle = value;
    chartColorCtx.fillRect(0, 0, 1, 1);
    var d = chartColorCtx.getImageData(0, 0, 1, 1).data;
    return d[3] === 255 ? 'rgb(' + d[0] + ',' + d[1] + ',' + d[2] + ')'
        : 'rgba(' + d[0] + ',' + d[1] + ',' + d[2] + ',' + (d[3] / 255).toFixed(3) + ')';
}

function chartTheme() {
    var css = getComputedStyle(document.documentElement);
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    var tok = function (name) { return chartRgb(css.getPropertyValue(name).trim()); };
    var accent = tok('--accent');
    var sales = tok('--chart-sales');
    var cream = tok('--chart-cream');
    var cost = tok('--chart-cost');
    return {
        dark: dark,
        ink: tok('--ink'),
        soft: tok('--ink-soft'),
        faint: tok('--ink-faint'),
        grid: tok('--border'),
        surface: tok('--surface'),
        tipEdge: tok('--chart-tip-edge'),
        font: css.getPropertyValue('--font').trim(),
        accent: accent,
        sales: sales,
        cost: cost,
        profit: accent,
        cream: cream,
        series: [accent, sales, cream, cost, tok('--chart-4'), tok('--chart-5'), tok('--chart-6'), tok('--chart-7')]
    };
}

// Chart.js defaults + the "total in the middle" doughnut plugin. Safe to
// call on every data load.
function applyChartDefaults() {
    var t = chartTheme();
    Chart.defaults.font.family = t.font;
    Chart.defaults.font.size = 12;
    Chart.defaults.color = t.soft;
    Chart.defaults.borderColor = t.grid;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.elements.line.borderWidth = 2.5;
    Chart.defaults.elements.line.tension = 0.4;
    Chart.defaults.elements.point.radius = 0;
    Chart.defaults.elements.point.hoverRadius = 5;
    Chart.defaults.elements.bar.borderRadius = 6;
    Chart.defaults.elements.bar.borderSkipped = false;
    Chart.defaults.interaction = { mode: 'index', intersect: false };
    var tip = Chart.defaults.plugins.tooltip;
    tip.backgroundColor = t.surface;
    tip.titleColor = t.ink;
    tip.bodyColor = t.soft;
    tip.borderColor = t.tipEdge;
    tip.borderWidth = 1;
    tip.padding = 10;
    tip.cornerRadius = 10;
    tip.boxPadding = 4;
    tip.usePointStyle = true;
    if (!Chart.registry.plugins.get('donutCenter')) {
        Chart.register({
            id: 'donutCenter',
            afterDraw: function (chart) {
                var opts = chart.options.plugins && chart.options.plugins.donutCenter;
                if (!opts || !opts.title) return;
                var meta = chart.getDatasetMeta(0);
                if (!meta || !meta.data.length) return;
                var ctx = chart.ctx;
                var x = meta.data[0].x, y = meta.data[0].y;
                var th = chartTheme();
                ctx.save();
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = th.ink;
                ctx.font = "700 22px 'Inter', sans-serif";
                ctx.fillText(opts.title, x, y - 8);
                ctx.fillStyle = th.faint;
                ctx.font = "500 12px 'Inter', sans-serif";
                ctx.fillText(opts.subtitle || 'Total', x, y + 14);
                ctx.restore();
            }
        });
    }
}

// Flat sparkline with a soft fill, as inline SVG (no axes, no library).
function sparklineSVG(values, color) {
    var w = 100, h = 30, pad = 2;
    if (!values || values.length < 2) values = [0, 0];
    var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
    var span = max - min || 1;
    var pts = values.map(function (v, i) {
        var x = (i / (values.length - 1)) * w;
        var y = h - pad - ((v - min) / span) * (h - pad * 2);
        return x.toFixed(2) + ',' + y.toFixed(2);
    });
    var line = 'M' + pts.join(' L');
    return '<svg class="kpi-spark-svg" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true">' +
        '<path d="' + line + ' L' + w + ',' + h + ' L0,' + h + ' Z" fill="' + color + '" opacity="0.14"/>' +
        '<path d="' + line + '" fill="none" stroke="' + color + '" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>' +
        '</svg>';
}

// Fills one KPI tile (markup: .kpi-tile > .kpi-value, .kpi-meta, .kpi-spark).
//   o.value      text for the big number (title gets o.title)
//   o.delta      change vs previous period, or null (a % unless o.points)
//   o.points     true when delta is in percentage points (margin tiles)
//   o.goodWhenUp false for costs: an increase is shown red
//   o.note       text beside the chip, e.g. "17.1% of total sales"
//   o.series     values for the sparkline; o.color its colour
function fillKpiTile(el, o) {
    if (!el) return;
    var val = el.querySelector('.kpi-value');
    val.textContent = o.value;
    if (o.title) val.title = o.title;
    var meta = el.querySelector('.kpi-meta');
    var chip = '';
    var d = o.delta;
    if (d !== null && d !== undefined && isFinite(d)) {
        var up = d > 0, flat = Math.abs(d) < 0.05;
        var good = flat ? null : (up === (o.goodWhenUp !== false));
        var cls = flat ? 'flat' : (good ? 'good' : 'bad');
        var arrow = flat ? '→' : (up ? '↑' : '↓');
        chip = '<span class="kpi-chip kpi-chip-' + cls + '" title="vs previous period">' + arrow + ' ' +
            Math.abs(d).toFixed(1) + (o.points ? ' pts' : '%') + '</span>';
    }
    meta.innerHTML = chip;
    if (o.note) {
        var note = document.createElement('span');
        note.className = 'kpi-note';
        note.textContent = o.note;
        meta.appendChild(note);
    }
    var spark = el.querySelector('.kpi-spark');
    if (spark) spark.innerHTML = o.series ? sparklineSVG(o.series, o.color) : '';
}

// "Dot  Name  Amount  Share%" rows under a doughnut.
function renderShareList(container, items, colors, formatValue) {
    if (!container) return;
    var total = items.reduce(function (a, b) { return a + b.value; }, 0);
    container.innerHTML = '';
    if (!items.length) {
        container.innerHTML = '<div class="share-empty">No data yet</div>';
        return;
    }
    items.forEach(function (it, i) {
        var row = document.createElement('div');
        row.className = 'share-row';
        var dot = document.createElement('span');
        dot.className = 'share-dot';
        dot.style.background = colors[i % colors.length];
        var name = document.createElement('span');
        name.className = 'share-name';
        name.textContent = it.name;
        var amt = document.createElement('span');
        amt.className = 'share-amount';
        amt.textContent = formatValue(it.value);
        var pct = document.createElement('span');
        pct.className = 'share-pct';
        pct.textContent = (total ? (it.value / total) * 100 : 0).toFixed(1) + '%';
        row.appendChild(dot); row.appendChild(name); row.appendChild(amt); row.appendChild(pct);
        container.appendChild(row);
    });
}

// Common options for a smooth line chart.
function lineChartOptions(t, yFormat) {
    return {
        plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: function (c) { return ' ' + c.dataset.label + '  ' + (yFormat ? yFormat(c.parsed.y) : c.parsed.y); } } }
        },
        scales: {
            y: { beginAtZero: true, border: { display: false }, grid: { color: t.grid }, ticks: { padding: 8, callback: function (v) { return yFormat ? yFormat(v) : v; } } },
            x: { border: { display: false }, grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 18 } }
        }
    };
}

// Keeps a donut readable with many outlets: the biggest (max - 1) rows stay,
// the rest fold into one "Other (n)" slice, flagged other:true so callers can
// give it a neutral colour instead of recycling a palette colour.
function topWithOther(rows, max) {
    var sorted = rows.slice().sort(function (a, b) { return b.value - a.value; });
    if (sorted.length <= max) return sorted;
    var head = sorted.slice(0, max - 1);
    var tail = sorted.slice(max - 1);
    head.push({
        name: 'Other (' + tail.length + ')',
        value: tail.reduce(function (a, r) { return a + r.value; }, 0),
        other: true
    });
    return head;
}

// One colour per row: palette order, with the folded "Other" slice neutral.
function rowColors(rows, palette, neutral) {
    return rows.map(function (r, i) { return r.other ? neutral : palette[i % palette.length]; });
}

function shortDateLabel(iso) {
    var d = new Date(iso + 'T00:00:00');
    return isNaN(d) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

