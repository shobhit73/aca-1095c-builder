import io
"ssn": ["f1_2[0]", "ssn", "employee_ssn", "employeesocialsecuritynumber[0]"],
"address": ["f1_3[0]", "address[0]", "streetaddress[0]", "address1", "street"],
"city": ["f1_4[0]", "city[0]", "cityortown", "city_or_town"],
"state": ["f1_5[0]", "state[0]", "province", "state_or_province"],
"countryzip":["f1_6[0]", "zip[0]", "postalcode", "country_zip", "countryandzip", "country and zip"],
"plan": ["f1_7[0]", "planstartmonth[0]", "plan start month"]
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
