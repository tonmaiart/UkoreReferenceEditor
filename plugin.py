from __future__ import annotations

from pathlib import Path

TOOL_ID = "ukore_reference_editor"
TOOL_LABEL = "UkoreReferenceEditor"
# Convention-only string match with plugins/repo_internal/maya_launcher/plugin.py
# — both resolve to the same active Project's plugin_data via
# ProjectPluginConfigStore, no coupling API needed. See that plugin's README
# for the full "contributions"/"labels" shape this writes into.
MAYA_ENV_BRIDGE_PLUGIN_ID = "maya_launcher_env_bridge"
ANY_VERSION = "*"


def register(api) -> None:
    tool_root = Path(__file__).resolve().parent

    bridge = api.project_plugin_config_store(MAYA_ENV_BRIDGE_PLUGIN_ID)
    if bridge is None:
        return
    contributions = bridge.get("contributions", {})
    contributions[TOOL_ID] = {
        # api.app_root is contributed too so `import core.storage.metadata_store` /
        # `core.vcs.paths` / `core.extensibility.config_store` resolve inside
        # Maya's Python — repo_paths.py needs the real Project/Repo registry, same reason
        # plugins/repo_internal/PublishApi/plugin.py contributes api.app_root too.
        "PYTHONPATH": {ANY_VERSION: [str(tool_root / "maya-scripts"), str(api.app_root)]},
    }
    bridge.set("contributions", contributions)
    labels = bridge.get("labels", {})
    labels[TOOL_ID] = TOOL_LABEL
    bridge.set("labels", labels)
