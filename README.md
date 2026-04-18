# Typed Answer Checker by AI

An Anki add-on that uses Google Gemini AI to evaluate typed answers that do not exactly match the expected answer.

## How It Works

When reviewing a typed-answer card, Anki performs a strict character-by-character comparison. This add-on intercepts that result: if the typed answer does not match exactly, a **Check with AI** button appears below the answer. Clicking it (or pressing **C**) sends the question, expected answer, and your typed answer to Gemini, which returns a brief explanation of whether the answer is acceptable.

If the first model in your list fails, the add-on automatically retries with the next model. The button shows **Retrying… (n/m)** during fallback attempts. An error and **Retry** button appear only when all models have been exhausted. Clicking **Retry** starts over from the first model.

The AI response is rendered inline on the card — no separate window.

## Features

- Automatically detects typed-answer mismatches and injects the check button
- Keyboard shortcut **C** to trigger the check without using the mouse
- Multiple model support with automatic sequential fallback on error
- Renders the AI response as formatted HTML (supports bold, italic, lists, code blocks, headers)
- Shows a **Retry** button on API errors
- Per-deck and per-card-type prompt overrides — different decks or note type/card combinations can use different prompts
- Config dialog accessible from **Tools > Add-ons**

## Requirements

- Anki 2.1.50 or later (min point version 50)
- A [Google AI Studio](https://aistudio.google.com/) API key

## Setup

1. Install the add-on.
2. Open **Tools > Add-ons**, select **Typed Answer Checker by AI**, and click **Config**.
3. Paste your Google AI Studio API key into the **Google AI Studio API key** field.
4. Add one or more Gemini models in the model list. The add-on tries them in order, falling back to the next on failure.
5. Click **OK**.

## Configuration

| Setting | Description |
|---|---|
| **Models** | Ordered list of Gemini model IDs to try. Select a preset from each dropdown, or choose **Custom** to enter any model ID. The last empty dropdown is a placeholder for adding a new entry. |
| **Google AI Studio API key** | Your API key from [aistudio.google.com](https://aistudio.google.com/). |
| **Default prompt** | The prompt template sent to Gemini for all cards unless overridden. |
| **Per-deck prompt** | Select a deck from the Prompt Settings dropdown to set a custom prompt for all cards in that deck. |
| **Per-card-type prompt** | Select a specific note type + card combination to set a custom prompt for it. Takes priority over deck-level prompts. |

### Model list behaviour

- The list always ends with an empty trailing dropdown — selecting a model there adds it to the list.
- Changing any non-last dropdown to **None** removes that row.
- If only one row remains and it is set to **None**, an error is shown and the dialog cannot be saved.
- Duplicate model IDs are allowed; this causes the add-on to retry the same model before moving on.

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

Any model ID supported by the Gemini API can be entered as a custom value. Built-in presets:

- `gemini-2.5-flash`
- `gemini-2.5-pro`
- `gemini-3.1-flash-lite-preview` (default)
- `gemini-3-flash-preview`
- `gemini-3.1-pro-preview`
