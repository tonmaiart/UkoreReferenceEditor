"""Reference/texture scanning and redirect orchestration — the Maya-`cmds`
layer. `interface.py` (manual UI) and `maya-plug-ins/ukoreMaya.py`'s
automatic scene-open callback both call into this module rather than
duplicating the scan/redirect logic.

Two independent kinds of broken path get the exact same
matcher.find_match_for_path/resolve_redirect algorithm applied: Maya file
references (`scan_references`/`RefEntry`) and file-texture nodes' own
fileTextureName attribute (`scan_textures`/`TextureEntry`) — the only real
difference between them is *how* a fix gets applied (`redirect_reference`'s
`loadReference` vs. `redirect_texture`'s `setAttr`), so `_classify_path`
holds the one shared scope/suggested-path algorithm both entry builders
call, and `_sort_for_auto_fix`/`_confirm_redirect` hold the one shared
auto-redirect/confirm-dialog policy `auto_check_and_redirect` applies to
both lists.

`RefEntry` also carries a `status` or `"outdated"` on top of `exists` —
whether a newer published version (a sibling vNNN folder) exists for a
reference that itself still resolves fine. This absorbs
`plugins/repo_internal/MayaToolkit`'s old "Update All Reference and Picker" menu
action (`UkoreMaya.core.function.update_references`, itself built on
`UkoreMaya.core.utils.get_latest_version_in_folder_based`/`Logic.py`) —
reused directly rather than reimplemented, same cross-plugin convention
`PublishApi` already uses for importing `UkoreMaya`/`tmlib`
by name. Unlike a missing/broken reference, an outdated one is never
auto-updated on scene open — only via the artist's own "Update Version"
button in the UI, matching the old menu action's own manual,
confirm-before-updating behavior."""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from pathlib import Path

import maya.cmds as cmds

from UkoreReferenceEditor import matcher, repo_paths

_LOG_PREFIX = "[UkoreReferenceEditor]"
_DIALOG_TITLE = "Ukore Reference Editor"


@dataclass
class RefEntry:
    ref_path: str
    ref_node: str | None
    exists: bool
    scope: str  # "internal" | "external" | "unmatched"
    status: str  # "missing" | "outdated" | "ok"
    is_loaded: bool = False
    version: str | None = None
    next_version: str | None = None
    latest_version_path: Path | None = None
    matched_project: object | None = None
    matched_repo: object | None = None
    suggested_path: Path | None = None


@dataclass
class TextureEntry:
    file_path: str
    node_name: str
    attr_name: str
    exists: bool
    scope: str  # "internal" | "external" | "unmatched"
    status: str  # "missing" | "ok" — textures have no version-check concept
    matched_project: object | None = None
    matched_repo: object | None = None
    suggested_path: Path | None = None


def _normalized(raw_path: str) -> str:
    # Same split/normpath UkoreMaya.core.function.update_references() already
    # uses to strip Maya's "{copyNumber}" suffix off a reference path before
    # treating it as a real filesystem path — harmless no-op for a texture
    # path, which never has that suffix.
    return os.path.normpath(raw_path.split("{")[0])


def _classify_path(
    clean_path: str,
    exists: bool,
    active_repo,
    projects: list,
    root_ws: str | None,
    is_sequence: bool = False,
) -> tuple:
    """The one matcher.find_match_for_path/resolve_redirect algorithm both
    scan_references and scan_textures apply — returns (scope,
    matched_project, matched_repo, suggested_path)."""
    matched_project = None
    matched_repo = None
    match_index = None
    scope = "unmatched"
    match = matcher.find_match_for_path(clean_path, projects)
    if match is not None:
        matched_project, matched_repo, match_index = match
        # Repo identity, not Project identity: a repo-level match is
        # "internal" only when it's the exact repo currently active in
        # Maya. A project-level fallback match (matched_repo is None) can
        # never be internal — there's no specific repo to confirm identity
        # against, so it's treated as external (safe default: prompts
        # rather than silently redirecting). See developer/GLOSSARY.md's "Ukore
        # Reference Editor: 'project' means Repo, not Project" entry.
        is_same_repo = matched_repo is not None and active_repo is not None and matched_repo.id == active_repo.id
        scope = "internal" if is_same_repo else "external"

    suggested_path = None
    if not exists and matched_project is not None and root_ws:
        suggested_path = matcher.resolve_redirect(
            clean_path, matched_project, matched_repo, match_index, root_ws, is_sequence=is_sequence
        )

    return scope, matched_project, matched_repo, suggested_path


def _check_outdated(clean_path: str) -> Path | None:
    """The latest published version's file for `clean_path`, or None if
    there isn't a newer one (or the check doesn't apply at all). Reuses
    `UkoreMaya.core.utils.get_latest_version_in_folder_based` directly —
    the exact function the old "Update All Reference and Picker" menu
    action was built on (see `UkoreMaya/core/Logic.py`'s
    `get_latest_version_in_folder_based`, which requires the path to
    already exist and to sit under a "publish" folder with vXXX version
    subfolders — anything else, including any error, just means "not
    applicable", not "outdated"). Only ever called for a reference that
    already `exists`, matching that function's own precondition."""
    try:
        from UkoreMaya.core import utils as ukoremaya_utils
    except ImportError:
        return None
    try:
        latest = ukoremaya_utils.get_latest_version_in_folder_based(clean_path)
    except Exception:
        return None
    if not latest:
        return None
    latest_normalized = os.path.normpath(latest)
    if latest_normalized == clean_path:
        return None
    return Path(latest_normalized)


def scan_references() -> list[RefEntry]:
    """Every reference in the currently open scene, classified against
    UkoreHub's Project/Repo registry: "internal" (belongs to the same repo
    currently active/open in Maya), "external" (belongs to a different
    repo — even one sharing the active repo's own UkoreHub Project umbrella,
    e.g. two repos both under "MerderaProject" are still external to each
    other), or "unmatched" (no known repo/project name found in the path at
    all). Deliberately repo-level, not Project-level: the studio's old
    colloquial "project" (e.g. "KafkaProj") maps onto a UkoreHub *Repo*, not
    a UkoreHub *Project* — see developer/GLOSSARY.md's "Ukore Reference Editor:
    'project' means Repo, not Project" entry.

    Also carries `status` ("missing"/"outdated"/"ok"), `version` (the vXXX
    segment parsed from the path, if any), and — when outdated —
    `latest_version_path` (see `_check_outdated`)."""
    raw_refs = cmds.file(q=True, r=True) or []
    print(f"{_LOG_PREFIX} scan_references: cmds.file(q=True, r=True) -> {raw_refs}")

    active_project, active_repo, _active_repo_path = repo_paths.get_active_repo()
    active_project_name = active_project.name if active_project else None
    active_repo_name = active_repo.name if active_repo else None
    print(
        f"{_LOG_PREFIX} scan_references: active_project={active_project_name!r} "
        f"active_repo={active_repo_name!r}"
    )

    projects = repo_paths.list_all_projects()
    print(f"{_LOG_PREFIX} scan_references: known projects = {[p.name for p in projects]}")

    root_ws = repo_paths.workspace_root()
    print(f"{_LOG_PREFIX} scan_references: workspace_root={root_ws!r}")

    entries: list[RefEntry] = []
    for raw_ref in raw_refs:
        try:
            entries.append(_build_ref_entry(raw_ref, active_repo, projects, root_ws))
        except Exception:
            print(f"{_LOG_PREFIX} scan_references: failed to process reference {raw_ref!r}, skipping it:")
            traceback.print_exc()

    return entries


def _build_ref_entry(raw_ref: str, active_repo, projects: list, root_ws: str | None) -> RefEntry:
    clean_path = _normalized(raw_ref)
    exists = os.path.exists(clean_path)
    ref_node = cmds.file(clean_path, q=True, rfn=True) if clean_path else None
    loaded = is_reference_loaded(ref_node)

    scope, matched_project, matched_repo, suggested_path = _classify_path(
        clean_path, exists, active_repo, projects, root_ws
    )

    version = matcher.extract_version(clean_path) if clean_path else None
    latest_version_path = _check_outdated(clean_path) if exists else None
    next_version = matcher.extract_version(str(latest_version_path)) if latest_version_path else None

    if not exists:
        status = "missing"
    elif latest_version_path is not None:
        status = "outdated"
    else:
        status = "ok"

    matched_repo_name = matched_repo.name if matched_repo else None
    print(
        f"{_LOG_PREFIX} scan_references:   {clean_path!r} status={status} loaded={loaded} "
        f"version={version!r} next={next_version!r} node={ref_node} repo={matched_repo_name!r} "
        f"scope={scope} suggested={suggested_path}"
    )

    return RefEntry(
        ref_path=clean_path,
        ref_node=ref_node,
        exists=exists,
        scope=scope,
        status=status,
        is_loaded=loaded,
        version=version,
        next_version=next_version,
        latest_version_path=latest_version_path,
        matched_project=matched_project,
        matched_repo=matched_repo,
        suggested_path=suggested_path,
    )


_TEXTURE_NODE_TYPE = "file"
_TEXTURE_ATTR = "fileTextureName"


def scan_textures() -> list[TextureEntry]:
    """Every file-texture node's fileTextureName in the scene, classified
    with the exact same matcher.find_match_for_path/resolve_redirect
    algorithm scan_references uses — a texture path just doesn't have a
    reference node to load through, only a node.attribute to setAttr
    (redirect_texture), and has no version-check concept (no "outdated"
    status). A path containing a UDIM/frame token (e.g.
    "texture.<UDIM>.exr") is matched/resolved by its containing folder
    instead of an exact filename — see matcher.has_sequence_token /
    resolve_redirect's is_sequence branch."""
    node_names = cmds.ls(type=_TEXTURE_NODE_TYPE) or []
    print(f"{_LOG_PREFIX} scan_textures: found {len(node_names)} file texture node(s): {node_names}")

    active_project, active_repo, _active_repo_path = repo_paths.get_active_repo()
    projects = repo_paths.list_all_projects()
    root_ws = repo_paths.workspace_root()

    entries: list[TextureEntry] = []
    for node_name in node_names:
        try:
            entries.append(_build_texture_entry(node_name, active_repo, projects, root_ws))
        except Exception:
            print(f"{_LOG_PREFIX} scan_textures: failed to process {node_name!r}, skipping it:")
            traceback.print_exc()

    return entries


def _build_texture_entry(node_name: str, active_repo, projects: list, root_ws: str | None) -> TextureEntry:
    attr = f"{node_name}.{_TEXTURE_ATTR}"
    raw_path = cmds.getAttr(attr) or ""
    clean_path = _normalized(raw_path) if raw_path else ""
    is_sequence = bool(clean_path) and matcher.has_sequence_token(clean_path)
    if not clean_path:
        exists = False
    elif is_sequence:
        exists = Path(clean_path).parent.is_dir()
    else:
        exists = os.path.exists(clean_path)

    scope, matched_project, matched_repo, suggested_path = _classify_path(
        clean_path, exists, active_repo, projects, root_ws, is_sequence=is_sequence
    )
    status = "ok" if exists else "missing"

    matched_repo_name = matched_repo.name if matched_repo else None
    print(
        f"{_LOG_PREFIX} scan_textures:   {node_name} -> {clean_path!r} status={status} "
        f"sequence={is_sequence} repo={matched_repo_name!r} scope={scope} suggested={suggested_path}"
    )

    return TextureEntry(
        file_path=clean_path,
        node_name=node_name,
        attr_name=_TEXTURE_ATTR,
        exists=exists,
        scope=scope,
        status=status,
        matched_project=matched_project,
        matched_repo=matched_repo,
        suggested_path=suggested_path,
    )


def is_reference_loaded(ref_node: str | None) -> bool:
    """Whether `ref_node` is currently loaded — backs the Maya File tab's
    leading "Loaded" checkbox column. `cmds.referenceQuery(isLoaded=True)`
    is Maya's own way to ask this; a reference with no node at all (e.g. a
    scan failure) is reported as not loaded."""
    if not ref_node:
        return False
    try:
        return bool(cmds.referenceQuery(ref_node, isLoaded=True))
    except Exception:
        return False


def set_reference_loaded(ref_node: str | None, loaded: bool) -> bool:
    """Loads or unloads a reference node in place — no path change, unlike
    redirect_reference. Backs both the per-row "Loaded" checkbox and the
    "Load All References"/"Unload All References" toolbar buttons."""
    if not ref_node:
        return False
    try:
        if loaded:
            cmds.file(loadReference=ref_node)
        else:
            cmds.file(unloadReference=ref_node)
        return True
    except Exception as exc:  # noqa: BLE001 - one bad load/unload must not break a batch action
        action = "load" if loaded else "unload"
        cmds.warning(f"Failed to {action} reference {ref_node}: {exc}")
        return False


def redirect_reference(ref_node: str | None, new_path: Path) -> bool:
    """Loads `new_path` into an already-referenced node — same
    `loadReference` call shape UkoreMaya.core.function.update_references()
    already uses for the same purpose (swapping a reference to a new file
    without re-creating the reference node/namespace)."""
    if not ref_node:
        return False
    try:
        cmds.file(str(new_path), loadReference=ref_node, options="v=0;", ignoreVersion=True)
        return True
    except Exception as exc:  # noqa: BLE001 - a bad redirect must not break the rest of the batch
        cmds.warning(f"Failed to redirect reference: {ref_node} -> {new_path}\n{exc}")
        return False


def update_reference_version(ref_node: str | None, latest_version_path: Path) -> bool:
    """The "Update Version" button's action — redirects to the newer
    published version, then refreshes the Dreamwall picker the same way the
    old "Update All Reference and Picker" menu action did
    (`UkoreMaya.core.function.update_references()` called
    `import_all_picker()` after any successful update; this editor absorbed
    that menu action, see `maya-plug-ins/ukoreMaya.py`). Picker refresh is
    best-effort — a failure there shouldn't be reported as the version
    update itself having failed."""
    ok = redirect_reference(ref_node, latest_version_path)
    if ok:
        try:
            from UkoreMaya.core import function as ukoremaya_function

            ukoremaya_function.import_all_picker()
        except Exception:
            print(f"{_LOG_PREFIX} update_reference_version: picker refresh failed:")
            traceback.print_exc()
    return ok


def redirect_texture(node_name: str, attr_name: str, new_path: Path) -> bool:
    """setAttr's a file-texture node's path attribute to the resolved
    location — no reference node/load step involved, just the string value
    a texture lookup reads at render/viewport-refresh time."""
    attr = f"{node_name}.{attr_name}"
    try:
        cmds.setAttr(attr, str(new_path), type="string")
        return True
    except Exception as exc:  # noqa: BLE001 - a bad redirect must not break the rest of the batch
        cmds.warning(f"Failed to redirect texture: {attr} -> {new_path}\n{exc}")
        return False


def _connect_input_targets() -> list[Path]:
    """Every filesystem folder the active repo has declared reaching into
    via Project Editor's "Connect Input Path" — an external redirect (of a
    reference or a texture) is only auto-applied when its suggested path
    falls inside one of these, otherwise it needs a Redirect Now/Skip
    confirmation. `custom_path["path"]` is stripped of a leading separator
    before joining — raw user input, and an untouched leading "/" silently
    resets a `WindowsPath` join to the current drive root instead of
    extending it (same bug class as
    `developer/bug-history/2026-07-20-playblast-custom-path-leading-slash.md` /
    `developer/bug-history/2026-08-03-publishapi-custom-path-leading-slash.md`, which
    flagged this exact line as a not-yet-fixed instance)."""
    targets: list[Path] = []
    for ref in repo_paths.get_pipeline_refs():
        resolved = repo_paths.resolve_ref(ref)
        if resolved is None:
            continue
        _project, _repo, repo_path = resolved
        custom_path = repo_paths.get_custom_path(ref["project_id"], ref["repo_id"], ref.get("custom_path_id"))
        if custom_path is None:
            continue
        targets.append(repo_path / custom_path["path"].lstrip("/\\"))
    return targets


def _sort_for_auto_fix(entries: list, connect_input_targets_getter) -> tuple[list, list]:
    """Splits already-classified entries (RefEntry or TextureEntry — both
    carry the same scope/suggested_path fields) into (auto_redirected,
    needs_confirmation), shared by auto_check_and_redirect's reference and
    texture passes. Only ever called with missing (not outdated) entries —
    an outdated reference is never auto-updated, see module docstring."""
    auto_redirected = []
    needs_confirmation = []
    for entry in entries:
        if entry.suggested_path is None:
            continue
        if entry.scope == "internal":
            auto_redirected.append(entry)
        elif entry.scope == "external":
            if any(matcher.is_within(entry.suggested_path, target) for target in connect_input_targets_getter()):
                auto_redirected.append(entry)
            else:
                needs_confirmation.append(entry)
    return auto_redirected, needs_confirmation


def auto_fix_entries(entries: list, redirect_fn) -> int:
    """Applies the internal/external-covered-by-Connect-Input-Path portion
    of auto_check_and_redirect's own policy to an already-scanned entry
    list (RefEntry or TextureEntry) — shared by that automatic kAfterOpen
    flow and interface.py's manual Rescan button, which finds and applies a
    redirect immediately for anything it safely can, instead of leaving a
    suggestion for the artist to click a Redirect button for. Deliberately
    does **not** show a Redirect Now/Skip confirmDialog for anything else
    (external, not covered by Connect Input Path) the way
    auto_check_and_redirect does — the manual UI already has a Redirect
    button per row for the artist to decide through, so a modal popup per
    uncovered entry on every Rescan click would be spammy rather than
    helpful; those rows are simply left as "missing" with their suggestion
    intact. `redirect_fn(entry, new_path) -> bool` is the caller's own
    reference/texture-specific redirect call. Returns how many were
    actually auto-redirected."""
    missing = [e for e in entries if getattr(e, "status", None) == "missing" and e.suggested_path is not None]
    if not missing:
        return 0

    connect_input_targets: list[Path] | None = None

    def get_connect_input_targets() -> list[Path]:
        nonlocal connect_input_targets
        if connect_input_targets is None:
            connect_input_targets = _connect_input_targets()
        return connect_input_targets

    auto_redirected, _needs_confirmation = _sort_for_auto_fix(missing, get_connect_input_targets)

    fixed = 0
    for entry in auto_redirected:
        if redirect_fn(entry, entry.suggested_path):
            fixed += 1
    return fixed


def _confirm_redirect(display_path: str, suggested_path: Path) -> bool:
    result = cmds.confirmDialog(
        title=_DIALOG_TITLE,
        message=(
            "File points at another project's old location:\n\n"
            f"{display_path}\n\nSuggested new location:\n{suggested_path}\n\n"
            "Redirect now?"
        ),
        button=["Redirect Now", "Skip"],
        defaultButton="Redirect Now",
        cancelButton="Skip",
        dismissString="Skip",
    )
    return result == "Redirect Now"


def auto_check_and_redirect() -> bool:
    """The automatic entry point, called once per scene open (see
    maya-plug-ins/ukoreMaya.py's kAfterOpen callback). Runs both
    scan_references and scan_textures through the same policy: internal
    matches and external matches already covered by one of the active
    repo's own Connect Input Path connections redirect immediately with a
    notification; anything else external gets a Redirect Now/Skip prompt,
    same UX shape as UkoreMaya.core.function.update_references()'s own
    confirmDialog. Only ever acts on *missing* references/textures —
    "outdated" is UI-only, manual (Update Version button), never automatic.

    Also (re)loads every *reference* that's already fine as-is (textures
    need no such step — a texture attribute has no separate load state).
    That's a no-op when the scene opened normally (manual File > Open —
    every valid reference is already loaded by the time this runs). It's
    load-bearing when the scene opened via maya_launcher's
    `-loadReferenceDepth "none" -prompt false` (see that plugin's
    `open_maya_file`/`_set_project_and_open_command`), which leaves *every*
    reference unloaded regardless of whether its file resolves —
    specifically so Maya's own native "could not find file" dialog never
    gets a chance to fire before this function has a chance to redirect the
    broken ones first. Without this step, a scene opened that way would
    come up with even its perfectly valid references sitting unloaded
    forever.

    Returns whether the caller should pop the full Ukore Reference Editor
    UI open afterward — True when the scene has any reference at all
    (regardless of status; an artist opening a referenced scene benefits
    from seeing the Maya File tab even if nothing is broken) or still has a
    missing texture once every safe/confirmed redirect above has already
    been applied. `ukoreMaya.py`'s kAfterOpen callback is the only caller
    and opens the UI on a True return via the same
    `menu_utils.ukore_reference_editor()` the menu item itself uses."""
    ref_entries = scan_references()
    texture_entries = scan_textures()

    connect_input_targets: list[Path] | None = None

    def get_connect_input_targets() -> list[Path]:
        nonlocal connect_input_targets
        if connect_input_targets is None:
            connect_input_targets = _connect_input_targets()
        return connect_input_targets

    loaded_as_is = 0
    for entry in ref_entries:
        if entry.exists and redirect_reference(entry.ref_node, Path(entry.ref_path)):
            loaded_as_is += 1
    if loaded_as_is:
        print(f"{_LOG_PREFIX} auto_check_and_redirect: loaded {loaded_as_is} reference(s) as-is.")

    missing_refs = [e for e in ref_entries if e.status == "missing"]
    missing_textures = [e for e in texture_entries if e.status == "missing"]

    ref_auto, ref_confirm = _sort_for_auto_fix(missing_refs, get_connect_input_targets)
    texture_auto, texture_confirm = _sort_for_auto_fix(missing_textures, get_connect_input_targets)

    texture_fixed = 0
    for entry in ref_auto:
        redirect_reference(entry.ref_node, entry.suggested_path)
    for entry in texture_auto:
        if redirect_texture(entry.node_name, entry.attr_name, entry.suggested_path):
            texture_fixed += 1

    total_auto = len(ref_auto) + len(texture_auto)
    if total_auto:
        cmds.inViewMessage(
            amg=f"<hl>{_DIALOG_TITLE}</hl> auto-redirected {total_auto} file(s).",
            pos="midCenter",
            fade=True,
        )
        lines = [f"- {e.ref_path} -> {e.suggested_path}" for e in ref_auto]
        lines += [f"- {e.file_path} -> {e.suggested_path}" for e in texture_auto]
        print(f"# {_DIALOG_TITLE} auto-redirected:\n" + "\n".join(lines))

    for entry in ref_confirm:
        if _confirm_redirect(entry.ref_path, entry.suggested_path):
            redirect_reference(entry.ref_node, entry.suggested_path)
    for entry in texture_confirm:
        if _confirm_redirect(entry.file_path, entry.suggested_path):
            if redirect_texture(entry.node_name, entry.attr_name, entry.suggested_path):
                texture_fixed += 1

    still_missing_textures = len(missing_textures) - texture_fixed
    return bool(ref_entries) or still_missing_textures > 0
