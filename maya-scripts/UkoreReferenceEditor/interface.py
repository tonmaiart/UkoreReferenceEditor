"""Ukore Reference Editor's manual UI — a table of every broken/matched file
in the scene with its Internal/External/Unmatched scope, split into two
tabs ("Maya File" for references, "Textures" for file-texture nodes) since
those need different redirect calls (loadReference vs. setAttr) but share
the exact same scan/classify algorithm underneath. Not built on
tmlib.ui.interface_template's ToolkitWindow, since that loads a Designer
.ui file by toolkit name and this needs a dynamically populated table
rather than a static form — just the same MayaQWidgetDockableMixin +
Maya-window-parenting shape it uses underneath."""

from __future__ import annotations

import traceback
from pathlib import Path
from importlib import reload

import maya.cmds as cmds
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
from maya import OpenMayaUI
from tmlib.module.PySide import QtCore, QtGui, QtWidgets, wrapInstance
from tmlib.ui import uitools

from UkoreReferenceEditor import core, matcher

reload(matcher)
reload(core)

_LOG_PREFIX = "[UkoreReferenceEditor]"

_ICONS_DIR = Path(__file__).resolve().parent / "icons"
_STATUS_ICON_FILENAMES = {
    "ok": "icons8-check-mark-48.png",
    "missing": "icons8-cancel-48.png",
    "outdated": "icons8-update-48.png",
}
_STATUS_LABELS = {"ok": "OK", "missing": "Missing", "outdated": "Outdated"}
_SCOPE_LABELS = {"internal": "Internal", "external": "External", "unmatched": "Unmatched"}


def _get_maya_window():
    main_window_ptr = OpenMayaUI.MQtUtil.mainWindow()
    return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)


def _status_icon(status: str) -> QtGui.QIcon:
    filename = _STATUS_ICON_FILENAMES.get(status)
    if not filename:
        return QtGui.QIcon()
    icon_path = _ICONS_DIR / filename
    return QtGui.QIcon(str(icon_path)) if icon_path.is_file() else QtGui.QIcon()


def _redirect_spec(entry):
    """(button label, target path, action kind) for the row's Redirect
    button, or None if there's nothing to redirect to. Update Version is a
    separate, always-present button on the Maya File tab (see
    _open_update_version_dialog) — not handled here."""
    if getattr(entry, "status", None) == "missing" and entry.suggested_path is not None:
        return "Redirect", entry.suggested_path, "redirect"
    return None


class _EntryTable(QtWidgets.QWidget):
    """One tab's contents — a toolbar (Rescan, plus Load All/Unload All
    References on the Maya File tab), status line, and table. Only the
    scan/redirect functions and column shape differ between the "Maya
    File" tab (references — Loaded checkbox, Version column, Update
    Version button) and the "Textures" tab (file-texture nodes — no load
    state, no version concept); everything else (table population, error
    handling, debug logging, the Repath flow) is identical, so it lives
    here once instead of twice."""

    def __init__(
        self,
        label: str,
        scan_fn,
        redirect_fn,
        update_version_fn,
        path_attr: str,
        has_version_column: bool,
        has_load_checkbox: bool = False,
        has_find_all_button: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._label = label
        self._scan_fn = scan_fn
        self._redirect_fn = redirect_fn
        self._update_version_fn = update_version_fn
        self._path_attr = path_attr
        self._has_version_column = has_version_column
        self._has_load_checkbox = has_load_checkbox
        self._entries: list = []

        self._columns = []
        if has_load_checkbox:
            self._columns.append("Loaded")
        self._columns.append("Status")
        self._columns.append("File")
        if has_version_column:
            self._columns.append("Version")
            self._columns.append("Next Version")
        self._columns.append("Scope")
        self._columns.append("Actions")
        self._load_checkbox_col = 0 if has_load_checkbox else None

        layout = QtWidgets.QVBoxLayout(self)

        toolbar = QtWidgets.QHBoxLayout()
        rescan_button = QtWidgets.QPushButton("Rescan")
        rescan_button.clicked.connect(self.reload_table)
        toolbar.addWidget(rescan_button)

        if has_load_checkbox:
            load_all_button = QtWidgets.QPushButton("Load All References")
            load_all_button.clicked.connect(self._load_all)
            toolbar.addWidget(load_all_button)

            unload_all_button = QtWidgets.QPushButton("Unload All References")
            unload_all_button.clicked.connect(self._unload_all)
            toolbar.addWidget(unload_all_button)

        if has_find_all_button:
            find_all_button = QtWidgets.QPushButton("Find All Missing File...")
            find_all_button.clicked.connect(self._find_all_missing)
            toolbar.addWidget(find_all_button)

        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.status_label = QtWidgets.QLabel("")
        layout.addWidget(self.status_label)

        self.table = QtWidgets.QTableWidget(0, len(self._columns), self)
        self.table.setHorizontalHeaderLabels(self._columns)
        # Narrow columns (checkbox, icon/short-text Status, Version/Next
        # Version, Scope, the button-holding Actions column) size to their
        # own content instead of stretching — a uniform Stretch on every
        # column left Status/Version eating far more width than an icon or
        # "v021" ever needs, squeezing the File column that actually needs
        # the room. Only File stretches, so the table still always fills
        # the window exactly (no dead space, no horizontal scrollbar)
        # without wasting space on the narrow ones. The File column itself
        # only ever holds a filename now (see reload_table) — the full path
        # lives in the File Info panel below instead, which is what saves
        # the space in the first place.
        header = self.table.horizontalHeader()
        for index, column_name in enumerate(self._columns):
            if column_name == "File":
                header.setSectionResizeMode(index, QtWidgets.QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(index, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        if has_load_checkbox:
            self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._update_file_info)
        layout.addWidget(self.table)

        info_group = QtWidgets.QGroupBox("File Info")
        info_layout = QtWidgets.QFormLayout(info_group)

        def _selectable_label() -> QtWidgets.QLabel:
            info_label = QtWidgets.QLabel("")
            info_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard)
            info_label.setWordWrap(True)
            return info_label

        self.info_path_label = _selectable_label()
        info_layout.addRow("Full Reference Path:", self.info_path_label)

        self.info_status_label = _selectable_label()
        info_layout.addRow("Status:", self.info_status_label)

        self.info_scope_label = _selectable_label()
        info_layout.addRow("Scope:", self.info_scope_label)

        self.info_repo_label = _selectable_label()
        info_layout.addRow("Matched Repo:", self.info_repo_label)

        self.info_version_label = None
        if has_version_column:
            self.info_version_label = _selectable_label()
            info_layout.addRow("Version -> Next Version:", self.info_version_label)

        self.info_node_label = _selectable_label()
        info_layout.addRow("Reference Node:" if has_load_checkbox else "Node.Attribute:", self.info_node_label)

        layout.addWidget(info_group)
        self._clear_file_info()

    def reload_table(self):
        print(f"{_LOG_PREFIX} [{self._label}] Rescan clicked — scanning...")
        try:
            entries = self._scan_fn()
            # Auto-apply anything safe to auto-apply (see
            # core.auto_fix_entries's own docstring for exactly what
            # "safe" means here) right away, then rescan once more so the
            # table reflects the real post-fix state instead of a stale
            # "missing" row for something that's already been redirected.
            fixed = core.auto_fix_entries(entries, self._redirect_fn)
            if fixed:
                print(f"{_LOG_PREFIX} [{self._label}] auto-fixed {fixed} entrie(s), rescanning...")
                entries = self._scan_fn()
            self._entries = entries
        except Exception as exc:
            traceback.print_exc()
            message = f"Rescan failed: {exc}"
            print(f"{_LOG_PREFIX} [{self._label}] {message}")
            cmds.warning(f"{_LOG_PREFIX} [{self._label}] {message}")
            self.status_label.setText(message)
            self.table.setRowCount(0)
            return

        print(f"{_LOG_PREFIX} [{self._label}] scan returned {len(self._entries)} entrie(s):")
        for entry in self._entries:
            path = getattr(entry, self._path_attr)
            print(f"{_LOG_PREFIX}   path={path!r} status={entry.status} scope={entry.scope}")

        # setItem() below would otherwise fire itemChanged for every cell
        # (not just the checkbox column) while the table repopulates,
        # racing _on_item_changed against Maya calls for rows that haven't
        # even been fully built yet.
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(self._entries))

            for row, entry in enumerate(self._entries):
                col = 0

                if self._has_load_checkbox:
                    loaded_item = QtWidgets.QTableWidgetItem()
                    loaded_item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
                    loaded_item.setCheckState(
                        QtCore.Qt.Checked if getattr(entry, "is_loaded", False) else QtCore.Qt.Unchecked
                    )
                    self.table.setItem(row, col, loaded_item)
                    col += 1

                status_item = QtWidgets.QTableWidgetItem(_STATUS_LABELS.get(entry.status, entry.status))
                status_item.setIcon(_status_icon(entry.status))
                self.table.setItem(row, col, status_item)
                col += 1

                full_path = getattr(entry, self._path_attr)
                filename_item = QtWidgets.QTableWidgetItem(Path(full_path).name if full_path else "")
                filename_item.setToolTip(full_path)
                self.table.setItem(row, col, filename_item)
                col += 1

                if self._has_version_column:
                    self.table.setItem(row, col, QtWidgets.QTableWidgetItem(getattr(entry, "version", None) or ""))
                    col += 1
                    self.table.setItem(
                        row, col, QtWidgets.QTableWidgetItem(getattr(entry, "next_version", None) or "")
                    )
                    col += 1

                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(_SCOPE_LABELS[entry.scope]))
                col += 1

                self.table.setCellWidget(row, col, self._build_actions_widget(row, entry))
        finally:
            self.table.blockSignals(False)

        missing_count = sum(1 for e in self._entries if e.status == "missing")
        outdated_count = sum(1 for e in self._entries if e.status == "outdated")
        self.status_label.setText(
            f"{len(self._entries)} entrie(s) — {missing_count} missing, {outdated_count} outdated. "
            "See Script Editor for details."
        )

        # Repopulating the table drops whatever selection existed before —
        # the File Info panel would otherwise keep showing a stale row.
        self._clear_file_info()

    def _clear_file_info(self):
        self.info_path_label.setText("")
        self.info_status_label.setText("")
        self.info_scope_label.setText("")
        self.info_repo_label.setText("")
        if self.info_version_label is not None:
            self.info_version_label.setText("")
        self.info_node_label.setText("")

    def _update_file_info(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._entries):
            self._clear_file_info()
            return

        entry = self._entries[row]
        full_path = getattr(entry, self._path_attr)
        self.info_path_label.setText(full_path or "")
        self.info_status_label.setText(_STATUS_LABELS.get(entry.status, entry.status))
        self.info_scope_label.setText(_SCOPE_LABELS[entry.scope])

        matched_repo = getattr(entry, "matched_repo", None)
        self.info_repo_label.setText(matched_repo.name if matched_repo else "")

        if self.info_version_label is not None:
            version = getattr(entry, "version", None) or "-"
            next_version = getattr(entry, "next_version", None)
            self.info_version_label.setText(f"{version} -> {next_version}" if next_version else version)

        if self._has_load_checkbox:
            self.info_node_label.setText(getattr(entry, "ref_node", None) or "")
        else:
            self.info_node_label.setText(f"{entry.node_name}.{entry.attr_name}")

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._load_checkbox_col is None or item.column() != self._load_checkbox_col:
            return
        row = item.row()
        if row >= len(self._entries):
            return
        entry = self._entries[row]
        loaded = item.checkState() == QtCore.Qt.Checked
        action = "Load" if loaded else "Unload"
        print(f"{_LOG_PREFIX} [{self._label}] {action}: {getattr(entry, self._path_attr)!r}")
        ok = core.set_reference_loaded(entry.ref_node, loaded)
        print(f"{_LOG_PREFIX} [{self._label}] {action} returned {ok}")
        entry.is_loaded = loaded if ok else entry.is_loaded

    def _load_all(self):
        print(f"{_LOG_PREFIX} [{self._label}] Load All References clicked")
        for entry in self._entries:
            core.set_reference_loaded(entry.ref_node, True)
        self.reload_table()

    def _unload_all(self):
        print(f"{_LOG_PREFIX} [{self._label}] Unload All References clicked")
        for entry in self._entries:
            core.set_reference_loaded(entry.ref_node, False)
        self.reload_table()

    def _find_all_missing(self):
        missing_entries = [e for e in self._entries if e.status == "missing"]
        if not missing_entries:
            message = "No missing files to search for."
            print(f"{_LOG_PREFIX} [{self._label}] Find All Missing File: {message}")
            cmds.warning(f"{_LOG_PREFIX} [{self._label}] {message}")
            return

        # fileMode=3 is Maya's "existing directory only" mode — this button
        # is specifically "search one folder for everything missing", not
        # the single-file-or-folder choice Repath offers per row.
        chosen = cmds.fileDialog2(
            fileMode=3,
            caption="Find All Missing File — Select Folder to Search",
            okCaption="Search",
        )
        if not chosen:
            return
        search_root = Path(chosen[0])

        print(
            f"{_LOG_PREFIX} [{self._label}] Find All Missing File: searching {search_root} "
            f"for {len(missing_entries)} missing file(s)..."
        )
        results = []
        for entry in missing_entries:
            current_path = getattr(entry, self._path_attr)
            resolved = matcher.resolve_manual_target(current_path, search_root)
            print(f"{_LOG_PREFIX}   {current_path!r} -> {resolved}")
            results.append((entry, resolved))

        self._show_find_all_report(results)

    def _show_find_all_report(self, results: list):
        """`results` is [(entry, resolved_path_or_None), ...] — reports
        every match/non-match from _find_all_missing and only actually
        applies anything once the artist reviews the list and clicks
        Confirm Update; Cancel (or closing the dialog) discards all of it."""
        found_count = sum(1 for _entry, resolved in results if resolved is not None)

        dialog = QtWidgets.QDialog(self)
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

            current_path = getattr(entry, self._path_attr)
            report_table.setItem(row, 1, QtWidgets.QTableWidgetItem(Path(current_path).name if current_path else ""))
            report_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(resolved) if resolved else "Not found"))

        dialog_layout.addWidget(report_table)

        buttons = QtWidgets.QDialogButtonBox(parent=dialog)
        buttons.addButton("Confirm Update", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QtWidgets.QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)

        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            print(f"{_LOG_PREFIX} [{self._label}] Find All Missing File: cancelled, nothing applied.")
            return

        applied = 0
        for row, (entry, resolved) in enumerate(results):
            if resolved is None or check_items[row].checkState() != QtCore.Qt.Checked:
                continue
            if self._redirect_fn(entry, resolved):
                applied += 1

        print(f"{_LOG_PREFIX} [{self._label}] Find All Missing File: applied {applied} update(s).")
        if applied:
            self.reload_table()

    def _build_actions_widget(self, row: int, entry) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(container)
        row_layout.setContentsMargins(2, 0, 2, 0)

        spec = _redirect_spec(entry)
        if spec is not None:
            label, target, kind = spec
            redirect_button = QtWidgets.QPushButton(label)
            redirect_button.clicked.connect(
                lambda _checked=False, r=row, t=target, k=kind: self._run_action(r, t, k)
            )
            row_layout.addWidget(redirect_button)

        if self._has_version_column:
            update_button = QtWidgets.QPushButton("Update Version...")
            update_button.clicked.connect(lambda _checked=False, r=row: self._open_update_version_dialog(r))
            row_layout.addWidget(update_button)

        repath_file_button = QtWidgets.QPushButton("Repath File...")
        repath_file_button.clicked.connect(
            lambda _checked=False, r=row: self._repath_row(r, file_mode=1, caption="Repath File...")
        )
        row_layout.addWidget(repath_file_button)

        repath_search_button = QtWidgets.QPushButton("Repath Search...")
        repath_search_button.clicked.connect(
            lambda _checked=False, r=row: self._repath_row(r, file_mode=3, caption="Repath Search...")
        )
        row_layout.addWidget(repath_search_button)

        return container

    def _run_action(self, row: int, target_path: Path, kind: str):
        entry = self._entries[row]
        path = getattr(entry, self._path_attr)
        fn = self._update_version_fn if kind == "update_version" else self._redirect_fn
        print(f"{_LOG_PREFIX} [{self._label}] {kind}: {path!r} -> {target_path!r}")
        ok = fn(entry, target_path)
        print(f"{_LOG_PREFIX} [{self._label}] {kind} returned {ok}")
        self.reload_table()

    def _open_update_version_dialog(self, row: int):
        entry = self._entries[row]
        current_path = getattr(entry, self._path_attr)
        versions = matcher.list_available_versions(current_path)
        if not versions:
            message = f"No published versions found for {current_path!r}"
            print(f"{_LOG_PREFIX} [{self._label}] Update Version: {message}")
            cmds.warning(f"{_LOG_PREFIX} [{self._label}] {message}")
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Update Version")
        dialog_layout = QtWidgets.QVBoxLayout(dialog)
        dialog_layout.addWidget(QtWidgets.QLabel(f"Current: {current_path}"))

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
        self._run_action(row, chosen_path, "update_version")

    def _repath_row(self, row: int, file_mode: int, caption: str):
        """Backs both Repath buttons — `file_mode=1` ("Repath File...",
        Maya's single-existing-file mode) is a direct override with the
        chosen file; `file_mode=3` ("Repath Search...", Maya's
        existing-directory-only mode, same as _find_all_missing's picker)
        searches the chosen folder recursively for a file matching the
        current path's own filename via matcher.resolve_manual_target. Two
        dedicated buttons/fileModes instead of the old single ambiguous
        fileMode=2 ("pick a file OR a folder") button — Maya's own docs
        describe fileMode=2 as returning "the name of a directory" even
        though files are displayed, which made a file pick silently behave
        like a folder pick; see
        developer/bug-history/2026-08-05-repath-filemode2-native-dialog-directory-only.md.
        dialogStyle=2 forces Maya's own cross-platform dialog rather than
        the OS-native one, the convention used everywhere else in this
        codebase that calls fileDialog2."""
        entry = self._entries[row]
        current_path = getattr(entry, self._path_attr)
        starting_dir = ""
        if current_path:
            parent = Path(current_path).parent
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

        chosen_path = Path(chosen[0])
        resolved = matcher.resolve_manual_target(current_path, chosen_path)
        if resolved is None:
            message = f"No file named {Path(current_path).name!r} found under {chosen_path}"
            print(f"{_LOG_PREFIX} [{self._label}] {caption}: {message}")
            cmds.warning(f"{_LOG_PREFIX} [{self._label}] {caption}: {message}")
            return

        print(f"{_LOG_PREFIX} [{self._label}] {caption}: {current_path!r} -> {resolved!r}")
        ok = self._redirect_fn(entry, resolved)
        print(f"{_LOG_PREFIX} [{self._label}] {caption} returned {ok}")
        self.reload_table()


class MainWindow(MayaQWidgetDockableMixin, QtWidgets.QMainWindow):
    WINDOW_OBJECT = "UkoreReferenceEditor"

    def __init__(self):
        super().__init__(parent=_get_maya_window())

        # A previous MainWindow's workspaceControl (from an earlier
        # File.launch("UkoreReferenceEditor") in this same Maya session)
        # otherwise survives after the window is closed, and re-showing a
        # second instance with the same objectName then raises "Object's
        # name '...WorkspaceControl' is not unique." — same cleanup
        # tmlib.ui.interface_template.ToolkitWindow does for every other
        # tool window in this codebase, done here too since this window
        # doesn't go through ToolkitWindow.
        uitools.deleteControl(self.WINDOW_OBJECT)

        self.setWindowTitle("Ukore Reference Editor")
        self.setObjectName(self.WINDOW_OBJECT)
        self.resize(1050, 640)

        tabs = QtWidgets.QTabWidget(self)

        self.reference_tab = _EntryTable(
            label="Maya File",
            scan_fn=core.scan_references,
            redirect_fn=lambda entry, new_path: core.redirect_reference(entry.ref_node, new_path),
            update_version_fn=lambda entry, new_path: core.update_reference_version(entry.ref_node, new_path),
            path_attr="ref_path",
            has_version_column=True,
            has_load_checkbox=True,
        )
        self.texture_tab = _EntryTable(
            label="Textures",
            scan_fn=core.scan_textures,
            redirect_fn=lambda entry, new_path: core.redirect_texture(entry.node_name, entry.attr_name, new_path),
            update_version_fn=lambda entry, new_path: core.redirect_texture(entry.node_name, entry.attr_name, new_path),
            path_attr="file_path",
            has_version_column=False,
            has_load_checkbox=False,
            has_find_all_button=True,
        )
        tabs.addTab(self.reference_tab, "Maya File")
        tabs.addTab(self.texture_tab, "Textures")

        self.setCentralWidget(tabs)

        self.reference_tab.reload_table()
        self.texture_tab.reload_table()
