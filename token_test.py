"""One-off: how much does the serialization format cost?"""

import pandas as pd
from anthropic import Anthropic

from commentary_bot import EXPENSE_FILE, MODEL

client = Anthropic()
df = pd.read_csv(EXPENSE_FILE)

variants = {
    "to_csv": df.to_csv(index=False).strip(),
    "to_markdown": df.to_markdown(index=False).strip(),
    "to_string": df.to_string(index=False).strip(),
}

for name, text in variants.items():
    n = client.messages.count_tokens(
        model=MODEL, messages=[{"role": "user", "content": text}]
    ).input_tokens
    print(f"{name:12} {n:>6,} tokens  {len(text):>6,} chars")