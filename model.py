# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene-local, palette-scoped swatches and target assignments.

Compatibility: v0.1 global roles / bindings remain available as legacy RNA fields
and are copied once into each existing palette during migration.
"""
from contextlib import contextmanager
from uuid import uuid4

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy.types import PropertyGroup

from .color_math import rgba_to_hex

_MUTATION_DEPTH = 0


@contextmanager
def suspend_callbacks():
    global _MUTATION_DEPTH
    _MUTATION_DEPTH += 1
    try:
        yield
    finally:
        _MUTATION_DEPTH -= 1


def callbacks_suspended():
    return _MUTATION_DEPTH > 0


def selected_palette(state):
    if 0 <= state.active_palette < len(state.palettes):
        return state.palettes[state.active_palette]
    return None


def selected_swatch(state):
    palette = selected_palette(state)
    if palette is not None and 0 <= palette.active_swatch < len(palette.swatches):
        return palette.swatches[palette.active_swatch]
    return None


def selected_role(state):
    """An editable named swatch in the currently selected palette."""
    return selected_swatch(state)


def find_swatch(palette, role_uid):
    if palette is not None:
        return next((swatch for swatch in palette.swatches if swatch.role_uid == role_uid), None)
    return None


def update_hex_input(state):
    swatch = selected_swatch(state)
    state.hex_input = rgba_to_hex(swatch.color) if swatch else ''


def on_palette_change(self, context):
    if callbacks_suspended():
        return
    update_hex_input(self)
    if self.apply_on_switch:
        from .bindings import apply_palette
        apply_palette(self.id_data, update_status=True, switch=True)


def on_swatch_selection(self, context):
    if not callbacks_suspended():
        scene = self.id_data
        if isinstance(scene, bpy.types.Scene) and (selected_palette(scene.ps_state) is not None and
                                                    selected_palette(scene.ps_state).as_pointer() == self.as_pointer()):
            update_hex_input(scene.ps_state)


def on_swatch_color(self, context):
    if callbacks_suspended() or not self.role_uid:
        return
    scene = self.id_data
    if not isinstance(scene, bpy.types.Scene):
        return
    state = scene.ps_state
    palette = selected_palette(state)
    match = find_swatch(palette, self.role_uid)
    if match is None or match.as_pointer() != self.as_pointer():
        return
    update_hex_input(state)
    if state.live_update:
        from .bindings import apply_palette
        apply_palette(scene, only_role_uid=self.role_uid, update_status=False, switch=False)


def on_binding_change(self, context):
    if callbacks_suspended() or not self.enabled:
        return
    scene = self.id_data
    if not isinstance(scene, bpy.types.Scene):
        return
    palette = selected_palette(scene.ps_state)
    if palette is None or not any(b.as_pointer() == self.as_pointer() for b in palette.bindings):
        return
    swatch = find_swatch(palette, self.role_uid)
    if swatch is not None and scene.ps_state.live_update:
        from .bindings import apply_one_binding
        apply_one_binding(self, swatch.color)


class PS_Role(PropertyGroup):
    """Legacy v0.1 global role; maintained for a lossless one-time migration."""
    uid: StringProperty(name='Legacy Role ID')
    name: StringProperty(name='Legacy Role Name')


class PS_Swatch(PropertyGroup):
    role_uid: StringProperty(name='Swatch ID')
    name: StringProperty(name='Color Name', default='New Color')
    color: FloatVectorProperty(
        name='Color', subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.45, 0.45, 0.45, 1.0), update=on_swatch_color,
    )


_BINDING_KINDS = (
    ('NODE_SOCKET', 'Node Color Socket', ''),
    ('RAMP_STOP', 'Color Ramp Stop', ''),
    ('OBJECT_COLOR', 'Object Viewport Color', ''),
    ('MATERIAL_COLOR', 'Material Viewport Color', ''),
    ('LIGHT_COLOR', 'Light Color', ''),
    ('WORLD_COLOR', 'World Color', ''),
)


class PS_Binding(PropertyGroup):
    role_uid: StringProperty(name='Swatch ID')
    kind: EnumProperty(name='Target', items=_BINDING_KINDS, default='NODE_SOCKET')
    enabled: BoolProperty(name='Enabled', default=True, update=on_binding_change)
    affect_alpha: BoolProperty(name='Link Alpha', default=False,
                               description='Also replace target alpha', update=on_binding_change)
    object_ref: PointerProperty(type=bpy.types.Object)
    material_ref: PointerProperty(type=bpy.types.Material)
    light_ref: PointerProperty(type=bpy.types.Light)
    world_ref: PointerProperty(type=bpy.types.World)
    # Only standalone groups may be stored in tree_ref. Embedded node trees
    # must be resolved through their owner ID (Material, Light, World, Scene).
    tree_ref: PointerProperty(type=bpy.types.NodeTree)
    tree_owner: EnumProperty(name='Node Tree Owner', items=(
        ('LEGACY', 'Legacy', ''), ('GROUP', 'Node Group', ''),
        ('MATERIAL', 'Material', ''), ('LIGHT', 'Light', ''),
        ('WORLD', 'World', ''), ('SCENE', 'Scene', ''),
        ('TEXTURE', 'Texture', ''),
    ), default='LEGACY')
    scene_ref: PointerProperty(type=bpy.types.Scene)
    texture_ref: PointerProperty(type=bpy.types.Texture)
    node_uid: StringProperty(name='Node ID')
    node_name: StringProperty(name='Node')
    direction: EnumProperty(items=(('INPUT', 'Input', ''), ('OUTPUT', 'Output', '')),
                            name='Socket Direction', default='INPUT')
    socket_id: StringProperty(name='Socket ID')
    socket_name: StringProperty(name='Socket Name')
    socket_index: IntProperty(name='Socket Index', default=-1)
    ramp_index: IntProperty(name='Ramp Stop Index', default=-1)
    original_color: FloatVectorProperty(name='Before First Link', size=4, subtype='COLOR',
                                        default=(0, 0, 0, 1))


BINDING_FIELDS = (
    'role_uid', 'kind', 'enabled', 'affect_alpha', 'object_ref', 'material_ref',
    'light_ref', 'world_ref', 'tree_ref', 'tree_owner', 'scene_ref',
    'texture_ref', 'node_uid', 'node_name', 'direction',
    'socket_id', 'socket_name', 'socket_index', 'ramp_index', 'original_color',
)


def copy_binding(source, destination):
    """Copy Blender RNA pointers and properties without relying on bpy.ops."""
    with suspend_callbacks():
        for field in BINDING_FIELDS:
            value = getattr(source, field)
            if field == 'tree_ref' and value is not None and getattr(value, 'is_embedded_data', False):
                # Do not propagate v0.2's invalid embedded NodeTree pointers.
                continue
            setattr(destination, field, value)


def copy_swatch(source, destination, color_transform=None):
    with suspend_callbacks():
        destination.role_uid = source.role_uid
        destination.name = source.name
        destination.color = color_transform(tuple(source.color)) if color_transform else tuple(source.color)


class PS_Palette(PropertyGroup):
    uid: StringProperty(name='Palette ID')
    name: StringProperty(name='Palette Name', default='Palette')
    swatches: CollectionProperty(type=PS_Swatch)
    bindings: CollectionProperty(type=PS_Binding)
    active_swatch: IntProperty(name='Selected Color', default=0, min=0, update=on_swatch_selection)
    active_binding: IntProperty(name='Selected Assignment', default=0, min=0)


class PS_State(PropertyGroup):
    # Keep these EXACT property names and types to migrate v0.1 scenes.
    roles: CollectionProperty(type=PS_Role)
    bindings: CollectionProperty(type=PS_Binding)
    active_role: IntProperty(name='Legacy Selected Role', default=0, min=0)
    active_binding: IntProperty(name='Legacy Selected Link', default=0, min=0)

    palettes: CollectionProperty(type=PS_Palette)
    active_palette: IntProperty(name='Active Palette', default=0, min=0, update=on_palette_change)
    data_version: IntProperty(name='Data Version', default=0)
    last_applied_uid: StringProperty(name='Last Applied Palette ID', default='')
    apply_on_switch: BoolProperty(name='Apply on Selection', default=True,
                                  description='One click on a palette updates its connected targets')
    switch_unassigned: EnumProperty(name='When a target is absent from next palette', items=(
        ('RESTORE', 'Restore Original', 'Restore targets controlled only by the previous palette'),
        ('KEEP', 'Keep Last Color', 'Leave those targets as they are'),
    ), default='RESTORE')
    live_update: BoolProperty(name='Live Color Editing', default=True,
                              description='Changing a swatch recolors its connected targets immediately')
    hex_input: StringProperty(name='Hex', default='#FFFFFF', description='sRGB hex code')
    last_status: StringProperty(name='Last Sync', default='No palette assignments yet')
    link_filter: EnumProperty(name='Show Assignments', items=(
        ('ALL', 'All', 'Show all assignments in active palette'),
        ('ROLE', 'Selected Color', 'Show only assignments for selected color'),
    ), default='ALL')


def migrate_scene(scene):
    """Upgrade .blend data saved by Palette Studio 0.1 without deleting backups."""
    st = scene.ps_state
    if st.data_version >= 2:
        return False
    old_names = {r.uid: r.name for r in st.roles}
    with suspend_callbacks():
        for palette in st.palettes:
            if not palette.uid:
                palette.uid = uuid4().hex
            for i, swatch in enumerate(palette.swatches):
                if not swatch.role_uid:
                    swatch.role_uid = uuid4().hex
                if not swatch.name or swatch.name == 'New Color':
                    swatch.name = old_names.get(swatch.role_uid, f'Color {i + 1:02d}')
            if st.bindings and not palette.bindings:
                for legacy in st.bindings:
                    copy_binding(legacy, palette.bindings.add())
            palette.active_swatch = min(st.active_role, max(0, len(palette.swatches) - 1))
            palette.active_binding = min(st.active_binding, max(0, len(palette.bindings) - 1))
        if st.palettes and not st.last_applied_uid:
            st.last_applied_uid = selected_palette(st).uid
        st.data_version = 2
    update_hex_input(st)
    return True


CLASSES = (PS_Role, PS_Swatch, PS_Binding, PS_Palette, PS_State)
