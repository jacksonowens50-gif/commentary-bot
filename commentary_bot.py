"""commentary-bot: budget-vs-actual CSV in, variance commentary out.

Deterministic numbers are computed here and handed to the model as given.
The model's job is judgment and prose; arithmetic is a solved problem and
you don't pay $10/MTok for a solved problem.
"""

import os
from pathlib import Path
import pandas as pd
from anthropic import Anthropic


## LLM Model
MODEL = "claude-sonnet-5"

##LLM Tokens
MAX_TOKENS = 10000

# ---------- paths ----------
# Anchored to this file, not the working directory. A scheduled job does not
# start where you think it does.
PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
EXPENSE_FILE = DATA_DIR / "budget-vs-actual.csv"
BOOKINGS_FILE = DATA_DIR / "bookings-memo.csv"
PROMPT_DIR = PROJECT_DIR / "prompts"
OUTPUT_DIR = PROJECT_DIR / "output"

# ---------- what this run is about ----------
DEPARTMENT = "Sales & Marketing"
PERIOD = "March 2026, month only (no YTD provided)"
GRAIN = "region-account"

# ---------- materiality ----------
# Both conditions, not either: "or" qualifies 19 of 42 lines, "and" qualifies 6.
# The threshold you choose is the commentary you get.
THRESHOLD_DOLLARS = 10_000
THRESHOLD_PCT = 10.0          # percent, not a fraction

# ---------- sign conventions ----------
# Expenses: Variance = Budget - Actual   (positive = favorable)
# Bookings: Variance = Actual - Budget   (positive = favorable)
# Same meaning, opposite arithmetic, two files. This is the single most common
# place a commentary gets a sign backwards.
TOLERANCE = 0.01


class DataError(Exception):
    """Raised when the source files don't tie to themselves."""


def load_data():
    """Read both fixtures. No interpretation here."""
    return pd.read_csv(EXPENSE_FILE), pd.read_csv(BOOKINGS_FILE)


def validate(expenses, bookings):
    """Fail before the first API call, not after it."""
    problems = []

    off = ((expenses["Budget"] - expenses["Actual"]) - expenses["Variance"]).abs()
    if (off > TOLERANCE).any():
        problems.append(
            f"{int((off > TOLERANCE).sum())} expense row(s) where "
            "Variance != Budget - Actual"
        )

    off = ((bookings["Actual"] - bookings["Budget"]) - bookings["Variance"]).abs()
    if (off > TOLERANCE).any():
        problems.append(
            f"{int((off > TOLERANCE).sum())} bookings row(s) where "
            "Variance != Actual - Budget"
        )

    if problems:
        raise DataError("source data is inconsistent:\n  " + "\n  ".join(problems))


def materiality(expenses):
    """Split the file into what gets explained and what gets aggregated."""
    material = expenses[
        (expenses["Variance"].abs() > THRESHOLD_DOLLARS)
        & (expenses["Variance_Pct"].abs() > THRESHOLD_PCT)
    ]
    total = expenses["Variance"].sum()
    material_sum = material["Variance"].sum()

    return {
        "material": material,
        "total": total,
        "material_sum": material_sum,
        "residual": total - material_sum,
        "immaterial_count": len(expenses) - len(material),
    }


def region_rollup(expenses):
    """Net says what it cost. Gross says how much happened. They diverge."""
    regions = expenses.groupby("Region").agg(
        budget=("Budget", "sum"),
        actual=("Actual", "sum"),
        net=("Variance", "sum"),
        gross=("Variance", lambda s: s.abs().sum()),
    )
    regions["net_pct"] = regions["net"] / regions["budget"] * 100
    return regions


def get_api_key():
    """Read ANTHROPIC_API_KEY from the environment, or stop with a clear message."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Create a key at "
            "https://console.anthropic.com/settings/keys and set it with:\n"
            '  [Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")\n'
            "Then open a NEW terminal — environment variables are read at process start."
        )
    return key

def build_prompt(expenses, bookings, m, regions):
    """Fill the house template. The prompt is data, not code."""
    system = (PROMPT_DIR / "system.txt").read_text(encoding="utf-8")
    template = (PROMPT_DIR / "user_template.txt").read_text(encoding="utf-8")

    region_table = (
        regions.reset_index()
        .rename(
            columns={
                "budget": "Budget",
                "actual": "Actual",
                "net": "Net_Variance",
                "gross": "Gross_Variance",
                "net_pct": "Net_Pct",
            }
        )
        .to_csv(index=False, float_format="%.1f")
        .strip()
    )

    user = template.format(
        department=DEPARTMENT,
        period=PERIOD,
        threshold_dollars=f"${THRESHOLD_DOLLARS:,.0f}",
        threshold_pct=f"{THRESHOLD_PCT:.0f}%",
        grain=GRAIN,
        expense_rows=expenses.to_csv(index=False).strip(),
        volume_rows=bookings.to_csv(index=False).strip(),
        total_variance=f"{m['total']:,.0f}",
        material_count=len(m["material"]),
        material_sum=f"{m['material_sum']:,.0f}",
        residual=f"{m['residual']:,.0f}",
        region_rollup=region_table,
    )
    return system, user

def count_input_tokens(client, system, user):
    """Free and rate-limited. Nothing is generated and nothing is billed."""
    r = client.messages.count_tokens(
        model=MODEL,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.input_tokens

def get_commentary(client, system, user, effort="high"):
    """Send the prompt. Truncation is a silent failure, so make it loud."""
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort},
    )
    if msg.stop_reason == "max_tokens":
        raise RuntimeError(
            f"Output truncated at {msg.usage.output_tokens} tokens. "
            f"Raise MAX_TOKENS above {MAX_TOKENS}."
        )
    text = "".join(block.text for block in msg.content if block.type == "text")
    if not text.strip():
        raise RuntimeError(f"No text block in response (stop_reason={msg.stop_reason})")
    return text, msg.usage

def main():
    get_api_key()               # fail fast: no point loading data without a key

    expenses, bookings = load_data()
    validate(expenses, bookings)

    m = materiality(expenses)
    regions = region_rollup(expenses)

    print(f"{len(expenses)} expense rows | total variance {m['total']:,.0f}")
    print(
        f"{len(m['material'])} lines clear materiality "
        f"(>${THRESHOLD_DOLLARS:,.0f} AND >{THRESHOLD_PCT:.0f}%), "
        f"summing to {m['material_sum']:,.0f}"
    )
    print(f"Residual across {m['immaterial_count']} other lines: {m['residual']:,.0f}\n")

    print(
        m["material"][["Region", "Account", "Variance", "Variance_Pct"]]
        .to_string(index=False)
    )
    print()
    print(regions.to_string(float_format=lambda x: f"{x:,.1f}"))

    system, user = build_prompt(expenses, bookings, m, regions)
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "last_prompt.txt").write_text(
        f"===== SYSTEM =====\n{system}\n===== USER =====\n{user}",
        encoding="utf-8",
    )
    print(f"\nPrompt written to {OUTPUT_DIR / 'last_prompt.txt'}")

    client = Anthropic()
    tokens = count_input_tokens(client, system, user)
    print(f"Input tokens: {tokens:,}")

    text, usage = get_commentary(client, system, user)
    (OUTPUT_DIR / "commentary.md").write_text(text, encoding="utf-8")
    print(f"\n{usage.input_tokens:,} in / {usage.output_tokens:,} out\n")
    print(text)


if __name__ == "__main__":
    main()