"""PySide6 desktop interface."""

from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import QObject, QSortFilterProxyModel, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressDialog, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QToolBar, QTreeView, QVBoxLayout, QWidget,
)

from . import __version__
from .export import to_csv, to_txt
from .mating import score_pair, suggest_mates
from .model import Mate, TraceResult
from .parser import parse_pdf
from .trace import Design

COLUMNS = ["Kart", "Bileşen", "Parça", "Pin", "Pin adı", "Net", "FPGA kartı", "FPGA", "Ball",
           "FPGA pin adı", "Bank", "Bank gerilimi", "Ara katman"]
ROLE_LABELS = {"other": "Cihaz", "connector": "Konektör", "buffer": "Buffer / çevirici"}
AUTO_MATE_SCORE = 0.6


# ----------------------------------------------------------------------------- background parsing
class ParseWorker(QObject):
    progress = Signal(str, int, int)
    done = Signal(object)
    failed = Signal(str, str)

    def __init__(self, paths: list[str]):
        super().__init__()
        self.paths = paths

    def run(self):
        boards = []
        for path in self.paths:
            name = os.path.splitext(os.path.basename(path))[0]
            try:
                boards.append(parse_pdf(path, name=name,
                                        progress=lambda i, n, nm=name: self.progress.emit(nm, i, n)))
            except Exception as exc:  # report and continue with the remaining files
                self.failed.emit(path, f"{exc}\n\n{traceback.format_exc()}")
        self.done.emit(boards)


class TreeFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)


# ----------------------------------------------------------------------------- mating dialog
class MateDialog(QDialog):
    def __init__(self, design: Design, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Konektör eşleştirme")
        self.resize(980, 460)
        self.design = design
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Hangi kartın hangi konektörünün birbirine takıldığını seçin. Öneriler parça numarası, "
            "ortak pin numaraları, sinyal adı benzerliği ve güç/toprak desenine göre puanlanır.\n"
            "Eşleşen konektörlerde pin N ⇄ pin N kabul edilir."))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Kullan", "Kart A", "Konektör A", "Kart B", "Konektör B",
                                              "Puan / gerekçe"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.table)

        manual = QHBoxLayout()
        self.cb = [QComboBox() for _ in range(4)]
        for i, label in enumerate(["Kart A", "Konektör A", "Kart B", "Konektör B"]):
            manual.addWidget(QLabel(label))
            manual.addWidget(self.cb[i], 1)
        add = QPushButton("Elle ekle")
        add.clicked.connect(self._add_manual)
        manual.addWidget(add)
        lay.addLayout(manual)
        for i in (0, 2):
            self.cb[i].addItems(list(design.boards))
            self.cb[i].currentTextChanged.connect(lambda _t, i=i: self._fill_connectors(i))
        if len(design.boards) > 1:
            self.cb[2].setCurrentIndex(1)
        self._fill_connectors(0)
        self._fill_connectors(2)

        row = QHBoxLayout()
        again = QPushButton("Önerileri yeniden hesapla")
        again.clicked.connect(self._suggest)
        row.addWidget(again)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)

        for m in design.mates:
            s = score_pair(design.boards[m.board_a], m.ref_a, design.boards[m.board_b], m.ref_b)
            self._add_row(m, s.score, s.reasons, True)
        if not design.mates:
            self._suggest()

    def _fill_connectors(self, i: int):
        board = self.design.boards.get(self.cb[i].currentText())
        self.cb[i + 1].clear()
        if board:
            refs = sorted((r for r, c in board.components.items() if c.role == "connector"),
                          key=lambda r: (len(r), r))
            self.cb[i + 1].addItems([f"{r}  ({board.components[r].part or '?'}, {len(board.components[r].pins)} pin)"
                                     for r in refs])

    def _add_row(self, mate: Mate, score: float, reasons: list[str], checked: bool):
        for r in range(self.table.rowCount()):
            if self._mate_at(r) == mate:
                return
        r = self.table.rowCount()
        self.table.insertRow(r)
        chk = QTableWidgetItem()
        chk.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.table.setItem(r, 0, chk)
        for c, v in enumerate([mate.board_a, mate.ref_a, mate.board_b, mate.ref_b], start=1):
            it = QTableWidgetItem(v)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, c, it)
        it = QTableWidgetItem(f"{score:.2f} — " + "; ".join(reasons))
        it.setToolTip("\n".join(reasons))
        self.table.setItem(r, 5, it)

    def _mate_at(self, r: int) -> Mate:
        return Mate(*(self.table.item(r, c).text() for c in range(1, 5)))

    def _suggest(self):
        for s in suggest_mates(self.design.boards, threshold=0.3):
            self._add_row(s.mate, s.score, s.reasons, s.score >= AUTO_MATE_SCORE)

    def _add_manual(self):
        ba, bb = self.cb[0].currentText(), self.cb[2].currentText()
        ra, rb = self.cb[1].currentText().split(" ")[0], self.cb[3].currentText().split(" ")[0]
        if not (ba and bb and ra and rb) or ba == bb:
            QMessageBox.warning(self, "Eşleştirme", "İki farklı kartta birer konektör seçin.")
            return
        s = score_pair(self.design.boards[ba], ra, self.design.boards[bb], rb)
        self._add_row(Mate(ba, ra, bb, rb), s.score, ["elle eklendi"] + s.reasons, True)

    def mates(self) -> list[Mate]:
        return [self._mate_at(r) for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.Checked]


# ----------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"FPGA Pin Tracer {__version__}")
        self.resize(1500, 860)
        self.design = Design()
        self.trace_cache: dict[tuple[str, str, bool], list[TraceResult]] = {}
        self.peripherals: list[tuple[str, str, int]] = []
        self.results: list[TraceResult] = []
        self._thread = None

        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        for text, slot in [("Şematik ekle…", self.add_pdfs), ("Kartı kaldır", self.remove_board),
                           ("Konektör eşleştirme…", self.edit_mates), (None, None),
                           ("CSV dışa aktar…", lambda: self.export("csv")),
                           ("TXT dışa aktar…", lambda: self.export("txt"))]:
            if text is None:
                tb.addSeparator()
                continue
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb.addAction(a)

        # left: boards + component picker
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Kartlar</b> (adı değiştirmek için çift tıklayın)"))
        self.board_list = QListWidget()
        self.board_list.setMaximumHeight(130)
        self.board_list.itemDoubleClicked.connect(self.rename_board)
        ll.addWidget(self.board_list)
        self.mate_label = QLabel("Konektör eşleşmesi yok")
        self.mate_label.setWordWrap(True)
        ll.addWidget(self.mate_label)

        ll.addWidget(QLabel("<b>Bileşenler</b> (FPGA'ya bağlı olanlar)"))
        frow = QHBoxLayout()
        self.comp_search = QLineEdit()
        self.comp_search.setPlaceholderText("Ara: U12, TMP117, J1…")
        self.comp_search.textChanged.connect(self.filter_components)
        self.role_filter = QComboBox()
        self.role_filter.addItems(["Tümü", "Cihaz", "Konektör", "Buffer / çevirici"])
        self.role_filter.currentTextChanged.connect(self.filter_components)
        frow.addWidget(self.comp_search, 1)
        frow.addWidget(self.role_filter)
        ll.addLayout(frow)
        self.comp_list = QListWidget()
        self.comp_list.itemChanged.connect(self.refresh_results)
        ll.addWidget(self.comp_list, 1)
        brow = QHBoxLayout()
        for text, state in (("Görünenleri seç", Qt.Checked), ("Temizle", Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, s=state: self.set_all_components(s))
            brow.addWidget(b)
        ll.addLayout(brow)

        # right: result tree
        right = QWidget()
        rl = QVBoxLayout(right)
        orow = QHBoxLayout()
        self.result_filter = QLineEdit()
        self.result_filter.setPlaceholderText("Tabloda filtrele: pin, net, ball, bank…")
        self.show_hops = QCheckBox("Ara katmanları göster")
        self.show_hops.toggled.connect(self.apply_expand)
        self.show_unconnected = QCheckBox("FPGA'ya ulaşmayan pinleri de göster")
        self.show_unconnected.toggled.connect(self.refresh_results)
        orow.addWidget(self.result_filter, 1)
        orow.addWidget(self.show_hops)
        orow.addWidget(self.show_unconnected)
        rl.addLayout(orow)
        self.model = QStandardItemModel(0, len(COLUMNS))
        self.model.setHorizontalHeaderLabels(COLUMNS)
        self.proxy = TreeFilter()
        self.proxy.setSourceModel(self.model)
        self.result_filter.textChanged.connect(self.proxy.setFilterFixedString)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setSortingEnabled(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.header().setSectionResizeMode(QHeaderView.Interactive)
        rl.addWidget(self.tree, 1)
        self.summary = QLabel("Başlamak için 'Şematik ekle…' ile bir veya daha fazla PDF seçin.")
        rl.addWidget(self.summary)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([360, 1140])
        self.setCentralWidget(split)

    # ------------------------------------------------------------------ boards
    def add_pdfs(self, paths: list[str] | None = None):
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, "Şematik PDF seç", "", "PDF (*.pdf)")
        if not paths:
            return
        self.progress = QProgressDialog("Şematikler okunuyor…", None, 0, 0, self)
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.show()
        self._thread = QThread()
        self._worker = ParseWorker(paths)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.failed.connect(lambda p, e: QMessageBox.critical(self, "Okuma hatası", f"{p}\n\n{e}"))
        self._worker.done.connect(self._on_parsed)
        self._worker.done.connect(self._thread.quit)
        self._thread.start()

    def _on_progress(self, name: str, i: int, n: int):
        self.progress.setLabelText(f"{name}: sayfa {min(i + 1, n)} / {n}")

    def _on_parsed(self, boards):
        if getattr(self, "progress", None):
            self.progress.close()
        for b in boards:
            base, k = b.name, 2
            while b.name in self.design.boards:
                b.name = f"{base}_{k}"
                k += 1
            self.design.add_board(b)
        if len(self.design.boards) > 1:
            known = {(m.board_a, m.ref_a) for m in self.design.mates} | {(m.board_b, m.ref_b) for m in self.design.mates}
            new = [s for s in suggest_mates(self.design.boards) if s.score >= AUTO_MATE_SCORE
                   and (s.mate.board_a, s.mate.ref_a) not in known and (s.mate.board_b, s.mate.ref_b) not in known]
            self.design.mates += [s.mate for s in new]
            if new:
                QMessageBox.information(
                    self, "Konektör eşleştirme",
                    "Şu konektörler otomatik eşleştirildi:\n\n" +
                    "\n".join(f"• {s.mate.board_a}/{s.mate.ref_a} ⇄ {s.mate.board_b}/{s.mate.ref_b}  (puan {s.score:.2f})"
                              for s in new) +
                    "\n\nKontrol etmek veya değiştirmek için 'Konektör eşleştirme…' düğmesini kullanın.")
        self.design_changed()
        empty = [b for b in boards if sum(len(c.pins) for c in b.components.values()) < 10]
        if empty:
            import io
            from .diagnose import diagnose
            buf = io.StringIO()
            for b in empty:
                diagnose(b.source, out=buf)
            verdict = "\n".join(ln for ln in buf.getvalue().splitlines()
                                if ln.startswith(("===", "SONUÇ", "  ", "oluşturan", "sayfa:")))
            QMessageBox.warning(
                self, "Şematik okunamadı",
                "Bu PDF'lerden neredeyse hiç bağlantı çıkarılamadı:\n\n" + verdict +
                "\n\nAyrıntı için PDF'i diagnose.bat üzerine sürükleyin (çıktıda şema içeriği yer almaz).")

    def remove_board(self):
        item = self.board_list.currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        self.design.boards.pop(name, None)
        self.design.mates = [m for m in self.design.mates if name not in (m.board_a, m.board_b)]
        self.design_changed()

    def rename_board(self, item: QListWidgetItem):
        old = item.data(Qt.UserRole)
        new, ok = QInputDialog.getText(self, "Kart adı", "Yeni ad:", text=old)
        new = new.strip()
        if not ok or not new or new == old or new in self.design.boards:
            return
        b = self.design.boards.pop(old)
        b.name = new
        self.design.boards[new] = b
        self.design.mates = [Mate(new if m.board_a == old else m.board_a, m.ref_a,
                                  new if m.board_b == old else m.board_b, m.ref_b) for m in self.design.mates]
        self.design_changed()

    def edit_mates(self):
        if len(self.design.boards) < 2:
            QMessageBox.information(self, "Konektör eşleştirme", "Eşleştirme için en az iki kart ekleyin.")
            return
        dlg = MateDialog(self.design, self)
        if dlg.exec() == QDialog.Accepted:
            self.design.mates = dlg.mates()
            self.design_changed()

    # ------------------------------------------------------------------ state refresh
    def design_changed(self):
        self.trace_cache.clear()
        self.board_list.clear()
        for name, b in self.design.boards.items():
            fpgas = [f"{r} ({c.part})" for r, c in b.components.items() if c.role == "fpga"]
            it = QListWidgetItem(f"{name} — {b.stats()}" + (f" — FPGA: {', '.join(fpgas)}" if fpgas else ""))
            it.setData(Qt.UserRole, name)
            it.setToolTip(b.source)
            self.board_list.addItem(it)
        self.mate_label.setText(
            "Eşleşen konektörler: " + ", ".join(f"{m.board_a}/{m.ref_a} ⇄ {m.board_b}/{m.ref_b}"
                                                for m in self.design.mates)
            if self.design.mates else "Konektör eşleşmesi yok")
        checked = self.checked_components()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.peripherals = self.design.peripherals()
        finally:
            QApplication.restoreOverrideCursor()
        self.comp_list.blockSignals(True)
        self.comp_list.clear()
        for bname, ref, n in self.peripherals:
            c = self.design.boards[bname].components[ref]
            it = QListWidgetItem(f"{bname} / {ref}" + (f" — {c.part}" if c.part else "") + f"  ({n})")
            it.setData(Qt.UserRole, (bname, ref))
            it.setData(Qt.UserRole + 1, c.role)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if (bname, ref) in checked else Qt.Unchecked)
            self.comp_list.addItem(it)
        self.comp_list.blockSignals(False)
        if not any(c.role == "fpga" for b in self.design.boards.values() for c in b.components.values()) \
                and self.design.boards:
            self.summary.setText("Yüklenen kartlarda FPGA bulunamadı. FPGA'lı kartı da ekleyin.")
        self.filter_components()
        self.refresh_results()

    def filter_components(self, *_):
        q = self.comp_search.text().strip().lower()
        role = self.role_filter.currentText()
        for i in range(self.comp_list.count()):
            it = self.comp_list.item(i)
            ok = (not q or q in it.text().lower()) and \
                 (role == "Tümü" or ROLE_LABELS.get(it.data(Qt.UserRole + 1), "Cihaz") == role)
            it.setHidden(not ok)

    def set_all_components(self, state):
        self.comp_list.blockSignals(True)
        for i in range(self.comp_list.count()):
            it = self.comp_list.item(i)
            if not it.isHidden():
                it.setCheckState(state)
        self.comp_list.blockSignals(False)
        self.refresh_results()

    def checked_components(self) -> list[tuple[str, str]]:
        return [self.comp_list.item(i).data(Qt.UserRole) for i in range(self.comp_list.count())
                if self.comp_list.item(i).checkState() == Qt.Checked]

    def traces(self, bname: str, ref: str) -> list[TraceResult]:
        key = (bname, ref, self.show_unconnected.isChecked())
        if key not in self.trace_cache:
            self.trace_cache[key] = self.design.trace_component(bname, ref, include_unconnected=key[2])
        return self.trace_cache[key]

    def refresh_results(self, *_):
        self.results = [r for b, ref in self.checked_components() for r in self.traces(b, ref)]
        self.model.removeRows(0, self.model.rowCount())
        dead = QColor("#b04040")
        for r in self.results:
            s, e = r.start, r.end
            vals = [s.board, s.refdes, s.part, s.pin, s.pin_name, s.net,
                    e.board if e else "", e.refdes if e else "", e.pin if e else "—",
                    e.pin_name if e else "FPGA'ya ulaşmıyor", r.bank, r.bank_voltage,
                    " → ".join(h.note for h in r.hops if h.note)]
            row = [QStandardItem(str(v)) for v in vals]
            for it in row:
                it.setEditable(False)
                if e is None:
                    it.setForeground(dead)
            for h in r.hops:
                child = [QStandardItem(str(v)) for v in
                         [h.board, h.refdes, h.part, h.pin, h.pin_name, h.net, "", "", "", "", "", "", h.note]]
                for it in child:
                    it.setEditable(False)
                    it.setForeground(QColor("#606060"))
                row[0].appendRow(child)
            self.model.appendRow(row)
        self.apply_expand()
        for c in range(len(COLUMNS) - 1):
            self.tree.resizeColumnToContents(c)
        n_comp = len({(r.start.board, r.start.refdes) for r in self.results})
        n_ok = sum(1 for r in self.results if r.end)
        if self.design.boards:
            self.summary.setText(f"{n_comp} bileşen seçili, {n_ok} FPGA bağlantısı"
                                 + (f", {len(self.results) - n_ok} bağlantısız pin" if len(self.results) > n_ok else ""))

    def apply_expand(self, *_):
        if self.show_hops.isChecked():
            self.tree.expandAll()
        else:
            self.tree.collapseAll()

    # ------------------------------------------------------------------ export
    def export(self, kind: str):
        if not self.results:
            QMessageBox.information(self, "Dışa aktar", "Önce listeden en az bir bileşen seçin.")
            return
        flt = "CSV (*.csv)" if kind == "csv" else "Metin (*.txt)"
        path, _ = QFileDialog.getSaveFileName(self, "Dışa aktar", f"fpga_baglantilari.{kind}", flt)
        if not path:
            return
        hops = self.show_hops.isChecked()
        text = to_csv(self.results, include_path=hops) if kind == "csv" else to_txt(self.results, include_path=hops)
        # utf-8-sig so Excel opens Turkish characters correctly
        with open(path, "w", encoding="utf-8-sig" if kind == "csv" else "utf-8", newline="") as f:
            f.write(text)
        self.statusBar().showMessage(f"Kaydedildi: {path}", 6000)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("FPGA Pin Tracer")
    w = MainWindow()
    w.show()
    pdfs = [a for a in argv[1:] if a.lower().endswith(".pdf")]
    if pdfs:
        w.add_pdfs(pdfs)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
