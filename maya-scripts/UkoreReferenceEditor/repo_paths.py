"""UkoreHub Project/Repo lookups for Ukore Reference Editor, Maya-side.

Constructs UkoreHub's own stores straight off disk, same approach
plugins/repo_internal/PublishApi/maya-scripts/PublishApi/repo_paths.py uses (Maya's
Python has no PluginAPI instance to go through). Everything about the
*active* repo's pipeline connections (Connect Input Path) is reused directly
from PublishApi rather than reimplemented here — PublishApi is shared
infrastructure other tools import by convention (see that plugin's README),
and it's always on PYTHONPATH regardless of any repo's per-tool toggle, so
this import is always safe."""

from __future__ import annotations

from pathlib import Path

from PublishApi import repo_paths as publish_api_paths

get_active_repo = publish_api_paths.get_active_repo
get_pipeline_refs = publish_api_paths.get_pipeline_refs
resolve_ref = publish_api_paths.resolve_ref
get_custom_path = publish_api_paths.get_custom_path


def find_ukorehub_root() -> Path:
    """This file lives at plugins/repo_internal/UkoreReferenceEditor/maya-scripts/
    UkoreReferenceEditor/repo_paths.py — five parents up is the UkoreHub
    repo root, same depth as PublishApi's own repo_paths.py."""
    return Path(__file__).resolve().parents[5]


def list_all_projects() -> list:
    """Every Project UkoreHub currently knows about (each with its own
    .repos list already embedded — core/models.py's Project), for scanning
    a broken reference path's segments against every project's name, not
    just the active one."""
    root = find_ukorehub_root()
    from core.storage.metadata_store import MetadataStore

    store = MetadataStore(root / "data" / "projects.json")
    return store.list_projects()


def workspace_root() -> str | None:
    """The configured workspace root a cloned repo's Repo.local_path is
    relative to, or None if it hasn't been set up on this machine."""
    root = find_ukorehub_root()
    from core.storage.config_store import LocalConfigStore

    local_config = LocalConfigStore(root / "cache" / "local_config.json")
    return local_config.workspace_root
