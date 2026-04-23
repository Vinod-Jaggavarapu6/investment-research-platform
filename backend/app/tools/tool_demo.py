import os
import json
from dotenv import load_dotenv
from anthropic import Anthropic
from langsmith.wrappers import wrap_anthropic

load_dotenv()

client = wrap_anthropic(Anthropic())


# client = Anthropic()

RESPONSE_LOG = "responses.json"

def log_response(iteration: int, response):
    entry = {
        "iteration": iteration,
        "id": response.id,
        "model": response.model,
        "stop_reason": response.stop_reason,
        "usage": response.usage.model_dump(),
        "content": [block.model_dump() for block in response.content],
    }
    try:
        with open(RESPONSE_LOG, "r") as f:
            log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log = []
    log.append(entry)
    with open(RESPONSE_LOG, "w") as f:
        json.dump(log, f, indent=2)


def get_stock_price(ticker: str) -> str:
    mock_prices = {"AAPL": 182.34, "NVDA": 451.12, "MSFT": 378.90}
    price = mock_prices.get(ticker.upper())
    if price is None:
        return f"No data for {ticker}"
    return f"{ticker.upper()}: ${price}"


tools = [
    {
        "name": "get_stock_price",
        "description": "Get the current stock price for a ticker symbol.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker like 'AAPL'"}
            },
            "required": ["ticker"],
        },
    }
]

# --- 3. The agent loop ---
messages = [{"role": "user", "content": "What are the prices of AAPL, NVDA, and MSFT?"}]


MAX_ITERATIONS = 5
for iteration in range(MAX_ITERATIONS):

    print(f"\n--- Iteration {iteration + 1} ---")

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        tools=tools,
        messages=messages,
        temperature=0.2,
        system = "You are a helpful assistant that can call tools to get information. Answer the user's question by calling the appropriate tools. Only call tools if you need information to answer the user's question."
    )


    log_response(iteration + 1, response)
    print(f"Stop reason: {response.stop_reason}")

    # Append the assistant's response to history
    messages.append({"role": "assistant", "content": response.content})

    # If the model didn't request a tool, we're done
    if response.stop_reason != "tool_use":
        print("\nFinal answer:")
        for block in response.content:
            if block.type == "text":
                print(block.text)
        break

    # Otherwise, execute each tool call and append results
    tool_results = []
    for block in response.content:
        if block.type == "tool_use":
            print(f"Tool call: {block.name}({block.input})")
            if block.name == "get_stock_price":
                result = get_stock_price(**block.input)
                print(f"Tool result: {result}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

    messages.append({"role": "user", "content": tool_results})
else:
    print("Hit max iterations without a final answer")