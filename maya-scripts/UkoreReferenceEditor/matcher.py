"""Pure, Maya-agnostic path-matching logic for Ukore Reference Editor.

No `maya.cmds`/Qt imports here on purpose — this is the part of the tool
that's actually worth reasoning about in isolation (and the only part that
could ever be sanity-checked outside Maya)."""

from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath

from core.vcs.paths import sanitize_folder_name

# Maya file-texture tokens (UDIM tiling, image sequences) and the classic
# printf-style "#" frame padding — a path containing one of these will never
# literally exist on disk under any single filename, so it needs
# directory-based matching instead of exact-file matching (see
# has_sequence_token/resolve_redirect's is_sequence branch below).
_SEQUENCE_TOKEN_PATTERN = re.compile(r"<UDIM>|<Udim>|<udim>|<f>|<F>|<Frame>|<frame>|<FrameNum>|#+")

_VERSION_SEGMENT_PATTERN = re.compile(r"^v(\d+)$", re.IGNORECASE)


def has_sequence_token(path_str: str) -> bool:
    return bool(_SEQUENCE_TOKEN_PATTERN.search(path_str))


def extract_version(path_str: str) -> str | None:
    """The vNNN path segment closest to the file itself, or None if none of
    its segments match — the studio's publish convention always nests a
    published file one level under its own vNNN folder (see
    `UkoreMaya/core/Logic.py`'s `create_publish_path`), so this is what
    populates the Maya File table's own Version column."""
    for part in reversed(PureWindowsPath(path_str).parts):
        if _VERSION_SEGMENT_PATTERN.match(part):
            return part
    return None


def list_available_versions(current_path: str) -> list[tuple[str, Path]]:
    """Every published version found under the same vNNN-folder convention
    `UkoreMaya.core.Logic.get_latest_version_in_folder_based` scans (a
    "publish" ancestor folder, then vNNN subfolders a few levels below it —
    mirrors that function's own folder-discovery logic, since it isn't
    exposed as a reusable piece), but returns **every** version found
    (newest first) instead of only the latest — backs the Update Version
    dialog's dropdown, so an artist can roll back to an older version, not
    just jump to the newest. Returns `[]` for anything that doesn't fit
    that convention at all (no "publish" segment, nothing found, any
    error) — same "not applicable" contract `core.py`'s `_check_outdated`
    already treats `UkoreMaya`'s own function's failures as."""
    try:
        normalized = os.path.normpath(current_path)
        ext = Path(normalized).suffix.lstrip(".") if not os.path.isdir(normalized) else ""

        posix_path = normalized.replace("\\", "/")
        if "." in posix_path.rsplit("/", 1)[-1]:
            posix_path = posix_path.rsplit("/", 1)[0]

        if not os.path.exists(posix_path):
            return []

        parts = posix_path.split("/")
        if "publish" not in parts:
            return []
        pub_index = parts.index("publish")
        if len(parts) <= pub_index + 2:
            return []

        version_folders: list[str] = []
        parent_folder = ""
        for count in (3, 4, 5):
            parent_folder = "/".join(parts[: pub_index + count])
            if not os.path.isdir(parent_folder):
                return []
            version_folders = [
                name
                for name in os.listdir(parent_folder)
                if os.path.isdir(os.path.join(parent_folder, name)) and re.match(r"v\d+$", name, re.IGNORECASE)
            ]
            if version_folders:
                break

        if not version_folders:
            return []

        version_folders.sort(key=lambda name: int(name[1:]), reverse=True)

        versions: list[tuple[str, Path]] = []
        for version_folder in version_folders:
            folder_path = os.path.join(parent_folder, version_folder)
            try:
                files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
            except OSError:
                continue
            preferred = f"{version_folder}."
            matched = [f for f in files if f.startswith(preferred) and f.endswith(ext)]
            if matched:
                versions.append((version_folder, Path(os.path.normpath(os.path.join(folder_path, matched[0])))))

        return versions
    except Exception:
        return []


def resolve_manual_target(current_path: str, chosen: Path) -> Path | None:
    """The "Repath..." button's own resolution — a user-picked file is used
    directly; a user-picked folder is searched recursively for a file
    matching `current_path`'s own filename (first match wins, same
    fallback strategy `resolve_redirect`'s recursive search already uses).
    None if `chosen` is neither an existing file nor an existing folder, or
    if a folder search finds nothing."""
    if chosen.is_file():
        return chosen
    if chosen.is_dir():
        filename = PureWindowsPath(current_path).name
        if not filename:
            return None
        return next(chosen.rglob(filename), None)
    return None


def _repo_name_candidates(repo) -> set[str]:
    """A repo's on-disk folder name (the last segment of its stored
    `local_path`) is what an old absolute path segment usually matches —
    `local_path` is never recomputed after a rename (see
    developer/bug-history/2026-07-20-repo-path-resolved-from-stale-name.md), so a repo
    renamed since the old Drive-path days still has its *original* name
    baked into that folder, even though `repo.name` itself has since
    changed. Also matches the repo's current name, for paths written after
    a rename."""
    candidates = {repo.name.lower(), sanitize_folder_name(repo.name).lower()}
    local_path_leaf = PureWindowsPath(repo.local_path).name
    if local_path_leaf:
        candidates.add(local_path_leaf.lower())
    return candidates


def _project_name_candidates(project) -> set[str]:
    return {project.name.lower(), sanitize_folder_name(project.name).lower()}


def find_match_for_path(path_str: str, projects: list) -> tuple | None:
    """Scans every path segment (left to right) for a case-insensitive match
    against UkoreHub's registry — returns (project, repo_or_None, index) for
    the first hit, or None.

    Checked in two passes: **repo-level first** (a repo's own name/on-disk
    folder name), since once a project's content is split across several
    repos, an old path segment almost always pins down one specific repo,
    not the whole project. Only if no repo anywhere matches does this fall
    back to a **project-level** match (repo=None, meaning "search every repo
    in this project") — covers an old path that used the project's own name
    directly, from before it had more than one repo."""
    parts = PureWindowsPath(path_str).parts

    for index, part in enumerate(parts):
        lowered = part.lower()
        for project in projects:
            for repo in project.repos:
                if lowered in _repo_name_candidates(repo):
                    return project, repo, index

    for index, part in enumerate(parts):
        lowered = part.lower()
        for project in projects:
            if lowered in _project_name_candidates(project):
                return project, None, index

    return None


def _cloned_repo_roots(repos: list, workspace_root: str) -> list[Path]:
    roots = []
    for repo in repos:
        repo_root = Path(workspace_root) / repo.local_path
        if repo_root.is_dir():
            roots.append(repo_root)
    return roots


def resolve_redirect(
    broken_path: str, project, repo, index: int, workspace_root: str, is_sequence: bool = False
) -> Path | None:
    """Where the broken file likely lives now, or None if nothing was found.
    `repo`/`index` come from `find_match_for_path` — `repo` narrows the
    search to that one repo when the match was repo-level; a project-level
    match (`repo=None`) searches every repo in `project` instead.

    `is_sequence=True` (a texture path containing a UDIM/frame token — see
    `has_sequence_token`) resolves the *containing folder* instead of an
    exact filename, since a tokenized filename like `texture.<UDIM>.exr`
    never literally exists on disk under that name — the token itself is
    kept as-is in the result, only the folder it sits in gets redirected.
    Otherwise (a normal reference/single-file texture) tries a direct
    re-root first (cheap, and correct whenever a repo's internal folder
    layout still mirrors the old Drive structure below the matched
    segment), then falls back to a bounded per-repo recursive filename
    search for repos whose layout has since diverged."""
    parts = PureWindowsPath(broken_path).parts
    tail = parts[index + 1 :]
    if not tail:
        return None

    candidate_repos = [repo] if repo is not None else list(project.repos)
    repo_roots = _cloned_repo_roots(candidate_repos, workspace_root)

    if is_sequence:
        folder_tail = tail[:-1]
        filename = tail[-1]
        if not folder_tail:
            return None
        folder_name = folder_tail[-1]

        for repo_root in repo_roots:
            candidate_dir = repo_root.joinpath(*folder_tail)
            if candidate_dir.is_dir():
                return candidate_dir / filename

        for repo_root in repo_roots:
            hit = next((p for p in repo_root.rglob(folder_name) if p.is_dir()), None)
            if hit is not None:
                return hit / filename

        return None

    filename = tail[-1]

    for repo_root in repo_roots:
        candidate = repo_root.joinpath(*tail)
        if candidate.is_file():
            return candidate

    for repo_root in repo_roots:
        hit = next(repo_root.rglob(filename), None)
        if hit is not None:
            return hit

    return None


def is_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False
