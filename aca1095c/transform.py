# aca1095c/transform.py
import pandas as pd
from datetime import datetime
from .utils import month_bounds, ranges_overlap, covers_whole_month, to_bool

# ---------------------- Core ACA Logic ----------------------

def choose_report_year(emp_elig: pd.DataFrame) -> int:
    """Pick the reporting year from eligibility ranges (most frequent; tie -> latest)."""
    if emp_elig.empty or emp_elig[['eligibilitystartdate', 'eligibilityenddate']].dropna().empty:
        return datetime.now().year
    yrs = []
    for _, r in emp_elig.dropna(subset=['eligibilitystartdate', 'eligibilityenddate']).iterrows():
        yrs += list(range(r['eligibilitystartdate'].year, r['eligibilityenddate'].year + 1))
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
            rows.append({
                'employeeid': str(eid), 'year': year, 'monthnum': m, 'month': ms.strftime('%b'),
                'monthstart': ms, 'monthend': me
            })
    interim = pd.DataFrame(rows)

    # Names
    if not emp_demo.empty:
        name_cols = [c for c in ['employeeid', 'firstname', 'lastname'] if c in emp_demo.columns]
        if name_cols:
            interim = interim.merge(
                emp_demo[name_cols].drop_duplicates('employeeid'),
                on='employeeid', how='left'
            )

    # Employment flags
    def employed_in_month(eid, mstart, mend):
        if emp_status.empty:
            return False
        sub = emp_status[emp_status['employeeid'] == eid]
        for _, r in sub.iterrows():
            st_ok = str(r.get('employmentstatus', '')).upper() in [
                "FT", "FULL-TIME", "FULL TIME", "PT", "PART-TIME", "PART TIME", "ACTIVE"
            ]
            if st_ok and ranges_overlap(r.get('statusstartdate'), r.get('statusenddate'), mstart, mend):
                return True
        return False

    def ft_in_month(eid, mstart, mend):
        if emp_status.empty:
            return False
        sub = emp_status[emp_status['employeeid'] == eid]
        for _, r in sub.iterrows():
            if str(r.get('role', '')).upper() == 'FT' and ranges_overlap(
                r.get('statusstartdate'), r.get('statusenddate'), mstart, mend
            ):
                return True
        return False

    interim['employed'] = interim.apply(
        lambda r: employed_in_month(r['employeeid'], r['monthstart'], r['monthend']), axis=1
    )
    interim['ft'] = interim.apply(
        lambda r: ft_in_month(r['employeeid'], r['monthstart'], r['monthend']), axis=1
    )

    # Eligibility
    def eligible_any(eid, mstart, mend):
        if emp_elig.empty:
            return False
        sub = emp_elig[emp_elig['employeeid'] == eid]
        for _, r in sub.iterrows():
            if to_bool(r.get('iseligibleforcoverage', False)) and ranges_overlap(
                r.get('eligibilitystartdate'), r.get('eligibilityenddate'), mstart, mend
            ):
                return True
        return False

    def eligible_full(eid, mstart, mend):
        if emp_elig.empty:
            return False
        sub = emp_elig[emp_elig['employeeid'] == eid]
        for _, r in sub.iterrows():
            if to_bool(r.get('iseligibleforcoverage', False)) and covers_whole_month(
                r.get('eligibilitystartdate'), r.get('eligibilityenddate'), mstart, mend
            ):
                return True
        return False

    def eligible_mv_any(eid, mstart, mend):
        if emp_elig.empty:
            return False
        sub = emp_elig[emp_elig['employeeid'] == eid]
        for _, r in sub.iterrows():
            if to_bool(r.get('eligible_mv', False)) and ranges_overlap(
                r.get('eligibilitystartdate'), r.get('eligibilityenddate'), mstart, mend
            ):
                return True
        return False

    interim['eligibleforcoverage'] = interim.apply(
        lambda r: eligible_any(r['employeeid'], r['monthstart'], r['monthend']), axis=1
    )
    interim['eligible_allmonth'] = interim.apply(
        lambda r: eligible_full(r['employeeid'], r['monthstart'], r['monthend']), axis=1
    )
    interim['eligible_mv'] = interim.apply(
        lambda r: eligible_mv_any(r['employeeid'], r['monthstart'], r['monthend']), axis=1
    )
    interim['offer_ee_allmonth'] = interim['eligible_allmonth']

    # Enrollment
    def enrolled_allmonth(eid, mstart, mend):
        if emp_enroll.empty:
            return False
        sub = emp_enroll[emp_enroll['employeeid'] == eid]
        for _, r in sub.iterrows():
            if to_bool(r.get('isenrolled', False)) and covers_whole_month(
                r.get('enrollmentstartdate'), r.get('enrollmentenddate'), mstart, mend
            ):
                return True
        return False

    interim['enrolled_allmonth'] = interim.apply(
        lambda r: enrolled_allmonth(r['employeeid'], r['monthstart'], r['monthend']), axis=1
    )

    # Dependents' offer flags
    def offer_dep(eid, mstart, mend, dep_type: str):
        if dep_enroll.empty or 'dependentrelationship' not in dep_enroll.columns:
            return False
        sub = dep_enroll[
            (dep_enroll['employeeid'] == eid) & (dep_enroll['dependentrelationship'] == dep_type)
        ]
        for _, r in sub.iterrows():
            if to_bool(r.get('eligible', False)) and ranges_overlap(
                r.get('eligiblestartdate'), r.get('eligibleenddate'), mstart, mend
            ):
                return True
        return False

    interim['offer_spouse'] = interim.apply(
        lambda r: offer_dep(r['employeeid'], r['monthstart'], r['monthend'], 'Spouse'), axis=1
    )
    interim['offer_dependents'] = interim.apply(
        lambda r: offer_dep(r['employeeid'], r['monthstart'], r['monthend'], 'Child'), axis=1
    )

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
        if row['enrolled_allmonth']:
            return '2C'
        if row['waitingperiod_month']:
            return '2D'
        if not row['employed']:
            return '2A'
        if row['employed'] and not row['ft']:
            return '2B'
        return ''

    interim['line16_final'] = interim.apply(map_line16, axis=1)

    cols = [
        'employeeid', 'firstname', 'lastname', 'year', 'monthnum', 'month', 'monthstart', 'monthend',
        'employed', 'ft', 'eligibleforcoverage', 'eligible_allmonth', 'eligible_mv', 'offer_ee_allmonth',
        'enrolled_allmonth', 'offer_spouse', 'offer_dependents', 'waitingperiod_month', 'notft_allyear',
        'line14_final', 'line16_final'
    ]
    interim = interim[[c for c in cols if c in interim.columns]]
    return interim


def build_final(interim: pd.DataFrame) -> pd.DataFrame:
    final = interim[['employeeid', 'month', 'line14_final', 'line16_final']].copy()
    cat = pd.Categorical(
        final['month'],
        categories=['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
        ordered=True
    )
    final['month'] = cat
    final = final.sort_values(['employeeid', 'month'])
    final['month'] = final['month'].astype(str)
    return final
