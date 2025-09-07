# app.py — ACA 1095‑C PDF Generator (Employee‑by‑Employee)
# Streamlit app: upload Excel + a fillable 1095‑C PDF, compute Line14/16, and fill Part I & Part II.
# 
# How to run locally:
#   1) pip install streamlit pandas numpy python-dateutil openpyxl pdfrw reportlab
#   2) streamlit run app.py
#
# Notes:
# - Uses your Excel workbook sheets (case‑insensitive): Emp Demographic, Emp Status, Emp Eligibility,
#   Emp Enrollment, Dep Enrollment, Pay Deductions (optional).
# - Implements the rules in your “Aca One‑shot Processing Prompt (interim + Final)”.
# - Lets you map PDF form field names for Part I and Part II (Line 14 & 16) in case your template uses non‑standard names.
# - Produces both a fillable form and an optional flattened (printed) copy.

import io
import json
import calendar
from datetime import datetime
from dateutil.parser import parse as dtparse
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from pdfrw import PdfReader, PdfWriter, PageMerge
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

st.set_page_config(page_title="ACA 1095‑C Builder", layout="wide")

# ---------------------- UI Helpers ----------------------

def tag(text):
    st.markdown(f"<div style='display:inline-block;padding:2px 8px;border-radius:999px;background:#eef;border:1px solid #ccd;font-size:12px'>{text}</div>", unsafe_allow_html=True)

# ---------------------- Cleaning & Parsing ----------------------

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df

BOOL_TRUE = {"yes","y","true","1","t"}

def to_bool(val) -> bool:
    if pd.isna(val):
        return False
    return str(val).strip().lower() in BOOL_TRUE

# Clamp invalid dates to month end if needed

def parse_date_safe(d, default_end=False):
    if pd.isna(d) or d == "":
        return None
    try:
        return pd.to_datetime(d)
    except Exception:
        try:
            s = str(d)
            # try YYYY-MM or YYYY/MM
            if len(s) == 7 and ("-" in s or "/" in s):
                parts = s.replace("/","-").split("-")
                y, m = int(parts[0]), int(parts[1])
                last = calendar.monthrange(y, m)[1]
                return pd.to_datetime(f"{y}-{m:02d}-{last if default_end else 1}")
            # try YYYY-MM-DD with invalid day
            y, m, day = map(int, s.replace("/","-").split("-"))
            last_day = calendar.monthrange(y, m)[1]
            day = min(day, last_day)
            if default_end:
                day = last_day
            return pd.to_datetime(f"{y}-{m:02d}-{day:02d}")
        except Exception:
            return None

# Month overlap helpers

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
    # Build each table with expected columns; default missing to empty frames
    emp_demo = data.get('emp demographic', pd.DataFrame())
    if not emp_demo.empty:
        cols = [c for c in emp_demo.columns if c in ['employeeid','firstname','lastname','ssn','addressline1','city','state','zipcode','country','employername','ein','employeraddress','contacttelephone','employercity','employerstate','employercountry','employerzip']]
        emp_demo = emp_demo[cols]
    emp_status = data.get('emp status', pd.DataFrame())
    emp_elig = data.get('emp eligibility', pd.DataFrame())
    emp_enroll = data.get('emp enrollment', pd.DataFrame())
    dep_enroll = data.get('dep enrollment', pd.DataFrame())
    pay_ded = data.get('pay deductions', pd.DataFrame())

    # Coerce EmployeeID to str everywhere
    for df in [emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, pay_ded]:
        if not df.empty and 'employeeid' in df.columns:
            df['employeeid'] = df['employeeid'].astype(str)

    # Parse dates & flags
    if not emp_status.empty:
        for c in ['statusstartdate','statusenddate']:
            if c in emp_status.columns:
                emp_status[c] = emp_status[c].apply(lambda x: parse_date_safe(x, default_end=(c=='statusenddate')))
        if 'employmentstatus' in emp_status.columns:
            emp_status['employmentstatus'] = emp_status['employmentstatus'].astype(str).str.strip().str.upper()
        if 'role' in emp_status.columns:
            emp_status['role'] = emp_status['role'].astype(str).str.strip().str.upper()

    if not emp_elig.empty:
        for c in ['eligibilitystartdate','eligibilityenddate']:
            if c in emp_elig.columns:
                emp_elig[c] = emp_elig[c].apply(lambda x: parse_date_safe(x, default_end=(c=='eligibilityenddate')))
        # normalize booleans
        if 'iseligibleforcoverage' in emp_elig.columns:
            emp_elig['iseligibleforcoverage'] = emp_elig['iseligibleforcoverage'].apply(to_bool)
        # Minimum value
        mv_col = 'minimumvaluecoverage' if 'minimumvaluecoverage' in emp_elig.columns else ('mimimumvaluecoverage' if 'mimimumvaluecoverage' in emp_elig.columns else None)
        if mv_col:
            emp_elig['eligible_mv'] = emp_elig[mv_col].apply(to_bool)
        else:
            emp_elig['eligible_mv'] = False

    if not emp_enroll.empty:
        for c in ['enrollmentstartdate','enrollmentenddate']:
            if c in emp_enroll.columns:
                emp_enroll[c] = emp_enroll[c].apply(lambda x: parse_date_safe(x, default_end=(c=='enrollmentenddate')))
        if 'isenrolled' in emp_enroll.columns:
            emp_enroll['isenrolled'] = emp_enroll['isenrolled'].apply(to_bool)

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
        # fallback to current year
        return datetime.now().year
    yr_counts = []
    for _, r in emp_elig.dropna(subset=['eligibilitystartdate','eligibilityenddate']).iterrows():
        years = list(range(r['eligibilitystartdate'].year, r['eligibilityenddate'].year + 1))
        yr_counts.extend(years)
    # pick most frequent; tie -> latest
    best = None
    best_count = -1
    for y in sorted(set(yr_counts)):
        c = yr_counts.count(y)
        if c > best_count or (c == best_count and y > (best or 0)):
            best, best_count = y, c
    return best or datetime.now().year


def build_interim(emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, year: int) -> pd.DataFrame:
    # Basic month grid for all employees known in demo OR status OR elig
    employee_ids = set()
    for df in [emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll]:
        if not df.empty and 'employeeid' in df.columns:
            employee_ids.update(df['employeeid'].unique().tolist())
    employee_ids = sorted(list(employee_ids))

    months = list(range(1,13))
    rows = []
    for eid in employee_ids:
        for m in months:
            ms, me = month_bounds(year, m)
            rows.append({
                'employeeid': str(eid),
                'year': year,
                'monthnum': m,
                'month': ms.strftime('%b'),
                'monthstart': ms,
                'monthend': me,
            })
    interim = pd.DataFrame(rows)

    # Join names if available
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
    interim['eligible_allmonth'] = interim.apply(lambda r: eligible_full(r['employeeid'], r['monthstart'], r['monthend']), axis=1)
    interim['eligible_mv'] = interim.apply(lambda r: eligible_mv_any(r['employeeid'], r['monthstart'], r['monthend']), axis=1)
    interim['offer_ee_allmonth'] = interim['eligible_allmonth']

    # Enrollment
    def enrolled_allmonth(eid, mstart, mend):
        if emp_enroll.empty: return False
        sub = emp_enroll[emp_enroll['employeeid'] == eid]
        for _, r in sub.iterrows():
            if to_bool(r.get('isenrolled', False)) and covers_whole_month(r.get('enrollmentstartdate'), r.get('enrollmentenddate'), mstart, mend):
                return True
        return False

    interim['enrolled_allmonth'] = interim.apply(lambda r: enrolled_allmonth(r['employeeid'], r['monthstart'], r['monthend']), axis=1)

    # Dependent offers
    def offer_dep(eid, mstart, mend, dep_type: str):
        if dep_enroll.empty: return False
        sub = dep_enroll[(dep_enroll['employeeid'] == eid) & (dep_enroll.get('dependentrelationship','') == dep_type)] if 'dependentrelationship' in dep_enroll.columns else pd.DataFrame()
        for _, r in sub.iterrows():
            if to_bool(r.get('eligible', False)) and ranges_overlap(r.get('eligiblestartdate'), r.get('eligibleenddate'), mstart, mend):
                return True
        return False

    interim['offer_spouse'] = interim.apply(lambda r: offer_dep(r['employeeid'], r['monthstart'], r['monthend'], 'Spouse'), axis=1)
    interim['offer_dependents'] = interim.apply(lambda r: offer_dep(r['employeeid'], r['monthstart'], r['monthend'], 'Child'), axis=1)

    # Waiting period proxy
    interim['waitingperiod_month'] = interim['employed'] & interim['ft'] & (~interim['eligibleforcoverage'])

    # Not FT all year
    notft = interim.groupby('employeeid')['ft'].sum() == 0
    interim['notft_allyear'] = interim['employeeid'].map(notft)

    # Placeholders for safe harbor / relief (left blank if absent)
    for col in ["safeharbor_fpl","safeharbor_w2","safeharbor_rateofpay","multiemployerrelief","selfinsuredplan"]:
        interim[col] = None

    # Line 14 mapping per rules (no 1A unless explicit affordability provided)
    def map_line14(row):
        if row['offer_ee_allmonth'] and row['eligible_mv']:
            if row['offer_spouse'] and row['offer_dependents']:
                return '1E'  # use 1E (not 1A) when affordability missing
            if (not row['offer_spouse']) and (not row['offer_dependents']):
                return '1B'
            if (not row['offer_spouse']) and row['offer_dependents']:
                return '1C'
            if row['offer_spouse'] and (not row['offer_dependents']):
                return '1D'
            # non‑MV covered by earlier branch; 1F handled below
        if row['offer_ee_allmonth'] and (not row['eligible_mv']):
            return '1F'
        return '1H'

    interim['line14_final'] = interim.apply(map_line14, axis=1)

    # Line 16 mapping priority
    def map_line16(row):
        # 2E MultiemployerRelief => skip (unknown)
        if row['enrolled_allmonth']:
            return '2C'
        if row['waitingperiod_month']:
            return '2D'
        if not row['employed']:
            return '2A'
        if row['employed'] and not row['ft']:
            return '2B'
        # 2F/2G/2H require explicit safeharbor flags; absent here
        return ''

    interim['line16_final'] = interim.apply(map_line16, axis=1)

    # Order columns for clarity
    cols = ['employeeid','firstname','lastname','year','monthnum','month','monthstart','monthend',
            'employed','ft','eligibleforcoverage','eligible_allmonth','eligible_mv','offer_ee_allmonth',
            'enrolled_allmonth','offer_spouse','offer_dependents','waitingperiod_month','notft_allyear',
            'line14_final','line16_final']
    present = [c for c in cols if c in interim.columns]
    interim = interim[present]
    return interim


def build_final(interim: pd.DataFrame) -> pd.DataFrame:
    final = interim[['employeeid','month','line14_final','line16_final']].copy()
    # month order Jan..Dec
    cat = pd.Categorical(final['month'], categories=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], ordered=True)
    final['month'] = cat
    final = final.sort_values(['employeeid','month'])
    final['month'] = final['month'].astype(str)
    return final

# ---------------------- PDF Fill Utilities ----------------------

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
    filled_keys = []

    # Fill fields
    for page in pdf.pages:
        annots = getattr(page, 'Annots', None)
        if not annots:
            continue
        for a in annots:
            if a.Subtype == '/Widget' and a.T:
                key = str(a.T)[1:-1]
                if key in values:
                    a.V = values[key]
                    a.AP = None
                    filled_keys.append((page, a))

    out_io = io.BytesIO()
    PdfWriter().write(out_io, pdf)
    out_bytes = out_io.getvalue()

    if not flatten:
        return out_bytes

    # Flatten: draw text onto each filled field's rect and remove appearance (simple overlay)
    # Build overlay pages for all pages that had fills.
    pdf2 = PdfReader(io.BytesIO(out_bytes))

    # Collect per-page draws: {page_index: [(x1,y1,x2,y2,text), ...]}
    page_draws = {}
    for p_idx, page in enumerate(pdf2.pages):
        annots = getattr(page, 'Annots', None)
        if not annots:
            continue
        for a in annots:
            if a.Subtype == '/Widget' and a.T:
                key = str(a.T)[1:-1]
                if key in values and getattr(a, 'Rect', None):
                    rect = [float(v) for v in a.Rect]
                    x1, y1, x2, y2 = rect
                    page_draws.setdefault(p_idx, []).append((x1, y1, x2, y2, values[key]))

    writer = PdfWriter()
    for p_idx, page in enumerate(pdf2.pages):
        if p_idx in page_draws and page_draws[p_idx]:
            # Prepare a same‑size overlay
            media = page.MediaBox
            w = float(media[2])
            h = float(media[3])
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(w, h))
            c.setFont("Helvetica", 9)
            for (x1, y1, x2, y2, text) in page_draws[p_idx]:
                # draw centered vertically, left padded a bit
                tx = x1 + 2
                ty = (y1 + y2) / 2 - 3
                c.drawString(tx, ty, str(text))
            c.save()
            overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
            PageMerge(page).add(overlay).render()
        writer.addpage(page)
    bout = io.BytesIO()
    writer.write(bout)
    return bout.getvalue()

# ---------------------- App ----------------------

st.title("ACA 1095‑C Builder")

st.markdown(
    "> Normalize the uploaded ACA workbook (cleaning, month‑end clamping), build the 12‑month Interim grid for the Report Year (max eligibility overlap; tie→latest), compute Line 14/16 per rules, and fill your uploaded 1095‑C PDF for a chosen employee."
)

with st.sidebar:
    st.header("1) Upload Inputs")
    excel_file = st.file_uploader("Excel ACA workbook", type=["xlsx","xlsm","xls"], accept_multiple_files=False)
    pdf_file = st.file_uploader("Fillable 1095‑C PDF (sample/template)", type=["pdf"], accept_multiple_files=False)
    st.caption("Tip: use an official IRS fillable 1095‑C. You can map fields if the names are non‑standard.")

    st.header("2) Options")
    opt_flatten = st.checkbox("Also output a flattened copy (printed text)", value=True)
    opt_line15_from_pay = st.checkbox("Populate Line 15 from Pay Deductions (if present)", value=True)


if excel_file is None:
    st.info("Upload your Excel workbook to begin.")
    st.stop()

# Load data and compute
with st.spinner("Reading and preparing inputs…"):
    data = load_excel(excel_file.read())
    emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, pay_ded = prepare_inputs(data)
    report_year = choose_report_year(emp_elig)

st.success(f"Report Year selected: {report_year}")

with st.spinner("Building Interim grid and Final codes…"):
    interim = build_interim(emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, report_year)
    final = build_final(interim)

st.subheader("Interim (first 100 rows)")
st.dataframe(interim.head(100), use_container_width=True)

st.subheader("Final Line 14/16 (first 100 rows)")
st.dataframe(final.head(100), use_container_width=True)

# Employee selection
emp_options = (
    interim[['employeeid','firstname','lastname']]
    .drop_duplicates('employeeid')
    .assign(label=lambda d: d.apply(lambda r: f"{r['employeeid']} — {str(r.get('firstname') or '')} {str(r.get('lastname') or '')}".strip(), axis=1))
)
sel = st.selectbox("Choose an employee to generate PDF", emp_options['label'].tolist())
sel_id = sel.split(' — ')[0]

emp_line = interim[interim['employeeid']==sel_id]
emp_final = final[final['employeeid']==sel_id]

cols = st.columns(2)
with cols[0]:
    st.markdown("**Line 14 (Offer) by month**")
    st.table(emp_final[['month','line14_final']].set_index('month').T)
with cols[1]:
    st.markdown("**Line 16 (Relief) by month**")
    st.table(emp_final[['month','line16_final']].set_index('month').T)

# Line 15 calculation: derive monthly amount from Pay Deductions if option enabled
months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
line15 = {m: '' for m in months}
if opt_line15_from_pay and not pay_ded.empty and 'employeeid' in pay_ded.columns:
    row = pay_ded[pay_ded['employeeid']==sel_id]
    if not row.empty:
        for m in months:
            col = m[:3].capitalize() if m != 'Sep' else 'Sep'
            if col in row.columns:
                val = row.iloc[0][col]
                try:
                    amt = float(str(val).replace('$','').replace(',',''))
                    line15[m] = f"{amt:.2f}"
                except Exception:
                    pass

st.divider()

# PDF Mapping UI
st.subheader("PDF Field Mapping (Part I + Part II)")
if pdf_file is None:
    st.info("Upload a fillable 1095‑C PDF to enable mapping and generation.")
    st.stop()

pdf_bytes = pdf_file.read()
all_fields = extract_pdf_fields(pdf_bytes)
if not all_fields:
    st.warning("No form fields detected. I can only generate a flattened overlay copy. Generation will still work, but the 'fillable' output will be identical to the flattened one.")

with st.expander("Detected PDF fields (raw)"):
    st.write(all_fields)

# Build defaults: try to guess sequential names for Line 14 and 16
default_line14 = all_fields[:12] if len(all_fields) >= 12 else []
rest = all_fields[12:] if len(all_fields) > 12 else []
default_line16 = rest[:12] if len(rest) >= 12 else []

# Map Part I minimal fields
st.markdown("**Part I (Employee & Employer) field mapping (optional)**")
colA, colB, colC = st.columns(3)
with colA:
    fld_emp_name = st.selectbox("Employee Name (Line 1)", ["(none)"] + all_fields, index=0)
with colB:
    fld_emp_ssn = st.selectbox("Employee SSN (Line 2)", ["(none)"] + all_fields, index=0)
with colC:
    fld_plan_start = st.selectbox("Plan Start Month", ["(none)"] + all_fields, index=0)

colD, colE, colF = st.columns(3)
with colD:
    fld_emp_address = st.selectbox("Employee Address (Line 3)", ["(none)"] + all_fields, index=0)
with colE:
    fld_emp_city = st.selectbox("Employee City (Line 4)", ["(none)"] + all_fields, index=0)
with colF:
    fld_emp_state_zip = st.selectbox("Employee State/ZIP (Line 5/6 combined)", ["(none)"] + all_fields, index=0)

st.markdown("**Part II (Line 14 & Line 16) field mapping**")
map_cols = st.columns(3)
with map_cols[0]:
    st.caption("Line 14 fields (Jan..Dec)")
    l14 = []
    for i, m in enumerate(months):
        sel = st.selectbox(f"L14 {m}", ["(none)"] + all_fields, index=(all_fields.index(default_line14[i]) + 1 if i < len(default_line14) and default_line14[i] in all_fields else 0), key=f"l14_{i}")
        l14.append(sel if sel != "(none)" else None)
with map_cols[1]:
    st.caption("Line 15 (optional) — if mapped and enabled")
    l15 = []
    for i, m in enumerate(months):
        sel = st.selectbox(f"L15 {m}", ["(none)"] + all_fields, index=0, key=f"l15_{i}")
        l15.append(sel if sel != "(none)" else None)
with map_cols[2]:
    st.caption("Line 16 fields (Jan..Dec)")
    l16 = []
    for i, m in enumerate(months):
        sel = st.selectbox(f"L16 {m}", ["(none)"] + all_fields, index=(all_fields.index(default_line16[i]) + 1 if i < len(default_line16) and default_line16[i] in all_fields else 0), key=f"l16_{i}")
        l16.append(sel if sel != "(none)" else None)

st.divider()

# Build values for selected employee
emp_name = emp_demo[emp_demo['employeeid']==sel_id]
full_name = ""
if not emp_name.empty:
    fn = str(emp_name.iloc[0].get('firstname') or '').strip()
    ln = str(emp_name.iloc[0].get('lastname') or '').strip()
    mi = ""  # not tracked
    full_name = (fn + (" " + mi if mi else "") + (" " + ln if ln else "")).strip()

# minimal address/SSN fields (optional)
emp_ssn = str(emp_name.iloc[0].get('ssn') or '') if not emp_name.empty else ''
addr = str(emp_name.iloc[0].get('addressline1') or '') if not emp_name.empty else ''
city = str(emp_name.iloc[0].get('city') or '') if not emp_name.empty else ''
state = str(emp_name.iloc[0].get('state') or '') if not emp_name.empty else ''
zipc = str(emp_name.iloc[0].get('zipcode') or '') if not emp_name.empty else ''
state_zip = " ".join([p for p in [state, zipc] if p])

# Part II codes from final table
final_row = final[final['employeeid']==sel_id].set_index('month')
line14_map = {m: (final_row.loc[m, 'line14_final'] if m in final_row.index else '') for m in months}
line16_map = {m: (final_row.loc[m, 'line16_final'] if m in final_row.index else '') for m in months}

# Compose PDF values by mapping
values = {}
# Part I
if fld_emp_name and fld_emp_name != "(none)" and full_name:
    values[fld_emp_name] = full_name
if fld_emp_ssn and fld_emp_ssn != "(none)" and emp_ssn:
    values[fld_emp_ssn] = emp_ssn
if fld_emp_address and fld_emp_address != "(none)" and addr:
    values[fld_emp_address] = addr
if fld_emp_city and fld_emp_city != "(none)" and city:
    values[fld_emp_city] = city
if fld_emp_state_zip and fld_emp_state_zip != "(none)" and state_zip:
    values[fld_emp_state_zip] = state_zip
if fld_plan_start and fld_plan_start != "(none)":
    values[fld_plan_start] = f"{1:02d}"

# Part II
for i, m in enumerate(months):
    if l14[i]:
        values[l14[i]] = line14_map.get(m, '')
    if l16[i]:
        values[l16[i]] = line16_map.get(m, '')
    if opt_line15_from_pay and l15[i] and line15.get(m):
        values[l15[i]] = line15[m]

col_go1, col_go2 = st.columns([1,2])
with col_go1:
    gen = st.button("Generate PDF for selected employee")

if gen:
    with st.spinner("Filling PDF…"):
        fillable_bytes = fill_pdf_fields(pdf_bytes, values, flatten=False)
        st.download_button("Download filled (fillable) PDF", data=fillable_bytes, file_name=f"1095C_{sel_id}_fillable.pdf", mime="application/pdf")
        if opt_flatten or not all_fields:
            flat_bytes = fill_pdf_fields(fillable_bytes, values, flatten=True)
            st.download_button("Download flattened (printed) PDF", data=flat_bytes, file_name=f"1095C_{sel_id}_flattened.pdf", mime="application/pdf")

st.divider()

# Bulk generation helper (zip)
st.subheader("Bulk Generate (optional)")
st.caption("Generate for all employees using the same field mapping. Produces a .zip with one PDF per employee.")
bulk = st.button("Generate Zip for All Employees")
if bulk:
    import zipfile
    from tqdm import tqdm
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for eid in interim['employeeid'].drop_duplicates().tolist():
            # recompute values for each employee
            emp_name = emp_demo[emp_demo['employeeid']==eid]
            fn = str(emp_name.iloc[0].get('firstname') or '') if not emp_name.empty else ''
            ln = str(emp_name.iloc[0].get('lastname') or '') if not emp_name.empty else ''
            full_name = (fn + ' ' + ln).strip()
            emp_ssn = str(emp_name.iloc[0].get('ssn') or '') if not emp_name.empty else ''
            addr = str(emp_name.iloc[0].get('addressline1') or '') if not emp_name.empty else ''
            city = str(emp_name.iloc[0].get('city') or '') if not emp_name.empty else ''
            state = str(emp_name.iloc[0].get('state') or '') if not emp_name.empty else ''
            zipc = str(emp_name.iloc[0].get('zipcode') or '') if not emp_name.empty else ''
            state_zip = " ".join([p for p in [state, zipc] if p])

            final_row = final[final['employeeid']==eid].set_index('month')
            line14_map = {m: (final_row.loc[m, 'line14_final'] if m in final_row.index else '') for m in months}
            line16_map = {m: (final_row.loc[m, 'line16_final'] if m in final_row.index else '') for m in months}

            vals = {}
            if fld_emp_name and fld_emp_name != "(none)" and full_name:
                vals[fld_emp_name] = full_name
            if fld_emp_ssn and fld_emp_ssn != "(none)" and emp_ssn:
                vals[fld_emp_ssn] = emp_ssn
            if fld_emp_address and fld_emp_address != "(none)" and addr:
                vals[fld_emp_address] = addr
            if fld_emp_city and fld_emp_city != "(none)" and city:
                vals[fld_emp_city] = city
            if fld_emp_state_zip and fld_emp_state_zip != "(none)" and state_zip:
                vals[fld_emp_state_zip] = state_zip
            if fld_plan_start and fld_plan_start != "(none)":
                vals[fld_plan_start] = f"{1:02d}"

            for i, m in enumerate(months):
                if l14[i]:
                    vals[l14[i]] = line14_map.get(m, '')
                if l16[i]:
                    vals[l16[i]] = line16_map.get(m, '')
                if opt_line15_from_pay and l15[i] and line15.get(m):
                    vals[l15[i]] = line15[m]

            filled = fill_pdf_fields(pdf_bytes, vals, flatten=False)
            if opt_flatten or not all_fields:
                filled = fill_pdf_fields(filled, vals, flatten=True)
            zf.writestr(f"1095C_{eid}.pdf", filled)
    st.download_button("Download ZIP", data=buf.getvalue(), file_name="1095C_all_employees.zip", mime="application/zip")

st.caption("Built with Streamlit · Uses pdfrw/reportlab for PDF fill & flatten · Works with arbitrary fillable 1095‑C via field mapping.")
