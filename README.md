# commentary-bot

Reads a budget-vs-actual CSV, computes materiality and the region roll-up in
pandas, sends the result to Claude with a versioned house prompt, and returns
variance commentary written to `output/commentary.md`.

Project 3 of the learning track. The prompt it sends was developed and scored in
`learning-journal/ai/prompt-engineering-experiments.md` (Day 19); this repo is
that prompt made operational, with the arithmetic taken away from the model.

## What it produces

| Output | Contents |
|---|---|
| `output/commentary.md` | The variance commentary |
| `output/last_prompt.txt` | The fully assembled prompt, for inspection before spending |

## Setup

Requires Python 3.11+ and an [Anthropic API key](https://console.anthropic.com/settings/keys).

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
```

Then open a new terminal — environment variables are read at process start. The
key is read from the environment and is never written to disk by this project.

## Run

```powershell
python commentary_bot.py
```

Two scratch scripts sit alongside it and are not part of the pipeline:
`token_test.py` compares serialization formats using the free token-counting
endpoint, and `cost_test.py` runs the prompt five times and reports the cost
distribution.

## Cost analysis

Measured on the frozen March fixture (42 expense rows, 4 bookings rows), Claude
Sonnet 5, 2026-08-25. Prices verified the same day at
https://platform.claude.com/docs/en/about-claude/pricing — $2.00/MTok input,
$10.00/MTok output. Thinking tokens bill as output.

### Input is a design decision; output is a distribution

Input is fixed at **3,049 tokens ($0.0061)** every run — same prompt, same data,
and it can be counted exactly before a cent is spent. Output varied **4,495 to
6,475 across five identical runs**, a 44% spread. You can know your input cost
in advance and you can never know your output cost in advance, which is why any
single-point estimate for this workload is indefensible.

Serialization was measured, not assumed:

| Format | Tokens | Chars |
|---|---:|---:|
| `to_csv` | 1,711 | 3,204 |
| `to_markdown` | 2,385 | 6,775 |
| `to_string` | 1,730 | 5,496 |

Markdown costs 39% more than CSV with no measured quality benefit. Note that
`to_string` carries 71% more characters than CSV but only 1.1% more tokens —
runs of whitespace collapse, pipes and dashes do not. **Prompt size cannot be
estimated from file size.** CSV was chosen, but the saving at this volume is
about **$0.81/year**: immaterial, and stated as such rather than presented as an
optimization.

### Effort is the only lever that matters, and it is cheap

Claude Sonnet 5 uses adaptive thinking, steered by `output_config.effort`.

| Effort | Thinking | Visible | Cost/run | 50 depts, annual |
|---|---:|---:|---:|---:|
| low | 468 | 1,326 | $0.0240 | $14.40 |
| medium | 1,476 | 1,615 | $0.0370 | $22.20 |
| high | 3,595 | 1,665 | $0.0587 | $35.22 |

Visible output is nearly constant across all three — a 26% spread on the
document that actually ships. The cost difference is **entirely reasoning that
is generated, billed, and discarded**: roughly 70% of the bill at high effort.
You are not buying more words, you are buying better ones.

Quality is not constant. At low effort the model reached for the sanctioned
"driver not determinable from the data provided" escape hatch **three times
versus once at high**, using it in place of a timing inference the prior-year
column supported; it also misattributed region-level figures to a three-line
grouping. High effort produced the only correct and complete treatment of the
volume-driven commissions variance, naming all four regions, citing the flat
2.0% rate as evidence, and instructing the reader not to net it against the
unfavorable items.

An escape-hatch clause that reduces fabrication at high effort licenses
avoidance at low effort. The clause is in the prompt; the behaviour is set by a
parameter that is not.

**Downgrading from high to low saves $0.035 per commentary — 1.7 seconds of
analyst time at a $75/hr loaded rate.** Any effort reduction that adds two
seconds of review loses money. The cost-optimal setting is maximum quality, and
it is not close.

### The number that actually matters

| | Per department | 50 depts, annual |
|---|---:|---:|
| Inference, high effort | $0.059 | $35 |
| Review @ 10 min, $75/hr loaded | $12.50 | $7,500 |
| Manual drafting today @ 45 min | $56.25 | $33,750 |

Inference is **0.5% of the review line** and 0.1% of the manual baseline. The
scoping question is therefore never "what does the model cost." It is:

> **How much of the review can be retired, and what error rate decides that?**

Measured here: **2 of 6 outputs contained at least one incorrect figure** — a
double-counted reconciliation in one, a wrong line count and a bad "more than
explained by" claim in another. Neither was a fabricated driver; the underlying
analysis was correct in every run. The errors were arithmetic and attribution in
summary paragraphs — and both of those numbers are already computed in
`commentary_bot.py`, so the reconciliation sentence could be assembled in Python
and appended, leaving the model to write only what requires judgment.

At 600 runs/year, a 33% defect rate is roughly **200 commentaries a year with a
wrong number in them**, none of which raise an exception and all of which read
well. That is the entire argument for why review time, not token spend, is the
line item that decides whether this project has a return.

n=6. The direction is actionable; the rate is not quotable.

### Levers not yet implemented

- **Prompt caching** — the system prompt and three worked examples are
  byte-identical across departments. Cache writes cost 1.25x input, reads 0.1x,
  breaking even after one hit. The saving is bounded by the constant fraction of
  the prompt, which has not been measured here.
- **Batch API** — 50% off both directions. Monthly departmental commentary has
  no latency requirement, which is the definitional batch workload. Stacks with
  caching.

Both are worth under $20/year at this volume. They are recorded for scale, not
recommended for this deployment.

## Design notes

**Deterministic numbers are computed, not generated.** Total variance,
materiality, the residual, and the region net-vs-gross roll-up are calculated in
pandas and passed to the model inside a `<computed>` block it is told not to
recalculate. Day 19 scored twelve outputs and every one failed the
reconciliation criterion; no wording fixed it. Four lines of pandas did. The
general rule: anything deterministic should be computed, not generated — you do
not pay $10/MTok for a solved problem.

**Gross movement is passed in, not derived.** The house prompt asks the model to
compare each region's net variance against its gross absolute movement, but the
first assembled prompt never supplied the gross figures — it was asking for the
one thing the model reliably gets wrong. `region_rollup()` computes them and
they now travel in the `<computed>` block. EMEA nets to $(6,700) while carrying
$109,300 of gross movement, the largest of any region; that divergence is
invisible on any standard report and is the finding the roll-up exists to
surface.

**The prompt is a file, not a string.** `prompts/system.txt` and
`prompts/user_template.txt` are filled by `build_prompt()`. A wording change
shows up as a one-line diff in a pull request, and the controller with an
opinion about the materiality sentence can edit it without touching Python.

**The dangerous failure returns HTTP 200.** A commentary truncated at
`max_tokens` raises nothing — it arrives as a successful response that stops
mid-sentence and reads fine until it doesn't. `get_commentary()` checks
`stop_reason` and raises. The failures that raise are the cheap ones; budget
your attention for the ones that succeed.

**Response content is addressed by type, not by index.** `msg.content` is a list
of blocks and their order is not stable — on this model a thinking block often
precedes the text. Filtering on `block.type == "text"` is correct today and
stays correct when tool-use blocks appear in the same list.

**Source data is validated before the first API call.** `validate()` recomputes
each file's variance column under its own sign convention — budget less actual
for expenses, actual less budget for bookings — and refuses to proceed on a
mismatch. Two files, two conventions, one prompt: that is the most common place
a commentary gets a sign backwards, so it is checked rather than trusted.

**Retries are configured, not written.** The SDK already retries connection
errors, timeouts, 429 and 5xx with exponential backoff. It deliberately does not
retry 400 or 401, which are request bugs rather than network events. Knowing
what a library already does is cheaper than reimplementing it.

## Limitations

- One dataset, one department, one model, one day. n=6 on the defect rate, n=1
  per effort level. Direction is actionable; magnitudes are not quotable.
- Prices are hardcoded and verified 2026-08-25. They move, and $/MTok is not
  comparable across model generations — the 4.7+ tokenizer produces roughly 30%
  more tokens for the same text, so models must be compared on cost per
  completed task, not cost per token.
- The three worked examples in the user template are manufacturing-flavoured.
  Swapping them for a client's own prior commentary is most of the value in any
  engagement and takes about twenty minutes.
- The fixture withholds YTD and the forward plan on purpose, so timing calls
  remain inferences that no prompt at any effort level can close. That is a
  context problem, not a prompting problem, and it is what RAG is for.
- Materiality is computed in code and the six qualifying lines are named in the
  prompt. That fixed the reconciliation but may anchor the model onto the named
  set — the first run missed the sub-threshold commissions story entirely. Worth
  testing against a version that states the threshold without naming the lines.
