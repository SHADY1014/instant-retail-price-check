"""
即时零售截图价格核查工具 - PyQt5 桌面 GUI

使用流程：
  1. 导入美团截图（拖拽/选择/压缩包）
  2. 点击"开始识别" -> macOS Vision OCR + 字段解析
  3. 在表格中预览/修正识别结果
     - 可直接编辑每行的"所属区域"
     - 选中多行后点击"批量设置区域"一次性修改
  4. 点击"导出Excel" -> 自动按品牌分类，写入4个子表

完全本地运行，零网络请求，零封号风险。
"""

import os
import sys
import tempfile
import threading
import zipfile
from threading import Event
from datetime import datetime

from PyQt5.QtCore import Qt, QThread, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QPixmap, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

# 确保能 import 同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from city_pool import format_region, get_cities, get_provinces
from ocr_engine import run_ocr_parallel
from field_parser import FormFields, parse_ocr_to_fields, detect_brand
from review_rules import find_review_issues
from city_detector import detect_city_in_region
import summary_generator
import summary_speech
import excel_writer


def _open_folder(path):
    """Open a folder using the host operating system."""
    import subprocess
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def parse_price(val):
    """解析价格字符串为 float，处理 ¥/逗号/全角数字/单位等格式"""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return 0.0
    import re as _re
    s = _re.sub(r'[¥$￥]', '', s)
    s = _re.sub(r'[元块]', '', s)
    s = s.replace(',', '').replace('，', '')
    s = s.translate(str.maketrans('０１２３４５６７８９．', '0123456789.'))
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


# 表单列定义（对应 Excel A~P）
# 注意：所属区域列(1)是可编辑的，用户可逐行修改
TABLE_COLUMNS = [
    ("分公司", 80),         # A - 默认"漓泉销售公司"
    ("所属区域", 100),       # B - 可编辑，用户可逐行修改
    ("平台", 70),           # C
    ("店铺名称", 140),       # D
    ("产品名称", 160),       # E
    ("原价", 60),           # F
    ("成交价", 60),         # G
    ("商品优惠", 70),        # H
    ("满减", 50),           # I
    ("优惠券", 60),          # J
    ("红包", 50),           # K
    ("配送费", 60),         # L
    ("品牌", 50),           # 品牌分类列（自动识别，只读）
    ("备注", 80),           # P
]


class OCRWorker(QThread):
    """后台 OCR 识别线程"""

    progress = pyqtSignal(int, int, str)  # current, total, message
    finished_ocr = pyqtSignal(dict, list, bool)

    def __init__(self, image_paths):
        super().__init__()
        self.image_paths = image_paths
        self._cancel_event = Event()

    def cancel(self):
        """Request cooperative cancellation after currently running OCR calls."""
        self._cancel_event.set()

    def run(self):
        results = {}
        total = len(self.image_paths)
        if total == 0:
            self.progress.emit(total, total, "识别完成")
            self.finished_ocr.emit(results, [], False)
            return

        # Vision OCR 并行批量识别（8 workers，吞吐约 2.5 倍于串行）
        lock = threading.Lock()

        def on_done(done, _total, path):
            with lock:
                if path:
                    message = f"识别中({done}/{_total}): {os.path.basename(path)}"
                else:
                    message = "识别完成"
                self.progress.emit(done, _total, message)

        ocr_map = run_ocr_parallel(
            self.image_paths,
            progress_callback=on_done,
            should_cancel=self._cancel_event.is_set,
        )

        retry_paths = []
        for path in self.image_paths:
            ocr_data = ocr_map.get(path)
            if ocr_data is None:
                fields = FormFields()
                fields.remark = "OCR 未完成：已取消，可重试"
                retry_paths.append(path)
            elif isinstance(ocr_data, dict) and "error" in ocr_data:
                fields = FormFields()
                fields.remark = f"OCR失败: {ocr_data['error']}"
                retry_paths.append(path)
            else:
                fields = parse_ocr_to_fields(ocr_data)
            results[path] = fields

        message = "识别已取消" if self._cancel_event.is_set() else "识别完成"
        self.progress.emit(len(ocr_map), total, message)
        self.finished_ocr.emit(results, retry_paths, self._cancel_event.is_set())


class CityDetectWorker(QThread):
    """后台线程：通过店铺名自动识别城市"""

    progress = pyqtSignal(str)
    finished_cities = pyqtSignal(dict)  # {shop_name: city}

    def __init__(self, ocr_results, table, restrict_cities=None):
        super().__init__()
        self.ocr_results = ocr_results
        self.table = table
        # 限定城市集合（如 {"广州市","佛山市"}），None 表示暂不进行自动匹配
        self.restrict_cities = restrict_cities
        # 在主线程中提前收集店铺名列表，避免在 QThread.run() 中跨线程访问 QTableWidget
        self._shop_names = set()
        for row in range(table.rowCount()):
            shop_item = table.item(row, 3)  # D列=店铺名称
            if shop_item and shop_item.text().strip():
                self._shop_names.add(shop_item.text().strip())

    def run(self):
        # 使用主线程预先收集的店铺名列表（不在此处跨线程访问 QTableWidget）
        shop_names = self._shop_names

        if not shop_names:
            self.finished_cities.emit({})
            return

        # None 表示用户没有选择城市范围。为避免同名店铺跨城市误命中，
        # 自动匹配必须在明确范围内执行；取消选择时直接跳过。
        if not self.restrict_cities:
            logger.info("city_worker_skipped_without_scope shops=%d", len(shop_names))
            self.finished_cities.emit({})
            return

        # 学习库优先：历史人工确认的店铺直接命中（含店名规范化）
        # 仅信任 L1-L4（精确/别名/标准化/历史修正）；L5 模糊候选不自动使用，
        # 需人工确认后（learn）才会进入高可信命中
        learned = {}
        self.canonical_map = {}
        try:
            import database
            matched = database.batch_get_shop_city(list(shop_names))
            for name in shop_names:
                m = matched.get(name)
                if m and m["city"] and not m["conflict"] and m["level"] <= 4:
                    learned[name] = m["city"]
                    if m["shop_name"] != name:
                        self.canonical_map[name] = m["shop_name"]
        except Exception:
            self.canonical_map = {}

        # 城市标注只能来自人工投喂的学习库（source=manual/import），
        # 不再查旧 shop_city.db（其数据为早期自动积累，不可信）
        cached = dict(learned)

        # 如果指定了限定城市，只保留在限定范围内的结果（避免跨城误匹配）
        if self.restrict_cities:
            cached = {k: v for k, v in cached.items() if v in self.restrict_cities}

        cached_count = len(cached)
        total = len(shop_names)
        if self.restrict_cities:
            region_str = "、".join(sorted(self.restrict_cities))
            self.progress.emit(
                f"在 {region_str} 范围内匹配数据库，命中 {cached_count}/{total} 家，"
                f"剩余可点击\"🌐 联网识别城市\"搜索..."
            )
        else:
            self.progress.emit(
                f"本地数据库命中 {cached_count}/{total} 家，"
                f"剩余可点击\"🌐 联网识别城市\"指定区域搜索..."
            )

        self.finished_cities.emit(cached)


class ProvinceCitySelectDialog(QDialog):
    """OCR完成后选择省份+城市，用于限定数据库匹配范围

    支持多选省份（如同时选广东+广西），城市列表按选中的省份合并显示。
    选定后 CityDetectWorker 只在限定城市范围内匹配数据库，避免跨城误匹配。
    """

    def __init__(self, shop_count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择省份和城市")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._selected_cities = set()

        layout = QVBoxLayout(self)

        # 提示
        hint = QLabel(
            f"共识别到 {shop_count} 家店铺。\n"
            f"请勾选本批截图涉及的省份（可多选），再从城市列表中勾选对应城市。\n"
            f"未选择城市时不会自动匹配，之后可使用外置按钮处理。"
        )
        hint.setStyleSheet("font-size: 13px; color: #333; padding: 10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 省份多选（复选框）
        from PyQt5.QtWidgets import QScrollArea
        prov_row = QHBoxLayout()
        prov_row.addWidget(QLabel("省份:"))
        self._prov_checks = {}  # {省份名: QCheckBox}
        for prov in get_provinces():
            cb = QCheckBox(prov)
            cb.stateChanged.connect(self._on_province_toggled)
            prov_row.addWidget(cb)
            self._prov_checks[prov] = cb
        prov_row.addStretch()
        prov_widget = QWidget()
        prov_widget.setLayout(prov_row)
        layout.addWidget(prov_widget)

        # 城市多选列表
        layout.addWidget(QLabel("城市（可多选，跨省可选）:"))
        self._city_list = QListWidget()
        self._city_list.setSelectionMode(QListWidget.MultiSelection)
        layout.addWidget(self._city_list)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.button(QDialogButtonBox.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.Cancel).setText("暂不设置城市")
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_province_toggled(self):
        """省份复选框变化时，重新生成城市列表（保留已选城市）"""
        # 记录当前选中的城市
        prev_selected = {it.text() for it in self._city_list.selectedItems()}

        self._city_list.clear()
        for prov, cb in self._prov_checks.items():
            if cb.isChecked():
                for city in get_cities(prov):
                    item = QListWidgetItem(f"{city}市")
                    # 如果之前选过该城市，保持选中
                    if f"{city}市" in prev_selected:
                        item.setSelected(True)
                    self._city_list.addItem(item)

    def _on_accept(self):
        selected = self._city_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "提示", '请至少选择一个城市，或点击"暂不设置城市"')
            return
        self._selected_cities = {it.text() for it in selected}
        self.accept()

    def get_selected_cities(self):
        """返回选中的城市集合（如 {"广州市","佛山市","南宁市"}），未选返回空集合"""
        return self._selected_cities

    def get_selected_province(self):
        """返回第一个选中的省份名（用于预填），未选返回空字符串"""
        for prov, cb in self._prov_checks.items():
            if cb.isChecked():
                return prov
        return ""


class BatchCityDialog(QDialog):
    """批次城市选择对话框 - 让用户一次性选择当前批次截图所在的城市"""

    def __init__(self, unmatched_shops, parent=None, default_province=""):
        """
        Args:
            unmatched_shops: list[str] 未识别城市的店铺名列表
            default_province: str 预填的省份名（如"广东"），减少重复选择
        """
        super().__init__(parent)
        self.setWindowTitle("选择批次城市")
        self.setModal(True)
        self.setMinimumWidth(450)
        self._city = ""

        layout = QVBoxLayout(self)

        # 提示
        hint = QLabel(
            f"有 {len(unmatched_shops)} 家店铺未在数据库中找到城市信息。\n"
            f"本批截图可能含多个城市的店铺，请逐个选择城市，"
            f"每次选完城市后会让你勾选属于该城市的店铺。"
        )
        hint.setStyleSheet("font-size: 13px; color: #333; padding: 10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 未识别店铺列表（折叠显示，前5个+省略号）
        shop_list_text = "、".join(unmatched_shops[:5])
        if len(unmatched_shops) > 5:
            shop_list_text += f"……等{len(unmatched_shops)}家"
        shop_label = QLabel(f"未识别店铺: {shop_list_text}")
        shop_label.setStyleSheet("font-size: 11px; color: #999; padding: 5px;")
        shop_label.setWordWrap(True)
        layout.addWidget(shop_label)

        # 省份+城市级联选择
        select_row = QHBoxLayout()
        select_row.addWidget(QLabel("省份:"))
        self._province_combo = QComboBox()
        self._province_combo.addItems([""] + get_provinces())
        self._province_combo.currentTextChanged.connect(self._on_province_changed)
        select_row.addWidget(self._province_combo)

        select_row.addWidget(QLabel("城市:"))
        self._city_combo = QComboBox()
        select_row.addWidget(self._city_combo)
        select_row.addStretch()
        layout.addLayout(select_row)

        # 预填省份（触发城市列表联动）
        if default_province:
            self._province_combo.setCurrentText(default_province)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.button(QDialogButtonBox.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.Cancel).setText("取消")
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_province_changed(self, province):
        """省份变化时更新城市下拉框"""
        self._city_combo.clear()
        if province:
            cities = get_cities(province)
            self._city_combo.addItems(cities)

    def _on_accept(self):
        """确定按钮：记录选择的城市"""
        city = self._city_combo.currentText().strip()
        if not city:
            QMessageBox.warning(self, "提示", "请选择城市")
            return
        self._city = format_region(city)  # "南宁市"
        self.accept()

    def get_selected_city(self):
        """返回选择的城市（如"南宁市"）"""
        return self._city


class RegionNetworkWorker(QThread):
    """在指定城市集合内联网识别店铺城市（后台线程）"""

    progress = pyqtSignal(str)
    finished_cities = pyqtSignal(dict)

    def __init__(self, shop_names, restrict_cities):
        super().__init__()
        self.shop_names = list(shop_names)
        self.restrict_cities = set(restrict_cities)

    def run(self):
        if not self.shop_names:
            self.finished_cities.emit({})
            return

        # 先批量查本地数据库。只接受所选范围内的 L1-L4 高可信命中；
        # 范围外或 L5 模糊候选必须继续受限联网查询，不能写入表格。
        import database
        matched = database.batch_get_shop_city(self.shop_names)
        cached = {
            name: value["city"]
            for name, value in matched.items()
            if value.get("city") in self.restrict_cities
            and not value.get("conflict")
            and value.get("level", 99) <= 4
        }
        results = dict(cached)
        pending = [n for n in self.shop_names if n not in cached]

        total = len(self.shop_names)
        self.progress.emit(
            f"数据库命中 {len(cached)}/{total} 家，"
            f"联网识别 {len(pending)} 家（限定区域）..."
        )

        # 限定区域联网识别
        for i, name in enumerate(pending):
            self.progress.emit(f"联网识别中: {name} ({i+1}/{len(pending)})")
            city = detect_city_in_region(name, self.restrict_cities)
            if city:
                results[name] = city
            import time
            time.sleep(0.3)

        self.finished_cities.emit(results)


class NetworkCityReviewDialog(QDialog):
    """Confirm network city candidates before writing them to the table."""

    def __init__(self, candidates, allowed_cities, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认联网识别城市")
        self.setMinimumSize(640, 360)
        self._city_choices = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "请确认每家店铺的候选城市。留空表示不采用该候选；"
            "仅可选择本次已授权的城市范围。"
        ))
        table = QTableWidget(len(candidates), 3)
        table.setHorizontalHeaderLabels(["店铺", "系统候选", "确认城市"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for row, shop_name in enumerate(sorted(candidates)):
            candidate = candidates[shop_name]
            table.setItem(row, 0, QTableWidgetItem(shop_name))
            table.setItem(row, 1, QTableWidgetItem(candidate or "未找到"))
            combo = QComboBox()
            combo.addItem("不采用")
            combo.addItems(sorted(allowed_cities))
            if candidate and candidate in allowed_cities:
                combo.setCurrentText(candidate)
            self._city_choices[shop_name] = combo
            table.setCellWidget(row, 2, combo)
        layout.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确认并填入")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_decisions(self):
        return {
            shop_name: combo.currentText() if combo.currentIndex() > 0 else ""
            for shop_name, combo in self._city_choices.items()
        }


class RegionSelectDialog(QDialog):
    """区域选择对话框 - 让用户选择省份+城市，用于联网识别限定区域

    支持多选省份+多选城市（可跨省选择，如同时选广东+广西的城市联网搜索）
    """

    def __init__(self, unmatched_shops, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择区域进行联网识别")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._selected_cities = set()

        layout = QVBoxLayout(self)

        # 提示
        hint = QLabel(
            f"有 {len(unmatched_shops)} 家店铺未在数据库中找到城市。\n"
            f"请勾选涉及省份（可多选），再从城市列表中勾选对应城市，系统将在选定区域内联网搜索。"
        )
        hint.setStyleSheet("font-size: 12px; color: #666;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 省份多选（复选框）
        prov_row = QHBoxLayout()
        prov_row.addWidget(QLabel("省份:"))
        self._prov_checks = {}  # {省份名: QCheckBox}
        for prov in get_provinces():
            cb = QCheckBox(prov)
            cb.stateChanged.connect(self._on_province_toggled)
            prov_row.addWidget(cb)
            self._prov_checks[prov] = cb
        prov_row.addStretch()
        prov_widget = QWidget()
        prov_widget.setLayout(prov_row)
        layout.addWidget(prov_widget)

        # 城市多选列表
        layout.addWidget(QLabel("城市（可多选，跨省可选）:"))
        self._city_list = QListWidget()
        self._city_list.setSelectionMode(QListWidget.MultiSelection)
        layout.addWidget(self._city_list)

        # 未识别店铺预览
        preview_group = QGroupBox(f"未识别店铺（{len(unmatched_shops)} 家）")
        preview_layout = QVBoxLayout()
        self._preview = QLabel("\n".join(sorted(unmatched_shops)[:20]))
        if len(unmatched_shops) > 20:
            self._preview.setText(self._preview.text() + f"\n...等共 {len(unmatched_shops)} 家")
        self._preview.setStyleSheet("font-size: 11px; color: #888;")
        self._preview.setWordWrap(True)
        preview_layout.addWidget(self._preview)
        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.button(QDialogButtonBox.Ok).setText("开始联网识别")
        btn_box.button(QDialogButtonBox.Cancel).setText("取消")
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_province_toggled(self):
        """省份复选框变化时，重新生成城市列表（保留已选城市）"""
        prev_selected = {it.text() for it in self._city_list.selectedItems()}
        self._city_list.clear()
        for prov, cb in self._prov_checks.items():
            if cb.isChecked():
                for city in get_cities(prov):
                    item = QListWidgetItem(f"{city}市")
                    if f"{city}市" in prev_selected:
                        item.setSelected(True)
                    self._city_list.addItem(item)

    def _on_accept(self):
        selected = self._city_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "提示", "请至少选择一个城市")
            return
        self._selected_cities = {it.text() for it in selected}
        self.accept()

    def get_selected_cities(self):
        """返回选中的城市集合（如 {"贵阳市","遵义市"} ）"""
        return self._selected_cities



class DropArea(QLabel):
    """支持拖拽上传图片或压缩包的区域"""

    IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
    ARCHIVE_EXTS = (".zip", ".rar", ".7z")

    def __init__(self, on_files_dropped, on_paste=None):
        super().__init__()
        self.on_files_dropped = on_files_dropped
        self.on_paste = on_paste
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(80)
        self.setStyleSheet(
            "QLabel {"
            "  border: 2px dashed #aaa;"
            "  border-radius: 8px;"
            "  background: #f9f9f9;"
            "  color: #666;"
            "  font-size: 14px;"
            "}"
            "QLabel:hover {"
            "  border-color: #0070C0;"
            "  background: #eef5ff;"
            "}"
        )
        self.setText("拖拽截图/压缩包到这里，或点击下方按钮选择，也可 Ctrl+V 粘贴截图\n支持多张图片批量导入，支持 zip 压缩包自动解压")

    def _is_accepted(self, path):
        lower = path.lower()
        return lower.endswith(self.IMAGE_EXTS) or lower.endswith(self.ARCHIVE_EXTS)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(self._is_accepted(u.toLocalFile()) for u in urls):
                event.acceptProposedAction()
                self.setStyleSheet(
                    "QLabel {"
                    "  border: 2px solid #0070C0;"
                    "  border-radius: 8px;"
                    "  background: #d0e8ff;"
                    "}"
                )

    def dragLeaveEvent(self, event):
        self.setStyleSheet(
            "QLabel {"
            "  border: 2px dashed #aaa;"
            "  border-radius: 8px;"
            "  background: #f9f9f9;"
            "  color: #666;"
            "  font-size: 14px;"
            "}"
        )

    def dropEvent(self, event):
        self.setStyleSheet(
            "QLabel {"
            "  border: 2px dashed #aaa;"
            "  border-radius: 8px;"
            "  background: #f9f9f9;"
            "  color: #666;"
            "  font-size: 14px;"
            "}"
        )
        files = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if self._is_accepted(path):
                files.append(path)
        if files:
            self.on_files_dropped(files)


class ProvinceSelectDialog(QDialog):
    """省份多选对话框：让用户勾选要包含的省份"""

    def __init__(self, parent=None, default_selected=None):
        super().__init__(parent)
        self.setWindowTitle("选择省份")
        self.setModal(True)
        self._checkboxes = {}

        layout = QVBoxLayout(self)

        # 提示
        hint = QLabel("请勾选要纳入统计的省份：")
        hint.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(hint)

        # 省份复选框
        from city_pool import get_provinces
        provinces = get_provinces()
        if default_selected is None:
            default_selected = set(provinces)  # 默认全选

        check_layout = QGridLayout()
        for i, prov in enumerate(provinces):
            cb = QCheckBox(prov)
            cb.setChecked(prov in default_selected)
            cb.setStyleSheet("font-size: 14px; padding: 4px 8px;")
            self._checkboxes[prov] = cb
            check_layout.addWidget(cb, i // 3, i % 3)
        layout.addLayout(check_layout)

        # 全选/全不选按钮
        btn_row = QHBoxLayout()
        select_all_btn = QPushButton("全选")
        select_all_btn.clicked.connect(lambda: self._set_all(True))
        btn_row.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("全不选")
        deselect_all_btn.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(deselect_all_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 确定/取消
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _set_all(self, checked):
        for cb in self._checkboxes.values():
            cb.setChecked(checked)

    def get_selected_provinces(self):
        """返回选中的省份列表"""
        return [prov for prov, cb in self._checkboxes.items() if cb.isChecked()]


class ProductSelectDialog(QDialog):
    """产品类型选择对话框：U8 或 1998"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择产品类型")
        self.setModal(True)
        self._product_type = None

        layout = QVBoxLayout(self)

        hint = QLabel("请选择要生成汇总表的产品类型：")
        hint.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(hint)

        info = QLabel(
            "燕京U8：合格线60元，第二档55元（云贵区域）\n"
            "漓泉1998：广东合格线70元、第二档65元；广西合格线60元、第二档55元"
        )
        info.setStyleSheet("font-size: 12px; color: #666; padding: 8px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # 两个按钮式选项
        self._u8_btn = QPushButton("燕京U8（合格线60元）")
        self._u8_btn.setStyleSheet(
            "font-size: 14px; font-weight: bold; background: #1565C0; "
            "color: white; padding: 12px; border: none;"
        )
        self._u8_btn.clicked.connect(lambda: self._select("u8"))
        layout.addWidget(self._u8_btn)

        self._p1998_btn = QPushButton("漓泉1998（广东70元 / 广西60元）")
        self._p1998_btn.setStyleSheet(
            "font-size: 14px; font-weight: bold; background: #E65100; "
            "color: white; padding: 12px; border: none;"
        )
        self._p1998_btn.clicked.connect(lambda: self._select("1998"))
        layout.addWidget(self._p1998_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

    def _select(self, product_type):
        self._product_type = product_type
        self.accept()

    def get_product_type(self):
        """返回 'u8' 或 '1998'，未选择返回 None"""
        return self._product_type


class DuplicateReviewDialog(QDialog):
    """重复核查对话框

    列出所有重复组（店铺名+平台+城市+理论成交价四者相同），
    每组展示多条记录，默认勾选除最新一条外的所有行，用户确认后返回要删除的行号列表。
    """

    def __init__(self, groups, parent=None):
        """
        Args:
            groups: list of {key, count, redundant, items} 每条 item 含 row, shop_name, platform, city, product_name, final_price, delivery_fee, theory_price, collected_at
        """
        super().__init__(parent)
        self.setWindowTitle("重复核查 - 勾选要删除的行")
        self.setModal(True)
        self.setMinimumSize(950, 600)
        self._to_delete_rows = []
        self._group_checkboxes = []  # 每组每行的 checkbox，结构 [[cb, row], ...]

        layout = QVBoxLayout(self)

        # 顶部汇总
        total_groups = len(groups)
        total_redundant = sum(g["redundant"] for g in groups)
        summary = QLabel(
            f"<b>发现 {total_groups} 组重复，共 {total_redundant} 条冗余记录可清理。</b><br>"
            f"<span style='color:#666; font-size:12px;'>"
            f"口径：店铺名 + 平台 + 城市 + 理论成交价（成交价−配送费）四者相同视为重复。"
            f"默认勾选每组中除最新一条外的所有记录，可手动调整。</span>"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        # 滚动区放重复组卡片
        from PyQt5.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(4, 4, 4, 4)

        if not groups:
            empty = QLabel("未发现重复商品")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: #27AE60; font-size: 16px; padding: 40px;")
            scroll_layout.addWidget(empty)
        else:
            for gi, g in enumerate(groups):
                card = self._build_group_card(gi, g)
                scroll_layout.addWidget(card)
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # 底部按钮
        btn_box = QHBoxLayout()
        select_all_btn = QPushButton("全选待删项")
        select_all_btn.clicked.connect(self._select_all_default)
        btn_box.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("清空选择")
        deselect_all_btn.clicked.connect(self._deselect_all)
        btn_box.addWidget(deselect_all_btn)

        btn_box.addStretch()

        ok_btn = QPushButton("删除选中行")
        ok_btn.setStyleSheet("font-weight: bold; background: #C00000; color: white; padding: 6px 20px;")
        ok_btn.clicked.connect(self._on_accept)
        btn_box.addWidget(ok_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(cancel_btn)
        layout.addLayout(btn_box)

    def _build_group_card(self, gi, group):
        """构建单个重复组卡片"""
        card = QGroupBox()
        k = group["key"]
        title = (f"重复组 #{gi + 1}  -  店铺[{k['shop_name']}]  平台[{k['platform']}]  "
                 f"城市[{k['city']}]  理论价¥{k['theory_price']:.2f}  "
                 f"(共 {group['count']} 条，冗余 {group['redundant']} 条)")
        card.setTitle(title)
        card.setStyleSheet("QGroupBox { font-weight: bold; padding-top: 14px; }"
                           "QGroupBox::title { left: 10px; padding: 0 4px; }")

        vbox = QVBoxLayout(card)

        # 表格
        tbl = QTableWidget()
        tbl.setColumnCount(8)
        tbl.setHorizontalHeaderLabels(["删", "行号", "产品", "成交价", "配送费", "理论价", "采集时间", "店铺"])
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.NoSelection)
        row_checks = []
        for ii, it in enumerate(group["items"]):
            r = tbl.rowCount()
            tbl.insertRow(r)
            cb = QCheckBox()
            # 默认勾选除第一条（最新）外的所有行
            if ii > 0:
                cb.setChecked(True)
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            tbl.setCellWidget(r, 0, cb_widget)
            tbl.setItem(r, 1, QTableWidgetItem(str(it["row"] + 1)))  # 显示 1-based 行号
            tbl.setItem(r, 2, QTableWidgetItem(it["product_name"][:25]))
            tbl.setItem(r, 3, QTableWidgetItem(f"¥{it['final_price']:.2f}"))
            tbl.setItem(r, 4, QTableWidgetItem(f"¥{it['delivery_fee']:.2f}"))
            price_item = QTableWidgetItem(f"¥{it['theory_price']:.2f}")
            price_item.setForeground(Qt.blue)
            tbl.setItem(r, 5, price_item)
            tbl.setItem(r, 6, QTableWidgetItem(it.get("collected_at", "")[:19]))
            tbl.setItem(r, 7, QTableWidgetItem(it["shop_name"][:18]))
            row_checks.append([cb, it["row"]])
            # 待删行浅黄底，保留行正常底
            if ii > 0:
                for c in range(8):
                    it_c = tbl.item(r, c)
                    if it_c:
                        it_c.setBackground(Qt.yellow)
                        it_c.setData(Qt.BackgroundRole, Qt.yellow)
        self._group_checkboxes.append(row_checks)
        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setStretchLastSection(True)
        vbox.addWidget(tbl)
        return card

    def _select_all_default(self):
        """勾选所有非首行（保留每组最新的一条）"""
        for row_checks in self._group_checkboxes:
            for idx, (cb, _row) in enumerate(row_checks):
                cb.setChecked(idx > 0)

    def _deselect_all(self):
        for row_checks in self._group_checkboxes:
            for cb, _row in row_checks:
                cb.setChecked(False)

    def _on_accept(self):
        """收集勾选的行号并关闭对话框（不在对话框内弹二次确认，避免模态嵌套导致 accept() 失效）"""
        self._to_delete_rows = []
        for row_checks in self._group_checkboxes:
            for cb, row in row_checks:
                if cb.isChecked():
                    self._to_delete_rows.append(row)
        if not self._to_delete_rows:
            QMessageBox.information(self, "提示", "未勾选任何要删除的行")
            return
        self.accept()

    def get_rows_to_delete(self):
        """返回要删除的表格行号列表（0-based），未确认返回空列表"""
        return self._to_delete_rows


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.image_paths = []
        self.ocr_results = {}  # {image_path: FormFields}
        self.ocr_worker = None
        self._retry_paths = []
        self._ocr_is_retry = False
        self._temp_dirs = []  # 压缩包解压的临时目录，退出时清理
        self._last_province = ""  # 用户上次选的省份，用于 BatchCityDialog 预填
        self._last_cities = set()  # 用户上次选的城市集合
        self._updating_review_state = False
        self._network_consent_at = self._load_network_consent()
        self._network_request_id = None

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("即时零售截图价格核查")
        # 保留足够的表格可用空间，同时允许较小分辨率的笔记本正常显示。
        self.setMinimumSize(960, 640)
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            width = min(1200, max(960, int(available.width() * 0.90)))
            height = min(820, max(640, int(available.height() * 0.86)))
            self.resize(width, height)
        self.setStyleSheet(
            "QWidget { font-family: 'Microsoft YaHei UI', 'PingFang SC', "
            "'Heiti SC', 'Arial Unicode MS'; font-size: 13px; }"
            "QPushButton, QToolButton { "
            "background: #FFFFFF; color: #1F2933; border: 1px solid #B8C2CC; "
            "border-radius: 4px; padding: 0 12px; }"
            "QPushButton:hover, QToolButton:hover { background: #F3F6F8; }"
            "QPushButton:disabled, QToolButton:disabled { "
            "background: #F5F6F7; color: #AAB2BA; border-color: #DDE2E7; }"
            "QPushButton#pasteAction { "
            "background: #16A34A; color: white; border-color: #15803D; }"
            "QPushButton#pasteAction:hover { background: #15803D; }"
            "QPushButton#primaryAction { "
            "font-weight: bold; background: #087CC1; color: white; "
            "border-color: #0668A3; }"
            "QPushButton#primaryAction:hover { background: #0668A3; }"
            "QPushButton#exportAction { "
            "font-weight: bold; background: #16803B; color: white; "
            "border-color: #126B31; }"
            "QPushButton#exportAction:hover { background: #126B31; }"
        )

        layout = QVBoxLayout(self)

        # ========== 顶部：图片导入 ==========
        img_group = QGroupBox("① 导入美团截图")
        img_layout = QVBoxLayout(img_group)

        self.drop_area = DropArea(self._on_files_added, self._paste_from_clipboard)
        img_layout.addWidget(self.drop_area)

        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("📂 选择图片")
        self.add_btn.clicked.connect(self._select_files)
        btn_layout.addWidget(self.add_btn)

        self.zip_btn = QPushButton("📦 上传压缩包")
        self.zip_btn.clicked.connect(self._select_archive)
        btn_layout.addWidget(self.zip_btn)

        self.paste_btn = QPushButton("📋 粘贴截图")
        self.paste_btn.setObjectName("pasteAction")
        self.paste_btn.setToolTip("从剪贴板粘贴截图（Ctrl+V）")
        self.paste_btn.clicked.connect(self._paste_from_clipboard)
        btn_layout.addWidget(self.paste_btn)

        self.clear_btn = QPushButton("🗑 清空列表")
        self.clear_btn.clicked.connect(self._clear_files)
        btn_layout.addWidget(self.clear_btn)

        self.ocr_btn = QPushButton("🔍 开始 OCR 识别")
        self.ocr_btn.setObjectName("primaryAction")
        self.ocr_btn.clicked.connect(self._start_ocr)
        btn_layout.addWidget(self.ocr_btn)

        self.cancel_ocr_btn = QPushButton("取消识别")
        self.cancel_ocr_btn.setEnabled(False)
        self.cancel_ocr_btn.clicked.connect(self._cancel_ocr)
        btn_layout.addWidget(self.cancel_ocr_btn)

        self.retry_ocr_btn = QPushButton("重试失败项")
        self.retry_ocr_btn.setEnabled(False)
        self.retry_ocr_btn.clicked.connect(self._retry_failed_ocr)
        btn_layout.addWidget(self.retry_ocr_btn)

        btn_layout.addStretch()

        self.file_count_label = QLabel("未选择图片")
        btn_layout.addWidget(self.file_count_label)

        img_layout.addLayout(btn_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        img_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666;")
        img_layout.addWidget(self.status_label)

        layout.addWidget(img_group)

        # ========== 下部：识别结果表格 ==========
        result_group = QGroupBox("② 识别与核对（可手动修正，所属区域可逐行编辑）")
        result_layout = QVBoxLayout(result_group)

        review_layout = QHBoxLayout()
        self.review_summary_label = QLabel("尚未识别记录")
        self.review_summary_label.setStyleSheet("font-weight: bold; color: #555;")
        review_layout.addWidget(self.review_summary_label)
        review_layout.addStretch()
        self.review_filter = QCheckBox("仅看待核对")
        self.review_filter.setEnabled(False)
        self.review_filter.setToolTip("显示缺少关键字段或 OCR 识别失败的记录")
        self.review_filter.toggled.connect(self._refresh_review_state)
        review_layout.addWidget(self.review_filter)
        result_layout.addLayout(review_layout)

        self.table = QTableWidget()
        col_count = len(TABLE_COLUMNS) + 1  # +1 for 图片列
        self.table.setColumnCount(col_count)
        headers = [h for h, _ in TABLE_COLUMNS] + ["图片"]
        self.table.setHorizontalHeaderLabels(headers)
        for i, (_, width) in enumerate(TABLE_COLUMNS):
            self.table.setColumnWidth(i, width)
        self.table.setColumnWidth(col_count - 1, 80)
        self.table.horizontalHeader().setStretchLastSection(False)
        # 允许复制：选中整行/单元格后 Ctrl+C 复制
        self.table.setSelectionBehavior(QTableWidget.SelectItems)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.itemChanged.connect(self._on_table_item_changed)
        # 添加 Ctrl+C 复制快捷键
        from PyQt5.QtWidgets import QShortcut
        copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        copy_shortcut.activated.connect(self._copy_table_selection)
        # 添加 Ctrl+V 粘贴截图快捷键（全局）
        paste_shortcut = QShortcut(QKeySequence("Ctrl+V"), self)
        paste_shortcut.activated.connect(self._paste_from_clipboard)
        result_layout.addWidget(self.table)

        # ③ 导出与辅助工具：仅保留导出为主操作，低频功能按业务分组。
        export_layout = QHBoxLayout()
        export_label = QLabel("③ 导出：完成核对后生成巡查表")
        export_label.setStyleSheet("color: #666; font-size: 12px;")
        export_layout.addWidget(export_label)

        self.export_btn = QPushButton("📋 导出 Excel")
        self.export_btn.setObjectName("exportAction")
        self.export_btn.clicked.connect(self._export_excel)
        export_layout.addWidget(self.export_btn)

        self.net_city_btn = QPushButton("🌐 联网识别城市")
        self.net_city_btn.setToolTip(
            "在选择的城市范围内搜索未匹配店铺；联网结果需确认后才会写入表格"
        )
        self.net_city_btn.clicked.connect(self._network_detect_city)
        export_layout.addWidget(self.net_city_btn)

        self.batch_city_btn = QPushButton("📍 一键设置城市")
        self.batch_city_btn.setToolTip(
            "为当前表格中未设置城市的店铺，按城市批量补充所属区域\n"
            "人工确认的店铺城市会写入知识库，供后续识别使用"
        )
        self.batch_city_btn.clicked.connect(self._batch_set_shop_cities)
        export_layout.addWidget(self.batch_city_btn)

        self.dedup_btn = QPushButton("🔍 查重核查")
        self.dedup_btn.setToolTip(
            "按店铺、平台、城市和理论成交价查找重复记录"
        )
        self.dedup_btn.clicked.connect(self._check_duplicates)
        export_layout.addWidget(self.dedup_btn)

        export_layout.addStretch()

        report_menu = QMenu(self)
        report_menu.addAction("生成汇总表（含截图）", self._generate_summary)
        report_menu.addAction("生成总结话术", self._generate_speech)
        report_tools = QToolButton()
        report_tools.setText("导出与汇总")
        report_tools.setMenu(report_menu)
        report_tools.setPopupMode(QToolButton.InstantPopup)
        report_tools.setToolTip("基于已导出的巡查表生成汇总或话术")
        export_layout.addWidget(report_tools)

        data_menu = QMenu(self)
        data_menu.addAction("打开知识库", self._open_knowledge_base)
        data_tools = QToolButton()
        data_tools.setText("数据管理")
        data_tools.setMenu(data_menu)
        data_tools.setPopupMode(QToolButton.InstantPopup)
        data_tools.setToolTip("查看和维护店铺城市学习库")
        export_layout.addWidget(data_tools)

        # 工具栏按钮统一高度；宽度随文字内容伸缩，避免窄屏时截断文字。
        for button in (
            self.add_btn, self.zip_btn, self.paste_btn, self.clear_btn,
        self.ocr_btn, self.cancel_ocr_btn, self.retry_ocr_btn,
            self.export_btn, self.net_city_btn, self.batch_city_btn, self.dedup_btn,
            report_tools, data_tools,
        ):
            button.setFixedHeight(32)

        result_layout.addLayout(export_layout)

        layout.addWidget(result_group)

        # ========== 底部签名 ==========
        signature = QLabel("Design By 创新业务中心-江凯豪")
        signature.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        signature.setStyleSheet("color: #999; font-size: 11px; padding: 2px 8px;")
        layout.addWidget(signature)

    def _load_network_consent(self):
        """Load an earlier local authorization without storing any screenshot data."""
        try:
            from database.schema import get_meta
            return get_meta("network_city_consent_at", "")
        except Exception:
            return ""

    def _network_detect_city(self):
        """联网识别城市：用户先选省份+城市，再对未识别店铺在选定区域内联网搜索"""
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "提示", "表格中没有数据，请先识别图片")
            return

        # 首次联网前告知用户隐私信息
        if not self._network_consent_at:
            reply = QMessageBox.question(
                self, "联网识别告知",
                "联网识别会将店铺名称发送至百度地图接口进行搜索。\n"
                "这是第三方服务，请确认您了解此隐私风险。\n\n"
                "是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
            try:
                import database
                self._network_consent_at = database.record_network_city_consent()
            except Exception as exc:
                QMessageBox.warning(self, "联网识别不可用", f"无法记录授权信息：{exc}")
                return

        # 收集未识别城市的唯一店铺名
        unmatched = set()
        for row in range(self.table.rowCount()):
            shop_item = self.table.item(row, 3)  # D列=店铺名称
            region_item = self.table.item(row, 1)  # B列=所属区域
            if shop_item and shop_item.text().strip():
                region = region_item.text().strip() if region_item else ""
                if not region:
                    unmatched.add(shop_item.text().strip())

        if not unmatched:
            QMessageBox.information(self, "提示", "所有店铺都已识别城市，无需联网识别")
            return

        # 弹窗让用户选区域
        dialog = RegionSelectDialog(sorted(unmatched), self)
        if dialog.exec_() != QDialog.Accepted:
            return

        restrict_cities = dialog.get_selected_cities()
        if not restrict_cities:
            return

        try:
            import database
            self._network_request_id = database.create_network_city_request(
                self._network_consent_at, restrict_cities, unmatched
            )
        except Exception as exc:
            QMessageBox.warning(self, "联网识别不可用", f"无法创建审计记录：{exc}")
            return

        # 启动后台联网识别线程
        self._region_worker = RegionNetworkWorker(sorted(unmatched), restrict_cities)
        self._region_worker.progress.connect(
            lambda msg: self.status_label.setText(msg)
        )
        self._region_worker.finished_cities.connect(self._on_region_cities_detected)
        self._region_worker.start()
        self.status_label.setText("联网识别中，请稍候...")

    def _on_region_cities_detected(self, shop_to_city):
        """Record candidates, then apply only user-confirmed city decisions."""
        if not self._network_request_id:
            self.status_label.setText("联网识别完成，但审计请求不存在")
            return

        all_candidates = {
            shop: shop_to_city.get(shop, "")
            for shop in self._region_worker.shop_names
        }
        try:
            import database
            database.record_network_city_candidates(
                self._network_request_id, all_candidates, source="region_match"
            )
        except Exception as exc:
            QMessageBox.warning(self, "审计记录失败", str(exc))
            return

        dialog = NetworkCityReviewDialog(
            all_candidates, self._region_worker.restrict_cities, self
        )
        if dialog.exec_() != QDialog.Accepted:
            self.status_label.setText("已保留联网候选，未应用任何城市结果")
            return
        shop_to_city = dialog.get_decisions()
        try:
            database.record_network_city_decisions(
                self._network_request_id, shop_to_city
            )
        except Exception as exc:
            QMessageBox.warning(self, "审计记录失败", str(exc))
            return

        updated = 0
        for row in range(self.table.rowCount()):
            shop_item = self.table.item(row, 3)
            if shop_item and shop_item.text().strip():
                city = shop_to_city.get(shop_item.text().strip())
                if city:
                    region = format_region(city.replace("市", ""))
                    region_item = self.table.item(row, 1)
                    if region_item:
                        region_item.setText(region)
                    else:
                        region_item = QTableWidgetItem(region)
                        self.table.setItem(row, 1, region_item)
                    updated += 1

        # 统计仍未识别的
        still_miss = 0
        for row in range(self.table.rowCount()):
            shop_item = self.table.item(row, 3)
            region_item = self.table.item(row, 1)
            if shop_item and shop_item.text().strip():
                region = region_item.text().strip() if region_item else ""
                if not region:
                    still_miss += 1

        self._sort_table_by_region()
        if still_miss > 0:
            self.status_label.setText(
                f"联网识别完成，已填入 {updated} 行城市，"
                f"剩余 {still_miss} 行未识别（可点击\"🌐 联网识别城市\"手动修正）"
            )
        else:
            self.status_label.setText(
                f"联网识别完成，已填入 {updated} 行城市，全部识别成功"
            )

    def _on_files_added(self, files):
        """处理拖拽或选择的文件，自动识别图片和压缩包"""
        image_exts = (".png", ".jpg", ".jpeg", ".webp")
        archive_exts = (".zip",)

        for f in files:
            lower = f.lower()
            if lower.endswith(image_exts):
                if f not in self.image_paths:
                    self.image_paths.append(f)
            elif lower.endswith(archive_exts):
                # 解压压缩包，提取图片
                extracted = self._extract_archive(f)
                for img_path in extracted:
                    if img_path not in self.image_paths:
                        self.image_paths.append(img_path)
        self._update_file_count()

    def _select_archive(self):
        """选择压缩包文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择压缩包", "",
            "压缩包文件 (*.zip);;所有文件 (*)"
        )
        if file_path:
            self._on_files_added([file_path])

    def _extract_archive(self, zip_path):
        """解压 zip 压缩包，返回其中所有图片文件的路径列表

        安全限制：最多 500 张图片、单文件 50MB、总解压 1GB，防止 ZIP bomb。
        """
        image_exts = (".png", ".jpg", ".jpeg", ".webp")
        extracted = []
        MAX_FILES = 500
        MAX_SINGLE_SIZE = 50 * 1024 * 1024      # 50MB
        MAX_TOTAL_SIZE = 1024 * 1024 * 1024     # 1GB
        total_size = 0

        try:
            # 创建临时解压目录
            extract_dir = tempfile.mkdtemp(prefix="meituan_ocr_")
            self._temp_dirs.append(extract_dir)  # 记录，程序退出时清理

            with zipfile.ZipFile(zip_path, 'r') as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    # 检查文件名是否安全（防止 ../ 路径穿越）
                    safe_name = os.path.basename(info.filename)
                    if not safe_name or safe_name.startswith('.'):
                        continue
                    if not safe_name.lower().endswith(image_exts):
                        continue

                    # 限制文件数量
                    if len(extracted) >= MAX_FILES:
                        QMessageBox.warning(
                            self, "解压限制",
                            f"压缩包内图片超过 {MAX_FILES} 张上限，仅提取前 {MAX_FILES} 张。"
                        )
                        break

                    # 限制单文件大小
                    file_size = info.file_size
                    if file_size > MAX_SINGLE_SIZE:
                        self.status_label.setText(
                            f"跳过过大文件: {safe_name} ({file_size // 1024 // 1024}MB)"
                        )
                        continue

                    # 限制总解压大小
                    if total_size + file_size > MAX_TOTAL_SIZE:
                        QMessageBox.warning(
                            self, "解压限制",
                            f"压缩包总解压大小超过 1GB 上限，仅提取已解压的 {len(extracted)} 张。"
                        )
                        break

                    # 解压到临时目录（使用安全的文件名）
                    target_path = os.path.join(extract_dir, safe_name)
                    # 避免重名覆盖
                    base, ext = os.path.splitext(safe_name)
                    counter = 1
                    while os.path.exists(target_path):
                        target_path = os.path.join(extract_dir, f"{base}_{counter}{ext}")
                        counter += 1

                    # 分块写入，避免一次性读入大文件
                    with zf.open(info) as src, open(target_path, 'wb') as dst:
                        while True:
                            chunk = src.read(64 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                    extracted.append(target_path)
                    total_size += file_size

            if extracted:
                self.status_label.setText(
                    f"已从 {os.path.basename(zip_path)} 解压 {len(extracted)} 张图片"
                )
            else:
                self.status_label.setText(
                    f"压缩包中未找到图片文件: {os.path.basename(zip_path)}"
                )

        except zipfile.BadZipFile:
            QMessageBox.warning(self, "解压失败", f"无效的 zip 文件:\n{zip_path}")
        except Exception as e:
            QMessageBox.warning(self, "解压失败", f"解压 {os.path.basename(zip_path)} 时出错:\n{e}")

        return extracted

    def _select_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择美团截图", "",
            "图片文件 (*.png *.jpg *.jpeg *.webp)"
        )
        if files:
            self._on_files_added(files)

    def _paste_from_clipboard(self):
        """从剪贴板粘贴图片（截图工具截图后直接 Ctrl+V 粘贴）"""
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        # 情况1：剪贴板中有图片数据（截图工具截图）
        if mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                # 保存为临时文件
                import tempfile
                tmp_dir = tempfile.mkdtemp(prefix="clipboard_")
                self._temp_dirs.append(tmp_dir)
                ts = datetime.now().strftime("%H%M%S_%f")[:-3]
                tmp_path = os.path.join(tmp_dir, f"paste_{ts}.png")
                image.save(tmp_path, "PNG")
                self._on_files_added([tmp_path])
                return

        # 情况2：剪贴板中有文件URL（从文件管理器复制的图片文件）
        if mime.hasUrls():
            files = []
            image_exts = (".png", ".jpg", ".jpeg", ".webp")
            for url in mime.urls():
                path = url.toLocalFile()
                if path.lower().endswith(image_exts):
                    files.append(path)
            if files:
                self._on_files_added(files)
                return

        self.status_label.setText("剪贴板中没有图片，请先截图（如 Shift+Cmd+4）再粘贴")

    def _clear_files(self):
        self.image_paths.clear()
        self.ocr_results.clear()
        self._retry_paths.clear()
        self.retry_ocr_btn.setEnabled(False)
        self.table.setRowCount(0)
        self._refresh_review_state()
        self._update_file_count()
        self.status_label.setText("")
        # 清理解压的临时目录
        self._cleanup_temp_dirs()

    def _cleanup_temp_dirs(self):
        """清理压缩包解压的临时目录"""
        import shutil
        for d in self._temp_dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        self._temp_dirs.clear()

    def closeEvent(self, event):
        """窗口关闭时清理临时文件"""
        self._cleanup_temp_dirs()
        super().closeEvent(event)

    def _update_file_count(self):
        n = len(self.image_paths)
        self.file_count_label.setText(f"已选 {n} 张图片" if n > 0 else "未选择图片")

    def _start_ocr(self, retry=False):
        if self.ocr_worker and self.ocr_worker.isRunning():
            return
        image_paths = self._retry_paths if retry else self.image_paths
        if not image_paths:
            QMessageBox.warning(self, "提示", "请先导入美团截图")
            return

        self._ocr_is_retry = retry
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.ocr_btn.setEnabled(False)
        self.retry_ocr_btn.setEnabled(False)
        self.cancel_ocr_btn.setEnabled(True)
        self.status_label.setText(
            f"正在重试 {len(image_paths)} 张失败图片..." if retry else "开始识别..."
        )

        self.ocr_worker = OCRWorker(image_paths)
        self.ocr_worker.progress.connect(self._on_ocr_progress)
        self.ocr_worker.finished_ocr.connect(self._on_ocr_finished)
        self.ocr_worker.start()

    def _cancel_ocr(self):
        """Stop scheduling new OCR work while preserving completed results."""
        if self.ocr_worker and self.ocr_worker.isRunning():
            self.ocr_worker.cancel()
            self.cancel_ocr_btn.setEnabled(False)
            self.status_label.setText("正在停止 OCR，当前已开始的图片会完成后保留...")

    def _retry_failed_ocr(self):
        """Retry only the images that failed or were not completed."""
        self._start_ocr(retry=True)

    def _on_ocr_progress(self, current, total, message):
        if total > 0:
            self.progress_bar.setValue(int(current / total * 100))
        self.status_label.setText(message)

    def _on_ocr_finished(self, results, retry_paths, cancelled):
        is_retry = self._ocr_is_retry
        if is_retry:
            self.ocr_results.update(results)
            self._update_retry_rows(results)
        else:
            self.ocr_results = results
        self.progress_bar.setValue(100)
        self.ocr_btn.setEnabled(True)
        self.cancel_ocr_btn.setEnabled(False)
        self._retry_paths = retry_paths
        self.retry_ocr_btn.setEnabled(bool(retry_paths))

        succeeded = len(results) - len(retry_paths)
        summary = (
            f"成功 {succeeded} 条，待重试 {len(retry_paths)} 条"
            if retry_paths else f"成功 {succeeded} 条，未发现失败项"
        )
        if cancelled:
            summary = "识别已取消，" + summary
        self.status_label.setText(summary)

        if is_retry:
            self._refresh_review_state()
            return

        self._populate_table()

        # 先让用户选择省份+城市，限定数据库匹配范围（更精准，避免跨城误匹配）
        dialog = ProvinceCitySelectDialog(len(results), self)
        if dialog.exec_() != QDialog.Accepted:
            # 取消是“暂不设置城市”，不能退化为不限范围匹配。
            self._last_cities = set()
            self.status_label.setText(
                f"识别完成，共 {len(results)} 条结果；城市暂未设置，"
                f"可点击“一键设置城市”或“联网识别城市”继续处理"
            )
            return

        restrict_cities = dialog.get_selected_cities()
        if not restrict_cities:
            self._last_cities = set()
            self.status_label.setText(
                f"识别完成，共 {len(results)} 条结果；城市暂未设置，"
                f"可点击“一键设置城市”或“联网识别城市”继续处理"
            )
            return

        self._last_province = dialog.get_selected_province()
        self._last_cities = restrict_cities

        # 自动识别城市：在选定区域内查数据库，命中填入所属区域列
        # 在后台线程执行，避免卡UI
        self._city_worker = CityDetectWorker(
            self.ocr_results, self.table, restrict_cities=restrict_cities
        )
        self._city_worker.progress.connect(
            lambda msg: self.status_label.setText(msg)
        )
        self._city_worker.finished_cities.connect(self._on_cities_detected)
        self._city_worker.start()

    def _on_cities_detected(self, shop_to_city):
        """城市识别完成回调

        数据库命中的直接填入；未命中店铺由用户按需一键批量设置城市。
        """
        detected = 0
        unmatched_shops = set()
        # 学习库命中的店名规范化映射（OCR店名 -> 标准店名）
        canonical_map = getattr(getattr(self, "_city_worker", None), "canonical_map", {}) or {}
        for row in range(self.table.rowCount()):
            shop_item = self.table.item(row, 3)  # D列=店铺名称
            if shop_item and shop_item.text().strip():
                shop_name = shop_item.text().strip()
                city = shop_to_city.get(shop_name)
                if city:
                    # 学习库命中时，D列同步为标准店铺名（历史人工确认的规范写法）
                    canonical = canonical_map.get(shop_name)
                    if canonical:
                        shop_item.setText(canonical)
                    region = format_region(city.replace("市", ""))
                    item = self.table.item(row, 1)  # B列=所属区域
                    if item:
                        item.setText(region)
                    else:
                        item = QTableWidgetItem(region)
                        self.table.setItem(row, 1, item)
                    detected += 1
                else:
                    unmatched_shops.add(shop_name)

        # 按所属区域（城市）排序表格
        self._sort_table_by_region()

        # 不自动弹出城市分配，避免打断结果查看；由外置按钮按需发起。
        if unmatched_shops:
            self.status_label.setText(
                f"识别完成，共 {len(self.ocr_results)} 条结果，城市识别 {detected} 行；"
                f"{len(unmatched_shops)} 家未识别，可点击“一键设置城市”处理"
            )
        else:
            self.status_label.setText(
                f"识别完成，共 {len(self.ocr_results)} 条结果，"
                f"城市识别 {detected} 行，已按城市排序"
            )

    def _batch_set_shop_cities(self):
        """Open the manual city assignment workflow only when requested."""
        unmatched_shops = set()
        for row in range(self.table.rowCount()):
            shop_item = self.table.item(row, 3)
            region_item = self.table.item(row, 1)
            shop_name = shop_item.text().strip() if shop_item else ""
            region = region_item.text().strip() if region_item else ""
            if shop_name and not region:
                unmatched_shops.add(shop_name)

        if not unmatched_shops:
            QMessageBox.information(self, "提示", "当前表格中的店铺均已设置城市")
            return

        self._prompt_batch_city(unmatched_shops)

    def _prompt_batch_city(self, unmatched_shops):
        """弹窗让用户选择当前批次的城市，批量填入未识别的店铺

        支持多轮选择：一批截图可能含多个城市的店铺，
        每轮选一个城市并指定属于该城市的店铺，直到所有店铺都有城市或用户取消。
        """
        remaining = set(unmatched_shops)  # 剩余未设置的店铺
        all_updated = 0
        all_db_mappings = {}
        selected_cities = []  # 记录每轮选择的城市

        while remaining:
            # 弹窗选城市（预填上次选的省份，减少重复操作）
            dialog = BatchCityDialog(sorted(remaining), self,
                                     default_province=self._last_province)
            if dialog.exec_() != QDialog.Accepted:
                break  # 用户取消
            city = dialog.get_selected_city()
            selected_cities.append(city)

            # 让用户从剩余店铺中选择属于该城市的店铺
            # 使用多选对话框
            from PyQt5.QtWidgets import QListWidget, QListWidgetItem
            select_dialog = QDialog(self)
            select_dialog.setWindowTitle(f"选择属于「{city}」的店铺")
            select_dialog.setModal(True)
            select_dialog.setMinimumWidth(500)
            select_dialog.setMinimumHeight(400)
            sel_layout = QVBoxLayout(select_dialog)

            hint = QLabel(
                f"请勾选属于「{city}」的店铺（剩余 {len(remaining)} 家待分配，默认未选择）："
            )
            hint.setStyleSheet("font-size: 13px; padding: 5px;")
            sel_layout.addWidget(hint)

            list_widget = QListWidget()
            for shop in sorted(remaining):
                item = QListWidgetItem(shop)
                item.setCheckState(Qt.Unchecked)
                list_widget.addItem(item)
            sel_layout.addWidget(list_widget)

            # 全选/全不选按钮
            btn_row = QHBoxLayout()
            select_all_btn = QPushButton("全选")
            select_none_btn = QPushButton("全不选")
            select_all_btn.clicked.connect(lambda: [
                list_widget.item(i).setCheckState(Qt.Checked)
                for i in range(list_widget.count())
            ])
            select_none_btn.clicked.connect(lambda: [
                list_widget.item(i).setCheckState(Qt.Unchecked)
                for i in range(list_widget.count())
            ])
            btn_row.addWidget(select_all_btn)
            btn_row.addWidget(select_none_btn)
            btn_row.addStretch()
            sel_layout.addLayout(btn_row)

            btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            btn_box.button(QDialogButtonBox.Ok).setText("确定")
            btn_box.button(QDialogButtonBox.Cancel).setText("完成")
            btn_box.accepted.connect(select_dialog.accept)
            btn_box.rejected.connect(select_dialog.reject)
            sel_layout.addWidget(btn_box)

            if select_dialog.exec_() != QDialog.Accepted:
                break  # 用户点"完成"

            # 收集选中的店铺
            selected_shops = set()
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                if item.checkState() == Qt.Checked:
                    selected_shops.add(item.text())

            if not selected_shops:
                continue

            # 批量填入选中的店铺
            for row in range(self.table.rowCount()):
                shop_item = self.table.item(row, 3)
                if shop_item and shop_item.text().strip() in selected_shops:
                    region_item = self.table.item(row, 1)
                    if region_item:
                        region_item.setText(city)
                    else:
                        region_item = QTableWidgetItem(city)
                        self.table.setItem(row, 1, region_item)
                    all_updated += 1

            # 记录到数据库映射
            for shop in selected_shops:
                all_db_mappings[shop] = city

            # 从剩余列表中移除已设置的
            remaining -= selected_shops

        # 写入数据库积累
        if all_db_mappings:
            # 人工确认的城市写入唯一学习库（结构化知识，下次自动命中）
            try:
                import database
                for shop, city in all_db_mappings.items():
                    database.learn_correction(shop, shop, city, operator="gui")
            except Exception:
                pass

        # 重新排序
        self._sort_table_by_region()

        # 状态提示
        if all_updated and remaining:
            self.status_label.setText(
                f"已设置 {all_updated} 行城市，剩余 {len(remaining)} 家未设置，"
                f"可点击“一键设置城市”或“联网识别城市”继续处理"
            )
        elif all_updated:
            city_str = "、".join(selected_cities)
            self.status_label.setText(
                f"已批量填入 {all_updated} 行城市: {city_str}（已入库，下次自动识别）"
            )
        else:
            self.status_label.setText(
                f"识别完成，{len(unmatched_shops)} 家店铺未设置城市，"
                f"可点击“一键设置城市”或“联网识别城市”继续处理"
            )

    def _sort_table_by_region(self):
        """按所属区域（B列）排序表格，保持每行所有数据（含图片）一起移动"""
        rows_data = []
        for row in range(self.table.rowCount()):
            row_items = []
            for col in range(self.table.columnCount()):
                row_items.append(self.table.takeItem(row, col))
            # 排序键：所属区域（B列=1），空值排最后
            region_item = row_items[1]
            sort_key = region_item.text().strip() if region_item else ""
            rows_data.append((sort_key, row_items))

        # 按城市名排序，空值排最后
        rows_data.sort(key=lambda x: (x[0] == "", x[0]))

        # 重新填充表格
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows_data))
        for row_idx, (_, row_items) in enumerate(rows_data):
            for col, item in enumerate(row_items):
                if item:
                    self.table.setItem(row_idx, col, item)

        self.table.resizeRowsToContents()
        self._refresh_review_state()

    def _populate_table(self):
        self.table.setRowCount(0)
        for path, fields in self.ocr_results.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = self._table_values_from_fields(fields)

            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                # 品牌列只读
                if col == 12:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setForeground(Qt.gray)
                # 分公司列默认值提示
                if col == 0:
                    item.setToolTip("分公司默认为漓泉销售公司，可修改")
                self.table.setItem(row, col, item)

            # 图片缩略图
            thumb_item = QTableWidgetItem()
            thumb_item.setData(Qt.DecorationRole, self._make_thumbnail(path))
            thumb_item.setFlags(thumb_item.flags() & ~Qt.ItemIsEditable)
            # 把图片路径存到缩略图item的UserRole里，排序后也能正确取到
            thumb_item.setData(Qt.UserRole, path)
            self.table.setItem(row, len(TABLE_COLUMNS), thumb_item)

        self.table.resizeRowsToContents()
        self._refresh_review_state()

    def _table_values_from_fields(self, fields):
        """Convert OCR fields into the editable table's first fourteen values."""
        data = fields.to_dict()
        brand_idx = detect_brand(data["product_name"])
        brand_names = {0: "燕京", 1: "雪花", 2: "青岛", 3: "百威"}
        return [
            data["branch_company"], data["region"], data["platform"],
            data["shop_name"], data["product_name"],
            str(data["original_price"]) if data["original_price"] else "",
            str(data["final_price"]) if data["final_price"] else "",
            str(data["shop_discount"]) if data["shop_discount"] else "",
            str(data["full_reduction"]) if data["full_reduction"] else "",
            str(data["coupon"]) if data["coupon"] else "",
            str(data["red_packet"]) if data["red_packet"] else "",
            str(data["delivery_fee"]) if data["delivery_fee"] else "",
            brand_names.get(brand_idx, "燕京"), data["remark"],
        ]

    def _update_retry_rows(self, results):
        """Replace OCR values only for retried image rows, preserving other edits."""
        rows_by_path = {}
        for row in range(self.table.rowCount()):
            image_item = self.table.item(row, len(TABLE_COLUMNS))
            if image_item:
                rows_by_path[image_item.data(Qt.UserRole)] = row

        for path, fields in results.items():
            row = rows_by_path.get(path)
            if row is None:
                continue
            for col, value in enumerate(self._table_values_from_fields(fields)):
                item = self.table.item(row, col)
                if item:
                    item.setText(value)
                else:
                    self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeRowsToContents()

    def _on_table_item_changed(self, _item):
        """Refresh review indicators after a user corrects a table cell."""
        if not self._updating_review_state:
            self._refresh_review_state()

    def _get_row_review_issues(self, row):
        """Build one review record from the current editable table row."""
        def get_cell(col):
            item = self.table.item(row, col)
            return item.text().strip() if item else ""

        return find_review_issues({
            "region": get_cell(1),
            "shop_name": get_cell(3),
            "product_name": get_cell(4),
            "original_price": get_cell(5),
            "final_price": get_cell(6),
            "remark": get_cell(13),
        })

    def _get_review_rows(self):
        """Return table rows with their manual review reasons."""
        review_rows = []
        for row in range(self.table.rowCount()):
            issues = self._get_row_review_issues(row)
            if issues:
                review_rows.append((row, issues))
        return review_rows

    def _refresh_review_state(self, _checked=None):
        """Update row highlighting, filtering, and summary from table content."""
        if self._updating_review_state:
            return

        self._updating_review_state = True
        try:
            review_rows = dict(self._get_review_rows())
            total = self.table.rowCount()
            pending = len(review_rows)
            passed = total - pending
            self.review_filter.setEnabled(total > 0)
            if total == 0:
                self.review_filter.setChecked(False)
                self.review_summary_label.setText("尚未识别记录")
                return

            self.review_summary_label.setText(
                f"共 {total} 条，待核对 {pending} 条，已通过 {passed} 条"
            )
            filter_enabled = self.review_filter.isChecked()
            for row in range(total):
                issues = review_rows.get(row, [])
                self.table.setRowHidden(row, filter_enabled and not issues)
                background = QColor("#FFF3CD") if issues else QColor("white")
                tooltip = "待核对：" + "；".join(issues) if issues else "已通过基础核对"
                for col in range(self.table.columnCount()):
                    item = self.table.item(row, col)
                    if item:
                        item.setBackground(background)
                        item.setToolTip(tooltip)
        finally:
            self._updating_review_state = False

    def _make_thumbnail(self, path, size=70):
        # 降采样解码：只解码缩略图所需的尺寸，避免全尺寸 1080x2400 解码
        from PyQt5.QtGui import QImageReader
        reader = QImageReader(path)
        reader.setScaledSize(QSize(size, size * 2))
        img = reader.read()
        if img.isNull():
            return QPixmap()
        return QPixmap.fromImage(img)

    def _check_duplicates(self):
        """查重核查：按 店铺名+平台+城市+理论成交价 四元组找出重复行，弹窗让用户勾选删除。

        理论成交价 = 成交价(G列) - 配送费(L列)，与导出 Excel 的 M 列口径一致。
        """
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "提示", "表格中没有数据，请先识别图片")
            return

        from collections import defaultdict

        # 收集每行数据
        rows_data = []
        for row in range(self.table.rowCount()):
            def get_cell(col):
                item = self.table.item(row, col)
                return item.text().strip() if item else ""

            def get_num(col):
                return parse_price(get_cell(col))

            shop_name = get_cell(3)   # D 店铺名称
            platform = get_cell(2)    # C 平台
            city = get_cell(1)        # B 所属区域
            final_price = get_num(6)  # G 成交价
            delivery_fee = get_num(11)  # L 配送费
            product_name = get_cell(4)  # E 产品名称

            # 缺关键字段的行跳过（无法判定重复）
            if not shop_name or not city:
                continue

            theory_price = round(final_price - delivery_fee, 2)
            rows_data.append({
                "row": row,
                "shop_name": shop_name,
                "platform": platform or "美团闪购",
                "city": city,
                "product_name": product_name,
                "final_price": final_price,
                "delivery_fee": delivery_fee,
                "theory_price": theory_price,
                "collected_at": "",
            })

        # 按四元组分组
        groups_map = defaultdict(list)
        for rd in rows_data:
            key = (rd["shop_name"], rd["platform"], rd["city"], rd["theory_price"])
            groups_map[key].append(rd)

        # 只保留 count > 1 的组
        groups = []
        for key, items in groups_map.items():
            if len(items) < 2:
                continue
            groups.append({
                "key": {
                    "shop_name": key[0],
                    "platform": key[1],
                    "city": key[2],
                    "theory_price": key[3],
                },
                "count": len(items),
                "redundant": len(items) - 1,
                "items": items,
            })

        if not groups:
            QMessageBox.information(
                self, "查重结果",
                "未发现重复商品\n"
                "（口径：店铺名 + 平台 + 城市 + 理论成交价 四者相同）"
            )
            return

        total_redundant = sum(g["redundant"] for g in groups)
        reply = QMessageBox.question(
            self, "发现重复",
            f"发现 {len(groups)} 组重复，共 {total_redundant} 条冗余记录。\n"
            f"是否打开核查窗口勾选删除？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            return

        dialog = DuplicateReviewDialog(groups, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        rows_to_delete = dialog.get_rows_to_delete()
        if not rows_to_delete:
            return

        # 二次确认（放在主窗口层面，避免在模态对话框内嵌套 QMessageBox 导致 accept() 失效）
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除选中的 {len(rows_to_delete)} 行吗？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.No:
            return

        # 从大到小删除，避免行号错位
        rows_to_delete.sort(reverse=True)
        for row in rows_to_delete:
            self.table.removeRow(row)

        # 同步清理 ocr_results（按图片路径匹配已删行）
        # 重新收集剩余行的图片路径
        remaining_paths = set()
        for row in range(self.table.rowCount()):
            thumb_item = self.table.item(row, len(TABLE_COLUMNS))
            if thumb_item:
                p = thumb_item.data(Qt.UserRole)
                if p:
                    remaining_paths.add(p)
        removed_paths = [p for p in self.ocr_results.keys() if p not in remaining_paths]
        for p in removed_paths:
            self.ocr_results.pop(p, None)

        QMessageBox.information(
            self, "删除完成",
            f"已删除 {len(rows_to_delete)} 行重复记录。\n"
            f"当前表格剩余 {self.table.rowCount()} 行。"
        )
        self.status_label.setText(f"已清理 {len(rows_to_delete)} 行重复，剩余 {self.table.rowCount()} 行")

    def _open_knowledge_base(self):
        """打开店铺/城市智能匹配数据库（知识库）对话框"""
        from knowledge_dialog import KnowledgeBaseDialog
        dlg = KnowledgeBaseDialog(self)
        dlg.exec_()

    def _reapply_learning_cities(self):
        """知识库「重新匹配」：对当前表格所有店铺重新执行学习库匹配

        Returns:
            int: 命中并填入城市的行数
        """
        try:
            import database
        except ImportError:
            return 0

        shops = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 3)
            if item and item.text().strip():
                shops.append(item.text().strip())
        if not shops:
            return 0

        matched = database.batch_get_shop_city(shops)
        updated = 0
        for row in range(self.table.rowCount()):
            shop_item = self.table.item(row, 3)
            if not shop_item or not shop_item.text().strip():
                continue
            m = matched.get(shop_item.text().strip())
            # 仅信任 L1-L4（L5 模糊候选需人工确认）
            if m and m["city"] and not m["conflict"] and m["level"] <= 4:
                if m["shop_name"] != shop_item.text().strip():
                    shop_item.setText(m["shop_name"])
                region = format_region(m["city"].replace("市", ""))
                region_item = self.table.item(row, 1)
                if region_item:
                    region_item.setText(region)
                else:
                    self.table.setItem(row, 1, QTableWidgetItem(region))
                updated += 1
        if updated:
            self._sort_table_by_region()
        return updated

    def _export_excel(self):
        if not self.ocr_results:
            QMessageBox.warning(self, "提示", "没有可导出的数据，请先识别")
            return

        review_rows = self._get_review_rows()
        if review_rows:
            examples = "\n".join(
                f"第 {row + 1} 条：{'；'.join(issues)}"
                for row, issues in review_rows[:3]
            )
            remaining = len(review_rows) - 3
            if remaining > 0:
                examples += f"\n另有 {remaining} 条待核对"
            reply = QMessageBox.warning(
                self,
                "导出前核对",
                f"当前有 {len(review_rows)} 条记录需要人工核对：\n\n{examples}"
                "\n\n建议先修正后再导出。是否仍然导出？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # 从表格收集修正后的数据
        records = []
        for row in range(self.table.rowCount()):
            # 从缩略图item的UserRole取图片路径（排序后仍然正确）
            thumb_item = self.table.item(row, len(TABLE_COLUMNS))
            path = thumb_item.data(Qt.UserRole) if thumb_item else None

            def get_cell(col):
                item = self.table.item(row, col)
                return item.text() if item else ""

            def get_num(col):
                val = get_cell(col)
                if not val or val == "":
                    return 0.0
                try:
                    return float(val)
                except ValueError:
                    return 0.0

            record = {
                "branch_company": get_cell(0),  # A 列默认"漓泉销售公司"
                "region": get_cell(1),
                "platform": get_cell(2),
                "shop_name": get_cell(3),
                "product_name": get_cell(4),
                "original_price": get_num(5),
                "final_price": get_num(6),
                "shop_discount": get_num(7),
                "full_reduction": get_num(8),
                "coupon": get_num(9),
                "red_packet": get_num(10),
                "delivery_fee": get_num(11),
                "remark": get_cell(13),
                "image_path": path,
            }
            records.append(record)

        try:
            # 自动按品牌分类，写入4个子表
            output_path = excel_writer.write_all_brands(records)

            # 统计各品牌数量
            from collections import Counter
            brand_counts = Counter(detect_brand(r["product_name"]) for r in records)
            brand_names = {0: "燕京", 1: "雪花", 2: "青岛", 3: "百威"}
            stats = "\n".join(
                f"  {brand_names[idx]}: {brand_counts.get(idx, 0)} 条"
                for idx in range(4)
            )

            # 在文件管理器中显示导出的子文件夹
            _open_folder(os.path.dirname(output_path))

            QMessageBox.information(
                self, "导出成功",
                f"已导出 {len(records)} 条记录\n\n保存到:\n{output_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _generate_summary(self):
        """从已导出的巡查表xlsx生成分省/分地级市汇总 + 明细表(含截图)"""
        # 1. 选择巡查表文件
        default_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"
        )
        if not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~/Desktop")
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择巡查表 Excel 文件", default_dir,
            "Excel 文件 (*.xlsx);;所有文件 (*)"
        )
        if not file_path:
            return

        # 2. 选择产品类型
        prod_dialog = ProductSelectDialog(self)
        if prod_dialog.exec_() != QDialog.Accepted:
            return
        product_type = prod_dialog.get_product_type()
        if not product_type:
            QMessageBox.warning(self, "提示", "请选择产品类型")
            return

        # 3. 选择省份（多选）
        prov_dialog = ProvinceSelectDialog(self)
        if prov_dialog.exec_() != QDialog.Accepted:
            return
        selected_provinces = prov_dialog.get_selected_provinces()
        if not selected_provinces:
            QMessageBox.warning(self, "提示", "请至少选择一个省份")
            return

        # 4. 生成
        try:
            prod_name = "燕京U8" if product_type == "u8" else "漓泉1998"
            self.status_label.setText(f"正在生成{prod_name}汇总表（含截图）...")
            QApplication.processEvents()

            output_path = summary_generator.generate_summary_report(
                file_path, provinces=selected_provinces, product_type=product_type
            )

            _open_folder(os.path.dirname(output_path))

            prov_str = "、".join(selected_provinces)
            QMessageBox.information(
                self, "生成成功",
                f"{prod_name}价格合格率汇总表已生成:\n{output_path}\n\n"
                f"省份: {prov_str}\n"
                f"包含3个工作表: 分省汇总 / 分地级市汇总 / 明细表(含截图)"
            )
            self.status_label.setText(f"{prod_name}汇总表已生成: {os.path.basename(output_path)}")

        except Exception as e:
            QMessageBox.critical(self, "生成失败", str(e))
            self.status_label.setText("")

    def _generate_speech(self):
        """从巡查表 xlsx 智能生成总结话术，展示并可复制到剪贴板"""
        # 1. 选择巡查表文件
        default_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"
        )
        if not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~/Desktop")
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择巡查表 Excel 文件", default_dir,
            "Excel 文件 (*.xlsx);;所有文件 (*)"
        )
        if not file_path:
            return

        # 2. 选择省份（多选）
        prov_dialog = ProvinceSelectDialog(self)
        if prov_dialog.exec_() != QDialog.Accepted:
            return
        selected_provinces = prov_dialog.get_selected_provinces()
        if not selected_provinces:
            QMessageBox.warning(self, "提示", "请至少选择一个省份")
            return

        # 3. 生成话术
        try:
            self.status_label.setText("正在生成总结话术...")
            QApplication.processEvents()

            speech_text = summary_speech.generate_speech(
                file_path, provinces=selected_provinces
            )

            self.status_label.setText("总结话术已生成")

            # 4. 弹出对话框展示文本，带复制按钮
            dialog = QDialog(self)
            dialog.setWindowTitle("总结话术")
            dialog.setMinimumWidth(600)
            dialog.setMinimumHeight(400)

            layout = QVBoxLayout(dialog)

            # 文本框（只读）
            from PyQt5.QtWidgets import QTextEdit
            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setPlainText(speech_text)
            text_edit.setLineWrapMode(QTextEdit.WidgetWidth)
            layout.addWidget(text_edit)

            # 按钮区
            btn_layout = QHBoxLayout()

            copy_btn = QPushButton("📋 复制到剪贴板")
            copy_btn.setStyleSheet(
                "QPushButton { background-color: #6c5ce7; color: white; "
                "font-size: 14px; padding: 8px 20px; border-radius: 6px; }"
                "QPushButton:hover { background-color: #5b4cdb; }"
            )

            def _copy_to_clipboard():
                QApplication.clipboard().setText(speech_text)
                copy_btn.setText("✅ 已复制")
                QApplication.processEvents()
                # 1.5秒后恢复按钮文字
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(1500, lambda: copy_btn.setText("📋 复制到剪贴板"))

            copy_btn.clicked.connect(_copy_to_clipboard)
            btn_layout.addWidget(copy_btn)

            btn_layout.addStretch()

            close_btn = QPushButton("关闭")
            close_btn.setStyleSheet(
                "QPushButton { font-size: 14px; padding: 8px 20px; border-radius: 6px; }"
            )
            close_btn.clicked.connect(dialog.accept)
            btn_layout.addWidget(close_btn)

            layout.addLayout(btn_layout)

            dialog.exec_()

        except Exception as e:
            QMessageBox.critical(self, "生成失败", str(e))
            self.status_label.setText("")

    def _copy_table_selection(self):
        """复制表格选中内容到剪贴板（支持选中区域，Tab 分隔列，换行分隔行）"""
        from PyQt5.QtWidgets import QApplication
        selected = self.table.selectedRanges()
        if not selected:
            return

        rows_text = []
        for r in selected:
            for row in range(r.topRow(), r.bottomRow() + 1):
                cells = []
                for col in range(r.leftColumn(), r.rightColumn() + 1):
                    item = self.table.item(row, col)
                    cells.append(item.text() if item else "")
                rows_text.append("\t".join(cells))

        text = "\n".join(rows_text)
        QApplication.clipboard().setText(text)


def main():
    # 必须在 QApplication 创建前设置，才能让 Qt 按系统显示缩放比例
    # （如 Windows 125% / 150%）渲染窗口、字体和图标。
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
