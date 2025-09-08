from typing import Dict, List

MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

def build_part2_values(default_line14: List[str], default_line16: List[str],
line14_map: Dict[str, str], line16_map: Dict[str, str]) -> Dict[str, str]:
values = {}
for i, m in enumerate(MONTHS):
if i < len(default_line14) and default_line14[i]:
values[default_line14[i]] = line14_map.get(m, '')
if i < len(default_line16) and default_line16[i]:
values[default_line16[i]] = line16_map.get(m, '')
return values
