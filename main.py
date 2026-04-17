'''KlingAI 图片生成桌面程序入口。'''

from __future__ import annotations

import csv
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from api import DEFAULT_CONFIG_PATH, KlingAIClient, configure_logger, load_config


# 列定义只保留最核心的业务字段，避免界面被次要信息占满。
COLUMN_KEYS = ['image_path', 'prompt', 'status', 'saved_path']
COLUMN_LABELS = ['图片路径', '提示词', '状态', '结果文件']

# 常见导入表头别名，用于兼容中文与英文命名。
IMAGE_COLUMN_ALIASES = ['image_path', '图片路径', '图片', 'image', 'path']
PROMPT_COLUMN_ALIASES = ['prompt', '提示词', '描述词', '文案']


def normalize_header_name(header_name: str) -> str:
    '''把表头标准化，便于兼容不同来源的表格文件。'''

    return str(header_name).strip().lower().replace(' ', '').replace('_', '')


def pick_row_value(row_data: dict[str, str], aliases: list[str]) -> str:
    '''从一行 CSV 数据中按别名列表提取字段。'''

    normalized_mapping = {
        normalize_header_name(header_name): str(cell_value).strip()
        for header_name, cell_value in row_data.items()
    }
    for alias in aliases:
        normalized_alias = normalize_header_name(alias)
        if normalized_alias in normalized_mapping:
            return normalized_mapping[normalized_alias]
    return ''


def build_output_name(image_reference: str) -> str:
    '''根据原图片名称生成“原名 + _ai”的输出文件名主体。'''

    normalized_reference = image_reference.strip()
    if not normalized_reference:
        return 'generated_ai'

    parsed_reference = urlparse(normalized_reference)
    if parsed_reference.scheme in {'http', 'https'}:
        candidate_name = Path(unquote(parsed_reference.path)).stem
    else:
        candidate_name = Path(normalized_reference).stem

    safe_name = candidate_name or 'generated'
    return f'{safe_name}_ai'


def parse_table_file(file_path: str) -> list[dict[str, str]]:
    '''读取 CSV 表格，并抽取图片路径与提示词字段。'''

    resolved_path = Path(file_path)
    sample_text = resolved_path.read_text(encoding='utf-8-sig')
    try:
        dialect = csv.Sniffer().sniff(sample_text[:1024])
    except csv.Error:
        dialect = csv.excel

    parsed_rows: list[dict[str, str]] = []
    with resolved_path.open('r', encoding='utf-8-sig', newline='') as csv_file:
        reader = csv.DictReader(csv_file, dialect=dialect)
        for row in reader:
            image_path = pick_row_value(row, IMAGE_COLUMN_ALIASES)
            prompt = pick_row_value(row, PROMPT_COLUMN_ALIASES)
            if not image_path and not prompt:
                continue

            parsed_rows.append(
                {
                    'image_path': image_path,
                    'prompt': prompt,
                    'status': '待生成',
                    'saved_path': '',
                }
            )

    return parsed_rows


class GenerateWorker(QThread):
    '''后台生成线程。

    线程只负责串行执行任务和回传结果，所有界面更新仍在主线程完成。
    '''

    row_started = Signal(int, str)
    row_finished = Signal(int, dict)
    log_message = Signal(str)
    batch_finished = Signal()

    def __init__(self, client: KlingAIClient, rows: list[tuple[int, dict[str, str]]]) -> None:
        '''初始化待执行的任务列表。'''

        super().__init__()
        self.client = client
        self.rows = rows

    def run(self) -> None:
        '''依次执行每一行任务。'''

        for row_index, row_data in self.rows:
            self.row_started.emit(row_index, '生成中')
            self.log_message.emit(f'开始生成：第 {row_index + 1} 行')
            try:
                result = self.client.run_task(row_data)
                saved_path = result.get('saved_path', '')
                self.row_finished.emit(
                    row_index,
                    {
                        'status': '已完成',
                        'saved_path': saved_path,
                        'message': f'已保存到 {saved_path}',
                    },
                )
                self.log_message.emit(f'生成完成：第 {row_index + 1} 行')
            except Exception as exc:  # noqa: BLE001 - 线程内需要统一捕获异常并反馈给界面。
                self.row_finished.emit(
                    row_index,
                    {
                        'status': '失败',
                        'saved_path': '',
                        'message': str(exc),
                    },
                )
                self.log_message.emit(f'生成失败：第 {row_index + 1} 行，原因：{exc}')

        self.batch_finished.emit()


class MainWindow(QMainWindow):
    '''最简图片生成主窗口。'''

    def __init__(self) -> None:
        '''读取配置并构建主界面。'''

        super().__init__()
        self.setWindowTitle('KlingAI 图片生成')
        self.resize(1320, 840)

        self.config = load_config(DEFAULT_CONFIG_PATH)
        configure_logger(self.config.get('log_dir', '日志'))
        self.client = KlingAIClient(self.config)
        self.worker_thread: GenerateWorker | None = None

        self.table_widget = QTableWidget(0, len(COLUMN_KEYS))
        self.status_badge = QLabel('等待导入表格')
        self.source_preview_label = QLabel('选择表格中的一行后查看原图')
        self.result_preview_label = QLabel('生成完成后在这里预览结果')
        self.log_output = QPlainTextEdit()

        self.setup_ui()
        self.append_task_row()
        self.apply_styles()

    def setup_ui(self) -> None:
        '''构建精简后的主界面布局。'''

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(16)

        header_card = QFrame()
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(22, 18, 22, 18)
        header_layout.setSpacing(6)

        title_label = QLabel('KlingAI 图片生成器')
        title_label.setObjectName('title_label')
        subtitle_label = QLabel('从表格读取图片路径和提示词，批量生成新图片并保存为“原名_ai”。')
        subtitle_label.setObjectName('subtitle_label')
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)

        action_card = QFrame()
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(18, 14, 18, 14)
        action_layout.setSpacing(12)

        import_button = QPushButton('导入表格')
        add_button = QPushButton('新增一行')
        remove_button = QPushButton('删除选中')
        run_button = QPushButton('开始生成')
        run_button.setObjectName('primary_button')

        import_button.clicked.connect(self.import_table)
        add_button.clicked.connect(self.append_task_row)
        remove_button.clicked.connect(self.remove_selected_rows)
        run_button.clicked.connect(self.start_generation)

        action_layout.addWidget(import_button)
        action_layout.addWidget(add_button)
        action_layout.addWidget(remove_button)
        action_layout.addStretch(1)
        action_layout.addWidget(self.status_badge)
        action_layout.addWidget(run_button)

        self.table_widget.setHorizontalHeaderLabels(COLUMN_LABELS)
        self.table_widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_widget.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table_widget.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.itemSelectionChanged.connect(self.refresh_previews)
        self.table_widget.cellDoubleClicked.connect(self.on_cell_double_clicked)

        source_card = self.create_preview_card('原图预览', self.source_preview_label)
        result_card = self.create_preview_card('结果预览', self.result_preview_label)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(12)
        preview_layout.addWidget(source_card)
        preview_layout.addWidget(result_card)

        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText('生成日志会显示在这里')
        log_card = QFrame()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 14, 16, 14)
        log_layout.setSpacing(8)
        log_layout.addWidget(QLabel('运行日志'))
        log_layout.addWidget(self.log_output)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)
        right_layout.addWidget(preview_panel, stretch=3)
        right_layout.addWidget(log_card, stretch=2)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.table_widget)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)

        root_layout.addWidget(header_card)
        root_layout.addWidget(action_card)
        root_layout.addWidget(splitter, stretch=1)

    def create_preview_card(self, title_text: str, preview_label: QLabel) -> QFrame:
        '''构建统一风格的预览卡片。'''

        preview_label.setAlignment(Qt.AlignCenter)
        preview_label.setMinimumHeight(220)
        preview_label.setWordWrap(True)

        card = QFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_label = QLabel(title_text)
        title_label.setObjectName('section_title')
        layout.addWidget(title_label)
        layout.addWidget(preview_label, stretch=1)
        return card

    def apply_styles(self) -> None:
        '''应用统一的浅色卡片式界面样式。'''

        self.setStyleSheet(
            '''
            QMainWindow {
                background: #f3efe7;
            }
            QFrame {
                background: #fffaf2;
                border: 1px solid #e7dcc7;
                border-radius: 18px;
            }
            QLabel {
                color: #2a2d34;
                font-size: 13px;
            }
            QLabel#title_label {
                font-size: 28px;
                font-weight: 700;
                color: #1d3557;
            }
            QLabel#subtitle_label {
                color: #5f6b76;
                font-size: 13px;
            }
            QLabel#section_title {
                font-size: 15px;
                font-weight: 600;
                color: #1d3557;
            }
            QPushButton {
                background: #ffffff;
                color: #1d3557;
                border: 1px solid #d6c7ae;
                border-radius: 12px;
                padding: 10px 18px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f7f0e2;
            }
            QPushButton#primary_button {
                background: #1d7a72;
                color: #ffffff;
                border: none;
            }
            QPushButton#primary_button:hover {
                background: #17665f;
            }
            QTableWidget {
                background: #fffdf8;
                alternate-background-color: #faf4e9;
                border: 1px solid #e0d4be;
                border-radius: 16px;
                gridline-color: #eadfcb;
                color: #2a2d34;
                selection-background-color: #dfece4;
                selection-color: #1d3557;
            }
            QHeaderView::section {
                background: #efe5d3;
                color: #1d3557;
                border: none;
                padding: 10px;
                font-size: 13px;
                font-weight: 700;
            }
            QPlainTextEdit {
                background: #fffdf8;
                border: 1px solid #e0d4be;
                border-radius: 12px;
                padding: 10px;
                color: #2a2d34;
            }
            '''
        )
        self.status_badge.setStyleSheet(
            '''
            background: #e6f2ef;
            color: #17665f;
            border-radius: 12px;
            padding: 8px 14px;
            font-weight: 600;
            '''
        )

    def append_task_row(self, image_path: str = '', prompt: str = '') -> None:
        '''新增一行任务数据。'''

        row_index = self.table_widget.rowCount()
        self.table_widget.insertRow(row_index)
        default_values = {
            'image_path': image_path,
            'prompt': prompt,
            'status': '待生成',
            'saved_path': '',
        }
        for column_index, column_key in enumerate(COLUMN_KEYS):
            self.table_widget.setItem(row_index, column_index, QTableWidgetItem(default_values[column_key]))

    def import_table(self) -> None:
        '''从 CSV 表格导入任务。'''

        file_path, _ = QFileDialog.getOpenFileName(self, '导入表格', '', 'CSV 文件 (*.csv)')
        if not file_path:
            return

        try:
            rows = parse_table_file(file_path)
        except Exception as exc:  # noqa: BLE001 - 读取表格时需要把错误直接反馈给用户。
            QMessageBox.critical(self, '导入失败', str(exc))
            return

        self.table_widget.setRowCount(0)
        for row in rows:
            self.append_task_row(row['image_path'], row['prompt'])

        if not rows:
            self.append_task_row()

        self.status_badge.setText(f'已导入 {len(rows)} 条任务')
        self.log(f'导入表格完成：{file_path}')

    def remove_selected_rows(self) -> None:
        '''删除当前选中的任务行。'''

        selected_rows = sorted({index.row() for index in self.table_widget.selectedIndexes()}, reverse=True)
        for row_index in selected_rows:
            self.table_widget.removeRow(row_index)

        if self.table_widget.rowCount() == 0:
            self.append_task_row()

        self.refresh_previews()

    def on_cell_double_clicked(self, row_index: int, column_index: int) -> None:
        '''双击图片路径列时弹出文件选择器，简化手工录入流程。'''

        if column_index != COLUMN_KEYS.index('image_path'):
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            '选择图片',
            '',
            '图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)',
        )
        if not file_path:
            return

        self.set_cell_value(row_index, 'image_path', file_path)
        self.refresh_previews()

    def read_row_data(self, row_index: int) -> dict[str, str]:
        '''读取单行表格数据。'''

        row_data: dict[str, str] = {}
        for column_index, column_key in enumerate(COLUMN_KEYS):
            cell_item = self.table_widget.item(row_index, column_index)
            row_data[column_key] = cell_item.text().strip() if cell_item else ''
        return row_data

    def set_cell_value(self, row_index: int, column_key: str, value: str) -> None:
        '''更新指定单元格的内容。'''

        column_index = COLUMN_KEYS.index(column_key)
        item = self.table_widget.item(row_index, column_index)
        if item is None:
            item = QTableWidgetItem()
            self.table_widget.setItem(row_index, column_index, item)
        item.setText(value)

    def collect_rows_to_run(self) -> list[tuple[int, dict[str, str]]]:
        '''筛选出可执行的任务行，并补全输出文件名。'''

        rows_to_run: list[tuple[int, dict[str, str]]] = []
        for row_index in range(self.table_widget.rowCount()):
            row_data = self.read_row_data(row_index)
            image_path = row_data.get('image_path', '')
            prompt = row_data.get('prompt', '')

            if not image_path or not prompt:
                self.set_cell_value(row_index, 'status', '已跳过')
                continue

            row_data['output_name'] = build_output_name(image_path)
            rows_to_run.append((row_index, row_data))

        return rows_to_run

    def start_generation(self) -> None:
        '''启动批量图片生成。'''

        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, '提示', '当前已有任务在执行，请等待完成。')
            return

        rows_to_run = self.collect_rows_to_run()
        if not rows_to_run:
            QMessageBox.warning(self, '提示', '没有可执行的任务，请确认表格中同时包含图片路径和提示词。')
            return

        self.worker_thread = GenerateWorker(self.client, rows_to_run)
        self.worker_thread.row_started.connect(self.on_row_started)
        self.worker_thread.row_finished.connect(self.on_row_finished)
        self.worker_thread.log_message.connect(self.log)
        self.worker_thread.batch_finished.connect(self.on_batch_finished)
        self.worker_thread.start()

        self.table_widget.setDisabled(True)
        self.status_badge.setText(f'正在生成 {len(rows_to_run)} 条任务')
        self.log('批量生成已启动')

    def on_row_started(self, row_index: int, status_text: str) -> None:
        '''更新单行开始执行时的状态。'''

        self.set_cell_value(row_index, 'status', status_text)

    def on_row_finished(self, row_index: int, result: dict[str, str]) -> None:
        '''写回单行执行结果。'''

        self.set_cell_value(row_index, 'status', result.get('status', '未知'))
        self.set_cell_value(row_index, 'saved_path', result.get('saved_path', ''))
        self.status_badge.setText(f'第 {row_index + 1} 行：{result.get("status", "未知")}')
        self.refresh_previews()

    def on_batch_finished(self) -> None:
        '''批量任务全部结束后的收尾逻辑。'''

        self.table_widget.setDisabled(False)
        self.status_badge.setText('全部生成完成')
        self.log('批量生成结束')
        QMessageBox.information(self, '提示', '全部任务已经处理完成。')

    def log(self, message: str) -> None:
        '''向日志面板追加一行文本。'''

        self.log_output.appendPlainText(message)

    def refresh_previews(self) -> None:
        '''刷新原图与结果图的预览区域。'''

        current_row = self.table_widget.currentRow()
        if current_row < 0:
            self.set_preview_label(self.source_preview_label, '', '选择表格中的一行后查看原图')
            self.set_preview_label(self.result_preview_label, '', '生成完成后在这里预览结果')
            return

        row_data = self.read_row_data(current_row)
        self.set_preview_label(
            self.source_preview_label,
            row_data.get('image_path', ''),
            '当前图片路径不是本地文件，无法预览',
        )
        self.set_preview_label(
            self.result_preview_label,
            row_data.get('saved_path', ''),
            '结果图片还未生成',
        )

    def set_preview_label(self, label_widget: QLabel, file_path: str, fallback_text: str) -> None:
        '''根据路径更新预览图片或提示文字。'''

        if file_path and Path(file_path).exists():
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(
                    420,
                    260,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                label_widget.setPixmap(scaled_pixmap)
                label_widget.setText('')
                return

        label_widget.setPixmap(QPixmap())
        label_widget.setText(fallback_text)


def main() -> int:
    '''程序主入口。'''

    application = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    return application.exec()


if __name__ == '__main__':
    sys.exit(main())
