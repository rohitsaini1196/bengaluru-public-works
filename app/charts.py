"""Server-rendered SVG charts (no JavaScript, no chart library): they work on the live app and in the static export.

Every chart is accessible: role="img", a <title>, and the numbers are also printed as text."""
from markupsafe import Markup, escape


def crore(v):
    v = float(v or 0)
    if v >= 1e7:
        return f"₹{v / 1e7:,.1f} Cr"
    if v >= 1e5:
        return f"₹{v / 1e5:,.0f} L"
    return f"₹{v:,.0f}"


def columns(rows, title, height=210, highlight=None):
    """Vertical columns, e.g. payments per year. rows: [(label, value, note)]."""
    if not rows:
        return Markup("")
    n, w, gap, top, base = len(rows), 64, 10, 34, 30
    width = n * (w + gap)
    vmax = max(v for _, v, _ in rows) or 1
    out = [f'<svg class="chart cols" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}"><title>{escape(title)}</title>']
    for i, (label, v, note) in enumerate(rows):
        h = (height - top - base) * v / vmax
        x, y = i * (w + gap), height - base - h
        cls = "bar hi" if highlight and label == highlight else "bar"
        out.append(f'<rect class="{cls}" x="{x}" y="{y:.1f}" width="{w}" height="{h:.1f}"><title>{escape(label)}: {escape(crore(v))}'
                   f'{" · " + escape(note) if note else ""}</title></rect>')
        out.append(f'<text class="v" x="{x + w / 2}" y="{y - 6:.1f}" text-anchor="middle">{escape(crore(v).replace("₹", "").replace(" Cr", ""))}</text>')
        out.append(f'<text class="l" x="{x + w / 2}" y="{height - 10}" text-anchor="middle">{escape(label)}</text>')
    out.append("</svg>")
    return Markup("".join(out))


def hbars(rows, title, unit="money", highlight=0):
    """Horizontal bars as an HTML list (wraps nicely on phones). rows: [(label, value, href, note)].
    highlight: index or label of the row drawn in the accent colour (default: the first, i.e. the largest)."""
    if not rows:
        return Markup("")
    vmax = max(r[1] for r in rows) or 1
    out = [f'<ol class="hbars" aria-label="{escape(title)}">']
    for i, (label, v, href, note) in enumerate(rows):
        hi = (i == highlight) if isinstance(highlight, int) else (label == highlight)
        pct = max(0.6, 100 * v / vmax)
        name = f'<a href="{escape(href)}">{escape(label)}</a>' if href else escape(label)
        val = crore(v) if unit == "money" else f"{v:,.0f}"
        out.append(f'<li{" class=hi" if hi else ""}><div class="hb-top"><span class="hb-name">{name}</span><span class="hb-val">{escape(val)}</span></div>'
                   f'<div class="hb-track"><span class="hb-fill" style="width:{pct:.2f}%"></span></div>'
                   + (f'<div class="hb-note">{escape(note)}</div>' if note else "") + "</li>")
    out.append("</ol>")
    return Markup("".join(out))


def share(parts, title):
    """One 100 % bar split into parts. parts: [(label, value, css_class)]."""
    total = sum(v for _, v, _ in parts) or 1
    segs, keys = [], []
    for label, v, cls in parts:
        pct = 100 * v / total
        segs.append(f'<span class="seg {cls}" style="width:{pct:.3f}%" title="{escape(label)}: {escape(crore(v))} ({pct:.1f}%)"></span>')
        keys.append(f'<li><i class="sw {cls}"></i>{escape(label)} <b>{pct:.0f}%</b> <span class="muted">{escape(crore(v))}</span></li>')
    return Markup(f'<div class="share" role="img" aria-label="{escape(title)}">{"".join(segs)}</div><ul class="share-key">{"".join(keys)}</ul>')


def funnel(stages, total, title):
    """How many projects have evidence for each lifecycle stage. stages: [(label, count, explanation)]."""
    out = [f'<ol class="funnel" aria-label="{escape(title)}">']
    for label, n, expl in stages:
        pct = 100 * n / total if total else 0
        out.append(f'<li><span class="f-label">{escape(label)}</span><span class="f-track"><span class="f-fill" style="width:{pct:.1f}%"></span>'
                   f'</span><span class="f-n">{n}<small>/{total}</small></span><span class="f-expl">{escape(expl)}</span></li>')
    out.append("</ol>")
    return Markup("".join(out))


def lifecycle(stages, labels):
    """The six-stage strip for one project: filled where evidence exists."""
    out = ['<ol class="lifecycle">']
    for key, label in labels:
        on = bool(stages.get(key))
        out.append(f'<li class="{"on" if on else "off"}"><span class="lc-dot"></span><span class="lc-l">{escape(label)}</span></li>')
    out.append("</ol>")
    return Markup("".join(out))
