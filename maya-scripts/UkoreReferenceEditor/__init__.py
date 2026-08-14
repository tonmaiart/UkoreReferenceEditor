import maya.cmds as cmds

try:
    from UkoreMenu import registry, MenuItemSpec

    registry.register_item(
        MenuItemSpec(
            id="ukore_reference_editor",
            label="Ukore Reference Editor...",
            category="General",
            command="from tmlib.core import File; File.launch('UkoreReferenceEditor')",
            order=20,  # 👈 กำหนดเป็น 20 เพื่อให้อยู่ต่อจาก Maya File Browser (ซึ่งตั้งไว้ order=10)
        )
    )
except ImportError:
    pass