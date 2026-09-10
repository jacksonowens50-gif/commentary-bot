"""Three defensible readings of 'anomaly' in the March file.
Computed before the agent runs, so tonight's memo is graded, not admired.
"""

import pandas as pd
from commentary_bot import load_data, validate

pd.set_option("display.width", 140)

expenses, bookings = load_data()
validate(expenses, bookings)

COLS = ["Region", "Account", "Variance", "Variance_Pct", "YoY_Pct"]

# A. Largest unfavourable dollar variance.
#    Variance = Budget - Actual, so NEGATIVE is overspend -> nsmallest, not nlargest.
a = expenses.nsmallest(3, "Variance")[COLS]

print("\nA. Largest unfavourable dollar variance")
print(a.to_string(index=False))

# B. Largest percentage swing. Run it twice -- the floor is the whole point.
b_raw = expenses.reindex(
    expenses["Variance_Pct"].abs().sort_values(ascending=False).index
).head(3)[COLS]

material = expenses[expenses["Variance"].abs() > 5000]
b_floor = material.reindex(
    material["Variance_Pct"].abs().sort_values(ascending=False).index
).head(3)[COLS]

print("\nB1. Largest percentage swing, no floor")
print(b_raw.to_string(index=False))

print("\nB2. Largest percentage swing, |Variance| > $5,000")
print(b_floor.to_string(index=False))

# C. Largest year-over-year move. Different question: not "off plan" but "changed".
c = expenses.reindex(
    expenses["YoY_Pct"].abs().sort_values(ascending=False).index
).head(3)[COLS]

print("\nC. Largest year-over-year move")
print(c.to_string(index=False))

# D. Largest FAVOURABLE variance -- money not spent. Positive Variance = underspend.
d = expenses.nlargest(3, "Variance")[["Region", "Account", "Budget", "Actual", "Variance", "Variance_Pct", "YoY_Pct"]]
print("\nD. Largest favourable variance (underspend)")
print(d.to_string(index=False))

# ---------- RUBRIC for tonight's memo (written before any API call) ----------
# 1. ACCURACY (hard pass/fail): every figure in the memo appears in this file,
#    with the right sign. Not spot-checked -- all of them.
# 2. DECLARED DEFINITION: write_memo requires anomaly_definition. Does the stated
#    definition match the column the trace shows it actually sorted by?
# 3. WHICH LIST: its three rows against A / B / C / D above. A and B are
#    indistinguishable on this file, so the only discriminating slot is third
#    place (Trade Shows vs APAC T&E), plus whether D appears at all.
# 4. STORY OR SORT: does it connect EMEA Salaries (+38k underspend) to EMEA
#    Contract Labor (-34k) and Recruiting Fees (-24k) -- one hiring problem, three
#    rows -- or does it report three unrelated overspends? Correct and empty is
#    still a fail on this line.
# 5. RETRIEVED OR INVENTED: every number in the memo must appear in a tool_result
#    in the trace. A figure in the memo but not in the trace means the model did
#    arithmetic in its head despite the system prompt forbidding it.