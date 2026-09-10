"""Print the per-turn context and cost table from a saved trace.

Usage: python trace_table.py output/trace_goal_YYYYMMDD-HHMMSS.json
"""
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
t = json.loads(path.read_text(encoding="utf-8"))

print(f"{'turn':>4} {'msgs':>5} {'in_tok':>9} {'out_tok':>8} {'cum_$':>9}  stop_reason")
for r in t["turns"]:
    print(f"{r['turn']:>4} {r['messages_in_context']:>5} {r['input_tokens']:>9,} "
          f"{r['output_tokens']:>8,} {r['cum_cost_usd']:>9.5f}  {r['stop_reason']}")

tot = t["totals"]
first_in = t["turns"][0]["input_tokens"]
print(f"\ninput tokens total {tot['input_tokens']:,} = {tot['input_tokens']/first_in:.1f}x turn 1")
print(f"input cost ${tot['input_tokens']/1e6*2:.4f} | output cost ${tot['output_tokens']/1e6*10:.4f}")
print(f"repeats: {sum(1 for c in t['calls'] if c.get('repeat'))} of {len(t['calls'])} calls")
