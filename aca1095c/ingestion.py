# aca1095c/ingestion.py
import io
from typing import Dict
import pandas as pd

from .utils import normalize_columns, parse_date_safe, to_bool


# ---------------------- Excel Load & Normalize ----------------------
def load_excel(file_bytes: bytes) -> Dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    data: Dict[str, pd.DataFrame] = {}
    for sheet in xls.sheet_names:
        key = sheet.strip().lower()
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet)
        data[key] = normalize_columns(df)
    return data


def prepare_inputs(data: Dict[str, pd.DataFrame]):
    emp_demo = data.get("emp demographic", pd.DataFrame())
    if not emp_demo.empty:
        cols = [
            c
            for c in emp_demo.columns
            if c
            in [
                "employeeid",
                "firstname",
                "lastname",
                "ssn",
                "addressline1",
                "city",
                "state",
                "zipcode",
                "country",
                "employername",
                "ein",
                "employeraddress",
                "contacttelephone",
                "employercity",
                "employerstate",
                "employercountry",
                "employerzip",
            ]
        ]
        emp_demo = emp_demo[cols]

    emp_status = data.get("emp status", pd.DataFrame())
    emp_elig = data.get("emp eligibility", pd.DataFrame())
    emp_enroll = data.get("emp enrollment", pd.DataFrame())
    dep_enroll = data.get("dep enrollment", pd.DataFrame())
    pay_ded = data.get("pay deductions", pd.DataFrame())

    # EmployeeID to str  ← (this block MUST be indented inside the for)
    for df in [emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, pay_ded]:
        if not df.empty and "employeeid" in df.columns:
            df["employeeid"] = df["employeeid"].astype(str)

    # Status
    if not emp_status.empty:
        for c in ["statusstartdate", "statusenddate"]:
            if c in emp_status.columns:
                emp_status[c] = emp_status[c].apply(
                    lambda x: parse_date_safe(x, default_end=(c == "statusenddate"))
                )
        if "employmentstatus" in emp_status.columns:
            emp_status["employmentstatus"] = (
                emp_status["employmentstatus"].astype(str).str.strip().str.upper()
            )
        if "role" in emp_status.columns:
            emp_status["role"] = emp_status["role"].astype(str).str.strip().str.upper()

    # Eligibility
    if not emp_elig.empty:
        for c in ["eligibilitystartdate", "eligibilityenddate"]:
            if c in emp_elig.columns:
                emp_elig[c] = emp_elig[c].apply(
                    lambda x: parse_date_safe(x, default_end=(c == "eligibilityenddate"))
                )
        if "iseligibleforcoverage" in emp_elig.columns:
            emp_elig["iseligibleforcoverage"] = emp_elig["iseligibleforcoverage"].apply(
                to_bool
            )
        mv_col = (
            "minimumvaluecoverage"
            if "minimumvaluecoverage" in emp_elig.columns
            else (
                "mimimumvaluecoverage"
                if "mimimumvaluecoverage" in emp_elig.columns
                else None
            )
        )
        emp_elig["eligible_mv"] = emp_elig[mv_col].apply(to_bool) if mv_col else False

    # Enrollment
    if not emp_enroll.empty:
        for c in ["enrollmentstartdate", "enrollmentenddate"]:
            if c in emp_enroll.columns:
                emp_enroll[c] = emp_enroll[c].apply(
                    lambda x: parse_date_safe(x, default_end=(c == "enrollmentenddate"))
                )
        if "isenrolled" in emp_enroll.columns:
            emp_enroll["isenrolled"] = emp_enroll["isenrolled"].apply(to_bool)

    # Dependents
    if not dep_enroll.empty:
        for c in ["eligiblestartdate", "eligibleenddate"]:
            if c in dep_enroll.columns:
                dep_enroll[c] = dep_enroll[c].apply(
                    lambda x: parse_date_safe(x, default_end=(c == "eligibleenddate"))
                )
        for c in ["eligible", "enrolled"]:
            if c in dep_enroll.columns:
                dep_enroll[c] = dep_enroll[c].apply(to_bool)
        if "dependentrelationship" in dep_enroll.columns:
            dep_enroll["dependentrelationship"] = (
                dep_enroll["dependentrelationship"]
                .astype(str)
                .str.strip()
                .str.capitalize()
            )

    return emp_demo, emp_status, emp_elig, emp_enroll, dep_enroll, pay_ded
