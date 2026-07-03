# Typed Answer Checker by AI

An Anki add-on that uses Google Gemini and/or Anthropic Claude to evaluate typed answers that do not exactly match the expected answer.

## How It Works

When reviewing a typed-answer card, Anki performs a strict character-by-character comparison. This add-on intercepts that result: if the typed answer does not match exactly, a **Check with AI** button appears below the answer. Clicking it (or pressing **C**) sends the question, expected answer, and your typed answer to the AI, which returns a brief explanation of whether the answer is acceptable.

Each model in your list is routed to a provider by its ID prefix: `gemini-` models use your Google AI Studio key, `claude-` models use your Claude API key. A model with any other prefix (or whose provider key is not configured) is skipped automatically.

If the first model in your list fails, the add-on automatically retries with the next model. The button shows **Retrying… (n/m)** during fallback attempts. An error and **Retry** button appear only when all models have been exhausted. Clicking **Retry** starts over from the first model.

The AI response is rendered inline on the card — no separate window.

### Learning memory

The add-on keeps a per-deck **learning memory** — a short list of your recurring mistakes and weak points. Whenever you run an AI check on a card and then rate it (Again/Hard/Good/Easy), a background request asks your first configured model to update that deck's memory based on the question, your answer, the AI's feedback, your rating, and your review history. The update runs silently and never interrupts your review.

On every subsequent AI check, the deck's memory is appended to the prompt so the AI can personalise its feedback and emphasise your known weak points. The memory notes are kept in English internally but the AI is instructed to keep responding in the language of your prompt. Memory is stored in `user_files/memory.json` and is independent of your other settings (resetting settings does not clear it). You can wipe it from the config dialog with **Clear learning memory (all decks)**.

## Features

- Automatically detects typed-answer mismatches and injects the check button
- Keyboard shortcut **C** to trigger the check without using the mouse
- Multiple model support with automatic sequential fallback on error
- Renders the AI response as formatted HTML (supports bold, italic, lists, code blocks, headers)
- Shows a **Retry** button on API errors
- Per-deck and per-card-type prompt overrides — different decks or note type/card combinations can use different prompts
- Per-deck learning memory that records recurring weak points and personalises future checks
- Config dialog accessible from **Tools > Add-ons**

## Requirements

- Anki 2.1.50 or later (min point version 50)
- A [Google AI Studio](https://aistudio.google.com/) API key and/or an [Anthropic Claude](https://console.anthropic.com/) API key (at least one is required; provide both if you mix Gemini and Claude models)

## Setup

1. Install the add-on.
2. Open **Tools > Add-ons**, select **Typed Answer Checker by AI**, and click **Config**.
3. Paste your Google AI Studio API key into the **Google AI Studio API key** field and/or your Claude API key into the **Claude API key** field.
4. Add one or more Gemini and/or Claude models in the model list. The add-on tries them in order, falling back to the next on failure.
5. Click **OK**.

## Configuration

| Setting | Description |
|---|---|
| **Models** | Ordered list of Gemini and/or Claude model IDs to try. Select a preset from each dropdown, or choose **Custom** to enter any model ID (`gemini-…` or `claude-…`). The last empty dropdown is a placeholder for adding a new entry. |
| **Google AI Studio API key** | Your API key from [aistudio.google.com](https://aistudio.google.com/). Used for `gemini-` models. |
| **Claude API key** | Your API key from [console.anthropic.com](https://console.anthropic.com/). Used for `claude-` models. |
| **Default prompt** | The prompt template sent to the AI for all cards unless overridden. |
| **Per-deck prompt** | Select a deck from the Prompt Settings dropdown to set a custom prompt for all cards in that deck. |
| **Per-card-type prompt** | Select a specific note type + card combination to set a custom prompt for it. Takes priority over deck-level prompts. |

### Model list behaviour

- The list always ends with an empty trailing dropdown — selecting a model there adds it to the list.
- Changing any non-last dropdown to **None** removes that row.
- If only one row remains and it is set to **None**, an error is shown and the dialog cannot be saved.
- Duplicate model IDs are allowed; this causes the add-on to retry the same model before moving on.
- Each model is routed to its provider by ID prefix: `gemini-` → Gemini (Google AI Studio key), `claude-` → Claude (Claude API key). A custom model with any other prefix, or one whose provider key is blank, is skipped and the add-on falls through to the next model.

### Prompt resolution order

When a card is checked, the prompt is selected in this priority order:

1. **Card-type prompt** — if a custom prompt is set for the specific note type + card combination
2. **Deck prompt** — if a custom prompt is set for the card's deck
3. **Default prompt** — the fallback used when no override is configured

### Prompt templates

The prompt supports three placeholders:

| Placeholder | Replaced with |
|---|---|
| `{{cardQuestion}}` | The question side of the card (HTML stripped) |
| `{{cardAnswer}}` | The expected answer (HTML stripped, Unicode normalised) |
| `{{userAnswer}}` | The answer the user typed |

### Default prompt

```
You are helping a student check their answer. Determine if the typed answer is acceptable
as a correct answer, even if it is not word-for-word identical to the expected answer.

Question: {{cardQuestion}}
Expected answer: {{cardAnswer}}
Student's typed answer: {{userAnswer}}

Respond with a brief explanation of whether the answer is acceptable and why.
Keep it concise (2-3 sentences).
```

## Supported Models

Any model ID supported by the Gemini API or the Anthropic Claude API can be entered as a custom value (as long as it starts with `gemini-` or `claude-`). Built-in presets:

**Gemini** (Google AI Studio key)

- `gemini-2.5-flash`
- `gemini-2.5-pro`
- `gemini-3.1-flash-lite` (default)
- `gemini-3.5-flash`
- `gemini-3.1-pro-preview`

**Claude** (Claude API key)

- `claude-haiku-4-5`
- `claude-sonnet-5`
- `claude-sonnet-5-adaptive-thinking`
- `claude-opus-4-8`
- `claude-opus-4-8-adaptive-thinking`
- `claude-fable-5-adaptive-thinking`

Claude models run with thinking **off** by default. A model ID ending in `-adaptive-thinking` runs the same model with Claude's adaptive thinking enabled (slower and more expensive, but more reasoning). Haiku 4.5 has no adaptive-thinking mode; Fable 5 always thinks, so only its `-adaptive-thinking` preset is listed. You can also add these suffixes to any custom Claude model ID.
