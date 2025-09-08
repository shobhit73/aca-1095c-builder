import calendar
from typing import Tuple
import pandas as pd

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

def parse_date_safe(d, default_end: bool = False):
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
    return (
        a_start is not None and a_end is not None and
        a_start <= b_end and a_end >= b_start
    )

def covers_whole_month(r_start, r_end, m_start, m_end) -> bool:
    return (
        r_start is not None and r_end is not None and
        r_start <= m_start and r_end >= m_end
    )
