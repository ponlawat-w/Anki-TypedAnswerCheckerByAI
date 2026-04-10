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
    'model': 'gemini-2.5-flash',
    'customModelId': '',
    'apiKey': '',
    'defaultPrompt': DEFAULT_PROMPT,
    'cardTypePrompts': {},
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

DEFAULT_CARD_TYPE_KEY: str = '__default__'


def hasTypedAnswer(template: dict) -> bool:
    return '{{type:' in template.get('qfmt', '') or '{{type:' in template.get('afmt', '')


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
        self._previousCardTypeIndex: int = 0
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
        layout.addLayout(self._buildCardTypeRow())
        self._customPromptCheck = QCheckBox('Customised prompt')
        layout.addWidget(self._customPromptCheck)
        self._promptEdit = QTextEdit()
        self._promptEdit.setMinimumHeight(160)
        self._promptEdit.setPlaceholderText(
            'Use {{cardQuestion}}, {{cardAnswer}}, {{userAnswer}} as placeholders.'
        )
        layout.addWidget(self._promptEdit)
        return box

    def _buildCardTypeRow(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._cardTypeCombo = QComboBox()
        self._cardTypeCombo.addItem('Default')
        for (label, key) in getCardTypes():
            self._cardTypeCombo.addItem(label, userData = key)
        row.addWidget(QLabel('Card type:'))
        row.addWidget(self._cardTypeCombo)
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
        self._cardTypeCombo.currentIndexChanged.connect(self._onCardTypeChanged)
        self._customPromptCheck.stateChanged.connect(self._onCustomPromptCheckChanged)

    def _onModelChanged(self, index: int) -> None:
        self._customModelWidget.setVisible(self._modelCombo.currentText() == 'Custom')

    def _onCardTypeChanged(self, index: int) -> None:
        self._persistPromptForIndex(self._previousCardTypeIndex)
        self._updateCardTypeLabels()
        self._previousCardTypeIndex = index
        self._refreshPromptArea()

    def _onCustomPromptCheckChanged(self, state: int) -> None:
        key = self._currentCardTypeKey()
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
        self._updateCardTypeLabels()

    def _updateCardTypeLabels(self) -> None:
        for index in range(1, self._cardTypeCombo.count()):
            key: str = self._cardTypeCombo.itemData(index) or ''
            baseLabel: str = self._cardTypeCombo.itemText(index)
            if baseLabel.endswith(' **Customised Prompt**'):
                baseLabel = baseLabel[: -len(' **Customised Prompt**')]
            if self._tempPrompts.get(key, ''):
                self._cardTypeCombo.setItemText(index, baseLabel + ' **Customised Prompt**')
            else:
                self._cardTypeCombo.setItemText(index, baseLabel)

    def _isDefaultSelected(self) -> bool:
        return self._cardTypeCombo.currentIndex() == 0

    def _currentCardTypeKey(self) -> str:
        return self._cardTypeCombo.currentData() or ''

    def _getDefaultPromptText(self) -> str:
        return self._tempPrompts.get(DEFAULT_CARD_TYPE_KEY, self._config.get('defaultPrompt', DEFAULT_PROMPT))

    def _refreshPromptArea(self) -> None:
        if self._isDefaultSelected():
            self._customPromptCheck.blockSignals(True)
            self._customPromptCheck.setChecked(False)
            self._customPromptCheck.blockSignals(False)
            self._customPromptCheck.setEnabled(False)
            self._promptEdit.setEnabled(True)
            self._promptEdit.setPlainText(self._getDefaultPromptText())
            return

        key = self._currentCardTypeKey()
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
            self._tempPrompts[DEFAULT_CARD_TYPE_KEY] = self._promptEdit.toPlainText()
            return

        key: str = self._cardTypeCombo.itemData(index) or ''
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
        self._previousCardTypeIndex = 0
        self._tempPrompts[DEFAULT_CARD_TYPE_KEY] = self._config.get('defaultPrompt', DEFAULT_PROMPT)
        for (key, value) in self._config.get('cardTypePrompts', {}).items():
            if value:
                self._tempPrompts[key] = value

        self._modelCombo.setCurrentIndex(MODEL_KEY_TO_INDEX.get(self._config.get('model', 'gemini-2.5-flash'), 0))
        self._customModelEdit.setText(self._config.get('customModelId', ''))
        self._apiKeyEdit.setText(self._config.get('apiKey', ''))
        self._onModelChanged(self._modelCombo.currentIndex())

        self._updateCardTypeLabels()
        self._cardTypeCombo.setCurrentIndex(0)
        self._refreshPromptArea()

    def _saveAndClose(self) -> None:
        self._persistPromptForIndex(self._cardTypeCombo.currentIndex())
        newConfig = {
            'model': MODELS[self._modelCombo.currentIndex()],
            'customModelId': self._customModelEdit.text().strip(),
            'apiKey': self._apiKeyEdit.text().strip(),
            'defaultPrompt': self._tempPrompts.get(DEFAULT_CARD_TYPE_KEY, DEFAULT_PROMPT),
            'cardTypePrompts': {
                key: value
                for (key, value) in self._tempPrompts.items()
                if key != DEFAULT_CARD_TYPE_KEY and value
            },
        }
        mw.addonManager.writeConfig(ADDON_MODULE, newConfig)
        self.accept()

    def _resetToDefaults(self) -> None:
        mw.addonManager.writeConfig(ADDON_MODULE, DEFAULT_CONFIG)
        self._config = dict(DEFAULT_CONFIG)
        self._loadValues()
