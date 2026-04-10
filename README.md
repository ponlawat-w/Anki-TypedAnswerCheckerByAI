# Typed Answer Checker by AI

An Anki add-on that uses Google Gemini AI to evaluate typed answers that do not exactly match the expected answer.

## How It Works

When reviewing a typed-answer card, Anki performs a strict character-by-character comparison. This add-on intercepts that result: if the typed answer does not match exactly, a **Check with AI** button appears below the answer. Clicking it (or pressing **C**) sends the question, expected answer, and your typed answer to Gemini, which returns a brief explanation of whether the answer is acceptable.

The AI response is rendered inline on the card — no separate window.

## Features

- Automatically detects typed-answer mismatches and injects the check button
- Keyboard shortcut **C** to trigger the check without using the mouse
- Renders the AI response as formatted HTML (supports bold, italic, lists, code blocks, headers)
- Shows a **Retry** button on API errors
- Per-card-type prompt overrides — different note type/card combinations can use different prompts
- Config dialog accessible from **Tools > Add-ons**

## Requirements

- Anki 2.1.50 or later (min point version 50)
- A [Google AI Studio](https://aistudio.google.com/) API key

## Setup

1. Install the add-on.
2. Open **Tools > Add-ons**, select **Typed Answer Checker by AI**, and click **Config**.
3. Paste your Google AI Studio API key into the **Google AI Studio API key** field.
4. Select a Gemini model (default: `gemini-2.5-flash`).
5. Click **OK**.

## Configuration

| Setting | Description |
|---|---|
| **Model** | Gemini model to use. Choose a preset or select **Custom** to enter any model ID. |
| **Google AI Studio API key** | Your API key from [aistudio.google.com](https://aistudio.google.com/). |
| **Default prompt** | The prompt template sent to Gemini for all cards unless overridden. |
| **Per-card-type prompt** | Select a specific note type + card combination to set a custom prompt for it. |

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

- `gemini-2.5-flash` (default)
- `gemini-2.5-pro`
- `gemini-3.1-flash-lite-preview`
- `gemini-3-flash-preview`
- `gemini-3.1-pro-preview`
- Custom (any model ID supported by the Gemini API)
