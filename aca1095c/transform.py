import numpy as np
import pandas as pd
from datetime import datetime
from .utils import month_bounds, ranges_overlap, covers_whole_month, to_bool


# ---------------------- Core ACA Logic ----------------------


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


return final
