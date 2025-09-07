# app.py — ACA 1095-C PDF Generator (Two-step: Part II first, then optional Part I)
#
# Requirements (root requirements.txt):
#   streamlit>=1.36
#   pandas>=2.0
#   numpy>=1.23
#   python-dateutil>=2.8
#   openpyxl>=3.1
#   pdfrw>=0.4
#   reportlab>=4.0
#   pymupdf>=1.24
#
# Run: streamlit run app.py

import io
import re
import calendar
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from dateutil.parser import parse as dtparse  # noqa: F401
from pdfrw import PdfReader, PdfWriter, PageMerge, PdfDict
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter  # noqa: F401
import fitz  # PyMuPDF — used for robust Part I overlay by labels/widgets

st.set_page_config(page_title="ACA 1095-C Builder (Two-Step)", layout="wide")

# ---------------------- Cleaning & Parsing ----------------------

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df

BOOL_TRUE = {"yes", "y", "true", "1", "t"}

def to_bool(val) -> bool:
    if pd.isna(val):
        return False
    return str(val).strip().lower() in BOOL_TRUE


def parse_date_safe(d, default_end=False):
    """Parse dates; clamp invalid days to month end if needed."""
    if pd.isna(d) or d == "":
        return None
    try:
        return pd.to_datetime(d)
    except Exception:
        try:
            s = str(d).replace("/", "-")
            if len(s) == 7 and "-" in s:  # YYYY-MM
                y, m = map(int, s.split("-"))
                last = calendar.monthrange(y, m)[1]
                return pd.to_datetime(f"{y}-{m:02d}-{last if default_end else 1}")
            y, m, day = map(int, s.split("-"))  # YYYY-MM-DD (maybe invalid)
            last_day = calendar.monthrange(y, m)[1]
            day = min(day, last_day)
            if default_end:
                day = last_day
            return pd.to_datetime(f"{y}-{m:02d}-{day:02d}")
        except Exception:
            return None


def month_bounds(year: int, month: int) -> Tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(year=year, month=month, day=1)
    end = pd.Timestamp(year=year, month=month, day=calendar.monthrange(year, month)[1])
    return start, end


def ranges_overlap(a_start, a_end, b_start, b_end) -> bool:
    return (a_start is not None and a_end is not None and a_start <= b_end and a_end >= b_start)


def covers_whole_month(r_start, r_end, m_start, m_end) -> bool:
    return (r_start is not None and r_end is not None and r_start <= m_start and r_end >= m_end)

# ---------------------- Core ACA Logic ----------------------

def load_excel(file_bytes: bytes) -> Dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    data = {}
    for sheet in xls.sheet_names:
        key = sheet.strip().lower()
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet)
        data[key] = normalize_columns(df)
    return data


def prepare_inputs(data: Dict[str, pd.DataFrame]):
    emp_demo = data.get('emp demographic', pd.DataFrame())
    if not emp_demo.empty:
        cols = [c for c in emp_demo.columns if c in [
            'employeeid','firstname','lastname','ssn','addressline1','city','state','zipcode','country',
            'employername','ein','employeraddress','contacttelephone','employercity','employerstate','employercountry','employerzip']]
        emp_demo = emp_demo[cols]

    emp_status = data.get('emp status', pd.DataFrame())
    emp_elig   = data.get('emp eligibility', pd.DataFrame())
    emp_enroll = data.get('emp enrollment', pd.DataFrame())
    dep_enroll = data.get('dep enrollment', pd.DataFrame())
    pay_ded    = data.get('pay deductions', pd.DataFrame())

    # EmployeeID to str
    for df in [emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, pay_ded]:
        if not df.empty and 'employeeid' in df.columns:
            df['employeeid'] = df['employeeid'].astype(str)

    # Status
    if not emp_status.empty:
        for c in ['statusstartdate','statusenddate']:
            if c in emp_status.columns:
                emp_status[c] = emp_status[c].apply(lambda x: parse_date_safe(x, default_end=(c=='statusenddate')))
        if 'employmentstatus' in emp_status.columns:
            emp_status['employmentstatus'] = emp_status['employmentstatus'].astype(str).str.strip().str.upper()
        if 'role' in emp_status.columns:
            emp_status['role'] = emp_status['role'].astype(str).str.strip().str.upper()

    # Eligibility
    if not emp_elig.empty:
        for c in ['eligibilitystartdate','eligibilityenddate']:
            if c in emp_elig.columns:
                emp_elig[c] = emp_elig[c].apply(lambda x: parse_date_safe(x, default_end=(c=='eligibilityenddate')))
        if 'iseligibleforcoverage' in emp_elig.columns:
            emp_elig['iseligibleforcoverage'] = emp_elig['iseligibleforcoverage'].apply(to_bool)
        mv_col = 'minimumvaluecoverage' if 'minimumvaluecoverage' in emp_elig.columns else ('mimimumvaluecoverage' if 'mimimumvaluecoverage' in emp_elig.columns else None)
        emp_elig['eligible_mv'] = emp_elig[mv_col].apply(to_bool) if mv_col else False

    # Enrollment
    if not emp_enroll.empty:
        for c in ['enrollmentstartdate','enrollmentenddate']:
            if c in emp_enroll.columns:
                emp_enroll[c] = emp_enroll[c].apply(lambda x: parse_date_safe(x, default_end=(c=='enrollmentenddate')))
        if 'isenrolled' in emp_enroll.columns:
            emp_enroll['isenrolled'] = emp_enroll['isenrolled'].apply(to_bool)

    # Dependents
    if not dep_enroll.empty:
        for c in ['eligiblestartdate','eligibleenddate']:
            if c in dep_enroll.columns:
                dep_enroll[c] = dep_enroll[c].apply(lambda x: parse_date_safe(x, default_end=(c=='eligibleenddate')))
        for c in ['eligible','enrolled']:
            if c in dep_enroll.columns:
                dep_enroll[c] = dep_enroll[c].apply(to_bool)
        if 'dependentrelationship' in dep_enroll.columns:
            dep_enroll['dependentrelationship'] = dep_enroll['dependentrelationship'].astype(str).str.strip().str.capitalize()

    return emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, pay_ded


def choose_report_year(emp_elig: pd.DataFrame) -> int:
    if emp_elig.empty or emp_elig[['eligibilitystartdate','eligibilityenddate']].dropna().empty:
        return datetime.now().year
    yrs = []
    for _, r in emp_elig.dropna(subset=['eligibilitystartdate','eligibilityenddate']).iterrows():
        yrs += list(range(r['eligibilitystartdate'].year, r['eligibilityenddate'].year + 1))
    # pick most frequent, tie -> latest
    best, best_count = None, -1
    for y in sorted(set(yrs)):
        c = yrs.count(y)
        if c > best_count or (c == best_count and y > (best or 0)):
            best, best_count = y, c
    return best or datetime.now().year


def build_interim(emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, year: int) -> pd.DataFrame:
    # Base grid
    employee_ids = set()
    for df in [emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll]:
        if not df.empty and 'employeeid' in df.columns:
            employee_ids.update(df['employeeid'].unique().tolist())
    employee_ids = sorted(list(employee_ids))

    months = list(range(1, 13))
    rows = []
    for eid in employee_ids:
        for m in months:
            ms, me = month_bounds(year, m)
            rows.append({'employeeid': str(eid), 'year': year, 'monthnum': m, 'month': ms.strftime('%b'),
                         'monthstart': ms, 'monthend': me})
    interim = pd.DataFrame(rows)

    # Names
    if not emp_demo.empty:
        name_cols = [c for c in ['employeeid','firstname','lastname'] if c in emp_demo.columns]
        if name_cols:
            interim = interim.merge(emp_demo[name_cols].drop_duplicates('employeeid'), on='employeeid', how='left')

    # Employment flags
    def employed_in_month(eid, mstart, mend):
        if emp_status.empty: return False
        sub = emp_status[emp_status['employeeid'] == eid]
        for _, r in sub.iterrows():
            st_ok = str(r.get('employmentstatus','')).upper() in ["FT","FULL-TIME","FULL TIME","PT","PART-TIME","PART TIME","ACTIVE"]
            if st_ok and ranges_overlap(r.get('statusstartdate'), r.get('statusenddate'), mstart, mend):
                return True
        return False

    def ft_in_month(eid, mstart, mend):
        if emp_status.empty: return False
        sub = emp_status[emp_status['employeeid'] == eid]
        for _, r in sub.iterrows():
            if str(r.get('role','')).upper() == 'FT' and ranges_overlap(r.get('statusstartdate'), r.get('statusenddate'), mstart, mend):
                return True
        return False

    interim['employed'] = interim.apply(lambda r: employed_in_month(r['employeeid'], r['monthstart'], r['monthend']), axis=1)
    interim['ft'] = interim.apply(lambda r: ft_in_month(r['employeeid'], r['monthstart'], r['monthend']), axis=1)

    # Eligibility
    def eligible_any(eid, mstart, mend):
        if emp_elig.empty: return False
        sub = emp_elig[emp_elig['employeeid'] == eid]
        for _, r in sub.iterrows():
            if to_bool(r.get('iseligibleforcoverage', False)) and ranges_overlap(r.get('eligibilitystartdate'), r.get('eligibilityenddate'), mstart, mend):
                return True
        return False

    def eligible_full(eid, mstart, mend):
        if emp_elig.empty: return False
        sub = emp_elig[emp_elig['employeeid'] == eid]
        for _, r in sub.iterrows():
            if to_bool(r.get('iseligibleforcoverage', False)) and covers_whole_month(r.get('eligibilitystartdate'), r.get('eligibilityenddate'), mstart, mend):
                return True
        return False

    def eligible_mv_any(eid, mstart, mend):
        if emp_elig.empty: return False
        sub = emp_elig[emp_elig['employeeid'] == eid]
        for _, r in sub.iterrows():
            if to_bool(r.get('eligible_mv', False)) and ranges_overlap(r.get('eligibilitystartdate'), r.get('eligibilityenddate'), mstart, mend):
                return True
        return False

    interim['eligibleforcoverage'] = interim.apply(lambda r: eligible_any(r['employeeid'], r['monthstart'], r['monthend']), axis=1)
    interim['eligible_allmonth']  = interim.apply(lambda r: eligible_full(r['employeeid'], r['monthstart'], r['monthend']), axis=1)
    interim['eligible_mv']        = interim.apply(lambda r: eligible_mv_any(r['employeeid'], r['monthstart'], r['monthend']), axis=1)
    interim['offer_ee_allmonth']  = interim['eligible_allmonth']

    # Enrollment
    def enrolled_allmonth(eid, mstart, mend):
        if emp_enroll.empty: return False
        sub = emp_enroll[emp_enroll['employeeid'] == eid]
        for _, r in sub.iterrows():
            if to_bool(r.get('isenrolled', False)) and covers_whole_month(r.get('enrollmentstartdate'), r.get('enrollmentenddate'), mstart, mend):
                return True
        return False
    interim['enrolled_allmonth'] = interim.apply(lambda r: enrolled_allmonth(r['employeeid'], r['monthstart'], r['monthend']), axis=1)

    # Dependents' offer flags
    def offer_dep(eid, mstart, mend, dep_type: str):
        if dep_enroll.empty or 'dependentrelationship' not in dep_enroll.columns:
            return False
        sub = dep_enroll[(dep_enroll['employeeid'] == eid) & (dep_enroll['dependentrelationship'] == dep_type)]
        for _, r in sub.iterrows():
            if to_bool(r.get('eligible', False)) and ranges_overlap(r.get('eligiblestartdate'), r.get('eligibleenddate'), mstart, mend):
                return True
        return False

    interim['offer_spouse']     = interim.apply(lambda r: offer_dep(r['employeeid'], r['monthstart'], r['monthend'], 'Spouse'), axis=1)
    interim['offer_dependents'] = interim.apply(lambda r: offer_dep(r['employeeid'], r['monthstart'], r['monthend'], 'Child'), axis=1)

    # Waiting period & not FT all year
    interim['waitingperiod_month'] = interim['employed'] & interim['ft'] & (~interim['eligibleforcoverage'])
    notft = interim.groupby('employeeid')['ft'].sum() == 0
    interim['notft_allyear'] = interim['employeeid'].map(notft)

    # Line 14 mapping
    def map_line14(row):
        if row['offer_ee_allmonth'] and row['eligible_mv']:
            if row['offer_spouse'] and row['offer_dependents']:
                return '1E'
            if (not row['offer_spouse']) and (not row['offer_dependents']):
                return '1B'
            if (not row['offer_spouse']) and row['offer_dependents']:
                return '1C'
            if row['offer_spouse'] and (not row['offer_dependents']):
                return '1D'
        if row['offer_ee_allmonth'] and (not row['eligible_mv']):
            return '1F'
        return '1H'

    interim['line14_final'] = interim.apply(map_line14, axis=1)

    # Line 16 mapping priority
    def map_line16(row):
        if row['enrolled_allmonth']: return '2C'
        if row['waitingperiod_month']: return '2D'
        if not row['employed']: return '2A'
        if row['employed'] and not row['ft']: return '2B'
        return ''

    interim['line16_final'] = interim.apply(map_line16, axis=1)

    cols = ['employeeid','firstname','lastname','year','monthnum','month','monthstart','monthend',
            'employed','ft','eligibleforcoverage','eligible_allmonth','eligible_mv','offer_ee_allmonth',
            'enrolled_allmonth','offer_spouse','offer_dependents','waitingperiod_month','notft_allyear',
            'line14_final','line16_final']
    interim = interim[[c for c in cols if c in interim.columns]]
    return interim


def build_final(interim: pd.DataFrame) -> pd.DataFrame:
    final = interim[['employeeid','month','line14_final','line16_final']].copy()
    cat = pd.Categorical(final['month'], categories=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], ordered=True)
    final['month'] = cat
    final = final.sort_values(['employeeid','month'])
    final['month'] = final['month'].astype(str)
    return final

# ---------------------- PDF Utilities ----------------------

@st.cache_data(show_spinner=False)
def extract_pdf_fields(pdf_bytes: bytes) -> List[str]:
    try:
        pdf = PdfReader(io.BytesIO(pdf_bytes))
        names = []
        for page in pdf.pages:
            annots = getattr(page, 'Annots', None)
            if not annots:
                continue
            for a in annots:
                if a.Subtype == '/Widget' and a.T:
                    names.append(str(a.T)[1:-1])
        return list(dict.fromkeys(names))
    except Exception:
        return []


def fill_pdf_fields(pdf_bytes: bytes, values: Dict[str, str], flatten: bool=False) -> bytes:
    pdf = PdfReader(io.BytesIO(pdf_bytes))

    # Fill fields
    for page in pdf.pages:
        annots = getattr(page, 'Annots', None)
        if not annots: continue
        for a in annots:
            if a.Subtype == '/Widget' and a.T:
                key = str(a.T)[1:-1]
                if key in values:
                    a.V = values[key]
                    a.AP = None

    out_io = io.BytesIO(); PdfWriter().write(out_io, pdf)
    out_bytes = out_io.getvalue()
    if not flatten:
        return out_bytes

    # Flatten: draw appearances
    pdf2 = PdfReader(io.BytesIO(out_bytes))
    page_draws = {}
    for p_idx, page in enumerate(pdf2.pages):
        annots = getattr(page, 'Annots', None)
        if not annots: continue
        for a in annots:
            if a.Subtype == '/Widget' and a.T and getattr(a, 'Rect', None):
                key = str(a.T)[1:-1]
                if key in values:
                    x1, y1, x2, y2 = [float(v) for v in a.Rect]
                    page_draws.setdefault(p_idx, []).append((x1, y1, x2, y2, values[key]))

    writer = PdfWriter()
    for p_idx, page in enumerate(pdf2.pages):
        if p_idx in page_draws and page_draws[p_idx]:
            media = page.MediaBox
            w, h = float(media[2]), float(media[3])
            buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=(w, h))
            c.setFont("Helvetica", 9)
            for (x1, y1, x2, y2, text) in page_draws[p_idx]:
                c.drawString(x1 + 2, (y1 + y2)/2 - 3, str(text))
            c.save()
            overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
            PageMerge(page).add(overlay).render()
        writer.addpage(page)
    bout = io.BytesIO(); writer.write(bout)
    return bout.getvalue()

# ---- Widget geometry for auto-mapping (Part II) -----------------

def extract_pdf_widgets(pdf_bytes: bytes):
    try:
        pdf = PdfReader(io.BytesIO(pdf_bytes))
        widgets = []
        for p_idx, page in enumerate(pdf.pages):
            annots = getattr(page, 'Annots', None)
            if not annots: continue
            for a in annots:
                if a.Subtype == '/Widget' and a.T and getattr(a, 'Rect', None):
                    x1, y1, x2, y2 = [float(v) for v in a.Rect]
                    widgets.append({'page': p_idx, 'name': str(a.T)[1:-1],
                                    'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                                    'xc': (x1+x2)/2.0, 'yc': (y1+y2)/2.0})
        return widgets
    except Exception:
        return []


def _cluster_rows(widgets, y_tol: float = 8.0):
    rows = []
    for w in sorted(widgets, key=lambda z: -z['yc']):
        for row in rows:
            if abs(row[0]['yc'] - w['yc']) <= y_tol:
                row.append(w); break
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda z: z['xc'])
    return rows


def auto_map_fields(pdf_bytes: bytes):
    """Return ([12 names for Line14], [12 for Line16]) or ([], [])."""
    widgets = extract_pdf_widgets(pdf_bytes)
    if not widgets: return [], []
    first = [w for w in widgets if w['page'] == 0]
    rows = _cluster_rows(first, y_tol=10.0)
    cand = [r for r in rows if len(r) >= 12]
    cand.sort(key=lambda r: -np.mean([w['yc'] for w in r]))
    if len(cand) < 3: return [], []

    def pick(row):
        names = [w['name'] for w in row]
        return names[1:13] if len(names) >= 13 else names[:12]

    l14 = pick(cand[0]); l16 = pick(cand[2])
    if len(l14) == 12 and len(l16) == 12:
        return l14, l16
    return [], []

# ---- Part I Anchor Overlay (fallback) --------

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

    def yc_of(w): return (w["y1"] + w["y2"]) / 2.0
    def label_cy(r): return (r.y0 + r.y1) / 2.0

    def same_row_employer_band(r, tol=12):
        cy = label_cy(r)
        cands = [w for w in widgets if abs(yc_of(w) - cy) <= tol and w["xc"] > mid_x + 6]
        if not cands: return None
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
        if not val: continue
        r = find_first(terms)
        if not r: continue
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

# ---- Part I STRICT field fill (preferred) --------------

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
        "plan":      ["f1_7[0]", "planstartmonth[0]", "plan start month"]
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
            if not val: continue
            if target == "ssn": val = _format_ssn(val)

            a.V = val
            a.AP = None
            filled += 1

    # Ensure NeedAppearances (use PdfDict!)
    if not getattr(r.Root, "AcroForm", None):
        r.Root.AcroForm = PdfDict()
    r.Root.AcroForm.update(PdfDict(NeedAppearances=True))

    out = io.BytesIO()
    PdfWriter().write(out, r)
    return out.getvalue(), filled > 0

# ---------------------- App ----------------------

# Keep state between the two steps
if "part2_pdf_bytes" not in st.session_state:
    st.session_state.part2_pdf_bytes = None
if "part2_fields" not in st.session_state:
    st.session_state.part2_fields = None

st.title("ACA 1095-C Builder — Two-Step")
st.caption("Step 1: Generate Part II only. Step 2 (optional): Add Part I on top of the generated Part II PDF.")

with st.sidebar:
    st.header("1) Upload Inputs")
    excel_file = st.file_uploader("Excel ACA workbook", type=["xlsx","xlsm","xls"], accept_multiple_files=False)
    template_pdf = st.file_uploader("Fillable 1095-C PDF (template)", type=["pdf"], accept_multiple_files=False)
    st.caption("Tip: official IRS fillable PDFs work best.")

    st.header("2) Options")
    opt_flatten = st.checkbox("Also output a flattened copy (printed text)", value=True)
    opt_line15_from_pay = st.checkbox("Populate Line 15 from Pay Deductions (if present)", value=True)
    opt_overlay_part1 = st.checkbox(
        "Use overlay for Part I when strict field-fill is unavailable", value=True
    )

if excel_file is None or template_pdf is None:
    st.info("Upload your Excel workbook and a fillable PDF template to begin.")
    st.stop()

with st.spinner("Reading and preparing inputs…"):
    data = load_excel(excel_file.read())
    emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, pay_ded = prepare_inputs(data)
    report_year = choose_report_year(emp_elig)

st.success(f"Report Year selected: {report_year}")

with st.spinner("Building Interim grid and Final codes…"):
    interim = build_interim(emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, report_year)
    final   = build_final(interim)

st.subheader("Interim (first 100 rows)")
st.dataframe(interim.head(100), use_container_width=True)

st.subheader("Final Line 14/16 (first 100 rows)")
st.dataframe(final.head(100), use_container_width=True)

# Employee picker
emp_options = (
    interim[['employeeid','firstname','lastname']]
    .drop_duplicates('employeeid')
    .assign(label=lambda d: d.apply(lambda r: f"{r['employeeid']} — {str(r.get('firstname') or '')} {str(r.get('lastname') or '')}".strip(), axis=1))
)
sel_label = st.selectbox("Choose an employee", emp_options['label'].tolist())
sel_id = sel_label.split(' — ')[0]

# Compose Part I strings for later (Step 2)
emp_row = emp_demo[emp_demo['employeeid'] == sel_id]
full_name = emp_ssn = addr = city = state_only = state_zip = ""
if not emp_row.empty:
    fn = str(emp_row.iloc[0].get('firstname') or '').strip()
    ln = str(emp_row.iloc[0].get('lastname') or '').strip()
    full_name = (fn + (' ' if fn and ln else '') + ln).strip()
    emp_ssn   = str(emp_row.iloc[0].get('ssn') or '')
    addr      = str(emp_row.iloc[0].get('addressline1') or '')
    city      = str(emp_row.iloc[0].get('city') or '')
    stt       = str(emp_row.iloc[0].get('state') or '')
    zc        = str(emp_row.iloc[0].get('zipcode') or '')
    state_only = stt
    state_zip = ' '.join([p for p in [stt, zc] if p])

# Prepare Part II maps for the chosen employee
months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
final_row = final[final['employeeid']==sel_id].set_index('month')
line14_map = {m: (final_row.loc[m, 'line14_final'] if m in final_row.index else '') for m in months}
line16_map = {m: (final_row.loc[m, 'line16_final'] if m in final_row.index else '') for m in months}

# Optional Line 15 from Pay Deductions
line15 = {m: '' for m in months}
if opt_line15_from_pay and not pay_ded.empty and 'employeeid' in pay_ded.columns:
    row = pay_ded[pay_ded['employeeid']==sel_id]
    if not row.empty:
        for m in months:
            col = m[:3].capitalize()
            if col in row.columns:
                val = row.iloc[0][col]
                try:
                    amt = float(str(val).replace('$','').replace(',',''))
                    line15[m] = f"{amt:.2f}"
                except Exception:
                    pass

st.divider()

# =========================
# STEP 1 — Generate Part II only
# =========================
st.header("Step 1 — Generate Part II only")

template_pdf_bytes = template_pdf.read()
all_fields = extract_pdf_fields(template_pdf_bytes)
with st.expander("Detected PDF fields (raw)"):
    st.write(all_fields)

auto_l14, auto_l16 = auto_map_fields(template_pdf_bytes) if all_fields else ([], [])
if auto_l14 and auto_l16:
    default_line14, default_line16 = auto_l14, auto_l16
    st.success("Auto-mapped Line 14 & Line 16 fields.")
else:
    default_line14 = all_fields[:12] if len(all_fields) >= 12 else []
    rest = all_fields[12:] if len(all_fields) > 12 else []
    default_line16 = rest[:12] if len(rest) >= 12 else []

gen_part2 = st.button("Generate Part II (only)", type="primary")
if gen_part2:
    with st.spinner("Filling Part II…"):
        values = {}
        for i, m in enumerate(months):
            if i < len(default_line14) and default_line14[i]:
                values[default_line14[i]] = line14_map.get(m, '')
            if i < len(default_line16) and default_line16[i]:
                values[default_line16[i]] = line16_map.get(m, '')
        # (Optional) include Line 15 mapping here if your template exposes those fields.
        part2_bytes = fill_pdf_fields(template_pdf_bytes, values, flatten=False)
        st.session_state.part2_pdf_bytes = part2_bytes
        st.session_state.part2_fields = (default_line14, default_line16)
    st.success("Part II PDF created and stored in session.")
    st.download_button("Download Part II (fillable)", data=st.session_state.part2_pdf_bytes,
                       file_name=f"1095C_{sel_id}_PART2_only.pdf", mime="application/pdf")

    if opt_flatten:
        flat = fill_pdf_fields(st.session_state.part2_pdf_bytes, {}, flatten=True)
        st.download_button("Download Part II (flattened)", data=flat,
                           file_name=f"1095C_{sel_id}_PART2_only_flattened.pdf", mime="application/pdf")

st.divider()

# =========================
# STEP 2 — Optional Part I (independent of Step 1 success)
# =========================
st.header("Step 2 — (Optional) Add Part I onto an existing PDF")

# Allow user to choose the base PDF for Part I:
use_session = st.radio(
    "Choose source PDF for Part I:",
    ["Use Part II generated in Step 1 (session)", "Upload a PDF to add Part I"],
    index=0
)

uploaded_override = None
if use_session == "Upload a PDF to add Part I":
    uploaded_override = st.file_uploader("Upload a PDF (e.g., the Part II-only PDF)", type=["pdf"], accept_multiple_files=False)

# Display Part I values we’ll use
st.markdown("**Part I values preview**")
st.write({
    "name": full_name, "ssn": emp_ssn, "address": addr,
    "city": city, "state": state_only, "countryzip": state_zip, "plan": f"{1:02d}"
})

run_part1 = st.button("Add Part I now")
if run_part1:
    # Choose base bytes
    if uploaded_override is not None:
        base_bytes = uploaded_override.read()
    else:
        base_bytes = st.session_state.part2_pdf_bytes

    if not base_bytes:
        st.error("No base PDF available. Generate Part II first, or upload a PDF above.")
    else:
        with st.spinner("Adding Part I…"):
            part1_vals = {
                "name": full_name,
                "ssn": emp_ssn,
                "address": addr,
                "city": city,
                "state": state_only,
                "countryzip": state_zip,
                "plan": f"{1:02d}",
            }
            strict_bytes, ok = fill_part1_fields_strict(base_bytes, part1_vals)
            out_bytes = strict_bytes if ok else (
                overlay_part1_by_anchors(base_bytes, {
                    "name": part1_vals["name"],
                    "ssn": _format_ssn(part1_vals["ssn"]),
                    "address": part1_vals["address"],
                    "city": part1_vals["city"],
                    "statezip": part1_vals["countryzip"],
                    "plan": part1_vals["plan"],
                }) if opt_overlay_part1 else base_bytes
            )

        st.success("Part I added.")
        st.download_button("Download PDF (Part II + Part I)", data=out_bytes,
                           file_name=f"1095C_{sel_id}_PART2_plus_PART1.pdf", mime="application/pdf")

        if opt_flatten:
            flat2 = fill_pdf_fields(out_bytes, {}, flatten=True)
            st.download_button("Download Flattened (Part II + Part I)", data=flat2,
                               file_name=f"1095C_{sel_id}_PART2_plus_PART1_flattened.pdf", mime="application/pdf")
