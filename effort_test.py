"""Does thinking depth buy anything? Three efforts, one run each."""

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

PRICE_IN, PRICE_OUT = 2.00, 10.00

client = Anthropic()
expenses, bookings = load_data()
validate(expenses, bookings)
m = materiality(expenses)
regions = region_rollup(expenses)
system, user = build_prompt(expenses, bookings, m, regions)

for effort in ("low", "medium", "high"):
    text, usage = get_commentary(client, system, user, effort=effort)
    thinking = usage.output_tokens_details.thinking_tokens
    visible = usage.output_tokens - thinking
    cost = usage.input_tokens / 1e6 * PRICE_IN + usage.output_tokens / 1e6 * PRICE_OUT
    (OUTPUT_DIR / f"commentary_{effort}.md").write_text(text, encoding="utf-8")
    print(
        f"{effort:7} {usage.output_tokens:>6,} out "
        f"({thinking:>6,} thinking + {visible:>5,} visible)  ${cost:.4f}"
    )