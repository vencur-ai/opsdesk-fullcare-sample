"""HTML building blocks for OpsDesk Workforce: escaping, layout, tables, forms, pagination.

Server-rendered, no JavaScript needed for any action. Every control has a visible label, tables use
header cells with scope, dialogs are `<dialog open>` reached by URL.
"""

from __future__ import annotations

from html import escape
from typing import Any, Iterable
from urllib.parse import urlencode

from opsdesk.workforce.i18n import fmt_date, t

NAV = (("rota", "/rota"), ("staff", "/staff"), ("absences", "/absences"), ("requests", "/requests"),
       ("messages", "/messages"), ("agency", "/agency"), ("publish", "/publish"),
       ("understaffing", "/reports/understaffing/new"), ("reports", "/reports"), ("documents", "/documents"),
       ("timeclock", "/timeclock"), ("payroll", "/payroll"), ("council", "/council"), ("audit", "/audit"))


def e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def page_number(q: dict[str, str]) -> int:
    """`?page=` as a positive number; anything else is page 1 (3.1.1: no server error on a bad link)."""
    raw = str(q.get("page") or "1")
    return int(raw) if raw.isdigit() and int(raw) > 0 else 1


def url(path: str, **params: Any) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "", False)}
    return path + ("?" + urlencode(clean) if clean else "")


# 3.1: the office apps next to the rota, one switcher in every header
APPS = (("rota", "/rota", "OW"), ("mail", "/mail", "OM"), ("chat", "/chat", "OC"), ("calendar", "/calendar", "OCa"))


def page(app, state, *, title: str, active: str, body: str, flash: dict[str, Any] | None = None,
         unread: int = 0, open_requests: int = 0, area: str = "rota", side: str | None = None,
         badges: dict[str, int] | None = None, apps: tuple[tuple[str, str, str], ...] = APPS) -> str:
    loc = app.locale
    lang = "de" if loc == "de-DE" else "en"
    items = []
    for key, href in NAV:
        if key == "council" and not state.works_council_required and state.jurisdiction.value != "de":
            continue
        label = t(loc, f"nav.{key}")
        badge = ""
        if key == "messages" and unread:
            badge = f' <span class="count" aria-label="{unread}">{unread}</span>'
        if key == "requests" and open_requests:
            badge = f' <span class="count" aria-label="{open_requests}">{open_requests}</span>'
        current = ' aria-current="page" class="active"' if key == active else ""
        items.append(f'<li><a href="{href}"{current}>{e(label)}{badge}</a></li>')
    flash_html = ""
    if flash:
        kind = "ok" if flash.get("ok") else "err"
        role = "status" if flash.get("ok") else "alert"
        lines = "".join(f"<li>{e(x)}</li>" for x in flash.get("details", []))
        flash_html = (f'<div class="flash {kind}" role="{role}"><strong>{e(flash.get("title", ""))}</strong>'
                      f'{"<ul>" + lines + "</ul>" if lines else ""}</div>')
    switch = []
    for key, href, _logo in apps:
        n = (badges or {}).get(key, 0)
        count = f' <span class="count" aria-label="{n}">{n}</span>' if n else ""
        current = ' aria-current="page" class="active"' if key == area else ""
        switch.append(f'<a href="{href}"{current}>{e(t(loc, "app." + key))}{count}</a>')
    logo = next(x[2] for x in apps if x[0] == area)
    bell_n = (badges or {}).get("bell", 0)
    bell = (f'<a class="bell" href="/notifications" aria-label="{e(t(loc, "notif.title"))}: {bell_n}">🔔'
            + (f'<span class="count">{bell_n}</span>' if bell_n else "") + "</a>")
    session = ""
    if (badges or {}).get("session"):
        session = (f'<div class="session-notice" role="status">{e(t(loc, "session.notice"))} '
                   f'<a class="btn small" href="/session/stay">{e(t(loc, "session.stay"))}</a></div>')
    brand = t(loc, "app.brand." + area)
    nav_html = side if side is not None else f'<ul>{"".join(items)}</ul>'
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} – {e(state.home_name)} – {e(brand)}</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body class="area-{area}">
<header class="top">
  <div class="brand"><span class="logo" aria-hidden="true">{logo}</span> {e(brand)}</div>
  <nav class="apps" aria-label="{e(t(loc, 'app.switch'))}">{''.join(switch)}</nav>
  <div class="home" title="{e(state.home_name)} · {e(app.home.address)}">{e(state.home_name)} <span class="muted">· {e(app.home.address)}</span></div>
  <div class="who">{e(t(loc, "today"))}: <strong>{e(fmt_date(loc, state.current_date))}</strong>
    · {e(t(loc, "signed_in"))} <strong>{e(app.home.user_name)}</strong> <span class="muted">({e(app.home.user_title)})</span></div>
  {bell}
</header>
{session}
<div class="frame">
<nav class="side" aria-label="{e(t(loc, 'nav.main'))}">{nav_html}</nav>
<main id="main">
<h1>{e(title)}</h1>
{flash_html}
{body}
</main>
</div>
</body>
</html>"""


def table(headers: Iterable[str], rows: Iterable[Iterable[str]], *, caption: str = "", cls: str = "grid") -> str:
    head = "".join(f'<th scope="col">{h}</th>' for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    cap = f"<caption>{e(caption)}</caption>" if caption else ""
    return f'<table class="{cls}">{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def kv_table(rows: Iterable[tuple[str, str]]) -> str:
    """Label/value pairs (record overviews): no header row."""
    body = "".join(f'<tr><th scope="row">{k}</th><td>{v}</td></tr>' for k, v in rows)
    return f'<table class="grid kv"><tbody>{body}</tbody></table>'


def paginate(items: list[Any], page_no: int, per_page: int = 25) -> tuple[list[Any], int, int]:
    pages = max(1, (len(items) + per_page - 1) // per_page)
    page_no = min(max(1, page_no), pages)
    return items[(page_no - 1) * per_page: page_no * per_page], page_no, pages


def pager(loc: str, path: str, page_no: int, pages: int, total: int, **params: Any) -> str:
    if not total:
        return ""  # the empty state says it
    links = []
    for p in range(1, pages + 1):
        if p == page_no:
            links.append(f'<span aria-current="page" class="current">{p}</span>')
        else:
            links.append(f'<a href="{e(url(path, page=p, **params))}">{p}</a>')
    return (f'<nav class="pager" aria-label="{e(t(loc, "page"))}">{e(t(loc, "page"))} {page_no} {e(t(loc, "of"))} '
            f'{pages} · {total} {e(t(loc, "results"))} · {" ".join(links)}</nav>')


def select(name: str, label: str, options: list[tuple[str, str]], value: str = "", *, ident: str | None = None,
           required: bool = False) -> str:
    ident = ident or f"f-{name}"
    opts = "".join(f'<option value="{e(v)}"{" selected" if str(v) == str(value) else ""}>{e(lbl)}</option>'
                   for v, lbl in options)
    req = " required" if required else ""
    return f'<label for="{ident}">{e(label)}</label><select id="{ident}" name="{e(name)}"{req}>{opts}</select>'


def text_input(name: str, label: str, value: str = "", *, kind: str = "text", ident: str | None = None,
               required: bool = False, extra: str = "") -> str:
    ident = ident or f"f-{name}"
    req = " required" if required else ""
    return (f'<label for="{ident}">{e(label)}</label><input id="{ident}" type="{kind}" name="{e(name)}" '
            f'value="{e(value)}"{req} {extra}>')


def textarea(name: str, label: str, value: str = "", *, ident: str | None = None, required: bool = False,
             rows: int = 3) -> str:
    ident = ident or f"f-{name}"
    req = " required" if required else ""
    return (f'<label for="{ident}">{e(label)}</label><textarea id="{ident}" name="{e(name)}" rows="{rows}"{req}>'
            f'{e(value)}</textarea>')


def checkbox(name: str, label: str, checked: bool = False, *, ident: str | None = None, value: str = "1") -> str:
    ident = ident or f"f-{name}"
    return (f'<span class="check"><input type="checkbox" id="{ident}" name="{e(name)}" value="{e(value)}"'
            f'{" checked" if checked else ""}><label for="{ident}">{e(label)}</label></span>')


def hidden(**fields: Any) -> str:
    return "".join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in fields.items() if v is not None)


def button(label: str, *, kind: str = "primary", name: str | None = None, value: str | None = None) -> str:
    nv = f' name="{e(name)}" value="{e(value)}"' if name else ""
    return f'<button type="submit" class="btn {kind}"{nv}>{e(label)}</button>'


def form(action: str, inner: str, *, method: str = "post", cls: str = "form") -> str:
    return f'<form method="{method}" action="{e(action)}" class="{cls}">{inner}</form>'


def dialog(ident: str, title: str, inner: str, close_href: str, loc: str) -> str:
    return (f'<div class="backdrop"></div><dialog open id="{ident}" aria-labelledby="{ident}-title" aria-modal="true">'
            f'<header><h2 id="{ident}-title">{e(title)}</h2><a class="btn ghost" href="{e(close_href)}">'
            f'{e(t(loc, "close"))}</a></header>{inner}</dialog>')


def badge(text: str, kind: str = "") -> str:
    return f'<span class="badge {kind}">{e(text)}</span>'


def markdown(text: str) -> str:
    """The small Markdown subset of the home's documents (headings, lists, tables, paragraphs), escaped first."""
    out, para, items, rows = [], [], [], []

    def flush() -> None:
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")  # line breaks are meant (email headers, addresses)
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()
        if rows:
            head, body = rows[0], [r for r in rows[1:] if not set("".join(r)) <= set("-: ")]
            out.append('<table class="grid"><thead><tr>' + "".join(f'<th scope="col">{c}</th>' for c in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table>")
            rows.clear()

    for raw in text.splitlines():
        line = e(raw.rstrip())
        if line.startswith("|"):
            if para or items:
                flush()
            rows.append([c.strip() for c in line.strip("|").split("|")])
            continue
        if rows:
            flush()
        if not line.strip():
            flush()
        elif line.startswith("### "):
            flush(); out.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            flush(); out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("- "):
            if para:
                flush()
            items.append(line[2:])
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return "".join(out)
