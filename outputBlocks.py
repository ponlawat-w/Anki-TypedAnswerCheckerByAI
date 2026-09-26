import html
import json
import re

from aqt import mw

# UI-only block kinds (the worker-side kinds live in aiModelWorker).
BLOCK_STATUS: str = 'status'
BLOCK_FAILURE: str = 'failure'

RETRY_BUTTON_HTML: str = (
    '<button id="typedAnswerCheckerByAI-button"'
    ' onclick="pycmd(\'typedAnswerCheckerByAI-action-check\');"'
    ' style="padding:6px 16px; cursor:pointer;">Retry (C)</button>'
)

OUTPUT_STYLE: str = """
#typedAnswerCheckerByAI-container .typedAnswerCheckerByAI-step {
    font-size: 0.8em;
    margin: 2px 0;
}
#typedAnswerCheckerByAI-container .typedAnswerCheckerByAI-step > summary,
#typedAnswerCheckerByAI-container div.typedAnswerCheckerByAI-step,
#typedAnswerCheckerByAI-container .typedAnswerCheckerByAI-activeTitle {
    opacity: 0.6;
}
#typedAnswerCheckerByAI-container .typedAnswerCheckerByAI-step > summary {
    cursor: pointer;
}
#typedAnswerCheckerByAI-container .typedAnswerCheckerByAI-stepBody {
    margin: 4px 0 8px 0.4em;
    padding-left: 0.8em;
    border-left: 2px solid rgba(128, 128, 128, 0.4);
    opacity: 0.85;
}
#typedAnswerCheckerByAI-container .typedAnswerCheckerByAI-activeTitle {
    font-size: 0.8em;
    margin-top: 6px;
}
#typedAnswerCheckerByAI-container .typedAnswerCheckerByAI-active:not([data-kind="answer"]) .typedAnswerCheckerByAI-activeBody {
    font-size: 0.85em;
    opacity: 0.8;
}
#typedAnswerCheckerByAI-container [data-kind="failure"] .typedAnswerCheckerByAI-activeBody,
#typedAnswerCheckerByAI-container .typedAnswerCheckerByAI-step[data-kind="failure"] {
    color: #d9534f;
}
#typedAnswerCheckerByAI-container .typedAnswerCheckerByAI-actions {
    text-align: center;
    margin-top: 8px;
}
"""

# Only the active (latest) block is shown expanded; when a new block begins, the previous one
# moves into the history as a collapsed <details> (or a plain line when it has no body).
OUTPUT_SCRIPT: str = """
window.typedAnswerCheckerByAIOutput = {
    part: function(name) {
        const container = document.getElementById('typedAnswerCheckerByAI-container');
        return container ? container.querySelector('.typedAnswerCheckerByAI-' + name) : null;
    },
    start: function() {
        const container = document.getElementById('typedAnswerCheckerByAI-container');
        if (!container) return;
        container.style.textAlign = 'left';
        container.innerHTML = '<hr>'
            + '<div class="typedAnswerCheckerByAI-history"></div>'
            + '<div class="typedAnswerCheckerByAI-active">'
            + '<div class="typedAnswerCheckerByAI-activeTitle"></div>'
            + '<div class="typedAnswerCheckerByAI-activeBody"></div>'
            + '</div>'
            + '<div class="typedAnswerCheckerByAI-actions"></div>';
    },
    beginBlock: function(kind, title) {
        const active = this.part('active');
        if (!active) return;
        this.collapseActive(kind);
        active.dataset.kind = kind;
        this.part('activeTitle').textContent = title;
        this.part('activeBody').innerHTML = '';
    },
    collapseActive: function(nextKind) {
        const active = this.part('active');
        const kind = active.dataset.kind;
        if (!kind || kind === 'status') return;
        let title = this.part('activeTitle').textContent;
        if (kind === 'answer' && nextKind === 'failure') title = 'Incomplete: ' + title;
        const body = this.part('activeBody');
        const hasBody = body.textContent.trim() !== '';
        const step = document.createElement(hasBody ? 'details' : 'div');
        step.className = 'typedAnswerCheckerByAI-step';
        step.dataset.kind = kind;
        if (hasBody) {
            const summary = document.createElement('summary');
            summary.textContent = title;
            const stepBody = document.createElement('div');
            stepBody.className = 'typedAnswerCheckerByAI-stepBody';
            while (body.firstChild) stepBody.appendChild(body.firstChild);
            step.appendChild(summary);
            step.appendChild(stepBody);
            // Do not leave focus on the summary, or Space/Enter would toggle it instead of reviewing.
            step.addEventListener('toggle', function() {
                if (document.activeElement) document.activeElement.blur();
            });
        } else {
            step.textContent = title;
        }
        this.part('history').appendChild(step);
    },
    setTitle: function(title) {
        const titleElement = this.part('activeTitle');
        if (titleElement) titleElement.textContent = title;
    },
    setBody: function(bodyHtml) {
        const bodyElement = this.part('activeBody');
        if (bodyElement) bodyElement.innerHTML = bodyHtml;
    },
    setActions: function(actionsHtml) {
        const actionsElement = this.part('actions');
        if (actionsElement) actionsElement.innerHTML = actionsHtml;
    },
};
"""


def markdownToHtml(text: str) -> str:
    # Model output can quote web pages, so escape it before adding any markup.
    text = html.escape(text, quote = True)
    # Code blocks (must be processed before inline code)
    text = re.sub(r'```.*?\n(.*?)```', lambda m: f'<pre><code>{m.group(1)}</code></pre>', text, flags = re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Links (http/https only)
    text = re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', text)
    # Headers
    text = re.sub(r'^### (.+)$', r'<h4>\1</h4>', text, flags = re.MULTILINE)
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags = re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags = re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h1>\1</h1>', text, flags = re.MULTILINE)
    # Bold and italic
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Unordered lists
    text = re.sub(
        r'(?:^[*\-] .+$\n?)+',
        lambda m: '<ul>' + re.sub(r'^[*\-]\s+(.+)$\n?', r'<li>\1</li>', m.group(0), flags = re.MULTILINE) + '</ul>',
        text,
        flags = re.MULTILINE
    )
    # Line breaks
    text = re.sub(r'\n{2,}', '</p><p>', text)
    text = re.sub(r'\n', '<br>', text)
    # Others
    text = re.sub(r'^---$', '<hr>', text, flags = re.MULTILINE  )
    text = re.sub(r'\$\\rightarrow\$', '→', text)
    text = re.sub(r'\$\\leftarrow\$', '←', text)
    return f'<p>{text}</p>'


def plainTextToHtml(text: str) -> str:
    return f'<p>{html.escape(text, quote = True)}</p>'


def _callOutput(functionName: str, *arguments: str) -> None:
    argumentList = ', '.join(json.dumps(argument) for argument in arguments)
    mw.reviewer.web.eval(
        f'window.typedAnswerCheckerByAIOutput'
        f' && window.typedAnswerCheckerByAIOutput.{functionName}({argumentList});'
    )


def showOutputArea() -> None:
    mw.reviewer.web.eval(f"""
        (function() {{
            if (!document.getElementById('typedAnswerCheckerByAI-style')) {{
                const style = document.createElement('style');
                style.id = 'typedAnswerCheckerByAI-style';
                style.textContent = {json.dumps(OUTPUT_STYLE)};
                document.head.appendChild(style);
            }}
        }})();
        {OUTPUT_SCRIPT}
        window.typedAnswerCheckerByAIOutput.start();
    """)


def beginOutputBlock(kind: str, title: str) -> None:
    _callOutput('beginBlock', kind, title)


def setOutputBlockTitle(title: str) -> None:
    _callOutput('setTitle', title)


def setOutputBlockBody(bodyHtml: str) -> None:
    _callOutput('setBody', bodyHtml)


def showRetryButton() -> None:
    _callOutput('setActions', RETRY_BUTTON_HTML)
