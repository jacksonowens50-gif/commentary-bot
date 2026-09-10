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

SYSTEM_V2 = SYSTEM + (
    " You are working toward a goal, not answering a single question. Work "
    "iteratively: retrieve what you need, record each finding with save_finding as "
    "soon as you have it, and continue until you can support your conclusions. When "
    "the analysis is complete, call write_memo to submit. Prefer targeted queries "
    "over pulling the whole file. If the data cannot support part of the goal, say "
    "so in the memo rather than working around it."
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
            "name": "save_finding",
            "description": (
                "Record one finding so it survives outside the conversation. Call this "
                "immediately after you retrieve a figure worth keeping, before moving on "
                "to the next query. Keep 'detail' to one sentence and put the retrieved "
                "figures in it -- a finding without its numbers is not a finding."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string",
                              "description": "Short name, e.g. 'EMEA Salaries underspend'"},
                    "detail": {"type": "string",
                               "description": "One sentence containing the retrieved figures."},
                    "dollars": {"type": "number",
                                "description": "The variance in dollars, if the finding has one."},
                },
                "required": ["label", "detail"],
            },
        },
                {
            "name": "write_memo",
            "description": (
                "Submit the finished memo and end the analysis. Call this exactly once, "
                "when every figure the memo cites has been retrieved with query_expenses "
                "or computed with calculate. Do not call it to report partial progress -- "
                "if you still need a number, retrieve it first."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "memo_markdown": {"type": "string", "description": "The memo, in markdown."},
                    "anomaly_definition": {
                        "type": "string",
                        "description": "The definition of 'anomaly' you used, in one sentence.",
                    },
                },
                "required": ["memo_markdown", "anomaly_definition"],
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


def dispatch(name, args, expenses,trace):
    """Route a tool_use block to real code. Returns (result_string, is_error)."""
    try:
        if name == "query_expenses":
            return query_expenses(expenses, **args)
        if name == "calculate":
            return calculate(**args)
        if name == "save_finding":
            return save_finding(trace, **args)
        if name == "write_memo":
            return write_memo(trace, **args)
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

def save_finding(trace, label, detail, dollars=None):
    """Record one finding outside the conversation. Returns the running count."""
    trace["findings"].append({"label": label, "detail": detail, "dollars": dollars})
    n = len(trace["findings"])
    return f"Saved finding {n}: {label}. {n} finding(s) recorded so far.", False

def write_memo(trace, memo_markdown, anomaly_definition):
    """Terminal tool. Records the memo and ends the run."""
    trace["memo"] = memo_markdown
    trace["definition"] = anomaly_definition
    return f"Memo received ({len(memo_markdown)} chars). Analysis complete.", False

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

class Budget:
    """Three ceilings and a reason. Checked once per turn, before the API call."""

    def __init__(self, max_turns=12, max_tool_calls=25, max_cost_usd=0.50):
        self.max_turns = max_turns
        self.max_tool_calls = max_tool_calls
        self.max_cost_usd = max_cost_usd

    def exceeded(self, turn, tool_calls, cost_usd):
        """Return a human-readable stop reason, or None to keep going."""
        if turn > self.max_turns:
            return f"turn limit: {self.max_turns} turns"
        if tool_calls >= self.max_tool_calls:
            return f"tool-call limit: {tool_calls} calls"
        if cost_usd >= self.max_cost_usd:
            return f"cost limit: ${cost_usd:.4f} of ${self.max_cost_usd:.2f}"
        return None

def turn_cost(usage):
    """Dollars for one API response. Same constants summarize() uses."""
    return (usage.input_tokens / 1e6 * PRICE_IN_PER_MTOK
            + usage.output_tokens / 1e6 * PRICE_OUT_PER_MTOK)

def run_goal(client, goal, expenses, tools, budget=None, verbose=False, label="goal"):
    """Pursue a goal until the agent submits a memo or a ceiling stops it.

    Always returns a result dict. Never raises for a stopping condition.
    """
    budget = budget or Budget()
    messages = [{"role": "user", "content": goal}]
    trace = {"goal": goal, "turns": [], "calls": [], "findings": []}
    result = {"memo": None, "definition": None, "stopped_because": None, "trace": trace}

    spent = 0.0
    seen = {}
    turn = 0

    try:
        while True:
            turn += 1
            reason = budget.exceeded(turn, len(trace["calls"]), spent)
            if reason:
                result["stopped_because"] = f"ceiling: {reason}"
                return result

            if verbose:
                show(messages, turn)

            msg = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_V2,
                tools=tools,
                messages=messages,
            )
            spent += turn_cost(msg.usage)

            trace["turns"].append({
                "turn": turn,
                "messages_in_context": len(messages),
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
                "stop_reason": msg.stop_reason,
                "cum_cost_usd": round(spent, 5),
            })

            messages.append({"role": "assistant", "content": msg.content})

            if msg.stop_reason != "tool_use":
                result["answer"] = "".join(b.text for b in msg.content if b.type == "text")
                result["stopped_because"] = "model stopped without submitting"
                return result

            results = []
            for block in msg.content:
                if block.type != "tool_use":
                    continue

                sig = (block.name, json.dumps(block.input, sort_keys=True))
                if sig in seen:
                    output = (f"You already ran this exact call on turn {seen[sig]}. "
                              "Re-read that result above rather than repeating it, or "
                              "query something different.")
                    failed, elapsed_ms, repeat = True, 0.0, True
                else:
                    seen[sig] = turn
                    started = time.perf_counter()
                    output, failed = dispatch(block.name, block.input, expenses, trace)
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    repeat = False

                trace["calls"].append({
                    "turn": turn,
                    "tool": block.name,
                    "input": block.input,
                    "ok": not failed,
                    "repeat": repeat,
                    "ms": round(elapsed_ms, 1),
                    "result_chars": len(str(output)),
                    "result_preview": str(output)[:200],
                })

                if verbose or True:  # one line per call, always
                    flag = "REPEAT " if repeat else ("ERR " if failed else "")
                    arg_str = ", ".join(f"{k}={v!r}" for k, v in block.input.items()) or "-"
                    print(f"  >> [{turn}] {flag}{block.name}({arg_str[:80]}) "
                          f"-> {len(str(output))} chars")

                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": failed,
                })

            messages.append({"role": "user", "content": results})

            if trace.get("memo"):
                result["memo"] = trace["memo"]
                result["definition"] = trace["definition"]
                result["stopped_because"] = "submitted"
                return result

    except Exception as exc:
        result["stopped_because"] = f"crashed: {type(exc).__name__}: {exc}"
        raise
    finally:
        trace["stopped_because"] = result["stopped_because"]
        if trace["turns"]:
            trace["totals"] = summarize(trace)
        result["trace_path"] = str(save_trace(trace, label=label))



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


GOAL = ("Analyse the March 2026 expense file, identify the three biggest anomalies, "
        "and write me a short memo for the VP of Sales explaining what happened and "
        "what to check next.")


def main():
    expenses, bookings = load_data()
    validate(expenses, bookings)

    tools = build_tools(expenses)
    client = Anthropic()

    goal = sys.argv[1] if len(sys.argv) > 1 else GOAL
    label = sys.argv[2] if len(sys.argv) > 2 else "goal"

    result = run_goal(client, goal, expenses, tools, label=label)
    trace = result["trace"]

    print(f"\n{'=' * 64}\nSTOPPED: {result['stopped_because']}\n{'=' * 64}")

    if result["memo"]:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = OUTPUT_DIR / f"memo_{label}_{stamp}.md"
        path.write_text(
            f"<!-- definition used: {result['definition']} -->\n\n{result['memo']}",
            encoding="utf-8",
        )
        print(f"\nDEFINITION: {result['definition']}\n")
        print(result["memo"])
        print(f"\nmemo  : {path.name}")
    elif result.get("answer"):
        print("\nNo memo submitted. Final text was:\n")
        print(result["answer"][:1500])

    print(f"trace : {Path(result['trace_path']).name}")
    print(f"turns : {len(trace['turns'])}   tool calls: {len(trace['calls'])}   "
          f"findings: {len(trace['findings'])}   "
          f"cost: ${trace['turns'][-1]['cum_cost_usd']:.5f}" if trace["turns"] else "")

if __name__ == "__main__":
    main()
