# plugins/repo_internal/UkoreReferenceEditor/

Maya-side tool (originally built as `ReferenceRedirector`, renamed
2026-08-03) that fixes broken/outdated file paths in a Maya scene — both
Maya **file references** and **file-texture nodes'** own `fileTextureName`
paths — via a multi-tab table UI ("Maya File", "Textures", "Audio",
"Dreamwall Picker"). Three independent problems it handles:

1. **Redirecting** references/textures built around the studio's old
   absolute Google-Drive path convention (e.g.
   `G:\My Drive\Projects\KafkaProj\publish\Character\Kafka\Model\Proxy\v021\v021.ma`)
   onto UkoreHub's current per-repo layout, via the identical
   matching/redirect algorithm applied to both kinds of path (see "How a
   redirect is resolved" below; the only real difference is *how* a fix
   gets applied — `loadReference` for a reference, `setAttr` for a
   texture).
2. **Version-checking** (Maya File tab only) — flags a reference whose file
   still resolves fine but has a newer published version (a sibling `vNNN`
   folder) sitting next to it, absorbing `plugins/repo_internal/MayaToolkit`'s old
   "Update All Reference and Picker" menu action (see "Version checking"
   below).
3. **Dreamwall Picker loading** (`maya-scripts/UkoreReferenceEditor/picker.py`,
   merged in 2026-08-20 from the standalone `dw_publish_picker` plugin) —
   auto-detects character namespaces in the scene and loads their published
   Dreamwall Pickers from the active repo's `DreamwallPicker` Custom Path,
   fixing any missing picker-image source paths right after. Unlike the
   first two problems, this one has its own path-resolution algorithm
   (Custom-Path-driven, not `matcher.find_match_for_path`/`resolve_redirect`)
   — kept unchanged from the standalone plugin, see "Dreamwall Picker" below.

Like every other Maya tool plugin here, it has no UI of its own inside
UkoreHub and does not launch Maya — `plugin.py`'s `register(api)` just
contributes `PYTHONPATH` (its own `maya-scripts/`, plus `api.app_root` so
`core.store`/`core.paths` resolve inside Maya's Python) into
`plugins/repo_internal/maya_launcher/`'s shared `maya_launcher_env_bridge`
`PluginConfigStore` — see that plugin's README for the full bridge shape.
This also means it can be turned off per-repo via Repository Setting >
Enable Plugin (`Repo.required_plugin_ids`) like any other tool, with zero
extra code here. One exception: the Dreamwall Picker feature registers its
own independent `kAfterOpen` scene-open callback directly (`picker.py`'s
`register_scene_open_callback`, triggered via `plugin.py`'s own
`pre_open_mel` hook — same mechanism the standalone `dw_publish_picker`
plugin used), rather than going through `MayaToolkit`'s shared dispatcher
the way the reference/texture/audio auto-fix below does.

The actual UI entry point ("Ukore Reference Editor...") and the automatic
scene-open check both live in
`plugins/repo_internal/MayaToolkit/maya-plug-ins/ukoreMaya.py` /
`UkoreMaya/core/menu_utils.py` (`ukore_reference_editor()`) — this plugin is
a pure library, the same relationship `PublishApi` has to `MayaPublisher`.

## How a redirect is resolved

1. `matcher.find_match_for_path` scans a path's segments **repo first**:
   against every known `Repo`'s current name *and* the last folder segment
   of its stored `local_path` (case-insensitive, both raw and
   `core.paths.sanitize_folder_name` form). This matters because
   `local_path` is never recomputed after a rename (see
   `developer/bug-history/2026-07-20-repo-path-resolved-from-stale-name.md`) — a repo
   renamed since the old Drive-path days still has its *original* name
   baked into its on-disk folder, which is what an old absolute path
   segment actually matches, not the repo's current name and not
   necessarily the owning Project's name either (a project's old Drive
   folder is routinely a segment or two above where a specific repo's
   content actually lives once that project is split across several
   repos). Only if no repo anywhere matches does it fall back to a
   **project-level** match (against `Project.name`) covering every repo in
   that project — for an old path from before a project had more than one
   repo.
2. **Internal**/**external** compares the matched **repo**, not the matched
   Project, against whichever repo is currently active/open in Maya —
   `matched_repo.id == active_repo.id`. Two repos sharing the same
   UkoreHub Project (e.g. `RigTeam` and `AnimatorTeam` both filed under
   "MerderaProject") are still **external** to each other; a project-level
   fallback match (no specific repo pinpointed) is always external too,
   since there's no concrete repo identity to confirm. See developer/GLOSSARY.md's
   "Ukore Reference Editor: 'project' means Repo, not Project" entry for
   why this has to be repo-level.
3. `matcher.resolve_redirect` looks for the file under the matched repo
   specifically (or, for a project-level match, every *cloned* repo in that
   project): first a direct re-root of the path segments that followed the
   matched segment, then (if that misses) a bounded recursive filename
   search — covers a repo whose internal layout no longer mirrors the old
   Drive structure below that point. A texture path containing a UDIM/frame
   token (`<UDIM>`, `<f>`, `#...`, see `matcher.has_sequence_token`) is
   matched/resolved by its **containing folder** instead — a tokenized
   filename like `texture.<UDIM>.exr` never literally exists on disk under
   that exact name, so `resolve_redirect`'s `is_sequence=True` branch
   re-roots the folder and keeps the token filename as-is
   (`core.py`'s `_build_texture_entry` decides this per node from the raw
   `fileTextureName` value).
4. **Internal** matches redirect automatically. **External** matches only
   redirect automatically when the resolved location falls inside one of
   the active repo's own Project Editor "Connect Input Path" connections
   (`core.py`'s `_connect_input_targets`, reusing `PublishApi.repo_paths`'s
   pipeline-connection lookups) — otherwise it's left **missing** with its
   suggestion intact, redirectable via the row's own Redirect button.
   **Unmatched** paths (no known repo or project name found at all) are
   listed but never auto-acted on. Only ever applied to **missing**
   entries, never outdated ones (see below).

**Clicking Rescan itself applies this** — `interface.py`'s
`_EntryTable.reload_table` calls `core.py`'s `auto_fix_entries` right after
every scan (both tabs), which silently redirects anything internal or
Connect-Input-Path-covered before the table is even drawn, then rescans
once more so what's on screen already reflects the fixed state — there's
no separate "apply suggestions" step to click through by hand anymore.
Unlike `auto_check_and_redirect` (the automatic `kAfterOpen` entry point),
`auto_fix_entries` never pops a Redirect Now/Skip confirmDialog for an
external match outside Connect Input Path — the manual UI already has a
Redirect button per row for that, and popping a modal per uncovered entry
on every Rescan click would be spammy rather than helpful.

## Version checking (Maya File tab only)

`core.py`'s `_check_outdated` reuses
`UkoreMaya.core.utils.get_latest_version_in_folder_based` directly (the
exact function the old "Update All Reference and Picker" menu action was
built on, see `UkoreMaya/core/Logic.py`) rather than reimplementing
version-folder scanning — the same cross-plugin-by-name-import convention
`PublishApi` already uses for `UkoreMaya`/`tmlib`. Only
attempted for a reference that already resolves (`exists=True)` — the
underlying function requires the path to exist and to sit under a
"publish" folder with `vNNN` version subfolders; anything else, including
any error, just means "not outdated", not "missing" (that's a separate,
`exists=False` state). A `RefEntry`'s three possible `status` values:

- `"missing"` — the file doesn't exist; icon `icons8-cancel-48.png`. Gets a
  Redirect action (see above) when a suggestion was found.
- `"outdated"` — the file exists, but a newer `vNNN` sibling was found;
  icon `icons8-update-48.png`. Purely a signal in the Status column — the
  action itself (see below) is the same **Update Version...** button every
  Maya File row gets, not something conditional on this status.
- `"ok"` — neither of the above; icon `icons8-check-mark-48.png`.

`TextureEntry` only ever has `"missing"`/`"ok"` — there's no version-check
concept for textures in this feature set.

The Maya File tab has two adjacent, purely informational columns:
**Version** — `matcher.extract_version`'s parse of the current path's own
`vNNN` segment — and **Next Version** — the same parse applied to
`RefEntry.latest_version_path` (i.e. what `_check_outdated` found), blank
whenever there isn't one. Neither is itself editable; the actual rollback/
update choice happens in the dialog below.

**Update Version... button** (Maya File tab, every row, regardless of
status): opens a dialog with a dropdown of *every* published version found
under the same `vNNN` convention (`matcher.list_available_versions`, newest
first, "(latest)" labeled) — not just the newest, so an artist can roll
back to an older version as easily as jumping to the latest. Confirming
calls `core.py`'s `update_reference_version`, which redirects to whichever
version was picked, then calls
`UkoreMaya.core.function.import_all_picker()` afterward — the same
Dreamwall-picker-refresh step the old "Update All Reference and Picker"
menu action did after any update, preserved here since this editor
absorbed that menu action entirely (see
`plugins/repo_internal/MayaToolkit/maya-plug-ins/ukoreMaya.py`; the old standalone
menu item and `menu_utils.update_references()` were removed the same day
this editor was built). Never automatic — only via this button, unlike a
missing reference's Redirect (which can auto-fire on scene open) — silently
swapping a working reference to a different version without the artist
choosing it would be a bad surprise.

## Loading/unloading references (Maya File tab only)

The Maya File tab's leftmost column is a **Loaded** checkbox per row,
reflecting (and controlling) whether that reference is currently loaded in
Maya — `core.py`'s `is_reference_loaded`/`set_reference_loaded`
(`cmds.referenceQuery(isLoaded=True)` / `cmds.file(loadReference=...)` /
`cmds.file(unloadReference=...)`). Toggling a checkbox acts immediately
(`interface.py`'s `_EntryTable._on_item_changed`, wired to the table's
`itemChanged` signal — repopulating the table during `reload_table` blocks
signals first, so redrawing the checkboxes on rescan doesn't itself trigger
load/unload calls) without a full table rescan, so it stays responsive for
a scene with many references. Two toolbar buttons — **Load All
References**/**Unload All References** — apply the same call to every row
at once, then do a full `reload_table()` so the checkboxes end up reflecting
Maya's real post-batch state rather than an assumed one. Textures have no
load-state concept, so the Textures tab has neither the checkbox column nor
these two buttons.

## Repath — manual override

Every row (both tabs) has two buttons, independent of status/scope —
`interface.py`'s `_EntryTable._repath_row(row, file_mode, caption)` backs
both:

- **Repath File...** — `cmds.fileDialog2(fileMode=1, ...)`, Maya's single-
  existing-file mode; the chosen file is used directly as the redirect
  target.
- **Repath Search...** — `cmds.fileDialog2(fileMode=3, ...)`, Maya's
  existing-directory-only mode (same as Find All Missing File's picker
  below); the chosen folder is searched recursively (`Path.rglob`,
  `matcher.resolve_manual_target`) for a file matching the current path's
  own filename, first match wins.

Both are the escape hatch for anything the automatic project/repo-matching
algorithm above gets wrong or can't resolve at all. Deliberately two
dedicated buttons rather than one `fileMode=2` ("pick a file OR a folder")
button — Maya's own docs describe `fileMode=2` as returning "the name of a
directory" even though files are displayed in it, which made picking an
exact file unreliable (see
`developer/bug-history/2026-08-05-repath-filemode2-native-dialog-directory-only.md`).

## Find All Missing File (Textures tab only)

A toolbar button, next to Rescan, for fixing every missing texture at once
against a single folder instead of Repath-ing each row by hand —
`interface.py`'s `_EntryTable._find_all_missing`: picks one folder
(`cmds.fileDialog2(fileMode=3, ...)`, existing-directory-only, unlike
Repath's file-or-folder `fileMode=2`), then runs every currently-`"missing"`
`TextureEntry` through `matcher.resolve_manual_target(current_path,
search_root)` — same recursive-filename-search logic Repath's own
folder branch uses, just applied to the whole missing list against one
folder instead of one row at a time. Never applies anything by itself:
`_show_find_all_report` shows a result dialog first (Found/Not Found per
file, each found one pre-checked, not-found ones un-checkable) and only
redirects the checked, found ones once the artist clicks **Confirm
Update** — Cancel (or closing the dialog) discards the whole batch. Not
offered on the Maya File tab — references already have per-row Update
Version/Redirect plus the auto-fix-on-Rescan behavior above; this button
exists specifically because textures have neither.

## Beating Maya's own native "could not find file" dialog

Redirecting broken references from `auto_check_and_redirect` only helps if
it runs *before* Maya's own reference-resolution machinery gets a chance to
notice a reference is missing and show its own native dialog for it — a
`kAfterOpen` callback alone fires too late for that, since by then Maya has
already tried (and failed) to load every reference at `file -open` time.
`plugins/repo_internal/maya_launcher/plugin.py`'s `open_maya_file` opens with
`-loadReferenceDepth "none" -prompt false` whenever this plugin is enabled
for the launching repo (`UKORE_REFERENCE_EDITOR_TOOL_ID` there,
convention-only match with `TOOL_ID` here) — every reference then comes in
unloaded regardless of whether its file resolves, so Maya never attempts to
load any of them itself. Note it's specifically `-prompt false` that stops
Maya's native dialog, not `-loadReferenceDepth` — that flag alone still
leaves Maya validating each reference's path and showing the dialog anyway;
see
`developer/bug-history/2026-08-03-reference-native-dialog-not-suppressed-by-loadreferencedepth.md`.
`core.py`'s `auto_check_and_redirect` (the same `kAfterOpen` callback as
always) then does the loading itself: redirects broken ones per the rules
above, and explicitly (re)loads every reference that's already fine as-is
via `set_reference_loaded(ref_node, True)` — deliberately **not**
`redirect_reference(ref_node, Path(ref_path))` even though the path is
unchanged: passing that unchanged path through `cmds.file(path,
loadReference=...)` takes Maya's repath-and-reload code path instead of the
plain "load" path (a no-op after a normal load, load-bearing after a
deferred one — see that function's own docstring).

That call-shape fix alone (2026-08-14, first pass) turned out **not** to be
enough on its own — the artist still had to hit Reload by hand once. The
actual root cause is timing, not call shape: `auto_check_and_redirect` runs
synchronously inside Maya's `kAfterOpen` `MSceneMessage` callback, and
*any* `cmds.file(loadReference=...)` issued from inside that callback's
still-mid-transaction C++ stack frame — `set_reference_loaded`'s plain load
included — can come back `isLoaded=True` without Maya having actually
instantiated the reference's nodes into the DAG/viewport yet. The exact
same call succeeds cleanly a moment later, once Maya has fully unwound the
open transaction and reached its own idle point. Every
`cmds.file(loadReference=...)` this function issues (the as-is reload and
both redirect loops) is therefore scheduled via `core.py`'s `_deferred_load`
(`cmds.evalDeferred(functools.partial(fn, *args))`) rather than called
inline (2026-08-14, second pass — see `_deferred_load`'s own docstring).
Texture redirects (`redirect_texture`, plain `setAttr`) stay synchronous —
`setAttr` on `fileTextureName` has no DAG-instantiation step to race, so it
was never part of this bug. This only covers scenes launched through
MayaLauncher — a manual File > Open later in the same session still goes
through Maya's own normal reference resolution and can still show Maya's
native dialog for anything broken; `auto_check_and_redirect` still cleans
it up right afterward either way.

## Auto-opening the editor UI on scene open

`auto_check_and_redirect` returns a `bool`: whether the scene has any
reference at all (regardless of status — an artist opening a referenced
scene benefits from seeing the Maya File tab even when nothing is broken)
or still has a missing texture once every safe/confirmed redirect above
has already run. `ukoreMaya.py`'s `_on_scene_opened` (the `kAfterOpen`
callback, see above) checks this return value and, when True, pops the
full editor window open via the same `menu_utils.ukore_reference_editor()`
(`File.launch("UkoreReferenceEditor")`) the "Ukore Reference Editor..."
menu item itself uses — so the artist sees the table immediately on scene
open instead of having to open it by hand. A scene with no references and
no missing textures at all opens with no popup, same as before this
behavior existed.

## Dreamwall Picker

`picker.py`'s `get_dreamwall_picker_dir()` resolves the active repo's
`DreamwallPicker` Custom Path (`PublishApi.repo_paths.get_custom_paths`,
matched by id/label containing "dreamwallpicker"/"dreamwall picker") to a
folder of `<character>/vNNN/Picker.json` publishes — this lookup, and
`get_available_picker_versions`/`resolve_picker_file`'s vNNN-folder
resolution under it, are carried over unchanged from the standalone
`dw_publish_picker` plugin this merge absorbed, not reimplemented against
`matcher`'s project/repo path-matching algorithm (a Dreamwall Picker isn't
a broken Maya reference/texture path — its location is config-driven via
the Custom Path, not recovered from a stale absolute path).

- **Auto Load Picker** (`pushButton_auto_load_picker`)
  — `picker.import_all_picker()`, the direct migration of the standalone
  plugin's old "Load DW Publish Pickers" UkoreMenu item (now a button here
  instead — see "Merged from dw_publish_picker" below). Scans every
  character folder under the Dreamwall Picker directory against the
  scene's namespaces/node names; for each match, resolves the picker file
  (prompting a version-choice dialog when more than one `vNNN` exists),
  opens it in `dwpicker`, then fixes its image paths in-memory and remaps
  its shapes' `action.targets` onto the matching namespace (see below).
  There used to be a separate "Auto Resolve" button
  (`pushButton_auto_resolve_picker_path`) that proactively rewrote every
  character's `Picker.json` on disk ahead of time — removed (2026-08-25)
  once image-path fixing moved in-memory, since Picker.json is meant to
  stay read-only and Auto Load Picker's own per-character fix already
  covers the same ground once a picker is actually opened.
- **Change Picker Path...** (`pushButton_change_picker_path`) — manual
  override for the selected row: `cmds.fileDialog2(fileMode=1, ...)` picks
  a specific `Picker.json` directly (skipping the version dialog), which
  `picker.load_picker_for_character` then opens the same way Auto Load
  Picker's per-character step does.
- **Unload** (`pushButton_unload_picker`) — Auto Load Picker's counterpart:
  `picker.unload_all_pickers()` calls `dwpicker.close()` (not just
  `_dwpicker.clear()`, which the per-character loop above uses to empty the
  tabs before reloading) — dwpicker's own documented "close properly" entry
  point, which also unregisters the picker's Maya callbacks. A no-op if no
  picker window is currently open.

**Resolving picker image source paths** — `_open_picker_for_character`, after
`dwpicker.open_picker_file` has already loaded the document into memory,
walks `picker.document.shapes` and for any shape whose `image.path` doesn't
exist on disk (env vars expanded first, e.g.
`$DWPICKER_PROJECT_DIRECTORY/...`) resolves it in two passes:

1. A same-named file found via a recursive search under that Picker.json's
   own version folder (`_find_image_in_dir`) — cheap, handles an image
   simply moved/renamed within the same publish.
2. Only if that misses: `_resolve_image_via_project_match` falls back to
   the exact same `matcher.find_match_for_path`/`resolve_redirect`
   project/repo path-matching algorithm "How a redirect is resolved" above
   describes for Maya references/textures — a picker image was routinely
   published under the studio's old absolute Google-Drive convention same
   as any other asset, so a `bg_v4.png` no longer sitting in its own
   version folder still gets found by matching the path's project/repo
   segment and re-rooting (or recursively searching) from there. `active_repo`/
   `projects`/`root_ws` come from `_repo_match_context()`, fetched once per
   action (mirrors `core.py`'s `scan_references`/`scan_textures` fetching
   this triple once per scan rather than per entry) — pass `projects=None`
   to skip this second pass entirely.

A resolved path is written to `shape.options["image.path"]` and applied via
`shape.synchronize_image()` — in memory only, for the current Maya session.
`Picker.json` on disk is never touched (previously this ran as
`fix_picker_image_paths`, which read the raw dict and `json.dump`'d the
fix straight back to the file — removed 2026-08-25 so a published
Picker.json stays 100% read-only). This runs automatically after every
picker open (Auto Load Picker's per-character loop and Change Picker
Path...'s manual override both funnel through `_open_picker_for_character`),
so `dwpicker`'s own blocking MissingImages dialog never fires for an image
that simply moved with a publish or still points at the old Drive
convention.

**DWPICKER_PROJECT_DIRECTORY** — `picker.py`'s
`_sync_dwpicker_project_directory_env` sets this env var (dwpicker's own
built-in convention, see the vendored `ukore_dreamwall_picker` plugin's
`dwpicker/path.py` — `format_path`/`expand_path`, for collapsing/expanding
an `image.path` relative to a portable project root) to the active repo's
root, every time `get_dreamwall_picker_dir`/`_repo_match_context` resolves
it — i.e. on every picker action, not just once. Deliberately **not** a
static `maya_launcher_env_bridge` contribution the way `PYTHONPATH` is
contributed — `register(api)` only runs once at app startup (see
`developer/app/docs/plugin-api.md`), so a value written there would freeze
at whatever repo happened to be active then and go stale the moment the
artist switches active repo without restarting UkoreHub. This helps any
`image.path` that was published using dwpicker's own "auto-collapse path
with environment variable" preference (`AUTO_COLLAPSE_IMG_PATH_FROM_ENV`)
— it does **not** help an `image.path` stored as a plain absolute path
that's genuinely missing on disk (`dwpicker`'s own MissingImages dialog
shows the fully-expanded path either way, so a still-broken image after
this can mean either the file is really gone, or it was published with a
different `DWPICKER_PROJECT_DIRECTORY` root than the repo root assumed
here — worth confirming against the raw, un-expanded `image.path` in the
actual Picker.json if Auto Load Picker still can't clear it).

The `tableWidget_dreamwall_picker` table has no separate "File Info" panel
(unlike the other three tabs) — just Status/Character/File columns, File
being `QHeaderView.Stretch` with the full path in its tooltip, same
filename-only-plus-tooltip convention the other tabs' File column uses.

**Merged from dw_publish_picker (2026-08-20)** — this feature used to be
its own standalone plugin (`dw_publish_picker`, contributing its own
`maya-scripts/DwPublishPicker/` to the bridge and registering a "Load DW
Publish Pickers" UkoreMenu item). It was folded into UkoreReferenceEditor
so both tools share one bridge contribution/PYTHONPATH entry instead of
two, and its menu item was replaced by the Auto Load Picker button above.
The automatic scene-open auto-load (`auto_import_all_picker`, silent
unless the scene actually has a matching character) was preserved as-is —
`picker.py`'s `register_scene_open_callback()` now runs from
`UkoreReferenceEditor/__init__.py`'s own module-level code (mirroring the
old plugin's `__init__.py`), triggered early via `plugin.py`'s
`pre_open_mel` hook rather than at menu-registration time, since it must
already be registered before the very first scene open of the session —
see `__init__.py`'s own comment for why this had to be split from
`register_menu()` (which still needs `UkoreMenu` itself ready, so it stays
on `post_open_mel` instead).

## Files

- `manifest.json` / `plugin.py` — bridge contribution, see above.
- `maya-scripts/UkoreReferenceEditor/repo_paths.py` — Maya-side, constructs
  `MetadataStore`/`LocalConfigStore` straight off disk (no `PluginAPI`
  inside Maya); reuses `PublishApi.repo_paths` directly for active-repo and
  pipeline-connection lookups rather than reimplementing them — always
  resolves a repo's on-disk folder as `workspace_root / repo.local_path`,
  **never** `core.paths.resolve_repo_path(...)` (see
  `developer/bug-history/2026-07-20-repo-path-resolved-from-stale-name.md` — that
  helper is only correct at repo-creation time).
- `maya-scripts/UkoreReferenceEditor/matcher.py` — pure path-matching
  logic, no `maya.cmds`/Qt imports. `find_match_for_path`/`resolve_redirect`
  are shared by both references and textures; `has_sequence_token` is
  texture-specific (UDIM/frame-token detection); `extract_version` parses
  the Version column's value; `list_available_versions` backs the Update
  Version dialog's dropdown (every `vNNN` found, newest first — mirrors
  `UkoreMaya.core.Logic.get_latest_version_in_folder_based`'s own
  folder-discovery logic, since that function only exposes the single
  latest one); `resolve_manual_target` backs the Repath button.
- `maya-scripts/UkoreReferenceEditor/core.py` — `RefEntry` (incl.
  `is_loaded`, `next_version`)/`TextureEntry`,
  `scan_references`/`scan_textures` (both funnel through the shared
  `_classify_path`), `_check_outdated` (version checking),
  `is_reference_loaded`/`set_reference_loaded` (load-state toggle),
  `redirect_reference`/`redirect_texture`/`update_reference_version`,
  `_deferred_load` (schedules a reference-loading call past the
  `kAfterOpen` callback's own stack frame via `cmds.evalDeferred` — see
  "Beating Maya's own native..." below for why), `auto_fix_entries` (the
  manual Rescan button's own silent-safe-only auto-redirect, shared logic
  with the paragraph below — synchronous, since Rescan runs from a normal
  button click, not from inside `kAfterOpen`, so it doesn't need
  `_deferred_load`), and `auto_check_and_redirect` (the automatic entry
  point
  `ukoreMaya.py`'s `kAfterOpen` callback calls, applying the shared
  `_sort_for_auto_fix`/`_confirm_redirect` policy to both lists — missing
  entries only, never outdated ones).
- `maya-scripts/UkoreReferenceEditor/picker.py` — Dreamwall Picker loading,
  merged in from the standalone `dw_publish_picker` plugin — see "Dreamwall
  Picker" above for the full breakdown (`get_dreamwall_picker_dir`,
  `PickerVersionDialog`, `resolve_picker_file`/`get_available_picker_versions`,
  `_open_picker_for_character` (in-memory image-path fix),
  `import_all_picker`/`load_picker_for_character`, `scan_pickers`,
  `register_scene_open_callback`).
- `maya-scripts/UkoreReferenceEditor/interface.py` — `MainWindow`, a
  `QTabWidget` with four tabs ("Maya File", "Textures", "Audio", "Dreamwall
  Picker"), each backed by its own class wired to the matching module-level
  functions: `_ReferenceTab`/`_TextureTab`/`_AudioTab` (`core.py`'s
  `scan_*`/`redirect_*`/`update_version_*` triple) and `_PickerTab`
  (`picker.py`'s `scan_pickers`/`import_all_picker`/
  `load_picker_for_character` — see "Dreamwall Picker" above). `File.launch(
  "UkoreReferenceEditor")` is the shared entry point for all four.
  Maya File tab columns: Loaded (checkbox, leftmost), Status (icon —
  `icons8-check-mark-48.png`/`icons8-cancel-48.png`/`icons8-update-48.png`,
  from this plugin's own `maya-scripts/UkoreReferenceEditor/icons/`), File
  (filename only — see "File Info panel" below for the full path), Version,
  Next Version, Scope, Actions (Redirect when still missing after auto-fix
  + always Update Version.../Repath File.../Repath Search...). Textures
  tab: same minus Loaded, Version, Next Version. Only File is
  `QHeaderView.Stretch` — the narrow columns (Loaded, Status, Version, Next
  Version, Scope, Actions) are `QHeaderView.ResizeToContents`, so an icon
  or "v021" doesn't eat width the File column actually needs. The table
  still always exactly fills the window width with no horizontal
  scrollbar, since File absorbs whatever room the content-sized columns
  don't use.

## File Info panel

Below each tab's table, a "File Info" `QGroupBox` shows the **currently
selected row's** full detail — Full Reference Path (the actual full path
the File column now only shows the filename for), Status, Scope, Matched
Repo, Version -> Next Version (Maya File tab only), and the reference
node/`node.attribute` identifier — `interface.py`'s `_EntryTable.
_update_file_info`, wired to the table's `itemSelectionChanged` signal, all
labels `TextSelectableByMouse`/`TextSelectableByKeyboard` so the full path
can be copied out directly. Cleared (`_clear_file_info`) whenever the
table repopulates (Rescan drops whatever selection existed before) or
nothing is selected. This is what makes shortening the File column down to
just a filename (see above) not lose anything — the full path is always
one click away instead of permanently eating table width.

## Working on this plugin

Read/edit only files under this folder for changes to the matching/redirect/
version-check logic itself. The menu item and the automatic scene-open hook
live in `MayaToolkit` (see above) — a genuine cross-plugin task, not a
reason to read the rest of `MayaToolkit` by default.
# UkoreReferenceEditor
