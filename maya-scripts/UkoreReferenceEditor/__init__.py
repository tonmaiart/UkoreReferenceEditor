import maya.cmds as cmds

from UkoreReferenceEditor.core import register_scene_open_callback as _register_reference_scene_open_callback
from UkoreReferenceEditor.picker import register_scene_open_callback as _register_picker_scene_open_callback

# Registered at import time (mirrors the old standalone dw_publish_picker
# plugin's own __init__.py) — must already be in place before the very
# first scene open of the session, so this can't wait for register_menu()
# below, which needs UkoreMenu itself to be ready (see plugin.py's
# pre_open_mel hook, which triggers this via a bare `import
# UkoreReferenceEditor` before UkoreMenu is guaranteed available).
# core's own callback (added 2026-08-20) makes the reference/texture/audio
# auto-fix work on a native Maya File > Open too, not just when opened via
# UkoreHub's maya_launcher — see core.py's register_scene_open_callback
# docstring for why that was previously silently broken.
_register_reference_scene_open_callback()
_register_picker_scene_open_callback()


def register_menu() -> None:
    """Registers this plugin's own menu item + reload handler into
    UkoreMenu — called explicitly from plugin.py's post_open_mel hook
    (not at import time), since UkoreMenu isn't guaranteed ready yet when
    pre_open_mel's own early `import UkoreReferenceEditor` runs above."""
    try:
        from UkoreMenu import registry, MenuItemSpec, ReloadHandlerSpec, reload_package
    except ImportError:
        return

    registry.register_item(
        MenuItemSpec(
            id="ukore_reference_editor",
            label="Ukore Reference Editor...",
            category="General",
            command="from tmlib.core import File; File.launch('UkoreReferenceEditor')",
            order=20,  # 👈 กำหนดเป็น 20 เพื่อให้อยู่ต่อจาก Maya File Browser (ซึ่งตั้งไว้ order=10)
        )
    )
    registry.register_reload_handler(
        ReloadHandlerSpec(
            id="ukore_reference_editor",
            label="Ukore Reference Editor",
            callback=lambda: reload_package("UkoreReferenceEditor"),
            order=21,
        )
    )