import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

client = Anthropic()  # Reads ANTHROPIC_API_KEY from env automatically

response = client.messages.create(
    model="claude-haiku-4-5", 
    max_tokens=24,
    temperature=0,
    system="You are a concise financial analyst.",
    messages=[
        {"role": "user", "content": "In 2 sentences, what is a P/E ratio?"}
    ],
)

print(response.content[0].text)
print(f"\n--- Usage: {response.usage.input_tokens} in, {response.usage.output_tokens} out")