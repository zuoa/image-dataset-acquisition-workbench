import json

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    DropDownPushButton,
    DropDownToolButton,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PushButton,
    RoundMenu,
    SubtitleLabel,
    TableWidget,
    TextEdit,
    TransparentPushButton,
)
from qfluentwidgets import (
    ComboBox as FluentComboBox,
)
from qfluentwidgets import (
    DoubleSpinBox as FluentDoubleSpinBox,
)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    SpinBox as FluentSpinBox,
)

from .theme import SoftCard as CardWidget

STYLE = """
QLabel#muted { color: #8C91A5; }
QLabel#pageTitle { color: #252B43; font-size: 28px; font-weight: 500; }
QLabel#bigNumber { color: #182532; font-size: 25px; font-weight: 600; }
QListView#gallery { background: transparent; border: none; outline: none; }
QWidget#panel { background: #FFFFFF; border: 1px solid #ECEEF6; border-radius: 20px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""


class ComboBox(FluentComboBox):
    """Keep long archive names discoverable without expanding the whole window."""

    def __init__(self, parent=None):
        self._full_text = ""
        super().__init__(parent)
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMaxVisibleItems(12)

    def setText(self, text):
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        QPushButton.setText(
            self,
            self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, max(20, self.width() - 40)
            ),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def sizeHint(self):
        return QSize(180, 40)

    def minimumSizeHint(self):
        return QSize(80, 40)


class _SpinEditorGeometry:
    """Reserve space for Fluent's inline buttons on Qt 6.12 as well as 6.8–6.11.

    Qt 6.12 can reduce the native editor to one pixel when the component's
    stylesheet and inline-button layout both reserve the trailing button area.
    """

    def _layout_editor(self):
        self.lineEdit().setGeometry(10, 1, max(20, self.width() - 88), self.height() - 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_editor()

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_editor()

    def changeEvent(self, event):
        super().changeEvent(event)
        self._layout_editor()


class SpinBox(_SpinEditorGeometry, FluentSpinBox):
    pass


class DoubleSpinBox(_SpinEditorGeometry, FluentDoubleSpinBox):
    pass


class FoldSection(QWidget):
    """Keep infrequent controls available without crowding the acquisition view."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.toggle = TransparentPushButton("＋  " + title)
        self.toggle.setCheckable(True)
        self.toggle.setProperty("foldControl", True)
        layout.addWidget(self.toggle)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 6, 0, 12)
        self.content_layout.setSpacing(12)
        layout.addWidget(self.content)
        self.content.hide()
        self.toggle.toggled.connect(self._toggle)

    def _toggle(self, expanded):
        self.toggle.setText(("－  " if expanded else "＋  ") + self.title)
        self.content.setVisible(expanded)

    def set_expanded(self, expanded):
        self.toggle.setChecked(expanded)


def menu_button(title, actions, *, compact=False):
    button = DropDownToolButton(FIF.MORE) if compact else DropDownPushButton(title)
    button.setToolTip(title)
    if compact:
        button.setFixedSize(40, 40)
    menu = RoundMenu(parent=button)
    for label, callback in actions:
        action = Action(label, menu)
        action.triggered.connect(callback)
        menu.addAction(action)
    button.setMenu(menu)
    return button


class MetadataEditor(QWidget):
    """User-facing text fields; serialization stays inside the archive layer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.table = TableWidget(self)
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["字段", "值"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setFixedHeight(120)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        add = PushButton("添加字段")
        add.clicked.connect(self.add_row)
        remove = PushButton("移除选中行")
        remove.clicked.connect(self.remove_row)
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)

    def add_row(self):
        index = self.table.rowCount()
        self.table.insertRow(index)
        for column in range(2):
            self.table.setItem(index, column, QTableWidgetItem(""))
        self.table.setCurrentCell(index, 0)
        self.table.editItem(self.table.item(index, 0))

    def remove_row(self):
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def set_value(self, values):
        if isinstance(values, str):
            values = json.loads(values or "{}")
        self.table.setRowCount(0)
        for key, value in (values or {}).items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.table.setItem(row, 1, QTableWidgetItem(str(value)))

    def value(self):
        result = {}
        for row in range(self.table.rowCount()):
            key_item, value_item = self.table.item(row, 0), self.table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            value = value_item.text().strip() if value_item else ""
            if not key and not value:
                continue
            if not key or key in result:
                raise ValueError("自定义字段名称不能为空或重复")
            result[key] = value
        return result


def muted(text, parent=None):
    label = CaptionLabel(text, parent)
    label.setObjectName("muted")
    label.setTextColor(QColor("#858BA0"), QColor("#858BA0"))
    label.setWordWrap(True)
    return label


def video_duration(milliseconds):
    seconds = milliseconds // 1000
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def heading(title, subtitle="", eyebrow=None):
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    label = BodyLabel(title)
    label.setObjectName("pageTitle")
    label.setTextColor(QColor("#252B43"), QColor("#252B43"))
    layout.addWidget(label)
    if subtitle:
        layout.addWidget(muted(subtitle))
    return widget


def panel(title, subtitle=None):
    card = CardWidget()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 16, 18, 18)
    layout.setSpacing(12)
    if title:
        layout.addWidget(SubtitleLabel(title))
    if subtitle:
        layout.addWidget(muted(subtitle))
    return card, layout


def confirm(parent, title, message, destructive=False):
    dialog = MessageBox(title, message, parent.window())
    dialog.yesButton.setText("确认删除" if destructive else "确认")
    dialog.cancelButton.setText("取消")
    return bool(dialog.exec())


class FormDialog(MessageBoxBase):
    def __init__(self, parent, title, fields, values=None):
        super().__init__(parent.window())
        self.viewLayout.addWidget(SubtitleLabel(title, self))
        self.fields = {}
        self.error_label = muted("")
        self.error_label.setStyleSheet("color: #BD3535")
        form = QFormLayout()
        form.setSpacing(12)
        for key, label, multiline in fields:
            if key == "custom":
                edit = MetadataEditor(self)
                edit.set_value((values or {}).get(key, {}))
            else:
                edit = TextEdit(self) if multiline else LineEdit(self)
            text = str((values or {}).get(key, ""))
            if key == "custom":
                pass
            elif multiline:
                edit.setPlainText(text)
                edit.setFixedHeight(80)
            else:
                edit.setText(text)
                edit.setClearButtonEnabled(True)
            self.fields[key] = edit
            form.addRow(BodyLabel(label), edit)
        self.viewLayout.addLayout(form)
        self.viewLayout.addWidget(self.error_label)
        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(470)

    def values(self):
        return {
            k: e.value()
            if isinstance(e, MetadataEditor)
            else e.toPlainText().strip()
            if isinstance(e, TextEdit)
            else e.text().strip()
            for k, e in self.fields.items()
        }

    def validate(self):
        try:
            values = self.values()
        except ValueError as error:
            self.error_label.setText(str(error))
            return False
        if "name" in values and (not values["name"] or len(values["name"]) > 200):
            self.error_label.setText("名称不能为空，且不能超过 200 个字符")
            return False
        return True


def button_row(*buttons):
    layout = QHBoxLayout()
    for button in buttons:
        layout.addWidget(button)
    layout.addStretch()
    return layout


def list_view(parent, model, delegate, *, compact=False):
    from PySide6.QtWidgets import QAbstractItemView, QListView

    view = QListView(parent)
    view.setObjectName("gallery")
    view.setModel(model)
    view.setItemDelegate(delegate)
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setResizeMode(QListView.ResizeMode.Adjust)
    view.setMovement(QListView.Movement.Static)
    view.setUniformItemSizes(True)
    view.setMouseTracking(True)
    view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    if compact:
        view.setFlow(QListView.Flow.LeftToRight)
        view.setWrapping(False)
        view.setFixedHeight(140)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return view
