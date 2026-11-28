import json

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PushButton,
    RoundMenu,
    SubtitleLabel,
    TableWidget,
    TextEdit,
    ToolButton,
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

from .theme import (
    BORDER,
    BORDER_SUBTLE,
    DANGER,
    INK,
    INK_SECONDARY,
    MUTED,
    RADIUS_CARD,
    SURFACE,
    SURFACE_SUBTLE,
)
from .theme import SoftCard as CardWidget

STYLE = f"""
QLabel#muted {{ color: {MUTED}; }}
QLabel#pageTitle {{ color: {INK}; font-size: 24px; font-weight: 600; }}
QLabel#bigNumber {{ color: {INK}; font-size: 24px; font-weight: 600; }}
QListView#gallery {{ background: transparent; border: none; outline: none; }}
QWidget#panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


class IconBadge(QWidget):
    """Crisp Fluent icon glyph rendered at a fixed optical size and color."""

    def __init__(self, icon, color=INK_SECONDARY, size=15, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._color = color
        self._size = size
        self.setFixedSize(size + 2, size + 2)

    def set_icon(self, icon, color=None):
        self._icon = icon
        if color is not None:
            self._color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        offset = (self.width() - self._size) / 2
        self._icon.render(painter, QRectF(offset, offset, self._size, self._size), fill=self._color)


class ComboBox(FluentComboBox):
    """Keep long archive names discoverable without expanding the whole window."""

    def __init__(self, parent=None):
        self._full_text = ""
        super().__init__(parent)
        self.setMinimumHeight(36)
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
                self._full_text, Qt.TextElideMode.ElideRight, max(20, self.width() - 38)
            ),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def sizeHint(self):
        return QSize(168, 36)

    def minimumSizeHint(self):
        return QSize(80, 36)


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


def divider(parent=None):
    line = QFrame(parent)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {BORDER_SUBTLE}; border: none;")
    return line


def icon_button(icon, tooltip, callback=None, *, soft=False, danger=False):
    button = ToolButton(icon)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setFixedSize(36, 36)
    button.setProperty("vannusIconButton", True)
    if soft:
        button.setProperty("softAction", True)
    if danger:
        button.setProperty("dangerAction", True)
    if callback is not None:
        button.clicked.connect(callback)
    return button


class FoldSection(QWidget):
    """Collapsible panel section with crisp Fluent icon, title, summary, and disclosure chevron."""

    def __init__(self, title, parent=None, *, icon=None):
        super().__init__(parent)
        self.title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(divider(self))

        self.toggle = QPushButton("", self)
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setAccessibleName(title)
        self.toggle.setProperty("foldControl", True)
        self.toggle.setFixedHeight(34)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        header = QHBoxLayout(self.toggle)
        header.setContentsMargins(6, 0, 6, 0)
        header.setSpacing(8)
        if icon is not None:
            badge = IconBadge(icon, INK_SECONDARY, size=14, parent=self.toggle)
            badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            header.addWidget(badge)
        title_label = BodyLabel(title, self.toggle)
        title_label.setObjectName("sectionTitle")
        title_label.setTextColor(QColor(INK), QColor(INK))
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(title_label)
        header.addStretch(1)
        self.summary_label = CaptionLabel("", self.toggle)
        self.summary_label.setTextColor(QColor(MUTED), QColor(MUTED))
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.summary_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.summary_label.hide()
        header.addWidget(self.summary_label)
        self._chevron = IconBadge(FIF.CHEVRON_RIGHT_MED, MUTED, size=11, parent=self.toggle)
        self._chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header.addWidget(self._chevron)
        layout.addWidget(self.toggle)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(2, 4, 2, 10)
        self.content_layout.setSpacing(10)
        layout.addWidget(self.content)
        self.content.hide()
        self.toggle.toggled.connect(self._toggle)

    def _toggle(self, expanded):
        self._chevron.set_icon(FIF.CHEVRON_DOWN_MED if expanded else FIF.CHEVRON_RIGHT_MED)
        self.content.setVisible(expanded)

    def set_expanded(self, expanded):
        self.toggle.setChecked(expanded)

    def set_summary(self, text):
        self.summary_label.setText(text)
        self.summary_label.setVisible(bool(text))


def menu_button(title, actions, *, compact=False, icon=None):
    """Create a menu trigger button without overlapping icons on compact tool buttons."""
    if compact:
        button = ToolButton(icon or FIF.MORE)
        button.setFixedSize(36, 36)
        button.setProperty("vannusIconButton", True)
    else:
        button = DropDownPushButton(icon, title) if icon is not None else DropDownPushButton(title)
    button.setToolTip(title)
    button.setAccessibleName(title)
    menu = RoundMenu(parent=button)
    for entry in actions:
        if len(entry) == 3:
            act_icon, label, callback = entry
            action = Action(act_icon, label, menu)
        else:
            label, callback = entry
            action = Action(label, menu)
        action.triggered.connect(callback)
        menu.addAction(action)
    if compact:
        button._menu = menu
        button.clicked.connect(lambda: menu.exec(button.mapToGlobal(QPoint(0, button.height() + 4))))
    else:
        button.setMenu(menu)
    return button


def keycap_row(keys, description, parent=None):
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(8)
    for key in keys if isinstance(keys, (list, tuple)) else [keys]:
        chip = QLabel(key, row)
        chip.setProperty("compactControl", True)
        chip.setStyleSheet(
            f"QLabel {{ background: {SURFACE_SUBTLE}; color: {INK}; border: 1px solid {BORDER}; "
            "border-radius: 6px; padding: 3px 8px; font-size: 12px; font-weight: 500; }"
        )
        layout.addWidget(chip)
    label = BodyLabel(description, row)
    label.setTextColor(QColor(INK_SECONDARY), QColor(INK_SECONDARY))
    layout.addWidget(label, 1)
    return row


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
        self.table.setFixedHeight(112)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        row.setSpacing(8)
        add = PushButton(FIF.ADD, "添加字段")
        add.clicked.connect(self.add_row)
        remove = PushButton(FIF.DELETE, "移除选中行")
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
    label.setTextColor(QColor(MUTED), QColor(MUTED))
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
    layout.setSpacing(4)
    label = BodyLabel(title)
    label.setObjectName("pageTitle")
    label.setTextColor(QColor(INK), QColor(INK))
    layout.addWidget(label)
    if subtitle:
        layout.addWidget(muted(subtitle))
    return widget


def panel(title, subtitle=None, *, icon=None):
    card = CardWidget()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 16, 18, 18)
    layout.setSpacing(10)
    if title:
        if icon is not None:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(IconBadge(icon, INK_SECONDARY, size=16, parent=card))
            row.addWidget(SubtitleLabel(title), 1)
            layout.addLayout(row)
        else:
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
        self.error_label.setStyleSheet(f"color: {DANGER};")
        form = QFormLayout()
        form.setSpacing(10)
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
                edit.setFixedHeight(78)
            else:
                edit.setText(text)
                edit.setClearButtonEnabled(True)
            self.fields[key] = edit
            form.addRow(BodyLabel(label), edit)
        self.viewLayout.addLayout(form)
        self.viewLayout.addWidget(self.error_label)
        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(460)

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
    layout.setSpacing(8)
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
        view.setFixedHeight(128)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return view
