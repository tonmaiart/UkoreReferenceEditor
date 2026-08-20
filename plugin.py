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

    # เพิ่มส่วนนี้เข้าไปใน UkoreReferenceEditor/plugin.py
    hooks = bridge.get("launch_hooks", {})
    hooks[TOOL_ID] = {
        "order": 10,  # ให้รัน import ก่อนตัว UkoreMenu (order: 99) จะสั่ง rebuild_menu
        # Registers the Dreamwall Picker kAfterOpen callback (picker.py's
        # register_scene_open_callback, called from __init__.py's own
        # module-level code) before MayaLauncher's own `file -open`, so the
        # very first scene open of the session triggers it too — mirrors
        # the old standalone dw_publish_picker plugin's own pre_open_mel.
        "pre_open_mel": 'python("try:\\n    import UkoreReferenceEditor\\nexcept ImportError:\\n    pass");',
        "post_open_mel": (
            'python("try:\\n    import UkoreReferenceEditor; '
            'UkoreReferenceEditor.register_menu()\\nexcept ImportError:\\n    pass");'
        ),
    }
    bridge.set("launch_hooks", hooks)
