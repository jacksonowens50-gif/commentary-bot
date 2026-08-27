"""Scratch: what does a tool_use response actually look like?

Deliberately does NOT implement the tool. The point is to see the request
Claude emits, not to satisfy it.
"""

from anthropic import Anthropic

client = Anthropic()

TOOLS = [
    {
        "name": "get_row_count",
        "description": (
            "Return the number of rows in the March 2026 expense dataset. "
            "Use this when the user asks how much data there is, or when you "
            "need to know the size of the file before deciding how to filter it. "
            "Takes no parameters and always returns a single integer."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }
]

msg = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1000,
    tools=TOOLS,
    messages=[{"role": "user", "content": "How many rows are in the expense file?"}],
)

print("stop_reason:", msg.stop_reason)
print()
for block in msg.content:
    print("---", block.type, "---")
    print(block)
    print()
print(msg.usage)

bare = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1000,
    messages=[{"role": "user", "content": "How many rows are in the expense file?"}],
)
print("\nNo tools:", bare.usage.input_tokens, "input tokens")
print("With tools:", msg.usage.input_tokens, "input tokens")
print("Tool overhead:", msg.usage.input_tokens - bare.usage.input_tokens)

TINY = [{
    "name": "get_row_count",
    "description": "Row count.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}]

tiny = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1000,
    tools=TINY,
    messages=[{"role": "user", "content": "How many rows are in the expense file?"}],
)
print("\nTiny tool:", tiny.usage.input_tokens, "input tokens")
print("Fixed floor (approx):", tiny.usage.input_tokens - bare.usage.input_tokens)
print("Cost of your 4-sentence description:", msg.usage.input_tokens - tiny.usage.input_tokens)

# ---------- close the loop by hand ----------
tool_use = next(b for b in msg.content if b.type == "tool_use")

messages = [
    {"role": "user", "content": "How many rows are in the expense file?"},
    {"role": "assistant", "content": msg.content},          # <-- unmodified
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use.id, "content": "42"},
    ]},
]

final = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1000,
    tools=TOOLS,
    messages=messages,
)

print("\n--- after returning the result ---")
print("stop_reason:", final.stop_reason)
for b in final.content:
    print(b.type, ":", getattr(b, "text", b))
print(final.usage)