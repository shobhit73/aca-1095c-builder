import io
from typing import Dict, List

import numpy as np
from pdfrw import PdfReader, PdfWriter, PageMerge
from reportlab.pdfgen import canvas
import streamlit as st


# -------- AcroForm field listing (cached) --------
@st.cache_data(show_spinner=False)
def extract_pdf_fields(pdf_bytes: bytes) -> List[str]:
    try:
        pdf = PdfReader(io.BytesIO(pdf_bytes))
        names: List[str] = []
        for page in pdf.pages:
            annots = getattr(page, "Annots", None)
            if not annots:
                continue
            for a in annots:
                if a.Subtype == "/Widget" and a.T:
                    names.append(str(a.T)[1:-1])
        # de-dupe preserving order
        return list(dict.fromkeys(names))
    except Exception:
        return []


# -------- Fill fields & optional flatten --------
def fill_pdf_fields(pdf_bytes: bytes, values: Dict[str, str], flatten: bool = False) -> bytes:
    pdf = PdfReader(io.BytesIO(pdf_bytes))

    # Fill fields
    for page in pdf.pages:
        annots = getattr(page, "Annots", None)
        if not annots:
            continue
        for a in annots:
            if a.Subtype == "/Widget" and a.T:
                key = str(a.T)[1:-1]
                if key in values:
                    a.V = values[key]
                    a.AP = None

    out_io = io.BytesIO()
    PdfWriter().write(out_io, pdf)
    out_bytes = out_io.getvalue()
    if not flatten:
        return out_bytes

    # Flatten: draw appearances
    pdf2 = PdfReader(io.BytesIO(out_bytes))
    page_draws: Dict[int, List[tuple]] = {}
    for p_idx, page in enumerate(pdf2.pages):
        annots = getattr(page, "Annots", None)
        if not annots:
            continue
        for a in annots:
            if a.Subtype == "/Widget" and a.T and getattr(a, "Rect", None):
                key = str(a.T)[1:-1]
                if key in values:
                    x1, y1, x2, y2 = [float(v) for v in a.Rect]
                    page_draws.setdefault(p_idx, []).append((x1, y1, x2, y2, values[key]))

    writer = PdfWriter()
    for p_idx, page in enumerate(pdf2.pages):
        if p_idx in page_draws and page_draws[p_idx]:
            media = page.MediaBox
            w, h = float(media[2]), float(media[3])
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(w, h))
            c.setFont("Helvetica", 9)
            for (x1, y1, x2, y2, text) in page_draws[p_idx]:
                c.drawString(x1 + 2, (y1 + y2) / 2 - 3, str(text))
            c.save()
            overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
            PageMerge(page).add(overlay).render()
        writer.addpage(page)

    bout = io.BytesIO()
    writer.write(bout)
    return bout.getvalue()


# -------- Widget geometry & auto-mapping (Part II) --------
def extract_pdf_widgets(pdf_bytes: bytes):
    try:
        pdf = PdfReader(io.BytesIO(pdf_bytes))
        widgets = []
        for p_idx, page in enumerate(pdf.pages):
            annots = getattr(page, "Annots", None)
            if not annots:
                continue
            for a in annots:
                if a.Subtype == "/Widget" and a.T and getattr(a, "Rect", None):
                    x1, y1, x2, y2 = [float(v) for v in a.Rect]
                    widgets.append(
                        {
                            "page": p_idx,
                            "name": str(a.T)[1:-1],
                            "x1": x1,
                            "y1": y1,
                            "x2": x2,
                            "y2": y2,
                            "xc": (x1 + x2) / 2.0,
                            "yc": (y1 + y2) / 2.0,
                        }
                    )
        return widgets
    except Exception:
        return []


def _cluster_rows(widgets, y_tol: float = 8.0):
    rows: List[List[dict]] = []
    for w in sorted(widgets, key=lambda z: -z["yc"]):
        for row in rows:
            if abs(row[0]["yc"] - w["yc"]) <= y_tol:
                row.append(w)
                break
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda z: z["xc"])
    return rows


def auto_map_fields(pdf_bytes: bytes):
    """Return ([12 names for Line14], [12 for Line16]) or ([], [])."""
    widgets = extract_pdf_widgets(pdf_bytes)
    if not widgets:
        return [], []
    first = [w for w in widgets if w["page"] == 0]
    rows = _cluster_rows(first, y_tol=10.0)
    cand = [r for r in rows if len(r) >= 12]
    cand.sort(key=lambda r: -np.mean([w["yc"] for w in r]))
    if len(cand) < 3:
        return [], []

    def pick(row):
        names = [w["name"] for w in row]
        return names[1:13] if len(names) >= 13 else names[:12]

    l14 = pick(cand[0])
    l16 = pick(cand[2])
    if len(l14) == 12 and len(l16) == 12:
        return l14, l16
    return [], []
