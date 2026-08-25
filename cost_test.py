"""Five runs: how variable is output, and what does a run actually cost?"""

from anthropic import Anthropic

from commentary_bot import (
    OUTPUT_DIR,
    build_prompt,
    get_commentary,
    load_data,
    materiality,
    region_rollup,
    validate,
)

# $/MTok, Claude Sonnet 5, verified 2026-08-25 against platform.claude.com/docs
PRICE_IN, PRICE_OUT = 2.00, 10.00

client = Anthropic()
expenses, bookings = load_data()
validate(expenses, bookings)
m = materiality(expenses)
regions = region_rollup(expenses)
system, user = build_prompt(expenses, bookings, m, regions)

costs, outs = [], []
for i in range(1, 6):
    text, usage = get_commentary(client, system, user)
    cost = usage.input_tokens / 1e6 * PRICE_IN + usage.output_tokens / 1e6 * PRICE_OUT
    costs.append(cost)
    outs.append(usage.output_tokens)
    (OUTPUT_DIR / f"commentary_run{i}.md").write_text(text, encoding="utf-8")
    print(f"run {i}: {usage.input_tokens:,} in | {usage.output_tokens:,} out | ${cost:.4f}")

med = sorted(costs)[2]
print(f"\noutput tokens  min {min(outs):,}  median {sorted(outs)[2]:,}  max {max(outs):,}")
print(f"cost per run   min ${min(costs):.4f}  median ${med:.4f}  max ${max(costs):.4f}")
print(f"50 departments monthly ${med * 50:.2f}  |  annual ${med * 50 * 12:.2f}")