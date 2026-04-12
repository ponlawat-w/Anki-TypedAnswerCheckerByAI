import json
import os

DEFAULT_PROMPT: str = json.load(
    open(os.path.join(os.path.dirname(__file__), "config.json"))
)["prompts"]["default"]
