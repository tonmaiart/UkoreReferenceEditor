"""Ukore Reference Editor's UI — loads widget.ui (Qt Designer) and wires its
tables/buttons/info panels to core.py's scan/redirect functions, split into
two tabs ("Maya File" for references, "Textures" for file-texture nodes)
since those need different redirect calls (loadReference vs. setAttr) but
share the exact same scan/classify algorithm underneath. Not built on
tmlib.ui.interface_template's ToolkitWindow (which expects a toolkit
package with a ui.ui file discoverable by name via File.load_ui_external) —
widget.ui is loaded directly via tmlib.core.File.load_ui instead, same
MayaQWidgetDockableMixin + Maya-window-parenting shape ToolkitWindow uses
underneath."""

from __future__ import annotations

import traceback
from pathlib import Path
from importlib import reload

import maya.cmds as cmds
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
from maya import OpenMayaUI
from tmlib.module.PySide import QtCore, QtGui, QtWidgets, wrapInstance
from tmlib.core import File
from tmlib.ui import uitools

from UkoreReferenceEditor import core, matcher

reload(matcher)
reload(core)

_LOG_PREFIX = "[UkoreReferenceEditor]"
_UI_PATH = Path(__file__).resolve().parent / "widget.ui"

_STATUS_LABELS = {"ok": "OK", "missing": "Missing", "outdated": "Outdated"}
_SCOPE_LABELS = {"internal": "Internal", "external": "External", "unmatched": "Unmatched"}

_STATUS_ICON_PIXMAPS = {
    "ok": QtWidgets.QStyle.SP_DialogApplyButton,
    "missing": QtWidgets.QStyle.SP_DialogCancelButton,
    "outdated": QtWidgets.QStyle.SP_BrowserReload,
}
_LOADED_ICON_PIXMAPS = {
    True: QtWidgets.QStyle.SP_DialogYesButton,
    False: QtWidgets.QStyle.SP_DialogNoButton,
}

_REF_COLUMNS = ["Loaded", "Status", "Reference Node", "File", "Version", "Next Version", "Scope"]
(
    _REF_COL_LOADED,
    _REF_COL_STATUS,
    _REF_COL_NODE,
    _REF_COL_FILE,
    _REF_COL_VERSION,
    _REF_COL_NEXT_VERSION,
    _REF_COL_SCOPE,
) = range(len(_REF_COLUMNS))

_TEX_COLUMNS = ["Status", "File", "Scope"]
_TEX_COL_STATUS, _TEX_COL_FILE, _TEX_COL_SCOPE = range(len(_TEX_COLUMNS))


def _get_maya_window():
    main_window_ptr = OpenMayaUI.MQtUtil.mainWindow()
    return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)


def _standard_icon(pixmap) -> QtGui.QIcon:
    return QtWidgets.QApplication.instance().style().standardIcon(pixmap)


def _status_icon(status: str) -> QtGui.QIcon:
    return _standard_icon(_STATUS_ICON_PIXMAPS.get(status, QtWidgets.QStyle.SP_FileIcon))


def _loaded_icon(is_loaded: bool) -> QtGui.QIcon:
    return _standard_icon(_LOADED_ICON_PIXMAPS[bool(is_loaded)])


def _set_table_columns(
    table: QtWidgets.QTableWidget, columns: list, stretch_column: str, wide_columns: dict | None = None
):
    wide_columns = wide_columns or {}
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    header = table.horizontalHeader()
    for index, name in enumerate(columns):
        if name == stretch_column:
            header.setSectionResizeMode(index, QtWidgets.QHeaderView.Stretch)
        elif name in wide_columns:
            # Interactive (not ResizeToContents) so the wider starting
            # width actually sticks instead of immediately shrinking back
            # to fit the cell text.
            header.setSectionResizeMode(index, QtWidgets.QHeaderView.Interactive)
            table.setColumnWidth(index, wide_columns[name])
        else:
            header.setSectionResizeMode(index, QtWidgets.QHeaderView.ResizeToContents)


def _selected_rows(table: QtWidgets.QTableWidget) -> list[int]:
    return sorted({index.row() for index in table.selectionModel().selectedRows()})


class _ReferenceTab:
    """Wires the "Maya File" tab's tableWidget_maya_reference_file, its
    Load/Unload/Reload/Change Version/Search Folder.../Change Reference
    File.../Auto Resolve buttons, and its Reference File Info line edits.
    There's no dedicated "Rescan" button on this tab in widget.ui — Auto
    Resolve doubles as it (scan + apply the safe-only auto-fix pass, same
    as every other action's own post-action refresh, just with auto-fix
    turned on); every other button only refreshes to reflect its own
    action, without also silently auto-fixing unrelated rows."""

    def __init__(self, ui):
        self.table: QtWidgets.QTableWidget = ui.tableWidget_maya_reference_file
        self._entries: list = []

        _set_table_columns(self.table, _REF_COLUMNS, stretch_column="File", wide_columns={"Reference Node": 220})
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self._info_lines = (
            ui.lineEdit_reference_node,
            ui.lineEdit_absolute_reference_path,
            ui.lineEdit_status_2,
            ui.lineEdit_reference_repo_scope_2,
            ui.lineEdit_current_version,
            ui.lineEdit_lastest_version,
        )
        for line_edit in self._info_lines:
            line_edit.setReadOnly(True)
        self._clear_info_panel()

        self._load_button = ui.pushButton_load_selected_reference
        self._unload_button = ui.pushButton_unload_selected_reference
        self._update_action_buttons()

        ui.pushButton_load_selected_reference.clicked.connect(self._on_load)
        ui.pushButton_unload_selected_reference.clicked.connect(self._on_unload)
        ui.pushButton_reload_selected_reference.clicked.connect(self._on_reload)
        ui.pushButton_change_version.clicked.connect(self._on_change_version)
        ui.pushButton_search_by_folder.clicked.connect(self._on_search_folder)
        ui.pushButton_change_reference_file.clicked.connect(self._on_change_reference_file)
        ui.pushButton_auto_resolve.clicked.connect(self._on_auto_resolve)

    def reload_table(self, run_auto_fix: bool = True):
        print(f"{_LOG_PREFIX} [Maya File] scanning...")
        try:
            entries = core.scan_references()
            if run_auto_fix:
                fixed = core.auto_fix_entries(entries, self._redirect)
                if fixed:
                    print(f"{_LOG_PREFIX} [Maya File] auto-fixed {fixed} entrie(s), rescanning...")
                    entries = core.scan_references()
            self._entries = entries
        except Exception as exc:
            traceback.print_exc()
            cmds.warning(f"{_LOG_PREFIX} [Maya File] Rescan failed: {exc}")
            self.table.setRowCount(0)
            return

        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(self._entries))
            for row, entry in enumerate(self._entries):
                loaded_item = QtWidgets.QTableWidgetItem("Loaded" if entry.is_loaded else "Unloaded")
                loaded_item.setIcon(_loaded_icon(entry.is_loaded))
                self.table.setItem(row, _REF_COL_LOADED, loaded_item)

                status_item = QtWidgets.QTableWidgetItem(_STATUS_LABELS.get(entry.status, entry.status))
                status_item.setIcon(_status_icon(entry.status))
                self.table.setItem(row, _REF_COL_STATUS, status_item)

                self.table.setItem(row, _REF_COL_NODE, QtWidgets.QTableWidgetItem(entry.ref_node or ""))

                filename_item = QtWidgets.QTableWidgetItem(Path(entry.ref_path).name if entry.ref_path else "")
                filename_item.setToolTip(entry.ref_path or "")
                self.table.setItem(row, _REF_COL_FILE, filename_item)

                self.table.setItem(row, _REF_COL_VERSION, QtWidgets.QTableWidgetItem(entry.version or ""))
                self.table.setItem(
                    row, _REF_COL_NEXT_VERSION, QtWidgets.QTableWidgetItem(entry.next_version or "")
                )
                self.table.setItem(row, _REF_COL_SCOPE, QtWidgets.QTableWidgetItem(_SCOPE_LABELS[entry.scope]))
        finally:
            self.table.blockSignals(False)

        # Repopulating the table drops whatever selection existed before —
        # refreshes both the info panel and the Load/Unload enabled state
        # back to their "nothing selected" defaults.
        self._on_selection_changed()

    @staticmethod
    def _redirect(entry, new_path) -> bool:
        return core.redirect_reference(entry.ref_node, new_path)

    def _selected_entries(self) -> list:
        rows = _selected_rows(self.table)
        return [self._entries[row] for row in rows if row < len(self._entries)]

    def _clear_info_panel(self):
        for line_edit in self._info_lines:
            line_edit.setText("")

    def _on_selection_changed(self):
        self._update_info_panel()
        self._update_action_buttons()

    def _update_action_buttons(self):
        """Load is only useful while something selected is still unloaded;
        Unload is only useful while something selected is still loaded —
        both disabled outright with no selection at all."""
        entries = self._selected_entries()
        self._load_button.setEnabled(any(not entry.is_loaded for entry in entries))
        self._unload_button.setEnabled(any(entry.is_loaded for entry in entries))

    def _update_info_panel(self):
        rows = _selected_rows(self.table)
        if len(rows) != 1:
            self._clear_info_panel()
            return
        entry = self._entries[rows[0]]
        node, path, status, scope, version, next_version = self._info_lines
        node.setText(entry.ref_node or "")
        path.setText(entry.ref_path or "")
        status.setText(_STATUS_LABELS.get(entry.status, entry.status))
        scope.setText(_SCOPE_LABELS[entry.scope])
        version.setText(entry.version or "")
        next_version.setText(entry.next_version or "")

    def _on_load(self):
        entries = self._selected_entries()
        if not entries:
            cmds.warning(f"{_LOG_PREFIX} [Maya File] Load: no reference selected.")
            return
        for entry in entries:
            core.set_reference_loaded(entry.ref_node, True)
        self.reload_table(run_auto_fix=False)

    def _on_unload(self):
        entries = self._selected_entries()
        if not entries:
            cmds.warning(f"{_LOG_PREFIX} [Maya File] Unload: no reference selected.")
            return
        for entry in entries:
            core.set_reference_loaded(entry.ref_node, False)
        self.reload_table(run_auto_fix=False)

    def _on_reload(self):
        entries = self._selected_entries()
        if not entries:
            cmds.warning(f"{_LOG_PREFIX} [Maya File] Reload: no reference selected.")
            return
        for entry in entries:
            # loadReference always re-reads from disk even when the
            # reference is already loaded, so this forces a refresh rather
            # than just changing load state (unlike Load/Unload).
            core.set_reference_loaded(entry.ref_node, True)
        self.reload_table(run_auto_fix=False)

    def _on_change_version(self):
        rows = _selected_rows(self.table)
        if len(rows) != 1:
            cmds.warning(f"{_LOG_PREFIX} [Maya File] Change Version: select exactly one reference.")
            return
        entry = self._entries[rows[0]]
        versions = matcher.list_available_versions(entry.ref_path)
        if not versions:
            cmds.warning(f"{_LOG_PREFIX} [Maya File] No published versions found for {entry.ref_path!r}")
            return

        dialog = QtWidgets.QDialog(self.table)
        dialog.setWindowTitle("Change Version")
        dialog_layout = QtWidgets.QVBoxLayout(dialog)
        dialog_layout.addWidget(QtWidgets.QLabel(f"Current: {entry.ref_path}"))

        combo = QtWidgets.QComboBox(dialog)
        for version_label, version_path in versions:
            suffix = " (latest)" if version_path == versions[0][1] else ""
            combo.addItem(f"{version_label}{suffix}  —  {version_path.name}", str(version_path))
        dialog_layout.addWidget(combo)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, parent=dialog
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)

        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        chosen_path = Path(combo.currentData())
        ok = core.update_reference_version(entry.ref_node, chosen_path)
        print(f"{_LOG_PREFIX} [Maya File] Change Version: {entry.ref_path!r} -> {chosen_path!r} returned {ok}")
        self.reload_table(run_auto_fix=False)

    def _on_search_folder(self):
        rows = _selected_rows(self.table)
        if len(rows) != 1:
            cmds.warning(f"{_LOG_PREFIX} [Maya File] Search Folder...: select exactly one reference.")
            return
        self._repath(self._entries[rows[0]], file_mode=3, caption="Search Folder...")

    def _on_change_reference_file(self):
        rows = _selected_rows(self.table)
        if len(rows) != 1:
            cmds.warning(f"{_LOG_PREFIX} [Maya File] Change Reference File...: select exactly one reference.")
            return
        self._repath(self._entries[rows[0]], file_mode=1, caption="Change Reference File...")

    def _repath(self, entry, file_mode: int, caption: str):
        starting_dir = ""
        if entry.ref_path:
            parent = Path(entry.ref_path).parent
            if parent.is_dir():
                starting_dir = str(parent)

        chosen = cmds.fileDialog2(
            fileMode=file_mode,
            dialogStyle=2,
            caption=caption,
            okCaption="Select" if file_mode == 1 else "Search",
            startingDirectory=starting_dir,
        )
        if not chosen:
            return

        resolved = matcher.resolve_manual_target(entry.ref_path, Path(chosen[0]))
        if resolved is None:
            cmds.warning(
                f"{_LOG_PREFIX} [Maya File] {caption}: no file named "
                f"{Path(entry.ref_path).name!r} found under {chosen[0]}"
            )
            return

        ok = core.redirect_reference(entry.ref_node, resolved)
        print(f"{_LOG_PREFIX} [Maya File] {caption}: {entry.ref_path!r} -> {resolved!r} returned {ok}")
        self.reload_table(run_auto_fix=False)

    def _on_auto_resolve(self):
        self.reload_table(run_auto_fix=True)


class _TextureTab:
    """Wires the "Textures" tab's tableWidget_textures, its Rescan / Find
    All Missing File... buttons, and its Texture File Info line edits."""

    def __init__(self, ui):
        self.table: QtWidgets.QTableWidget = ui.tableWidget_textures
        self._entries: list = []

        _set_table_columns(self.table, _TEX_COLUMNS, stretch_column="File")
        self.table.itemSelectionChanged.connect(self._update_info_panel)

        self._info_lines = (
            ui.lineEdit_absolute_texture_path,
            ui.lineEdit_status,
            ui.lineEdit_reference_repo_scope,
        )
        for line_edit in self._info_lines:
            line_edit.setReadOnly(True)
        self._clear_info_panel()

        ui.pushButton_rescan_texture.clicked.connect(self.reload_table)
        ui.pushButton_find_all_missing_texture.clicked.connect(self._on_find_all_missing)

    def reload_table(self):
        print(f"{_LOG_PREFIX} [Textures] scanning...")
        try:
            entries = core.scan_textures()
            fixed = core.auto_fix_entries(entries, self._redirect)
            if fixed:
                print(f"{_LOG_PREFIX} [Textures] auto-fixed {fixed} entrie(s), rescanning...")
                entries = core.scan_textures()
            self._entries = entries
        except Exception as exc:
            traceback.print_exc()
            cmds.warning(f"{_LOG_PREFIX} [Textures] Rescan failed: {exc}")
            self.table.setRowCount(0)
            return

        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(self._entries))
            for row, entry in enumerate(self._entries):
                status_item = QtWidgets.QTableWidgetItem(_STATUS_LABELS.get(entry.status, entry.status))
                status_item.setIcon(_status_icon(entry.status))
                self.table.setItem(row, _TEX_COL_STATUS, status_item)

                filename_item = QtWidgets.QTableWidgetItem(Path(entry.file_path).name if entry.file_path else "")
                filename_item.setToolTip(entry.file_path or "")
                self.table.setItem(row, _TEX_COL_FILE, filename_item)

                self.table.setItem(row, _TEX_COL_SCOPE, QtWidgets.QTableWidgetItem(_SCOPE_LABELS[entry.scope]))
        finally:
            self.table.blockSignals(False)

        self._clear_info_panel()

    @staticmethod
    def _redirect(entry, new_path) -> bool:
        return core.redirect_texture(entry.node_name, entry.attr_name, new_path)

    def _clear_info_panel(self):
        for line_edit in self._info_lines:
            line_edit.setText("")

    def _update_info_panel(self):
        rows = _selected_rows(self.table)
        if len(rows) != 1:
            self._clear_info_panel()
            return
        entry = self._entries[rows[0]]
        path, status, scope = self._info_lines
        path.setText(entry.file_path or "")
        status.setText(_STATUS_LABELS.get(entry.status, entry.status))
        scope.setText(_SCOPE_LABELS[entry.scope])

    def _on_find_all_missing(self):
        missing_entries = [e for e in self._entries if e.status == "missing"]
        if not missing_entries:
            cmds.warning(f"{_LOG_PREFIX} [Textures] Find All Missing File: no missing files to search for.")
            return

        # fileMode=3 is Maya's "existing directory only" mode — this button
        # is specifically "search one folder for everything missing", not a
        # per-row choice.
        chosen = cmds.fileDialog2(
            fileMode=3,
            dialogStyle=2,
            caption="Find All Missing File — Select Folder to Search",
            okCaption="Search",
        )
        if not chosen:
            return
        search_root = Path(chosen[0])

        results = []
        for entry in missing_entries:
            resolved = matcher.resolve_manual_target(entry.file_path, search_root)
            results.append((entry, resolved))

        self._show_find_all_report(results)

    def _show_find_all_report(self, results: list):
        """`results` is [(entry, resolved_path_or_None), ...] — reports
        every match/non-match from _on_find_all_missing and only actually
        applies anything once the artist reviews the list and clicks
        Confirm Update; Cancel (or closing the dialog) discards all of it."""
        found_count = sum(1 for _entry, resolved in results if resolved is not None)

        dialog = QtWidgets.QDialog(self.table)
        dialog.setWindowTitle("Find All Missing File — Results")
        dialog.resize(700, 400)
        dialog_layout = QtWidgets.QVBoxLayout(dialog)
        dialog_layout.addWidget(
            QtWidgets.QLabel(f"Found {found_count} of {len(results)} missing file(s). Review, then confirm.")
        )

        report_table = QtWidgets.QTableWidget(len(results), 3, dialog)
        report_table.setHorizontalHeaderLabels(["Update?", "File", "Result"])
        report_header = report_table.horizontalHeader()
        report_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        report_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        report_header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        report_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        check_items = []
        for row, (entry, resolved) in enumerate(results):
            check_item = QtWidgets.QTableWidgetItem()
            if resolved is not None:
                check_item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
                check_item.setCheckState(QtCore.Qt.Checked)
            else:
                check_item.setFlags(QtCore.Qt.NoItemFlags)
                check_item.setCheckState(QtCore.Qt.Unchecked)
            report_table.setItem(row, 0, check_item)
            check_items.append(check_item)

            report_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(Path(entry.file_path).name if entry.file_path else "")
            )
            report_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(resolved) if resolved else "Not found"))

        dialog_layout.addWidget(report_table)

        buttons = QtWidgets.QDialogButtonBox(parent=dialog)
        buttons.addButton("Confirm Update", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QtWidgets.QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)

        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            print(f"{_LOG_PREFIX} [Textures] Find All Missing File: cancelled, nothing applied.")
            return

        applied = 0
        for row, (entry, resolved) in enumerate(results):
            if resolved is None or check_items[row].checkState() != QtCore.Qt.Checked:
                continue
            if self._redirect(entry, resolved):
                applied += 1

        print(f"{_LOG_PREFIX} [Textures] Find All Missing File: applied {applied} update(s).")
        if applied:
            self.reload_table()


class MainWindow(MayaQWidgetDockableMixin, QtWidgets.QMainWindow):
    WINDOW_OBJECT = "UkoreReferenceEditor"

    def __init__(self):
        super().__init__(parent=_get_maya_window())

        # A previous MainWindow's workspaceControl (from an earlier
        # File.launch("UkoreReferenceEditor") in this same Maya session)
        # otherwise survives after the window is closed, and re-showing a
        # second instance with the same objectName then raises "Object's
        # name '...WorkspaceControl' is not unique."
        uitools.deleteControl(self.WINDOW_OBJECT)

        self.setWindowTitle("Ukore Reference Editor")
        self.setObjectName(self.WINDOW_OBJECT)

        self.ui = File.load_ui(str(_UI_PATH))
        self.setCentralWidget(self.ui)
        self.resize(1050, 640)

        self.ui.lineEdit_current_repo_name.setReadOnly(True)
        self.ui.lineEdit_current_repo_path.setReadOnly(True)
        repo_name, repo_path = core.get_active_repo_info()
        self.ui.lineEdit_current_repo_name.setText(repo_name)
        self.ui.lineEdit_current_repo_path.setText(repo_path)

        self.reference_tab = _ReferenceTab(self.ui)
        self.texture_tab = _TextureTab(self.ui)

        self.reference_tab.reload_table()
        self.texture_tab.reload_table()
