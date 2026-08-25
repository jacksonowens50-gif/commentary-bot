from anthropic import Anthropic

client = Anthropic()

msg = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=300,
    system="You are a senior FP&A analyst. Answer in one sentence.",
    messages=[{"role": "user", "content": "What is a favorable expense variance?"}],
)

print(msg.content[0].text)
print(msg.usage)
print(msg.stop_reason)