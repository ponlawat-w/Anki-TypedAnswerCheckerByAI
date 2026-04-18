import json
import os
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
    QTimer,
    QVBoxLayout,
    QWidget,
)
from .prompt import DEFAULT_PROMPT

ADDON_MODULE: str = __name__.split('.')[0]

_defaultConfig: dict = json.load(
    open(os.path.join(os.path.dirname(__file__), 'config.json'))
)

SCHEMA_VERSION: int = _defaultConfig['schemaVersion']

PRESET_MODELS: list[str] = [
    'gemini-2.5-flash',
    'gemini-2.5-pro',
    'gemini-3.1-flash-lite-preview',
    'gemini-3-flash-preview',
    'gemini-3.1-pro-preview',
]

DEFAULT_CONFIG: dict = {
    'schemaVersion': SCHEMA_VERSION,
    'models': ['gemini-3.1-flash-lite-preview'],
    'apiKey': '',
    'prompts': {
        'default': DEFAULT_PROMPT,
        'decks': {},
        'cardTypes': {},
    },
}

DEFAULT_PROMPT_SETTINGS_KEY: str = '__default__'
DECK_KEY_PREFIX: str = 'deck::'
CARD_TYPE_KEY_PREFIX: str = 'cardType::'

CUSTOM_MODEL_PLACEHOLDER: str = 'Enter model ID from Google AI Studio, e.g. gemini-3.1-flash-lite-preview'
CUSTOM_MODEL_DEFAULT_TEXT: str = 'custom-model-name'


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
        self._modelRows: list[tuple[QComboBox, Optional[QLineEdit], QWidget, QLabel]] = []
        self._isLoading: bool = False
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

        self._modelErrorLabel = QLabel('At least one model must be selected.')
        self._modelErrorLabel.setStyleSheet('color: red;')
        self._modelErrorLabel.setVisible(False)
        layout.addWidget(self._modelErrorLabel)

        self._modelListContainer = QWidget()
        self._modelListLayout = QVBoxLayout(self._modelListContainer)
        self._modelListLayout.setContentsMargins(0, 0, 0, 0)
        self._modelListLayout.setSpacing(2)
        layout.addWidget(self._modelListContainer)

        layout.addLayout(self._buildApiKeyRow())
        return box

    def _buildModelComboOptions(self) -> list[str]:
        return ['None'] + PRESET_MODELS + ['Custom']

    def _updateModelRowLabels(self) -> None:
        for i, (_, _, _, label) in enumerate(self._modelRows):
            label.setText('Main Model' if i == 0 else f'Fallback Model #{i}')

    def _createModelRow(self, modelId: str) -> None:
        rowWidget = QWidget()
        rowLayout = QVBoxLayout(rowWidget)
        rowLayout.setContentsMargins(0, 0, 0, 0)
        rowLayout.setSpacing(2)

        rowLabel = QLabel()
        rowLayout.addWidget(rowLabel)

        combo = QComboBox()
        combo.addItems(self._buildModelComboOptions())

        lineEdit: Optional[QLineEdit] = None

        if modelId == '':
            combo.setCurrentIndex(0)
        elif modelId in PRESET_MODELS:
            combo.setCurrentIndex(PRESET_MODELS.index(modelId) + 1)
        else:
            combo.setCurrentIndex(len(PRESET_MODELS) + 1)
            lineEdit = self._buildCustomLineEdit(combo)
            lineEdit.setText(modelId)

        comboRow = QHBoxLayout()
        comboRow.addWidget(combo)
        rowLayout.addLayout(comboRow)

        if lineEdit is not None:
            paddedRow = QHBoxLayout()
            paddedRow.addSpacing(20)
            paddedRow.addWidget(lineEdit)
            rowLayout.addLayout(paddedRow)

        self._modelRows.append((combo, lineEdit, rowWidget, rowLabel))
        self._modelListLayout.addWidget(rowWidget)
        self._updateModelRowLabels()

        combo.currentIndexChanged.connect(
            lambda idx, c = combo: self._onRowModelChanged(c, idx)
        )

    def _buildCustomLineEdit(self, combo: QComboBox) -> QLineEdit:
        lineEdit = QLineEdit()
        lineEdit.setPlaceholderText(CUSTOM_MODEL_PLACEHOLDER)
        lineEdit.setText(CUSTOM_MODEL_DEFAULT_TEXT)
        lineEdit.editingFinished.connect(
            lambda le = lineEdit, c = combo: self._onCustomModelEditFinished(le, c)
        )
        return lineEdit

    def _appendEmptyModelRow(self) -> None:
        self._createModelRow('')

    def _findRowIndex(self, combo: QComboBox) -> int:
        for i, (c, _, _, _) in enumerate(self._modelRows):
            if c is combo:
                return i
        return -1

    def _removeModelRow(self, combo: QComboBox) -> None:
        index = self._findRowIndex(combo)
        if index < 0:
            return
        _, _, rowWidget, _ = self._modelRows[index]
        self._modelListLayout.removeWidget(rowWidget)
        rowWidget.setParent(None)
        self._modelRows.pop(index)
        self._updateModelRowLabels()

    def _updateCustomLineEditVisibility(self, combo: QComboBox, visible: bool) -> None:
        index = self._findRowIndex(combo)
        if index < 0:
            return
        c, lineEdit, rowWidget, rowLabel = self._modelRows[index]
        rowLayout = rowWidget.layout()

        if visible and lineEdit is None:
            lineEdit = self._buildCustomLineEdit(combo)
            paddedRow = QHBoxLayout()
            paddedRow.addSpacing(20)
            paddedRow.addWidget(lineEdit)
            rowLayout.addLayout(paddedRow)
            self._modelRows[index] = (c, lineEdit, rowWidget, rowLabel)
        elif not visible and lineEdit is not None:
            lineEdit.setVisible(False)
        elif visible and lineEdit is not None:
            lineEdit.setVisible(True)

    def _onRowModelChanged(self, combo: QComboBox, comboIndex: int) -> None:
        if self._isLoading:
            return

        isCustom = comboIndex == len(PRESET_MODELS) + 1
        isNone = comboIndex == 0
        isLast = self._findRowIndex(combo) == len(self._modelRows) - 1

        self._updateCustomLineEditVisibility(combo, isCustom)

        if isNone and not isLast:
            QTimer.singleShot(0, lambda c=combo: self._removeModelRow(c))
        elif not isNone and isLast:
            self._appendEmptyModelRow()

        self._updateModelErrorLabel()

    def _onCustomModelEditFinished(self, lineEdit: QLineEdit, combo: QComboBox) -> None:
        text = lineEdit.text().strip()
        if text in PRESET_MODELS:
            combo.blockSignals(True)
            combo.setCurrentIndex(PRESET_MODELS.index(text) + 1)
            combo.blockSignals(False)
            lineEdit.setVisible(False)
            index = self._findRowIndex(combo)
            if index >= 0:
                c, _, rowWidget, rowLabel = self._modelRows[index]
                self._modelRows[index] = (c, None, rowWidget, rowLabel)

        isLast = self._findRowIndex(combo) == len(self._modelRows) - 1
        if isLast and text:
            self._appendEmptyModelRow()

        self._updateModelErrorLabel()

    def _updateModelErrorLabel(self) -> None:
        showError = (
            len(self._modelRows) == 1
            and self._modelRows[0][0].currentIndex() == 0
        )
        self._modelErrorLabel.setVisible(showError)

    def _getRowModelId(self, combo: QComboBox, lineEdit: Optional[QLineEdit]) -> str:
        comboIndex = combo.currentIndex()
        if comboIndex == 0:
            return ''
        if comboIndex == len(PRESET_MODELS) + 1:
            return lineEdit.text().strip() if lineEdit else ''
        return PRESET_MODELS[comboIndex - 1]

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
        self._promptSettingsCombo.currentIndexChanged.connect(self._onPromptSettingsChanged)
        self._customPromptCheck.stateChanged.connect(self._onCustomPromptCheckChanged)

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

    def _clearModelRows(self) -> None:
        for _, _, rowWidget, _ in self._modelRows:
            self._modelListLayout.removeWidget(rowWidget)
            rowWidget.setParent(None)
        self._modelRows.clear()

    def _loadValues(self) -> None:
        self._isLoading = True
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

        self._clearModelRows()
        models: list[str] = self._config.get('models', ['gemini-3.1-flash-lite-preview'])
        for modelId in models:
            self._createModelRow(modelId)
        self._appendEmptyModelRow()
        self._updateModelErrorLabel()

        self._apiKeyEdit.setText(self._config.get('apiKey', ''))

        self._isLoading = False

        self._updatePromptSettingsLabels()
        self._promptSettingsCombo.setCurrentIndex(0)
        self._refreshPromptArea()

    def _saveAndClose(self) -> None:
        self._persistPromptForIndex(self._promptSettingsCombo.currentIndex())

        resolvedModels: list[str] = []
        for (combo, lineEdit, _, _) in self._modelRows:
            modelId = self._getRowModelId(combo, lineEdit)
            if modelId:
                resolvedModels.append(modelId)

        if not resolvedModels:
            self._modelErrorLabel.setVisible(True)
            return

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
            'schemaVersion': SCHEMA_VERSION,
            'models': resolvedModels,
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
