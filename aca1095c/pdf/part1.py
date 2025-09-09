import io
import re
import fitz  # PyMuPDF
from pdfrw import PdfReader, PdfWriter, PdfDict, PdfArray
# from .common import extract_pdf_widgets  # no longer required for Part I overlay


# --- Internal helpers ---------------------------------------------------------

IRS_PART1_FIELDS = [
    "f1_1[0]",  # First name
    "f1_2[0]",  # Middle initial
    "f1_3[0]",  # Last name
    "f1_4[0]",  # SSN
    "f1_5[0]",  # Street address (incl. apt no.)
    "f1_6[0]",  # City or town
    "f1_7[0]",  # State or province
    "f1_8[0]",  # Country & ZIP / Postal code (commonly ZIP)
]

def _format_ssn(s: str) -> str:
    ds = re.sub(r"\D", "", str(s or ""))
    return f"{ds[:3]}-{ds[3:5]}-{ds[5:9]}" if len(ds) >= 9 else (s or "")

def _split_name(full: str):
    """Return (first, mi, last). Best-effort split if 'name' is passed as a single string."""
    full = (full or "").strip()
    if not full:
        return "", "", ""
    parts = full.split()
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    # 3+ parts → assume middle initial is first char of middle token
    first = parts[0]
    last = parts[-1]
    mid = parts[1][0] if parts[1] else ""
    return first, mid, last

def _page1_widget_rects(reader: PdfReader):
    """Return dict: field_name -> (x0,y0,x1,y1) for page 1 widgets."""
    out = {}
    if not getattr(reader, "pages", None):
        return out
    p0 = reader.pages[0]
    annots = getattr(p0, "Annots", None)
    if not annots:
        return out
    # Annots can be an indirect array (PdfArray)
    arr = annots if isinstance(annots, PdfArray) else PdfArray(annots)
    for a in arr:
        try:
            if getattr(a, "Subtype", None) != "/Widget":
                continue
            name = getattr(a, "T", None)
            rect = getattr(a, "Rect", None)
            if not name or not rect or len(rect) != 4:
                continue
            nm = str(name)[1:-1]  # strip parentheses
            x0, y0, x1, y1 = [float(r) for r in rect]
            out[nm] = (x0, y0, x1, y1)
        except Exception:
            continue
    return out


# --- Overlay writer (now by actual field rectangles) --------------------------

def overlay_part1_by_anchors(pdf_bytes: bytes, values: dict) -> bytes:
    """
    Draw Part I (lines 1–6) values *inside the actual field rectangles* on page 1.
    Accepts either a combined 'name' or separate 'first', 'mi', 'last'.
    Accepted keys:
      - name OR (first, mi, last)
      - ssn (digits or formatted)
      - address
      - city
      - state / statezip / countryzip (we prefer 'state' + 'countryzip' if provided)
      - countryzip (ZIP or country+postal)
      - plan (ignored here by default; kept for compatibility)

    Returns new PDF bytes with overlay drawn; does not modify form fields.
    """
    # Normalize inputs
    first = (values.get("first") or "").strip()
    mi    = (values.get("mi") or "").strip()
    last  = (values.get("last") or "").strip()

    if not (first and last) and values.get("name"):
        first, mi, last = _split_name(values.get("name"))

    ssn  = _format_ssn(values.get("ssn", ""))
    addr = (values.get("address") or "").strip()
    city = (values.get("city") or "").strip()

    # prefer explicit 'state' and 'countryzip'; fall back to 'statezip'
    state = (values.get("state") or "").strip()
    czip  = (values.get("countryzip") or "").strip()
    if not (state or czip):
        sz = (values.get("statezip") or "").strip()
        # naive split: first token(s) → state, remainder → zip/country
        # (Keeps your existing behavior without asking questions)
        if "," in sz:
            state, czip = [t.strip() for t in sz.split(",", 1)]
        else:
            toks = sz.split()
            if toks:
                state = toks[0]
                czip = " ".join(toks[1:]) if len(toks) > 1 else ""

    # Open with fitz for drawing
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Use pdfrw to grab the exact widget rectangles on page 1
    r = PdfReader(io.BytesIO(pdf_bytes))
    rects = _page1_widget_rects(r)

    # Build {field_name: value} for Part I
    field_values = {
        "f1_1[0]": first,
        "f1_2[0]": mi,
        "f1_3[0]": last,
        "f1_4[0]": ssn,
        "f1_5[0]": addr,
        "f1_6[0]": city,
        "f1_7[0]": state,
        "f1_8[0]": czip,
    }

    page = doc[0]
    inset = 2.0
    fontsize = 10.5

    for fname in IRS_PART1_FIELDS:
        val = (field_values.get(fname) or "").strip()
        if not val:
            continue
        rect = rects.get(fname)
        if not rect:
            continue
        x0, y0, x1, y1 = rect
        box = fitz.Rect(x0 + inset, y0 + inset, x1 - inset, y1 - inset)
        page.insert_textbox(box, val, fontsize=fontsize, fontname="helv", align=0)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# --- Strict AcroForm fill (corrected mapping) ---------------------------------

def fill_part1_fields_strict(pdf_bytes: bytes, part1: dict) -> (bytes, bool):
    """
    Fill real AcroForm fields for Part I (lines 1–6) using pdfrw.
    Returns: (new_pdf_bytes, filled_any: bool)

    part1 may include:
      - name OR (first, mi, last)
      - ssn, address, city, state, countryzip
      - (plan is ignored here; Part I = lines 1–6)
    """
    # Normalize inputs (support both old + new keys)
    first = (part1.get("first") or "").strip()
    mi    = (part1.get("mi") or "").strip()
    last  = (part1.get("last") or "").strip()

    if not (first and last) and part1.get("name"):
        first, mi, last = _split_name(part1.get("name"))

    ssn  = _format_ssn(part1.get("ssn", ""))
    addr = (part1.get("address") or "").strip()
    city = (part1.get("city") or "").strip()
    state = (part1.get("state") or "").strip()
    czip  = (part1.get("countryzip") or part1.get("statezip") or "").strip()

    # Read PDF
    r = PdfReader(io.BytesIO(pdf_bytes))
    filled = 0

    # Map exact 2024 IRS field names
    field_map = {
        "f1_1[0]": first,
        "f1_2[0]": mi,
        "f1_3[0]": last,
        "f1_4[0]": ssn,
        "f1_5[0]": addr,
        "f1_6[0]": city,
        "f1_7[0]": state,
        "f1_8[0]": czip,
    }

    for page in getattr(r, "pages", []):
        annots = getattr(page, "Annots", None)
        if not annots:
            continue
        for a in annots:
            if getattr(a, "Subtype", None) != "/Widget" or not getattr(a, "T", None):
                continue
            key = str(a.T)[1:-1]  # strip parentheses
            if key in field_map:
                val = (field_map[key] or "").strip()
                if val:
                    a.V = val
                    a.AP = None
                    filled += 1

    # Set NeedAppearances for consistent rendering
    if not getattr(r.Root, "AcroForm", None):
        r.Root.AcroForm = PdfDict()
    r.Root.AcroForm.update(PdfDict(NeedAppearances=True))

    out = io.BytesIO()
    PdfWriter().write(out, r)
    return out.getvalue(), filled > 0
