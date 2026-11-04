"""A minimal, deterministic PDF writer for mail attachments (text pages, Helvetica, A4).

No third-party code: the file is assembled by hand (PDF 1.4, WinAnsi text, no dates or ids in the file),
so the same attachment always gives the same bytes. Long lines are wrapped; pages break every 50 lines.
"""

from __future__ import annotations

import textwrap

WIDTH, HEIGHT, MARGIN, LEADING, SIZE, PER_PAGE, WRAP = 595, 842, 56, 14, 10, 50, 92


def _latin(ch: str) -> str:
    """A letter the PDF's WinAnsi font cannot show is written without its accent (ć → c, ı → i), never as "?"."""
    try:
        ch.encode("cp1252")
        return ch
    except UnicodeEncodeError:
        import unicodedata
        base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
        return base or {"ı": "i", "ł": "l", "Ł": "L", "đ": "d", "ø": "o"}.get(ch, "-")


def _escape(line: str) -> bytes:
    raw = "".join(_latin(ch) for ch in line).encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def render(text: str, title: str = "") -> bytes:
    lines: list[str] = []
    for raw in text.splitlines() or [""]:
        lines += textwrap.wrap(raw, WRAP) or [""]
    pages = [lines[i:i + PER_PAGE] for i in range(0, len(lines), PER_PAGE)] or [[]]
    objects: list[bytes] = []
    # 1 catalog, 2 pages, 3 font, then per page: page object + content stream
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, page_lines in enumerate(pages):
        content = [f"BT /F1 {SIZE} Tf {LEADING} TL {MARGIN} {HEIGHT - MARGIN} Td".encode()]
        for j, line in enumerate(page_lines):
            content.append((b"(" if j == 0 else b"T* (") + _escape(line) + b") Tj")
        content.append(b"ET")
        stream = b"\n".join(content)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {WIDTH} {HEIGHT}] /Resources << /Font << /F1 3 0 R >> >> "
                       f"/Contents {5 + 2 * i} 0 R >>".encode())
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    info = f" /Info << /Title ({_escape(title).decode('cp1252')}) >>" if title else ""
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n".encode("cp1252")
    return bytes(out)
