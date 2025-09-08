# ACA 1095-C PDF Generator (Two-step: Part II first, then optional Part I)
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
