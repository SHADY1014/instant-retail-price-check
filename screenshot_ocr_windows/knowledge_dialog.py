"""
店铺/城市智能匹配数据库 — 知识库管理对话框

功能：统计 / 导入 Excel（投喂）/ 查看店铺·别名·冲突·学习记录·投喂记录 / 冲突裁决 / 重新匹配
"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import database


def _fill_table(table, headers, rows):
    """通用表格填充：headers=list[str], rows=list[list[str]]"""
    table.clear()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(str(val)))
    table.resizeColumnsToContents()
    table.setEditTriggers(QTableWidget.NoEditTriggers)


class KnowledgeBaseDialog(QDialog):
    """知识库对话框"""

    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("📚 知识库 — 店铺/城市智能匹配数据库")
        self.resize(960, 600)

        layout = QVBoxLayout(self)

        # 顶部操作栏
        top = QHBoxLayout()
        self.import_btn = QPushButton("📥 导入 Excel（可多选投喂）")
        self.import_btn.setToolTip(
            "选择人工修正后的巡查表 Excel（支持一次多选多个文件），\n"
            "系统学习其中的店铺名/别名/城市关系\n"
            "同一文件重复导入自动跳过（幂等）"
        )
        self.import_btn.clicked.connect(self._import_excel)
        top.addWidget(self.import_btn)

        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.clicked.connect(self._refresh_all)
        top.addWidget(self.refresh_btn)

        self.reapply_btn = QPushButton("🔁 重新匹配当前表格")
        self.reapply_btn.setToolTip("对主界面表格所有店铺重新执行学习库匹配（L1-L5）")
        self.reapply_btn.clicked.connect(self._reapply)
        top.addWidget(self.reapply_btn)

        self.clean_invalid_btn = QPushButton("清理异常投喂数据")
        self.clean_invalid_btn.setToolTip(
            "预览疑似规格、表头或数值被误导入或迁移为店铺的记录")
        self.clean_invalid_btn.clicked.connect(self._clean_invalid_imports)
        top.addWidget(self.clean_invalid_btn)

        top.addStretch()
        layout.addLayout(top)

        # 统计
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("font-size: 13px; padding: 6px; background: #F5F5F5;")
        layout.addWidget(self.stats_label)

        # Tab
        self.tabs = QTabWidget()
        self.tab_shops = self._make_table(["店铺ID", "标准店铺名", "城市", "省份", "状态",
                                           "置信度", "来源", "命中", "正确", "更新时间"])
        self.tab_aliases = self._make_table(["别名ID", "店铺ID", "标准店铺名", "别名",
                                             "来源", "命中", "更新时间"])
        self.tab_conflicts = self._make_table(["match_id", "店铺ID", "标准店铺名", "城市",
                                               "状态", "来源", "置信度"])
        self.tab_corrections = self._make_table(["ID", "批次", "OCR店名", "修正后店名",
                                                 "城市", "操作人", "时间"])
        self.tab_batches = self._make_table(["批次ID", "文件名", "时间", "总行数", "新店铺",
                                             "新别名", "更新店铺", "更新城市", "冲突",
                                             "忽略", "操作人", "状态"])

        self.tabs.addTab(self.tab_shops, "店铺库")
        self.tabs.addTab(self.tab_aliases, "别名")
        self.tabs.addTab(self.tab_conflicts, "冲突（需人工裁决）")
        self.tabs.addTab(self.tab_corrections, "人工修正记录")
        self.tabs.addTab(self.tab_batches, "投喂记录")
        layout.addWidget(self.tabs, 1)

        # 冲突裁决按钮
        resolve_row = QHBoxLayout()
        self.resolve_btn = QPushButton("⚖️ 裁决选中冲突（人工确认城市）")
        self.resolve_btn.clicked.connect(self._resolve_selected_conflict)
        resolve_row.addWidget(self.resolve_btn)
        resolve_row.addStretch()
        layout.addLayout(resolve_row)

        self._refresh_all()

    # ---------------------------------------------------------
    def _make_table(self, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        return table

    def _refresh_all(self):
        self._refresh_stats()
        self._refresh_shops()
        self._refresh_aliases()
        self._refresh_conflicts()
        self._refresh_corrections()
        self._refresh_batches()

    def _refresh_stats(self):
        try:
            s = database.get_stats()
        except Exception as e:
            self.stats_label.setText(f"数据库读取失败: {e}")
            return
        self.stats_label.setText(
            f"📊 店铺 {s['shops']} ｜ 别名 {s['aliases']} ｜ 城市匹配 {s['city_matches']} ｜ "
            f"人工确认 {s['corrections']} ｜ 冲突 {s['conflicts']} ｜ 投喂 {s['batches']} 次"
        )

    def _refresh_shops(self):
        rows = []
        try:
            for s in database.list_shops(limit=2000):
                rows.append([s["shop_id"], s["canonical_name"], s["city"] or "",
                             s["province"] or "", s["status"], s["confidence"],
                             s["source"], s["use_count"], s["correct_count"],
                             (s["updated_at"] or "")[:16]])
        except Exception:
            pass
        _fill_table(self.tab_shops,
                    ["店铺ID", "标准店铺名", "城市", "省份", "状态", "置信度",
                     "来源", "命中", "正确", "更新时间"], rows)

    def _refresh_aliases(self):
        rows = []
        try:
            for a in database.list_aliases(limit=3000):
                rows.append([a["alias_id"], a["shop_id"], a["canonical_name"],
                             a["alias"], a["source"], a["use_count"],
                             (a["updated_at"] or "")[:16]])
        except Exception:
            pass
        _fill_table(self.tab_aliases,
                    ["别名ID", "店铺ID", "标准店铺名", "别名", "来源", "命中", "更新时间"], rows)

    def _refresh_conflicts(self):
        rows = []
        try:
            for c in database.get_conflicts():
                rows.append([c["match_id"], c["shop_id"], c["canonical_name"],
                             c["city"], c["status"], c["source"], c["confidence"]])
        except Exception:
            pass
        _fill_table(self.tab_conflicts,
                    ["match_id", "店铺ID", "标准店铺名", "城市", "状态", "来源", "置信度"], rows)

    def _refresh_corrections(self):
        rows = []
        try:
            for c in database.list_corrections(limit=1000):
                rows.append([c["correction_id"], c["batch_id"] or "", c["ocr_shop_name"],
                             c["corrected_shop_name"], c["city"] or "", c["operator"],
                             (c["created_at"] or "")[:16]])
        except Exception:
            pass
        _fill_table(self.tab_corrections,
                    ["ID", "批次", "OCR店名", "修正后店名", "城市", "操作人", "时间"], rows)

    def _refresh_batches(self):
        rows = []
        try:
            for b in database.list_batches(limit=500):
                rows.append([b["batch_id"], b["filename"], (b["import_time"] or "")[:16],
                             b["total_rows"], b["new_shops"], b["new_aliases"],
                             b["updated_shops"], b["updated_cities"], b["conflicts"],
                             b["ignored_rows"], b["operator"], b["status"]])
        except Exception:
            pass
        _fill_table(self.tab_batches,
                    ["批次ID", "文件名", "时间", "总行数", "新店铺", "新别名", "更新店铺",
                     "更新城市", "冲突", "忽略", "操作人", "状态"], rows)

    # ---------------------------------------------------------
    def _import_excel(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择人工修正后的巡查表 Excel（可多选）", "", "Excel 文件 (*.xlsx)"
        )
        if not paths:
            return

        lines = [f"本次投喂 {len(paths)} 个文件：", ""]
        ok_cnt = dup_cnt = fail_cnt = 0
        for path in paths:
            try:
                report = database.import_excel(path, operator="gui")
            except Exception as e:
                fail_cnt += 1
                lines.append(f"❌ {os.path.basename(path)}: 失败 {e}")
                continue

            if report.get("duplicate"):
                dup_cnt += 1
                lines.append(
                    f"⏭️ {report['filename']}: 已导入过（批次 #{report['batch_id']}，"
                    f"{report.get('import_time', '')[:16]}），自动跳过"
                )
            else:
                ok_cnt += 1
                lines.append(
                    f"✅ {report['filename']}:\n"
                    f"     总记录 {report['total_rows']} ｜ 新增店铺 {report['new_shops']} ｜ "
                    f"新增别名 {report['new_aliases']}\n"
                    f"     更新已有 {report['updated_shops']} ｜ 更新城市 {report['updated_cities']} ｜ "
                    f"冲突 {report['conflicts']} ｜ 忽略 {report['ignored_rows']}"
                )
                skipped = report.get("detail", {}).get("skipped_sheets", [])
                if skipped:
                    lines.append(f"     已跳过非巡查明细表：{'、'.join(skipped)}")

        lines += ["", f"汇总: 成功 {ok_cnt} ｜ 重复跳过 {dup_cnt} ｜ 失败 {fail_cnt}"]
        self._refresh_all()
        QMessageBox.information(self, "投喂完成", "\n".join(lines))

    def _clean_invalid_imports(self):
        """Preview then remove only invalid records selected by the user."""
        try:
            candidates = database.list_invalid_imported_shops()
        except Exception as exc:
            QMessageBox.critical(self, "读取失败", str(exc))
            return

        if not candidates:
            QMessageBox.information(self, "无需清理", "未发现异常投喂数据。")
            return

        names = "\n".join(
            f"- {row['canonical_name']}（ID {row['shop_id']}）"
            for row in candidates)
        answer = QMessageBox.question(
            self, "确认清理异常投喂数据",
            "以下记录疑似把规格、表头或数值误导入为店铺：\n\n"
            f"{names}\n\n将同时删除这些记录的别名、城市匹配和投喂修正记录。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            deleted = database.delete_invalid_imported_shops(
                [row["shop_id"] for row in candidates])
        except Exception as exc:
            QMessageBox.critical(self, "清理失败", str(exc))
            return
        self._refresh_all()
        QMessageBox.information(self, "清理完成", f"已清理 {len(deleted)} 条异常投喂记录。")

    def _resolve_selected_conflict(self):
        row = self.tab_conflicts.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先在冲突列表中选中一行")
            return
        item = self.tab_conflicts.item(row, 1)  # 店铺ID
        if not item:
            return
        shop_id = int(item.text())
        shop_name = self.tab_conflicts.item(row, 2).text()

        # 列出该店全部城市候选（confirmed/conflict）
        try:
            matches = database.get_city_matches(shop_id)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))
            return
        cities = [m["city"] for m in matches if m["status"] != "rejected"]
        if not cities:
            QMessageBox.warning(self, "提示", "该店铺没有可裁决的城市记录")
            return

        city, ok = QInputDialog.getItem(
            self, "裁决冲突",
            f"「{shop_name}」实际属于哪个城市？",
            cities, 0, False,
        )
        if not ok or not city:
            return
        database.resolve_conflict(shop_id, city, operator="gui")
        QMessageBox.information(
            self, "已裁决",
            f"「{shop_name}」 -> {city}\n已确认并更新，后续自动命中该结果。"
        )
        self._refresh_all()

    def _reapply(self):
        if not self.main_window:
            return
        updated = self.main_window._reapply_learning_cities()
        QMessageBox.information(
            self, "重新匹配完成",
            f"学习库重新匹配完成，命中并填入 {updated} 行。"
        )
        self._refresh_all()
