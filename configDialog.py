from typing import Optional

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from .prompt import DEFAULT_PROMPT

ADDON_MODULE: str = __name__.split('.')[0]

DEFAULT_CONFIG: dict = {
    'model': 'gemini-3.1-flash-lite-preview',
    'customModelId': '',
    'apiKey': '',
    'prompts': {
        'default': DEFAULT_PROMPT,
        'decks': {},
        'cardTypes': {},
    },
}

MODELS: list[str] = [
    'gemini-2.5-flash',
    'gemini-2.5-pro',
    'gemini-3.1-flash-lite-preview',
    'gemini-3-flash-preview',
    'gemini-3.1-pro-preview',
    'custom',
]

MODEL_KEY_TO_INDEX: dict[str, int] = {key: index for index, key in enumerate(MODELS)}

DEFAULT_PROMPT_SETTINGS_KEY: str = '__default__'
DECK_KEY_PREFIX: str = 'deck::'
CARD_TYPE_KEY_PREFIX: str = 'cardType::'


def hasTypedAnswer(template: dict) -> bool:
    return '{{type:' in template.get('qfmt', '') or '{{type:' in template.get('afmt', '')


def getDecks() -> list[tuple[str, str]]:
    if not mw.col:
        return []
    result: list[tuple[str, str]] = []
    for deck in mw.col.decks.all():
        deckName: str = deck['name']
        label = f'[Deck] {deckName}'
        result.append((label, deckName))
    result.sort(key = lambda x: x[0])
    return result


def getCardTypes() -> list[tuple[str, str]]:
    if not mw.col:
        return []
    result: list[tuple[str, str]] = []
    for notetype in mw.col.models.all():
        noteName: str = notetype['name']
        for template in notetype['tmpls']:
            if not hasTypedAnswer(template):
                continue
            cardName: str = template['name']
            label = f'[{noteName}] - [{cardName}]'
            key = f'{noteName}::{cardName}'
            result.append((label, key))
    return result


class ConfigDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('AI Typed Answer Checker — Config')
        self.setMinimumWidth(520)
        self._config: dict = mw.addonManager.getConfig(ADDON_MODULE) or {}
        self._tempPrompts: dict[str, str] = {}
        self._previousPromptSettingsIndex: int = 0
        self._buildUi()
        self._loadValues()
        self._connectSignals()

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._buildModelSection())
        layout.addWidget(self._buildPromptSection())
        layout.addWidget(self._buildResetSection())
        layout.addWidget(self._buildButtonBox())

    def _buildModelSection(self) -> QGroupBox:
        box = QGroupBox('AI Model Configuration')
        layout = QVBoxLayout(box)
        layout.addLayout(self._buildModelRow())
        self._customModelWidget = self._buildCustomModelWidget()
        layout.addWidget(self._customModelWidget)
        layout.addLayout(self._buildApiKeyRow())
        return box

    def _buildModelRow(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._modelCombo = QComboBox()
        self._modelCombo.addItems(['Custom' if model == 'custom' else model for model in MODELS])
        row.addWidget(QLabel('Model:'))
        row.addWidget(self._modelCombo)
        return row

    def _buildCustomModelWidget(self) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        self._customModelEdit = QLineEdit()
        self._customModelEdit.setPlaceholderText('e.g. gemini-2.5-flash')
        row.addWidget(QLabel('Custom model ID:'))
        row.addWidget(self._customModelEdit)
        return widget

    def _buildApiKeyRow(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._apiKeyEdit = QLineEdit()
        self._apiKeyEdit.setEchoMode(QLineEdit.EchoMode.Password)
        row.addWidget(QLabel('Google AI Studio API key:'))
        row.addWidget(self._apiKeyEdit)
        return row

    def _buildPromptSection(self) -> QGroupBox:
        box = QGroupBox('Prompt Configuration')
        layout = QVBoxLayout(box)
        layout.addLayout(self._buildPromptSettingsRow())
        self._customPromptCheck = QCheckBox('Customised prompt')
        layout.addWidget(self._customPromptCheck)
        self._promptEdit = QTextEdit()
        self._promptEdit.setMinimumHeight(160)
        self._promptEdit.setPlaceholderText(
            'Use {{cardQuestion}}, {{cardAnswer}}, {{userAnswer}} as placeholders.'
        )
        layout.addWidget(self._promptEdit)
        return box

    def _buildPromptSettingsRow(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._promptSettingsCombo = QComboBox()
        self._promptSettingsCombo.addItem('Default')
        for (label, deckName) in getDecks():
            self._promptSettingsCombo.addItem(label, userData = f'{DECK_KEY_PREFIX}{deckName}')
        for (label, cardTypeKey) in getCardTypes():
            self._promptSettingsCombo.addItem(label, userData = f'{CARD_TYPE_KEY_PREFIX}{cardTypeKey}')
        row.addWidget(QLabel('Prompt Settings:'))
        row.addWidget(self._promptSettingsCombo)
        return row

    def _buildResetSection(self) -> QGroupBox:
        box = QGroupBox('Reset')
        layout = QVBoxLayout(box)
        resetButton = QPushButton('Reset to default settings')
        resetButton.clicked.connect(self._resetToDefaults)
        layout.addWidget(resetButton)
        return box

    def _buildButtonBox(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._saveAndClose)
        buttons.rejected.connect(self.reject)
        return buttons

    def _connectSignals(self) -> None:
        self._modelCombo.currentIndexChanged.connect(self._onModelChanged)
        self._promptSettingsCombo.currentIndexChanged.connect(self._onPromptSettingsChanged)
        self._customPromptCheck.stateChanged.connect(self._onCustomPromptCheckChanged)

    def _onModelChanged(self, index: int) -> None:
        self._customModelWidget.setVisible(self._modelCombo.currentText() == 'Custom')

    def _onPromptSettingsChanged(self, index: int) -> None:
        self._persistPromptForIndex(self._previousPromptSettingsIndex)
        self._updatePromptSettingsLabels()
        self._previousPromptSettingsIndex = index
        self._refreshPromptArea()

    def _onCustomPromptCheckChanged(self, state: int) -> None:
        key = self._currentPromptSettingsKey()
        if not key:
            return
        if self._customPromptCheck.isChecked():
            self._promptEdit.setEnabled(True)
            existingCustom = self._tempPrompts.get(key, '')
            if existingCustom:
                self._promptEdit.setPlainText(existingCustom)
            else:
                self._promptEdit.setPlainText(self._getDefaultPromptText())
        else:
            self._tempPrompts.pop(key, None)
            self._promptEdit.setEnabled(False)
            self._promptEdit.setPlainText('')
        self._updatePromptSettingsLabels()

    def _updatePromptSettingsLabels(self) -> None:
        for index in range(1, self._promptSettingsCombo.count()):
            key: str = self._promptSettingsCombo.itemData(index) or ''
            baseLabel: str = self._promptSettingsCombo.itemText(index)
            if baseLabel.endswith(' **Customised Prompt**'):
                baseLabel = baseLabel[: -len(' **Customised Prompt**')]
            if self._tempPrompts.get(key, ''):
                self._promptSettingsCombo.setItemText(index, baseLabel + ' **Customised Prompt**')
            else:
                self._promptSettingsCombo.setItemText(index, baseLabel)

    def _isDefaultSelected(self) -> bool:
        return self._promptSettingsCombo.currentIndex() == 0

    def _currentPromptSettingsKey(self) -> str:
        return self._promptSettingsCombo.currentData() or ''

    def _getDefaultPromptText(self) -> str:
        return self._tempPrompts.get(
            DEFAULT_PROMPT_SETTINGS_KEY,
            self._config.get('prompts', {}).get('default', DEFAULT_PROMPT)
        )

    def _refreshPromptArea(self) -> None:
        if self._isDefaultSelected():
            self._customPromptCheck.blockSignals(True)
            self._customPromptCheck.setChecked(False)
            self._customPromptCheck.blockSignals(False)
            self._customPromptCheck.setEnabled(False)
            self._promptEdit.setEnabled(True)
            self._promptEdit.setPlainText(self._getDefaultPromptText())
            return

        key = self._currentPromptSettingsKey()
        hasCustom = bool(self._tempPrompts.get(key, ''))
        self._customPromptCheck.setEnabled(True)
        self._customPromptCheck.blockSignals(True)
        self._customPromptCheck.setChecked(hasCustom)
        self._customPromptCheck.blockSignals(False)

        if hasCustom:
            self._promptEdit.setEnabled(True)
            self._promptEdit.setPlainText(self._tempPrompts[key])
        else:
            self._promptEdit.setEnabled(False)
            self._promptEdit.setPlainText('')

    def _persistPromptForIndex(self, index: int) -> None:
        if index == 0:
            self._tempPrompts[DEFAULT_PROMPT_SETTINGS_KEY] = self._promptEdit.toPlainText()
            return

        key: str = self._promptSettingsCombo.itemData(index) or ''
        if not key:
            return

        if self._customPromptCheck.isChecked():
            text = self._promptEdit.toPlainText()
            if text:
                self._tempPrompts[key] = text
            else:
                self._tempPrompts.pop(key, None)
        else:
            self._tempPrompts.pop(key, None)

    def _loadValues(self) -> None:
        self._tempPrompts = {}
        self._previousPromptSettingsIndex = 0
        prompts: dict = self._config.get('prompts', {})
        self._tempPrompts[DEFAULT_PROMPT_SETTINGS_KEY] = prompts.get('default', DEFAULT_PROMPT)
        for (deckName, value) in prompts.get('decks', {}).items():
            if value:
                self._tempPrompts[f'{DECK_KEY_PREFIX}{deckName}'] = value
        for (cardTypeKey, value) in prompts.get('cardTypes', {}).items():
            if value:
                self._tempPrompts[f'{CARD_TYPE_KEY_PREFIX}{cardTypeKey}'] = value

        self._modelCombo.setCurrentIndex(MODEL_KEY_TO_INDEX.get(self._config.get('model', 'gemini-3.1-flash-lite-preview'), 0))
        self._customModelEdit.setText(self._config.get('customModelId', ''))
        self._apiKeyEdit.setText(self._config.get('apiKey', ''))
        self._onModelChanged(self._modelCombo.currentIndex())

        self._updatePromptSettingsLabels()
        self._promptSettingsCombo.setCurrentIndex(0)
        self._refreshPromptArea()

    def _saveAndClose(self) -> None:
        self._persistPromptForIndex(self._promptSettingsCombo.currentIndex())
        deckPrompts: dict[str, str] = {}
        cardTypePrompts: dict[str, str] = {}
        for (key, value) in self._tempPrompts.items():
            if key == DEFAULT_PROMPT_SETTINGS_KEY or not value:
                continue
            if key.startswith(DECK_KEY_PREFIX):
                deckPrompts[key[len(DECK_KEY_PREFIX):]] = value
            elif key.startswith(CARD_TYPE_KEY_PREFIX):
                cardTypePrompts[key[len(CARD_TYPE_KEY_PREFIX):]] = value
        newConfig = {
            'model': MODELS[self._modelCombo.currentIndex()],
            'customModelId': self._customModelEdit.text().strip(),
            'apiKey': self._apiKeyEdit.text().strip(),
            'prompts': {
                'default': self._tempPrompts.get(DEFAULT_PROMPT_SETTINGS_KEY, DEFAULT_PROMPT),
                'decks': deckPrompts,
                'cardTypes': cardTypePrompts,
            },
        }
        mw.addonManager.writeConfig(ADDON_MODULE, newConfig)
        self.accept()

    def _resetToDefaults(self) -> None:
        mw.addonManager.writeConfig(ADDON_MODULE, DEFAULT_CONFIG)
        self._config = dict(DEFAULT_CONFIG)
        self._loadValues()