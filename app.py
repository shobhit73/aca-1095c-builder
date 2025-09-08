# ACA 1095-C PDF Generator (Two-step: Part II first, then optional Part I)
# Run: streamlit run app.py

import streamlit as st
import pandas as pd

from aca1095c import (
    load_excel, prepare_inputs,
    choose_report_year, build_interim, build_final,
)
from aca1095c.pdf import (
    extract_pdf_fields, fill_pdf_fields, auto_map_fields,
    fill_part1_fields_strict, overlay_part1_by_anchors, _format_ssn,
    build_part2_values, MONTHS,
)

st.set_page_config(page_title="ACA 1095-C Builder (Two-Step)", layout="wide")

# Keep state between the two steps
if "part2_pdf_bytes" not in st.session_state:
    st.session_state.part2_pdf_bytes = None
if "part2_fields" not in st.session_state:
    st.session_state.part2_fields = None

st.title("ACA 1095-C Builder — Two-Step")
st.caption("Step 1: Generate Part II only. Step 2 (optional): Add Part I on top of the generated Part II PDF.")

# ---------------- Sidebar: Inputs + Options ----------------
with st.sidebar:
    st.header("1) Upload Inputs")
    excel_file = st.file_uploader("Excel ACA workbook", type=["xlsx", "xlsm", "xls"], accept_multiple_files=False)
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

# ---------------- Load & Prepare ----------------
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

# ---------------- Employee picker ----------------
emp_options = (
    interim[["employeeid", "firstname", "lastname"]]
    .drop_duplicates("employeeid")
    .assign(label=lambda d: d.apply(
        lambda r: f"{r['employeeid']} — {str(r.get('firstname') or '')} {str(r.get('lastname') or '')}".strip(),
        axis=1
    ))
)
sel_label = st.selectbox("Choose an employee", emp_options["label"].tolist())
sel_id = sel_label.split(" — ")[0]

# Compose Part I strings for later (Step 2)
emp_row = emp_demo[emp_demo["employeeid"] == sel_id]
full_name = emp_ssn = addr = city = state_only = state_zip = ""
if not emp_row.empty:
    fn = str(emp_row.iloc[0].get("firstname") or "").strip()
    ln = str(emp_row.iloc[0].get("lastname") or "").strip()
    full_name = (fn + (" " if fn and ln else "") + ln).strip()
    emp_ssn = str(emp_row.iloc[0].get("ssn") or "")
    addr = str(emp_row.iloc[0].get("addressline1") or "")
    city = str(emp_row.iloc[0].get("city") or "")
    stt = str(emp_row.iloc[0].get("state") or "")
    zc = str(emp_row.iloc[0].get("zipcode") or "")
    state_only = stt
    state_zip = " ".join([p for p in [stt, zc] if p])

# Prepare Part II maps for the chosen employee
final_row = final[final["employeeid"] == sel_id].set_index("month")
line14_map = {m: (final_row.loc[m, "line14_final"] if m in final_row.index else "") for m in MONTHS}
line16_map = {m: (final_row.loc[m, "line16_final"] if m in final_row.index else "") for m in MONTHS}

# Optional Line 15 from Pay Deductions (not used unless your PDF exposes Line 15 fields)
line15 = {m: "" for m in MONTHS}
if opt_line15_from_pay and not pay_ded.empty and "employeeid" in pay_ded.columns:
    row = pay_ded[pay_ded["employeeid"] == sel_id]
    if not row.empty:
        for m in MONTHS:
            col = m[:3].capitalize()
            if col in row.columns:
                val = row.iloc[0][col]
                try:
                    amt = float(str(val).replace("$", "").replace(",", ""))
                    line15[m] = f"{amt:.2f}"
                except Exception:
                    pass

st.divider()

# ============================================================
# STEP 1 — Generate Part II only
# ============================================================
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

if st.button("Generate Part II (only)", type="primary"):
    with st.spinner("Filling Part II…"):
        values = build_part2_values(default_line14, default_line16, line14_map, line16_map)
        # (Optional) include Line 15 mapping here if your template exposes those fields.
        part2_bytes = fill_pdf_fields(template_pdf_bytes, values, flatten=False)
        st.session_state.part2_pdf_bytes = part2_bytes
        st.session_state.part2_fields = (default_line14, default_line16)
    st.success("Part II PDF created and stored in session.")
    st.download_button(
        "Download Part II (fillable)",
        data=st.session_state.part2_pdf_bytes,
        file_name=f"1095C_{sel_id}_PART2_only.pdf",
        mime="application/pdf",
    )

    if opt_flatten:
        flat = fill_pdf_fields(st.session_state.part2_pdf_bytes, {}, flatten=True)
        st.download_button(
            "Download Part II (flattened)",
            data=flat,
            file_name=f"1095C_{sel_id}_PART2_only_flattened.pdf",
            mime="application/pdf",
        )

st.divider()

# ============================================================
# STEP 2 — (Optional) Add Part I onto an existing PDF
# ============================================================
st.header("Step 2 — (Optional) Add Part I onto an existing PDF")

# Allow user to choose the base PDF for Part I:
use_session = st.radio(
    "Choose source PDF for Part I:",
    ["Use Part II generated in Step 1 (session)", "Upload a PDF to add Part I"],
    index=0,
)

uploaded_override = None
if use_session == "Upload a PDF to add Part I":
    uploaded_override = st.file_uploader(
        "Upload a PDF (e.g., the Part II-only PDF)", type=["pdf"], accept_multiple_files=False
    )

# Display Part I values we’ll use
st.markdown("**Part I values preview**")
st.write({
    "name": full_name, "ssn": emp_ssn, "address": addr,
    "city": city, "state": state_only, "countryzip": state_zip, "plan": f"{1:02d}",
})

if st.button("Add Part I now"):
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
            out_bytes = (
                strict_bytes if ok
                else (
                    overlay_part1_by_anchors(base_bytes, {
                        "name": part1_vals["name"],
                        "ssn": _format_ssn(part1_vals["ssn"]),
                        "address": part1_vals["address"],
                        "city": part1_vals["city"],
                        "statezip": part1_vals["countryzip"],
                        "plan": part1_vals["plan"],
                    }) if opt_overlay_part1 else base_bytes
                )
            )

        st.success("Part I added.")
        st.download_button(
            "Download PDF (Part II + Part I)",
            data=out_bytes,
            file_name=f"1095C_{sel_id}_PART2_plus_PART1.pdf",
            mime="application/pdf",
        )

        if opt_flatten:
            flat2 = fill_pdf_fields(out_bytes, {}, flatten=True)
            st.download_button(
                "Download Flattened (Part II + Part I)",
                data=flat2,
                file_name=f"1095C_{sel_id}_PART2_plus_PART1_flattened.pdf",
                mime="application/pdf",
            )
