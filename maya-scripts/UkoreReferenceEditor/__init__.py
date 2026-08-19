import maya.cmds as cmds

try:
    from UkoreMenu import registry, MenuItemSpec, ReloadHandlerSpec, reload_package

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
except ImportError:
    pass