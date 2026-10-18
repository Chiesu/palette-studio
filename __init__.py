# SPDX-License-Identifier: GPL-3.0-or-later
"""Palette Studio: scene-wide color palettes, with independent colors and links."""

bl_info = {
    'name': 'Palette Studio',
    'author': 'Palette Studio contributors',
    'version': (0, 3, 0),
    'blender': (4, 5, 0),
    'location': '3D View / Shader Editor > Sidebar > Palette',
    'description': 'Independent scene palettes; link color fields directly and switch looks',
    'category': 'Material',
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import PointerProperty

from . import model, ops, ui, button_links

CLASSES = model.CLASSES + ops.CLASSES + button_links.CLASSES + ui.CLASSES


def _migrate_all():
    for scene in bpy.data.scenes:
        try:
            model.migrate_scene(scene)
        except (AttributeError, RuntimeError, ReferenceError) as exc:
            print(f'[Palette Studio] Migration warning in scene {scene.name}: {exc}')
    return None


@persistent
def _load_post(_unused):
    _migrate_all()


def register():
    for cls in model.CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ps_state = PointerProperty(type=model.PS_State)
    for cls in ops.CLASSES + button_links.CLASSES + ui.CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.UI_MT_button_context_menu.append(button_links.draw_color_button_menu)
    # Loading an older .blend or enabling the new version in an already-open file.
    if getattr(bpy, 'app', None):
        if _load_post not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(_load_post)
        if not bpy.app.timers.is_registered(_migrate_all):
            bpy.app.timers.register(_migrate_all, first_interval=0.15)


def unregister():
    if getattr(bpy, 'app', None):
        if _load_post in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_load_post)
        if bpy.app.timers.is_registered(_migrate_all):
            bpy.app.timers.unregister(_migrate_all)
    bpy.types.UI_MT_button_context_menu.remove(button_links.draw_color_button_menu)
    for cls in reversed(ops.CLASSES + button_links.CLASSES + ui.CLASSES):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, 'ps_state'):
        del bpy.types.Scene.ps_state
    for cls in reversed(model.CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == '__main__':
    register()
