"""analyst_agent_v1: ask questions about the March expense file.

Claude chooses which query to run; pandas runs it. The model never
computes a number — it decides which number to ask for.
"""

from anthropic import Anthropic

from commentary_bot import load_data, validate

import ast
import json
import operator
import time
import sys
from datetime import datetime
from pathlib import Path

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4000
MAX_TURNS = 8

OUTPUT_DIR = Path(__file__).parent / "output"

# Sonnet 5 list price, verified 2026-08-25. Thinking tokens bill as output.
PRICE_IN_PER_MTOK = 2.00
PRICE_OUT_PER_MTOK = 10.00

SYSTEM = (
    "You are a senior FP&A analyst answering questions about a single "
    "department's March 2026 expense file. Retrieve every figure with the "
    "tools; never state a number you have not retrieved or calculated with a "
    "tool. If the data cannot answer the question, say so plainly and say what "
    "would be needed. Report variances using the file's convention: positive "
    "is favorable."
    "State the interpretation you used whenever a question is ambiguous about which"
    "measure, which grouping, or which time period — and always state the units of"
    "any figure you compute."
)

# ---------- tools ----------
def build_tools(expenses):
    """Tool schemas, with enums drawn from the data itself."""
    regions = expenses["Region"].unique().tolist()
    accounts = sorted(expenses["Account"].unique().tolist())
    categories = sorted(expenses["Category"].unique().tolist())
    columns = ["Budget", "Actual", "Variance", "Variance_Pct",
               "Prior_Year_Actual", "YoY_Pct"]

    return [
        {
            "name": "query_expenses",
            "description": (
                "Query the March 2026 departmental expense file for Sales & Marketing. "
                "Returns matching rows as CSV at the Region x Account grain, one row "
                "per combination, 42 rows in total when unfiltered. Use this whenever "
                "you need actual figures — never estimate a number you could retrieve. "
                "Filters combine with AND; omit a filter to include all of its values. "
                "Sign convention: Variance = Budget - Actual, so a POSITIVE variance is "
                "FAVORABLE (underspend) and a negative variance is unfavorable "
                "(overspend). Prior_Year_Actual is the same month last year, not a "
                "full-year or year-to-date figure. This dataset contains no forecast, "
                "no budget phasing, and no year-to-date columns."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string", "enum": regions,
                        "description": "Restrict to one region. Omit for all regions.",
                    },
                    "account": {
                        "type": "string", "enum": accounts,
                        "description": "Restrict to one GL account.",
                    },
                    "category": {
                        "type": "string", "enum": categories,
                        "description": (
                            "Restrict to one category. NOTE: categories group several "
                            "accounts. The 'Travel & Entertainment' CATEGORY contains "
                            "both the 'Travel & Entertainment' and 'Meals & "
                            "Entertainment' ACCOUNTS."
                        ),
                    },
                    "sort_by": {
                        "type": "string", "enum": columns,
                        "description": "Sort descending by this column before applying limit.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Return at most this many rows. Omit for all.",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "calculate",
            "description": (
                "Evaluate a pure arithmetic expression and return the numeric "
                "result. Use this for any multiplication, division, percentage, "
                "annualization or growth-rate computation rather than doing the "
                "arithmetic yourself. Accepts digits, decimal points, parentheses "
                "and the operators + - * / ** only; variable names, function calls "
                "and cell references are rejected. Example: to annualize a monthly "
                "overrun of 20,000, pass '20000 * 12'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "e.g. '(36000 - 15000) / 15000 * 100'",
                    },
                },
                "required": ["expression"],
            },
        },
    ]


def dispatch(name, args, expenses):
    """Route a tool_use block to real code. Returns (result_string, is_error)."""
    try:
        if name == "query_expenses":
            return query_expenses(expenses, **args)
        if name == "calculate":
            return calculate(**args)
        return f"Unknown tool: {name}", True
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}", True

def query_expenses(expenses, region=None, account=None, category=None,
                   sort_by=None, limit=None):
    """Filter the expense file and return matching rows as CSV text."""
    df = expenses
    if region:
        df = df[df["Region"] == region]
    if account:
        df = df[df["Account"] == account]
    if category:
        df = df[df["Category"] == category]
    if sort_by:
        df = df.sort_values(sort_by, ascending=False)
    if limit:
        df = df.head(int(limit))

    if df.empty:
        return ("0 rows matched. This file covers March 2026 only, "
                "4 regions and 12 accounts."), True

    return f"{len(df)} row(s):\n" + df.to_csv(index=False), False


# ---------- calculate ----------
# Arithmetic only, by construction. ast.parse builds a tree WITHOUT executing
# anything; _eval_node then walks that tree and only knows how to handle three
# node types. A function call parses to ast.Call, a variable to ast.Name --
# neither has a branch here, so both raise. Nothing is forbidden; everything
# outside the allowlist is simply absent.
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node):
    """Walk the parse tree, permitting arithmetic and nothing else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


def calculate(expression):
    """Arithmetic only. No names, no calls, no attributes, no subscripts."""
    tree = ast.parse(expression, mode="eval")
    return f"{_eval_node(tree.body)}", False

def run_agent(client, question, expenses, tools, max_turns=MAX_TURNS, verbose=True):
    """Ask a question; let Claude call tools until it can answer.

    Returns (answer, messages, trace). The trace records every tool call and
    every turn's token usage -- it is the artifact that makes a run auditable.
    """
    messages = [{"role": "user", "content": question}]
    trace = {"question": question, "turns": [], "calls": []}

    for turn in range(1, max_turns + 1):
        if verbose:
            show(messages, turn)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            tools=tools,
            messages=messages,
        )

        trace["turns"].append({
            "turn": turn,
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "thinking_tokens": getattr(
                msg.usage.output_tokens_details, "thinking_tokens", 0),
            "stop_reason": msg.stop_reason,
        })

        # 1. Append the assistant turn EXACTLY as it came back.
        messages.append({"role": "assistant", "content": msg.content})

        # 2. No tool requested -> the model is done talking.
        if msg.stop_reason != "tool_use":
            answer = "".join(b.text for b in msg.content if b.type == "text")
            return answer, messages, trace

        # 3. Execute EVERY tool_use block in this turn.
        results = []
        for block in msg.content:
            if block.type != "tool_use":
                continue
            started = time.perf_counter()
            output, failed = dispatch(block.name, block.input, expenses)
            elapsed_ms = (time.perf_counter() - started) * 1000

            trace["calls"].append({
                "turn": turn,
                "tool": block.name,
                "input": block.input,
                "ok": not failed,
                "ms": round(elapsed_ms, 1),
                "result_chars": len(str(output)),
                "result_preview": str(output)[:200],
            })
            if verbose:
                flag = "ERR " if failed else ""
                arg_str = ", ".join(f"{k}={v!r}" for k, v in block.input.items()) or "-"
                print(f"  >> [{turn}] {flag}{block.name}({arg_str}) "
                      f"-> {len(str(output))} chars, {elapsed_ms:.1f}ms")

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
                "is_error": failed,
            })

        # 4. Send them all back in ONE user message.
        messages.append({"role": "user", "content": results})

    raise RuntimeError(f"Agent did not converge in {max_turns} turns.")

def show(messages, turn):
    """Print the whole conversation as the API sees it, every turn."""
    print(f"\n{'=' * 64}\nTURN {turn}  —  {len(messages)} messages in context\n{'=' * 64}")
    for m in messages:
        blocks = m["content"]
        if isinstance(blocks, str):
            print(f"  {m['role']:9} text        : {blocks[:90]}")
            continue
        for b in blocks:
            btype = getattr(b, "type", None) or b.get("type")
            if btype == "text":
                print(f"  {m['role']:9} text        : {getattr(b, 'text', '')[:90]}")
            elif btype == "thinking":
                text = getattr(b, "thinking", "") or ""
                sig = getattr(b, "signature", "") or ""
                print(f"  {m['role']:9} thinking    : {len(text)} chars text, {len(sig)} chars signature")
            elif btype == "tool_use":
                print(f"  {m['role']:9} tool_use    : {b.name}({b.input})")
            elif btype == "tool_result":
                body = str(b.get("content"))[:80].replace("\n", " | ")
                print(f"  {m['role']:9} tool_result : {body}")


def summarize(trace):
    """Price the whole loop. Input is re-sent every turn; this is where you see it."""
    tin = sum(t["input_tokens"] for t in trace["turns"])
    tout = sum(t["output_tokens"] for t in trace["turns"])
    cost = tin / 1e6 * PRICE_IN_PER_MTOK + tout / 1e6 * PRICE_OUT_PER_MTOK
    trace["totals"] = {
        "turns": len(trace["turns"]),
        "tool_calls": len(trace["calls"]),
        "input_tokens": tin,
        "output_tokens": tout,
        "cost_usd": round(cost, 5),
    }
    return trace["totals"]


def save_trace(trace, label="run"):
    OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTPUT_DIR / f"trace_{label}_{stamp}.json"
    path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    return path


def main():
    expenses, bookings = load_data()
    validate(expenses, bookings)

    tools = build_tools(expenses)
    client = Anthropic()

    question = sys.argv[1] if len(sys.argv) > 1 else "Which region's travel expense grew fastest?"
    label = sys.argv[2] if len(sys.argv) > 2 else "q1"
    answer, messages, trace = run_agent(client, question, expenses, tools)

    totals = summarize(trace)
    path = save_trace(trace, label=label)

    print(f"\n{'=' * 64}\nANSWER\n{'=' * 64}\n{answer}\n")
    print(f"{'=' * 64}\nRUN SUMMARY\n{'=' * 64}")
    print(f"  turns        : {totals['turns']}")
    print(f"  tool calls   : {totals['tool_calls']}")
    print(f"  input tokens : {totals['input_tokens']:,}  (re-sent every turn)")
    print(f"  output tokens: {totals['output_tokens']:,}")
    print(f"  cost         : ${totals['cost_usd']:.5f}")
    print(f"  trace        : {path.name}")


if __name__ == "__main__":
    main()
