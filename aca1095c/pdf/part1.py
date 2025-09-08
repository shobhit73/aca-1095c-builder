import io
import re
import fitz  # PyMuPDF
from pdfrw import PdfReader, PdfWriter, PdfDict
from .common import extract_pdf_widgets


def overlay_part1_by_anchors(pdf_bytes: bytes, values: dict) -> bytes:
    """
    Draw Part I text near labels in left column.
    keys: name, ssn, address, city, statezip, plan
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    W = page.rect.width
    mid_x = W / 2.0

    widgets = [w for w in extract_pdf_widgets(pdf_bytes) if w.get("page") == 0]

    def yc_of(w): 
        return (w["y1"] + w["y2"]) / 2.0

    def label_cy(r): 
        return (r.y0 + r.y1) / 2.0

    def same_row_employer_band(r, tol=12):
        cy = label_cy(r)
        cands = [w for w in widgets if abs(yc_of(w) - cy) <= tol and w["xc"] > mid_x + 6]
        if not cands:
            return None
        cands.sort(key=lambda w: w["x1"])
        return cands[0]

    def left_cell_box(label_rect, emp_widget):
        x1 = max(label_rect.x1 + 6, 36)
        x2 = mid_x - 12
        if emp_widget:
            y1, y2 = emp_widget["y1"], emp_widget["y2"]
        else:
            y1 = label_rect.y0 + 10
            y2 = label_rect.y0 + 22
        return fitz.Rect(x1, y1, x2, y2)

    LABELS = [
        (["1 Name of employee", "Name of employee"], "name"),
        (["2 Social security number", "Social security number", "SSN"], "ssn"),
        (["3 Street address", "Street address"], "address"),
        (["4 City or town", "City or town"], "city"),
        (["5 State or province", "Country and ZIP", "postal code"], "statezip"),
    ]

    def find_first(terms):
        for t in terms:
            hits = page.search_for(t)
            if hits:
                return hits[0]
        return None

    for terms, key in LABELS:
        val = (values.get(key) or "").strip()
        if not val:
            continue
        r = find_first(terms)
        if not r:
            continue
        emp_band = same_row_employer_band(r)
        box = left_cell_box(r, emp_band)
        page.insert_textbox(box, val, fontsize=9, fontname="helv", align=0)

    r_ps = find_first(["Plan Start Month", "Plan Start"])
    psv = (values.get("plan") or "").strip()
    if r_ps and psv:
        emp_ps = None
        cy = label_cy(r_ps)
        cands = [w for w in widgets if abs(yc_of(w) - cy) <= 12 and w["x1"] > (r_ps.x1 + 4) and (w["x2"] - w["x1"]) < 60]
        if cands:
            cands.sort(key=lambda w: w["x1"])
            emp_ps = cands[0]
        if emp_ps:
            ps_box = fitz.Rect(emp_ps["x1"] + 2, emp_ps["y1"] - 1, emp_ps["x2"] - 2, emp_ps["y2"] + 1)
        else:
            ps_box = fitz.Rect(r_ps.x1 + 6, r_ps.y0 + 10, r_ps.x1 + 45, r_ps.y0 + 22)
        page.insert_textbox(ps_box, psv, fontsize=9, fontname="helv", align=0)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _format_ssn(s: str) -> str:
    ds = re.sub(r"\D", "", str(s or ""))
    return f"{ds[:3]}-{ds[3:5]}-{ds[5:9]}" if len(ds) == 9 else (s or "")


def fill_part1_fields_strict(pdf_bytes: bytes, part1: dict) -> (bytes, bool):
    """
    Try to fill real AcroForm fields for Part I using pdfrw.
    Returns (new_pdf_bytes, filled_any: bool)
      part1 keys: name, ssn, address, city, state, countryzip, plan
    """
    CANDS = {
        "name":      ["f1_1[0]", "employeename[0]", "employee_name", "name", "employee name"],
        "ssn":       ["f1_2[0]", "ssn", "employee_ssn", "employeesocialsecuritynumber[0]"],
        "address":   ["f1_3[0]", "address[0]", "streetaddress[0]", "address1", "street"],
        "city":      ["f1_4[0]", "city[0]", "cityortown", "city_or_town"],
        "state":     ["f1_5[0]", "state[0]", "province", "state_or_province"],
        "countryzip":["f1_6[0]", "zip[0]", "postalcode", "country_zip", "countryandzip", "country and zip"],
        "plan":      ["f1_7[0]", "planstartmonth[0]", "plan start month"],
    }
    lower_cands = {k: [v.lower() for v in vals] for k, vals in CANDS.items()}

    r = PdfReader(io.BytesIO(pdf_bytes))
    filled = 0

    for page in getattr(r, "pages", []):
        annots = getattr(page, "Annots", None)
        if not annots:
            continue
        for a in annots:
            if getattr(a, "Subtype", None) != "/Widget" or not getattr(a, "T", None):
                continue
            key = str(a.T)[1:-1]
            k = key.lower()

            def matches(cands):
                return any(k == c or k.endswith(c) for c in cands)

            target = None
            for logical, cands in lower_cands.items():
                if matches(cands):
                    target = logical
                    break

            if not target:
                continue

            val = (part1.get(target) or "").strip()
            if not val:
                continue
            if target == "ssn":
                val = _format_ssn(val)

            a.V = val
            a.AP = None
            filled += 1

    if not getattr(r.Root, "AcroForm", None):
        r.Root.AcroForm = PdfDict()
    r.Root.AcroForm.update(PdfDict(NeedAppearances=True))

    out = io.BytesIO()
    PdfWriter().write(out, r)
    return out.getvalue(), filled > 0
