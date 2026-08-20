"""Dreamwall Picker loading, migrated from the standalone `dw_publish_picker`
plugin (merged into UkoreReferenceEditor so both tools share one repo-path/
Maya-launch bridge instead of two). Auto-detects character namespaces in the
scene and loads matching Dreamwall Pickers published under the active repo's
'DreamwallPicker' Custom Path — same `get_dreamwall_picker_dir`/
`get_available_picker_versions`/`resolve_picker_file` resolution algorithm
the standalone plugin used, unchanged by the move. Backs the "Dreamwall
Picker" tab in widget.ui (see interface.py's `_PickerTab`) and still
self-registers its own kAfterOpen scene-open callback the same way the old
plugin did, independent of `core.py`'s reference/texture/audio auto-fix
flow — a Dreamwall Picker isn't a Maya reference or file-texture node, so it
doesn't fit `matcher.find_match_for_path`/`resolve_redirect`'s project/repo
path-matching algorithm; its own path resolution is Custom-Path-driven
instead (see `get_dreamwall_picker_dir`)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePath

import maya.api.OpenMaya as om
import maya.cmds as cmds
from tmlib.module.PySide import QtWidgets

from UkoreReferenceEditor import repo_paths

_LOG_PREFIX = "[UkoreReferenceEditor] [Dreamwall Picker]"
_VERSION_PATTERN = re.compile(r"^v(\d{3})$", re.IGNORECASE)


def get_maya_main_window() -> QtWidgets.QWidget | None:
    """Retrieve Maya's main window as a PySide widget parent."""
    for widget in QtWidgets.QApplication.topLevelWidgets():
        if widget.objectName() == "MayaWindow":
            return widget
    return None


def get_dreamwall_picker_dir() -> Path | None:
    """Resolve physical path for 'DreamwallPicker' Custom Path using active repo."""
    project, repo, repo_path = repo_paths.get_active_repo()
    if not (project and repo and repo_path):
        print(f"{_LOG_PREFIX} No active project/repo found in UkoreHub.")
        return None

    custom_paths = repo_paths.get_custom_paths(project.id, repo.id)
    target_cp = None
    for cp in custom_paths:
        cp_id = cp.get("id", "").lower()
        cp_label = cp.get("label", "").lower()
        if "dreamwallpicker" in cp_id or "dreamwallpicker" in cp_label or "dreamwall picker" in cp_label:
            target_cp = cp
            break

    if not target_cp:
        print(f"{_LOG_PREFIX} Custom Path 'DreamwallPicker' is not configured for this repo.")
        return None

    raw_path = target_cp.get("path", "").strip("/\\").replace("\\", "/")
    if not raw_path:
        print(f"{_LOG_PREFIX} Configured Custom Path 'DreamwallPicker' path is empty.")
        return None

    full_path = repo_path / PurePath(raw_path)

    if not full_path.is_dir():
        print(f"{_LOG_PREFIX} Target directory does not exist on disk: {full_path}")
        return None

    return full_path


class PickerVersionDialog(QtWidgets.QDialog):
    """Dialog prompting the user when picker version resolution needs decision."""

    RESULT_CANCEL = 0
    RESULT_LATEST = 1
    RESULT_PICK = 2

    def __init__(self, char_name: str, available_versions: list[str], parent=None):
        super().__init__(parent or get_maya_main_window())
        self.setWindowTitle(f"DW Picker — Version Choice ({char_name})")
        self.resize(380, 160)

        self.selected_version: str | None = None
        self.choice = self.RESULT_CANCEL

        layout = QtWidgets.QVBoxLayout(self)

        label_msg = QtWidgets.QLabel(
            f"<b>Character:</b> {char_name}<br>"
            f"Please choose how to load the Picker for this character:"
        )
        layout.addWidget(label_msg)

        combo_layout = QtWidgets.QHBoxLayout()
        combo_layout.addWidget(QtWidgets.QLabel("Select Version:"))
        self.version_combo = QtWidgets.QComboBox()
        self.version_combo.addItems(available_versions)
        if available_versions:
            self.version_combo.setCurrentIndex(len(available_versions) - 1)
        combo_layout.addWidget(self.version_combo)
        layout.addLayout(combo_layout)

        btn_layout = QtWidgets.QHBoxLayout()
        latest_str = available_versions[-1] if available_versions else "N/A"
        self.btn_latest = QtWidgets.QPushButton(f"Use Latest ({latest_str})")
        self.btn_pick = QtWidgets.QPushButton("Pick Version")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")

        btn_layout.addWidget(self.btn_latest)
        btn_layout.addWidget(self.btn_pick)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.btn_latest.clicked.connect(self._on_latest)
        self.btn_pick.clicked.connect(self._on_pick)
        self.btn_cancel.clicked.connect(self.reject)

    def _on_latest(self):
        self.choice = self.RESULT_LATEST
        self.accept()

    def _on_pick(self):
        self.choice = self.RESULT_PICK
        self.selected_version = self.version_combo.currentText()
        self.accept()


def get_available_picker_versions(char_dir: Path) -> list[str]:
    """Scans character directory and returns sorted list of vNNN version folder names."""
    if not char_dir.is_dir():
        return []

    versions = []
    for entry in os.listdir(char_dir):
        if (char_dir / entry).is_dir() and _VERSION_PATTERN.match(entry):
            if (char_dir / entry / "Picker.json").exists():
                versions.append(entry)

    versions.sort(key=lambda v: int(_VERSION_PATTERN.match(v).group(1)))
    return versions


def resolve_picker_file(char_dir: Path, char_name: str) -> Path | None:
    """Finds target Picker.json file, prompting User dialog if version needs decision."""
    versions = get_available_picker_versions(char_dir)

    if not versions:
        print(f"{_LOG_PREFIX} No valid vXXX folders with Picker.json found under: {char_dir}")
        return None

    if len(versions) == 1:
        return char_dir / versions[0] / "Picker.json"

    dialog = PickerVersionDialog(char_name=char_name, available_versions=versions)
    if dialog.exec_() != QtWidgets.QDialog.Accepted or dialog.choice == PickerVersionDialog.RESULT_CANCEL:
        print(f"{_LOG_PREFIX} Canceled loading picker for '{char_name}'.")
        return None

    target_version = None
    if dialog.choice == PickerVersionDialog.RESULT_LATEST:
        target_version = versions[-1]
    elif dialog.choice == PickerVersionDialog.RESULT_PICK:
        target_version = dialog.selected_version

    if target_version:
        picker_path = char_dir / target_version / "Picker.json"
        if picker_path.exists():
            return picker_path

    return None


def _find_image_in_dir(directory: Path, filename: str) -> Path | None:
    """Recursively search a directory for a file with the given name."""
    if not directory.is_dir():
        return None
    for candidate in directory.rglob(filename):
        if candidate.is_file():
            return candidate
    return None


def fix_picker_image_paths(picker_path: Path) -> bool:
    """Repath any missing shape 'image.path' entries to a same-named file found
    under this Picker.json's own version folder, so dwpicker's blocking
    MissingImages dialog never fires for images that simply moved with a
    publish (dwpicker's add_picker_from_file checks existence right after
    json.load, before UkoreHub ever gets a chance to intervene). Returns
    whether anything was actually rewritten — lets callers (e.g. Auto
    Resolve, which runs this over every character up front) report how many
    picker files it touched."""
    try:
        with open(picker_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as err:
        print(f"{_LOG_PREFIX} Error reading {picker_path}: {err}")
        return False

    version_dir = picker_path.parent
    modified = False

    for shape in data.get("shapes", []):
        img_path = shape.get("image.path")
        if not img_path:
            continue

        # dwpicker paths may contain env vars (e.g. $DWPICKER_PROJECT_DIRECTORY/...)
        if os.path.exists(os.path.expandvars(img_path)):
            continue

        img_filename = os.path.basename(img_path)
        if not img_filename:
            continue

        found = _find_image_in_dir(version_dir, img_filename)
        if not found:
            print(f"{_LOG_PREFIX} Could not auto-resolve missing image: {img_path}")
            continue

        shape["image.path"] = str(found).replace("\\", "/")
        modified = True

    if not modified:
        return False

    try:
        with open(picker_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"{_LOG_PREFIX} Auto-resolved missing image paths for: {picker_path.name}")
    except Exception as err:
        print(f"{_LOG_PREFIX} Warning: Could not rewrite {picker_path}: {err}")
        return False

    return True


def _matching_namespaces(char_folder: str, scene_namespaces: list[str]) -> list[str]:
    return [ns for ns in scene_namespaces if char_folder.lower() in ns.lower()]


def _open_picker_for_character(char_folder: str, picker_path: Path, matching_ns: list[str]) -> None:
    """Fixes `picker_path`'s image paths, opens it in dwpicker, then remaps
    every shape's action targets onto `matching_ns` (the scene namespace
    that owns this character) — the shared per-character step both
    `import_all_picker`'s scan loop and `load_picker_for_character`'s manual
    override use."""
    import dwpicker

    fix_picker_image_paths(picker_path)

    print(f"{_LOG_PREFIX} Opening picker: {picker_path}")
    dwpicker.open_picker_file(str(picker_path))

    picker = dwpicker.current()
    if not picker:
        return

    new_ns = matching_ns[0] if matching_ns else ""

    for shape in picker.document.shapes:
        targets = shape.options.get("action.targets", [])
        if not targets:
            continue

        new_targets = []
        for t in targets:
            base_name = t.split(":")[-1]
            if not new_ns or new_ns == ":":
                new_targets.append(base_name)
            else:
                new_targets.append(f"{new_ns}:{base_name}")

        shape.options["action.targets"] = new_targets

    picker.update()


def _scene_namespaces() -> list[str]:
    return [
        ns for ns in cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True)
        if ns not in ("UI", "shared")
    ]


def import_all_picker() -> None:
    """Scan Maya scene for character matching folders and open Picker files."""
    picker_dir = get_dreamwall_picker_dir()
    if not picker_dir or not picker_dir.exists():
        cmds.warning(f"{_LOG_PREFIX} Cannot import pickers: Invalid or missing DreamwallPicker directory.")
        return

    print(f"{_LOG_PREFIX} Searching Pickers from: {picker_dir}")

    try:
        import dwpicker
    except ImportError:
        cmds.warning(f"{_LOG_PREFIX} Error: 'dwpicker' module is not installed or available in Maya.")
        return

    if hasattr(dwpicker, "show"):
        dwpicker.show()

    if hasattr(dwpicker, "_dwpicker") and hasattr(dwpicker._dwpicker, "clear"):
        dwpicker._dwpicker.clear()

    scene_namespaces = _scene_namespaces()

    for char_folder in os.listdir(picker_dir):
        char_dir = picker_dir / char_folder
        if not char_dir.is_dir():
            continue

        matching_ns = _matching_namespaces(char_folder, scene_namespaces)
        ls_scene = cmds.ls(f"{char_folder}*", type="transform") or cmds.ls(f"{char_folder}*")

        if not (matching_ns or ls_scene):
            continue

        picker_path = resolve_picker_file(char_dir, char_folder)
        if not picker_path:
            continue

        _open_picker_for_character(char_folder, picker_path, matching_ns)

    print(f"{_LOG_PREFIX} Pickers successfully loaded.")


def load_picker_for_character(char_folder: str, picker_path: Path) -> bool:
    """Manual override for one character — backs the "Change Picker Path..."
    button (`interface.py`'s `_PickerTab`). Unlike `import_all_picker`'s
    scan, the artist has already picked the exact Picker.json file to use,
    so this skips `resolve_picker_file`'s version lookup/dialog entirely and
    opens `picker_path` directly."""
    if not picker_path.exists():
        cmds.warning(f"{_LOG_PREFIX} Change Picker Path: file does not exist: {picker_path}")
        return False

    try:
        import dwpicker  # noqa: F401 - import-availability check, used inside _open_picker_for_character
    except ImportError:
        cmds.warning(f"{_LOG_PREFIX} Error: 'dwpicker' module is not installed or available in Maya.")
        return False

    matching_ns = _matching_namespaces(char_folder, _scene_namespaces())
    _open_picker_for_character(char_folder, picker_path, matching_ns)
    return True


@dataclass
class PickerEntry:
    char_name: str
    picker_path: str  # resolved latest Picker.json path, "" if none found
    exists: bool
    status: str  # "missing" | "ok"


def scan_pickers() -> list[PickerEntry]:
    """One row per character folder found under the active repo's
    DreamwallPicker Custom Path — backs the "Dreamwall Picker" tab's table.
    Always resolves to the latest version (`get_available_picker_versions`'s
    own newest-last ordering, same convention `resolve_picker_file` uses for
    its single-version case) rather than popping `PickerVersionDialog` —
    that dialog only makes sense for the artist's own Auto Load Picker
    click, not a passive table scan."""
    picker_dir = get_dreamwall_picker_dir()
    if not picker_dir or not picker_dir.exists():
        return []

    entries: list[PickerEntry] = []
    for char_folder in sorted(os.listdir(picker_dir)):
        char_dir = picker_dir / char_folder
        if not char_dir.is_dir():
            continue

        versions = get_available_picker_versions(char_dir)
        if versions:
            picker_path = char_dir / versions[-1] / "Picker.json"
            entries.append(PickerEntry(char_folder, str(picker_path), True, "ok"))
        else:
            entries.append(PickerEntry(char_folder, "", False, "missing"))

    return entries


def auto_resolve_pickers() -> int:
    """Backs the "Auto Resolve" button — resolves every character's picker
    path (`scan_pickers` already does the lookup) and, for every one that
    resolved, runs `fix_picker_image_paths` on it: the same image-source-path
    resolution step `import_all_picker` runs right after resolving a
    picker's path, just applied proactively to every character the
    DreamwallPicker directory knows about instead of only ones already
    matched to something in the current scene. Returns how many Picker.json
    files actually got at least one image path rewritten."""
    fixed = 0
    for entry in scan_pickers():
        if not entry.exists:
            continue
        if fix_picker_image_paths(Path(entry.picker_path)):
            fixed += 1
    return fixed


def _scene_has_matching_character(picker_dir: Path) -> bool:
    """Whether the current scene has any namespace/node matching one of the
    character folders under picker_dir. Used by the automatic scene-open
    trigger to stay silent (no dwpicker panel, no warnings) for scenes that
    have nothing to do with any published Dreamwall Picker, instead of
    popping an empty picker on every unrelated file open."""
    scene_namespaces = _scene_namespaces()
    for char_folder in os.listdir(picker_dir):
        char_dir = picker_dir / char_folder
        if not char_dir.is_dir():
            continue
        if any(char_folder.lower() in ns.lower() for ns in scene_namespaces):
            return True
        if cmds.ls(f"{char_folder}*", type="transform") or cmds.ls(f"{char_folder}*"):
            return True
    return False


def auto_import_all_picker() -> None:
    """Automatic counterpart to import_all_picker() for the kAfterOpen scene
    callback below — same picker resolution/loading, but silent (no
    cmds.warning noise for repos/scenes that simply don't use pickers) and
    only runs once the newly opened scene actually contains a character
    matching one of the DreamwallPicker folders."""
    picker_dir = get_dreamwall_picker_dir()
    if not picker_dir or not picker_dir.exists():
        return
    if not _scene_has_matching_character(picker_dir):
        return
    import_all_picker()


def _auto_import_all_picker_deferred() -> None:
    try:
        auto_import_all_picker()
    except Exception as exc:  # noqa: BLE001 - one bad auto-load must not break scene open
        cmds.warning(f"{_LOG_PREFIX} Automatic picker load failed: {exc}")


def _on_scene_opened(*_args) -> None:
    # Deferred, not inline: this callback runs synchronously inside Maya's
    # kAfterOpen — still mid file-open transaction, where referenced
    # namespaces/nodes this scan depends on aren't reliably queryable yet
    # (same reasoning core.py documents for its own _deferred_load).
    # evalDeferred runs it once Maya reaches its own idle point after the
    # open transaction finishes.
    cmds.evalDeferred(_auto_import_all_picker_deferred)


_scene_open_callback_id = None


def register_scene_open_callback() -> None:
    """Auto-loads Dreamwall Pickers on every scene open — own independent
    kAfterOpen MSceneMessage callback (Maya allows multiple independent
    callbacks on the same message), separate from `core.py`'s
    reference/texture/audio auto-fix (that one is driven by MayaToolkit's
    own kAfterOpen dispatcher instead, see this plugin's README). Must be
    called from pre_open_mel (see plugin.py's launch_hooks) so it's already
    registered before MayaLauncher's own `file -open` command — otherwise
    the very first scene open of the Maya session wouldn't trigger it, only
    later manual File > Open calls would."""
    global _scene_open_callback_id
    if _scene_open_callback_id is not None:
        return
    _scene_open_callback_id = om.MSceneMessage.addCallback(
        om.MSceneMessage.kAfterOpen, _on_scene_opened
    )
