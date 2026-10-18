# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct linking from Blender's existing color swatches via right-click.

Blender provides `context.button_pointer` + `context.button_prop` for UI
button context menus. We only support explicit, resolvable RNA properties.
The regular Blender eyedropper samples pixels and cannot provide the actual
RNA destination, so this is NOT implemented as an ambiguous screen picker.
"""
from dataclasses import dataclass

import bpy
from bpy.types import Operator

from .bindings import (_identity, is_color_socket, link_node_socket,
                       link_ramp_stop, upsert_binding)
from .model import selected_palette, selected_swatch


@dataclass
class ButtonTarget:
    kind: str
    label: str
    pointer: object
    prop: str
    tree: object = None
    node: object = None
    direction: str = ''
    index: int = -1


def _button_data(context):
    pointer = getattr(context, 'button_pointer', None)
    prop = getattr(context, 'button_prop', None)
    if pointer is not None and prop is not None:
        return pointer, getattr(prop, 'identifier', '')
    # Blender 4.0+ also exposes a triple (owner, property name, array index).
    info = getattr(context, 'property', None)
    if info and isinstance(info, (tuple, list)) and len(info) >= 2:
        return info[0], str(info[1])
    return None, ''


def _tree_for_socket(pointer, node, context):
    # RNA id_data can refer to a NodeTree or its embedded owner depending
    # on which editor supplies the button context. Validate node membership.
    for candidate in (getattr(pointer, 'id_data', None),
                      getattr(node, 'id_data', None),
                      getattr(getattr(context, 'space_data', None), 'edit_tree', None),
                      getattr(context, 'material', None),
                      getattr(context, 'world', None),
                      getattr(context, 'light', None),
                      getattr(context, 'scene', None)):
        tree = (candidate if isinstance(candidate, bpy.types.NodeTree) else
                getattr(candidate, 'node_tree', None))
        if tree is None:
            continue
        try:
            if any(_identity(n) == _identity(node) for n in tree.nodes):
                return tree
        except (AttributeError, RuntimeError, ReferenceError):
            continue
    return None


def inspect_button(context):
    """Find the exact color property clicked, or return None for unsupported fields."""
    pointer, prop = _button_data(context)
    if pointer is None:
        return None
    simple = (
        (bpy.types.Material, 'diffuse_color', 'MATERIAL_COLOR', 'Material Viewport'),
        (bpy.types.Object, 'color', 'OBJECT_COLOR', 'Object Color'),
        (bpy.types.Light, 'color', 'LIGHT_COLOR', 'Light Color'),
        (bpy.types.World, 'color', 'WORLD_COLOR', 'World Color'),
    )
    for rna_type, name, kind, label in simple:
        if isinstance(pointer, rna_type) and prop == name:
            return ButtonTarget(kind, f'{pointer.name} / {label}', pointer, prop)

    if isinstance(pointer, bpy.types.NodeSocket) and prop == 'default_value':
        if not is_color_socket(pointer):
            return None
        node = getattr(pointer, 'node', None)
        tree = _tree_for_socket(pointer, node, context) if node is not None else None
        if node is None or tree is None:
            return None
        for direction, sockets in (('INPUT', node.inputs), ('OUTPUT', node.outputs)):
            for i, socket in enumerate(sockets):
                if _identity(socket) == _identity(pointer):
                    if direction == 'INPUT' and socket.is_linked:
                        return None
                    return ButtonTarget('NODE_SOCKET', f'{node.name} / {socket.name}',
                                        pointer, prop, tree, node, direction, i)
        return None

    if isinstance(pointer, bpy.types.ColorRampElement) and prop == 'color':
        # Stop elements have no public parent Node property. Find the stop in
        # the editable tree in the Node Editor, without guessing by color value.
        tree = getattr(getattr(context, 'space_data', None), 'edit_tree', None)
        if tree is None:
            return None
        for node in tree.nodes:
            ramp = getattr(node, 'color_ramp', None)
            if ramp is not None:
                for i, stop in enumerate(ramp.elements):
                    if _identity(stop) == _identity(pointer):
                        return ButtonTarget('RAMP_STOP', f'{node.name} / Stop {i+1}',
                                            pointer, prop, tree, node, index=i)
    return None


def link_button_target(scene, role_uid, target):
    if target.kind == 'NODE_SOCKET':
        return link_node_socket(scene, role_uid, target.tree, target.node,
                                target.direction, target.index)
    if target.kind == 'RAMP_STOP':
        return link_ramp_stop(scene, role_uid, target.tree, target.node, target.index)
    attr = {
        'OBJECT_COLOR': 'object_ref', 'MATERIAL_COLOR': 'material_ref',
        'LIGHT_COLOR': 'light_ref', 'WORLD_COLOR': 'world_ref',
    }.get(target.kind)
    if attr:
        return upsert_binding(scene, role_uid, target.kind, **{attr: target.pointer})
    raise ValueError('Unsupported clicked color target')


class PS_OT_link_button_color(Operator):
    bl_idname = 'ps.link_button_color'
    bl_label = 'Link This Color to Selected Swatch'
    bl_description = 'Link the right-clicked Blender color property to the active palette swatch'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(getattr(context, 'scene', None) and
                    selected_swatch(context.scene.ps_state) is not None and inspect_button(context))

    def execute(self, context):
        target = inspect_button(context)
        swatch = selected_swatch(context.scene.ps_state)
        if target is None or swatch is None:
            self.report({'WARNING'}, 'Right-click a supported Blender color property')
            return {'CANCELLED'}
        result, _ = link_button_target(context.scene, swatch.role_uid, target)
        if result.status in {'APPLIED', 'UNCHANGED'}:
            self.report({'INFO'}, f'Linked {target.label} to {swatch.name}')
            return {'FINISHED'}
        self.report({'WARNING'}, result.message or result.status)
        return {'CANCELLED'}


class PS_OT_sample_button_color(Operator):
    bl_idname = 'ps.sample_button_color'
    bl_label = 'Sample This Color into Selected Swatch'
    bl_description = 'Copy the actual Blender color value into the selected palette swatch, without linking'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(getattr(context, 'scene', None) and
                    selected_swatch(context.scene.ps_state) is not None and inspect_button(context))

    def execute(self, context):
        target = inspect_button(context)
        swatch = selected_swatch(context.scene.ps_state)
        if target is None or swatch is None:
            self.report({'WARNING'}, 'No supported color property was clicked')
            return {'CANCELLED'}
        try:
            color = tuple(getattr(target.pointer, target.prop))
            if len(color) == 3:
                color = color + (1.0,)
            if len(color) != 4:
                return {'CANCELLED'}
            swatch.color = color
        except (AttributeError, TypeError, ReferenceError, RuntimeError, ValueError):
            self.report({'WARNING'}, 'Could not read this Blender color')
            return {'CANCELLED'}
        self.report({'INFO'}, f'Sampled {target.label} into {swatch.name}')
        return {'FINISHED'}


def draw_color_button_menu(self, context):
    try:
        target = inspect_button(context)
        swatch = selected_swatch(context.scene.ps_state) if context.scene else None
        if target is None or swatch is None:
            return
        layout = self.layout
        layout.separator()
        layout.label(text=f'Palette Studio: {swatch.name}', icon='COLOR')
        layout.operator(PS_OT_link_button_color.bl_idname,
                        text='Link this color to selected swatch', icon='LINKED')
        layout.operator(PS_OT_sample_button_color.bl_idname,
                        text='Sample this color into swatch', icon='EYEDROPPER')
    except (AttributeError, RuntimeError, ReferenceError, TypeError):
        # Never break Blender's own right-click menu on unsupported fields.
        return


CLASSES = (PS_OT_link_button_color, PS_OT_sample_button_color)
