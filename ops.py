# SPDX-License-Identifier: GPL-3.0-or-later
"""Actions for independent, scene-applied palettes and editable color targets."""
import json
from pathlib import Path
from uuid import uuid4

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .bindings import (apply_one_binding, apply_palette, binding_label, binding_ref,
                       is_color_socket, link_node_socket, link_ramp_stop, locate,
                       restore_binding, restore_exclusive, target_color, upsert_binding,
                       _match)
from .color_math import (STARTER_PALETTES, STARTER_ROLES, STARTER_LOCAL_NAMES, hex_to_rgba,
                         rgba_to_hex, linear_to_srgb, variant)
from .model import (copy_binding, copy_swatch, find_swatch, migrate_scene,
                    selected_palette, selected_swatch, suspend_callbacks, update_hex_input)


def unique_name(candidate, existing):
    names = {str(name).strip().casefold() for name in existing}
    text = candidate.strip() or 'Untitled'
    if text.casefold() not in names:
        return text
    i = 2
    while f'{text} {i}'.casefold() in names:
        i += 1
    return f'{text} {i}'


def is_ready(context):
    st = context.scene.ps_state
    palette = selected_palette(st)
    return bool(palette and palette.swatches)


def active_role_uid(st):
    swatch = selected_swatch(st)
    return swatch.role_uid if swatch else None


def add_swatch(palette, name, color, uid=None):
    """Add a locally named color; different palettes may reuse the same names."""
    swatch = palette.swatches.add()
    swatch.role_uid = uid or uuid4().hex
    swatch.name = unique_name(name, (s.name for s in list(palette.swatches)[:-1]))
    swatch.color = color
    return swatch


def new_palette(st, name, source=None, transform=None, copy_links=False):
    """Create empty or duplicate palette. Never share mutable RNA collections."""
    palette = st.palettes.add()
    palette.uid = uuid4().hex
    palette.name = unique_name(name, (p.name for p in list(st.palettes)[:-1]))
    if source:
        for swatch in source.swatches:
            copy_swatch(swatch, palette.swatches.add(), color_transform=transform)
        if copy_links:
            for binding in source.bindings:
                copy_binding(binding, palette.bindings.add())
        palette.active_swatch = min(source.active_swatch, max(0, len(palette.swatches) - 1))
    return palette


def report_assignment(operator, result, created=True):
    if result.status in {'APPLIED', 'UNCHANGED'}:
        operator.report({'INFO'}, 'Target added to active palette' if created else 'Target reassigned in active palette')
        return True
    operator.report({'WARNING'}, result.message or result.status)
    return False


def find_principled(material):
    if not material or not material.use_nodes or not material.node_tree:
        return None
    tree = material.node_tree
    candidates = [n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED' and n.inputs.get('Base Color')]
    if len(candidates) == 1:
        return candidates[0]
    active = tree.nodes.active
    if active in candidates:
        return active
    outputs = [n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL' and
               getattr(n, 'is_active_output', True)]
    direct = [link.from_node for link in tree.links if link.to_node in outputs and
              link.to_socket.name == 'Surface' and link.from_node in candidates]
    return direct[0] if len(direct) == 1 else None


def base_color_target(material):
    node = find_principled(material)
    if node is None:
        return None
    for i, sock in enumerate(node.inputs):
        if sock.name == 'Base Color' and is_color_socket(sock):
            return material.node_tree, node, i, sock
    return None


def world_background_target(world):
    if not (world and world.use_nodes and world.node_tree):
        return None
    tree = world.node_tree
    backgrounds = [n for n in tree.nodes if n.type == 'BACKGROUND']
    if len(backgrounds) == 1:
        node = backgrounds[0]
    else:
        outputs = [n for n in tree.nodes if n.type == 'OUTPUT_WORLD' and
                   getattr(n, 'is_active_output', True)]
        direct = [link.from_node for link in tree.links if link.to_node in outputs and
                  link.to_socket.name == 'Surface' and link.from_node in backgrounds]
        if len(direct) != 1:
            return None
        node = direct[0]
    for i, sock in enumerate(node.inputs):
        if sock.name == 'Color' and is_color_socket(sock):
            return tree, node, i, sock
    return None


class PS_OT_initialize(Operator):
    bl_idname = 'ps.initialize'
    bl_label = 'Create Palette Workspace'
    bl_description = 'Start a set of independent palettes without changing materials'
    bl_options = {'REGISTER', 'UNDO'}
    mode: EnumProperty(items=(('STARTER', '4 Sample Palettes', ''), ('EMPTY', 'Empty', '')),
                       default='STARTER')

    def execute(self, context):
        st = context.scene.ps_state
        migrate_scene(context.scene)
        if st.palettes:
            self.report({'WARNING'}, 'A palette workspace already exists')
            return {'CANCELLED'}
        with suspend_callbacks():
            if self.mode == 'STARTER':
                for pname, hex_colors in STARTER_PALETTES:
                    p = new_palette(st, pname)
                    for i, name in enumerate(STARTER_LOCAL_NAMES.get(pname, STARTER_ROLES)):
                        add_swatch(p, name, hex_to_rgba(hex_colors[i]))
            else:
                new_palette(st, 'Palette 01')
            st.active_palette = 0
            st.last_applied_uid = st.palettes[0].uid
            st.data_version = 2
            st.last_status = 'Palette created. Add colors, then assign targets.'
        update_hex_input(st)
        return {'FINISHED'}


class PS_OT_select_palette(Operator):
    bl_idname = 'ps.select_palette'
    bl_label = 'Activate Scene Palette'
    bl_description = 'Select palette and automatically apply its assigned scene colors'
    bl_options = {'REGISTER', 'UNDO'}
    index: IntProperty(default=-1)
    direction: IntProperty(default=0)

    def execute(self, context):
        st = context.scene.ps_state
        if not st.palettes:
            return {'CANCELLED'}
        index = (st.active_palette + self.direction) % len(st.palettes) if self.direction else self.index
        if not 0 <= index < len(st.palettes):
            return {'CANCELLED'}
        if index == st.active_palette:
            apply_palette(context.scene, switch=True)
        else:
            st.active_palette = index
        return {'FINISHED'}


class PS_OT_new_palette(Operator):
    bl_idname = 'ps.new_palette'
    bl_label = 'New Scene Palette'
    bl_description = 'Start empty, copy colors, or copy colors and their scene assignments'
    bl_options = {'REGISTER', 'UNDO'}
    name: StringProperty(name='Palette Name', default='New Palette')
    mode: EnumProperty(name='Create From', items=(
        ('EMPTY', 'Empty Palette', 'Create an independent palette with no colors or links'),
        ('COLORS', 'Copy Colors Only', 'Copy colors and names but do not link to scene targets'),
        ('FULL', 'Copy Colors + Targets', 'Copy colors and all assignments to the same targets'),
    ), default='EMPTY')

    def invoke(self, context, event):
        st = context.scene.ps_state
        self.name = unique_name('New Palette', (p.name for p in st.palettes))
        return context.window_manager.invoke_props_dialog(self, width=410)

    def execute(self, context):
        st = context.scene.ps_state
        source = selected_palette(st)
        with suspend_callbacks():
            new_palette(st, self.name, source if self.mode != 'EMPTY' else None,
                        copy_links=self.mode == 'FULL')
        st.active_palette = len(st.palettes) - 1
        return {'FINISHED'}


class PS_OT_duplicate_palette(Operator):
    bl_idname = 'ps.duplicate_palette'
    bl_label = 'Duplicate with Assignments'
    bl_description = 'Make a new palette with the same targets, then edit its colors independently'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = context.scene.ps_state
        source = selected_palette(st)
        if source is None:
            return {'CANCELLED'}
        with suspend_callbacks():
            new_palette(st, source.name + ' Copy', source, copy_links=True)
        st.active_palette = len(st.palettes) - 1
        return {'FINISHED'}


class PS_OT_delete_palette(Operator):
    bl_idname = 'ps.delete_palette'
    bl_label = 'Delete Scene Palette'
    bl_description = 'Remove only the selected palette and its own assignments'
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        st = context.scene.ps_state
        if len(st.palettes) <= 1:
            self.report({'WARNING'}, 'Keep at least one palette')
            return {'CANCELLED'}
        old = selected_palette(st)
        index = st.active_palette
        next_index = index - 1 if index == len(st.palettes) - 1 else index + 1
        incoming = st.palettes[next_index]
        if st.switch_unassigned == 'RESTORE' and st.last_applied_uid == old.uid:
            restore_exclusive(old, incoming)
        with suspend_callbacks():
            st.palettes.remove(index)
            st.active_palette = min(index, len(st.palettes) - 1)
        apply_palette(context.scene, switch=True)
        return {'FINISHED'}


class PS_OT_rename_palette(Operator):
    bl_idname = 'ps.rename_palette'
    bl_label = 'Rename Scene Palette'
    bl_options = {'REGISTER', 'UNDO'}
    name: StringProperty(name='Palette Name')

    def invoke(self, context, event):
        p = selected_palette(context.scene.ps_state)
        if not p:
            return {'CANCELLED'}
        self.name = p.name
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        st = context.scene.ps_state
        p = selected_palette(st)
        if not p:
            return {'CANCELLED'}
        p.name = unique_name(self.name, (x.name for x in st.palettes if x.as_pointer() != p.as_pointer()))
        return {'FINISHED'}


class PS_OT_create_variant(Operator):
    bl_idname = 'ps.create_variant'
    bl_label = 'Create Linked Palette Variation'
    bl_description = 'Create an independently editable color variation with all current target assignments'
    bl_options = {'REGISTER', 'UNDO'}
    style: EnumProperty(name='Variation', items=(
        ('WARM', 'Warmer', 'Gently add warmth'),
        ('COOL', 'Cooler', 'Gently add cool tones'),
        ('MUTED', 'Muted', 'Reduce saturation'),
        ('DARK', 'Darker', 'Darken values'),
        ('LIGHT', 'Lighter', 'Lighten values'),
    ), default='WARM')

    def execute(self, context):
        st = context.scene.ps_state
        source = selected_palette(st)
        if not source:
            return {'CANCELLED'}
        with suspend_callbacks():
            new_palette(st, f'{source.name} / {self.style.title()}', source,
                        transform=lambda col: variant(col, self.style), copy_links=True)
        st.active_palette = len(st.palettes) - 1
        return {'FINISHED'}


class PS_OT_select_role(Operator):
    bl_idname = 'ps.select_role'
    bl_label = 'Select Color in Current Palette'
    bl_options = {'INTERNAL'}
    index: IntProperty(default=0)

    def execute(self, context):
        p = selected_palette(context.scene.ps_state)
        if p is None or not 0 <= self.index < len(p.swatches):
            return {'CANCELLED'}
        p.active_swatch = self.index
        return {'FINISHED'}


class PS_OT_add_role(Operator):
    bl_idname = 'ps.add_role'
    bl_label = 'Add Color to This Palette'
    bl_description = 'New color appears ONLY in selected palette, not other palettes'
    bl_options = {'REGISTER', 'UNDO'}
    name: StringProperty(name='Color Name', default='New Color')
    hex_value: StringProperty(name='Initial sRGB Hex', default='#8A8A8A')

    def invoke(self, context, event):
        p = selected_palette(context.scene.ps_state)
        self.name = unique_name('New Color', (s.name for s in p.swatches))
        swatch = selected_swatch(context.scene.ps_state)
        self.hex_value = rgba_to_hex(swatch.color) if swatch else '#808080'
        return context.window_manager.invoke_props_dialog(self, width=365)

    def execute(self, context):
        st = context.scene.ps_state
        p = selected_palette(st)
        if p is None:
            return {'CANCELLED'}
        try:
            rgba = hex_to_rgba(self.hex_value)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        with suspend_callbacks():
            add_swatch(p, self.name, rgba)
            p.active_swatch = len(p.swatches) - 1
        update_hex_input(st)
        return {'FINISHED'}


class PS_OT_rename_role(Operator):
    bl_idname = 'ps.rename_role'
    bl_label = 'Rename Color in This Palette'
    bl_description = 'Rename only this palette color; other palette names and assignments remain unchanged'
    bl_options = {'REGISTER', 'UNDO'}
    name: StringProperty(name='Color Name')

    def invoke(self, context, event):
        swatch = selected_swatch(context.scene.ps_state)
        if swatch is None:
            return {'CANCELLED'}
        self.name = swatch.name
        return context.window_manager.invoke_props_dialog(self, width=365)

    def execute(self, context):
        st = context.scene.ps_state
        swatch = selected_swatch(st)
        p = selected_palette(st)
        if swatch is None:
            return {'CANCELLED'}
        swatch.name = unique_name(self.name, (s.name for s in p.swatches if s.as_pointer() != swatch.as_pointer()))
        return {'FINISHED'}


class PS_OT_move_role(Operator):
    bl_idname = 'ps.move_role'
    bl_label = 'Reorder Palette Colors'
    bl_options = {'REGISTER', 'UNDO'}
    direction: IntProperty(default=1)

    def execute(self, context):
        p = selected_palette(context.scene.ps_state)
        if p is None:
            return {'CANCELLED'}
        old, new = p.active_swatch, p.active_swatch + self.direction
        if not (0 <= old < len(p.swatches) and 0 <= new < len(p.swatches)):
            return {'CANCELLED'}
        with suspend_callbacks():
            p.swatches.move(old, new)
            p.active_swatch = new
        return {'FINISHED'}


class PS_OT_delete_role(Operator):
    bl_idname = 'ps.delete_role'
    bl_label = 'Delete Color in Current Palette'
    bl_description = 'Delete this palette color and its target links; other palettes remain unchanged'
    bl_options = {'REGISTER', 'UNDO'}
    restore: BoolProperty(name='Restore Original Target Colors', default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=410)

    def draw(self, context):
        p = selected_palette(context.scene.ps_state)
        swatch = selected_swatch(context.scene.ps_state)
        count = sum(b.role_uid == swatch.role_uid for b in p.bindings) if p and swatch else 0
        self.layout.label(text=f'Remove this color and {count} assignments from "{p.name}"?')
        self.layout.label(text='All other palettes are preserved.')
        self.layout.prop(self, 'restore')

    def execute(self, context):
        st = context.scene.ps_state
        p, swatch = selected_palette(st), selected_swatch(st)
        if swatch is None:
            return {'CANCELLED'}
        uid = swatch.role_uid
        if self.restore and st.last_applied_uid == p.uid:
            for b in p.bindings:
                if b.role_uid == uid:
                    restore_binding(b)
        with suspend_callbacks():
            for i in reversed(range(len(p.bindings))):
                if p.bindings[i].role_uid == uid:
                    p.bindings.remove(i)
            p.swatches.remove(p.active_swatch)
            p.active_swatch = min(p.active_swatch, max(0, len(p.swatches) - 1))
            p.active_binding = min(p.active_binding, max(0, len(p.bindings) - 1))
        update_hex_input(st)
        return {'FINISHED'}


class PS_OT_set_hex(Operator):
    bl_idname = 'ps.set_hex'
    bl_label = 'Set Swatch Hex'
    bl_description = 'Convert sRGB hex to Blender linear color for this palette only'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = context.scene.ps_state
        swatch = selected_swatch(st)
        if swatch is None:
            return {'CANCELLED'}
        try:
            swatch.color = hex_to_rgba(st.hex_input)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        update_hex_input(st)
        return {'FINISHED'}


class PS_OT_clipboard_hex(Operator):
    bl_idname = 'ps.clipboard_hex'
    bl_label = 'Copy or Paste Hex'
    bl_options = {'REGISTER', 'UNDO'}
    mode: EnumProperty(items=(('COPY', 'Copy', ''), ('PASTE', 'Paste', '')))

    def execute(self, context):
        st = context.scene.ps_state
        swatch = selected_swatch(st)
        if swatch is None:
            return {'CANCELLED'}
        if self.mode == 'COPY':
            context.window_manager.clipboard = rgba_to_hex(swatch.color)
        else:
            try:
                swatch.color = hex_to_rgba(context.window_manager.clipboard)
            except ValueError:
                self.report({'ERROR'}, 'Clipboard has no valid RGB/RGBA hex')
                return {'CANCELLED'}
            update_hex_input(st)
        return {'FINISHED'}


class PS_OT_apply(Operator):
    bl_idname = 'ps.apply'
    bl_label = 'Apply Selected Palette'
    bl_description = 'Apply every enabled color assignment from the active palette in one click'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        stats = apply_palette(context.scene, switch=True)
        self.report({'WARNING'} if stats.failed or stats.blocked or stats.missing else {'INFO'}, stats.short())
        return {'FINISHED'}


class PS_OT_bind_quick(Operator):
    bl_idname = 'ps.bind_quick'
    bl_label = 'Assign Selected Color to Targets'
    bl_description = 'Add assignments to the ACTIVE palette, not to other palettes'
    bl_options = {'REGISTER', 'UNDO'}
    kind: EnumProperty(items=(
        ('ACTIVE_BASE', 'Active Material Base Color', ''),
        ('ALL_MATERIALS', 'Selected Objects Materials Base Color', ''),
        ('ACTIVE_VIEWPORT', 'Active Material Viewport Color', ''),
        ('ACTIVE_OBJECT', 'Active Object Display Color', ''),
        ('ACTIVE_LIGHT', 'Active Light Color', ''),
        ('SELECTED_OBJECTS', 'Selected Object Display Colors', ''),
        ('SELECTED_LIGHTS', 'Selected Light Colors', ''),
        ('WORLD_BACKGROUND', 'World Background Color', ''),
    ))

    @classmethod
    def poll(cls, context):
        return is_ready(context)

    def execute(self, context):
        uid = active_role_uid(context.scene.ps_state)
        added = updated = skipped = 0
        problem = ''

        def record(result, created):
            nonlocal added, updated, skipped, problem
            if result.status in {'APPLIED', 'UNCHANGED'}:
                added += int(created)
                updated += int(not created)
            else:
                skipped += 1
                problem = result.message or result.status

        if self.kind in ('ACTIVE_BASE', 'ALL_MATERIALS'):
            mats = []
            if self.kind == 'ACTIVE_BASE':
                obj = context.active_object
                if obj and obj.active_material:
                    mats = [obj.active_material]
            else:
                for obj in context.selected_objects:
                    for slot in getattr(obj, 'material_slots', []):
                        if slot.material and not any(m.as_pointer() == slot.material.as_pointer() for m in mats):
                            mats.append(slot.material)
            if len(mats) > 500:
                self.report({'ERROR'}, 'Select fewer than 500 unique materials')
                return {'CANCELLED'}
            for mat in mats:
                target = base_color_target(mat)
                if target:
                    tree, node, idx, _ = target
                    record(*link_node_socket(context.scene, uid, tree, node, 'INPUT', idx, owner_hint=mat))
                else:
                    skipped += 1
                    problem = f'Cannot find unambiguous Principled Base Color in {mat.name}'

        elif self.kind == 'ACTIVE_VIEWPORT':
            obj = context.active_object
            mat = obj.active_material if obj else None
            if mat:
                record(*upsert_binding(context.scene, uid, 'MATERIAL_COLOR', material_ref=mat))
            else:
                skipped, problem = 1, 'No active material'
        elif self.kind == 'ACTIVE_OBJECT':
            if context.active_object:
                record(*upsert_binding(context.scene, uid, 'OBJECT_COLOR',
                                       object_ref=context.active_object))
            else:
                skipped, problem = 1, 'No active object'
        elif self.kind == 'ACTIVE_LIGHT':
            obj = context.active_object
            light = obj.data if obj is not None and obj.type == 'LIGHT' else None
            if light:
                record(*upsert_binding(context.scene, uid, 'LIGHT_COLOR', light_ref=light))
            else:
                skipped, problem = 1, 'Active object is not a light'
        elif self.kind == 'SELECTED_OBJECTS':
            for obj in context.selected_objects:
                record(*upsert_binding(context.scene, uid, 'OBJECT_COLOR', object_ref=obj))
        elif self.kind == 'SELECTED_LIGHTS':
            seen = set()
            for obj in context.selected_objects:
                light = obj.data if obj.type == 'LIGHT' else None
                if light and light.as_pointer() not in seen:
                    seen.add(light.as_pointer())
                    record(*upsert_binding(context.scene, uid, 'LIGHT_COLOR', light_ref=light))
        elif self.kind == 'WORLD_BACKGROUND':
            world = context.scene.world
            if world:
                if world.use_nodes:
                    target = world_background_target(world)
                    if target:
                        tree, node, idx, _ = target
                        record(*link_node_socket(context.scene, uid, tree, node, 'INPUT', idx, owner_hint=world))
                    else:
                        skipped, problem = 1, 'Cannot find an editable World Background color'
                else:
                    record(*upsert_binding(context.scene, uid, 'WORLD_COLOR', world_ref=world))
            else:
                skipped, problem = 1, 'Scene has no world'
        if not added and not updated:
            self.report({'WARNING'}, problem or 'No compatible scene targets selected')
            return {'CANCELLED'}
        message = f'{added} added, {updated} updated in current palette'
        if skipped:
            message += f'; {skipped} skipped ({problem})'
        context.scene.ps_state.last_status = message
        self.report({'WARNING'} if skipped else {'INFO'}, message)
        return {'FINISHED'}


class PS_OT_sample_quick(Operator):
    bl_idname = 'ps.sample_quick'
    bl_label = 'Sample Color into This Palette'
    bl_options = {'REGISTER', 'UNDO'}
    kind: EnumProperty(items=(
        ('ACTIVE_BASE', 'Active Base Color', ''),
        ('ACTIVE_VIEWPORT', 'Active Material Viewport', ''),
        ('OBJECT', 'Active Object Color', ''),
        ('ACTIVE_LIGHT', 'Active Light Color', ''),
        ('WORLD_BACKGROUND', 'World Background Color', ''),
    ))

    def execute(self, context):
        swatch = selected_swatch(context.scene.ps_state)
        if swatch is None:
            return {'CANCELLED'}
        source = None
        obj = context.active_object
        if self.kind == 'OBJECT' and obj:
            source = tuple(obj.color)
        elif self.kind == 'ACTIVE_LIGHT' and obj and obj.type == 'LIGHT':
            source = tuple(obj.data.color)
        elif self.kind == 'ACTIVE_VIEWPORT' and obj and obj.active_material:
            source = tuple(obj.active_material.diffuse_color)
        elif self.kind == 'ACTIVE_BASE' and obj and obj.active_material:
            target = base_color_target(obj.active_material)
            if target and not target[3].is_linked:
                source = tuple(target[3].default_value)
        elif self.kind == 'WORLD_BACKGROUND' and context.scene.world:
            world = context.scene.world
            if not world.use_nodes:
                source = tuple(world.color)
            else:
                target = world_background_target(world)
                if target and not target[3].is_linked:
                    source = tuple(target[3].default_value)
        if source is None:
            self.report({'WARNING'}, 'No editable source color found')
            return {'CANCELLED'}
        swatch.color = source + (1.0,) if len(source) == 3 else source
        return {'FINISHED'}


class PS_OT_bind_node_socket(Operator):
    bl_idname = 'ps.bind_node_socket'
    bl_label = 'Assign Individual Node Color'
    bl_description = 'Assign a specific RGBA socket. One material may have many different assignments.'
    bl_options = {'REGISTER', 'UNDO'}
    node_name: StringProperty()
    direction: EnumProperty(items=(('INPUT', 'Input', ''), ('OUTPUT', 'Output', '')))
    socket_index: IntProperty()
    sample: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return is_ready(context) and bool(getattr(context.space_data, 'edit_tree', None))

    def execute(self, context):
        tree = context.space_data.edit_tree
        node = tree.nodes.get(self.node_name)
        if node is None:
            return {'CANCELLED'}
        sockets = node.inputs if self.direction == 'INPUT' else node.outputs
        if not 0 <= self.socket_index < len(sockets):
            return {'CANCELLED'}
        socket = sockets[self.socket_index]
        if not is_color_socket(socket) or (self.direction == 'INPUT' and socket.is_linked):
            self.report({'WARNING'}, 'Socket is not an editable RGBA value')
            return {'CANCELLED'}
        if self.sample:
            selected_swatch(context.scene.ps_state).color = tuple(socket.default_value)
            return {'FINISHED'}
        result, created = link_node_socket(context.scene, active_role_uid(context.scene.ps_state),
                                           tree, node, self.direction, self.socket_index)
        return {'FINISHED'} if report_assignment(self, result, created) else {'CANCELLED'}


class PS_OT_bind_ramp_stop(Operator):
    bl_idname = 'ps.bind_ramp_stop'
    bl_label = 'Assign ColorRamp Stop'
    bl_description = 'Assign selected ramp stop by index; reassign after adding/removing ramp stops'
    bl_options = {'REGISTER', 'UNDO'}
    node_name: StringProperty()
    ramp_index: IntProperty(default=0)
    sample: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return is_ready(context) and bool(getattr(context.space_data, 'edit_tree', None))

    def execute(self, context):
        tree = context.space_data.edit_tree
        node = tree.nodes.get(self.node_name)
        ramp = getattr(node, 'color_ramp', None) if node else None
        if ramp is None or not 0 <= self.ramp_index < len(ramp.elements):
            return {'CANCELLED'}
        if self.sample:
            selected_swatch(context.scene.ps_state).color = tuple(ramp.elements[self.ramp_index].color)
            return {'FINISHED'}
        result, created = link_ramp_stop(context.scene, active_role_uid(context.scene.ps_state),
                                         tree, node, self.ramp_index)
        return {'FINISHED'} if report_assignment(self, result, created) else {'CANCELLED'}


class PS_OT_remove_binding(Operator):
    bl_idname = 'ps.remove_binding'
    bl_label = 'Remove Palette Assignment'
    bl_description = 'Unlink from active palette only. Can restore the original scene color.'
    bl_options = {'REGISTER', 'UNDO'}
    index: IntProperty(default=-1)
    restore: BoolProperty(name='Restore Original Color', default=False)

    def execute(self, context):
        st = context.scene.ps_state
        p = selected_palette(st)
        idx = self.index if self.index >= 0 else p.active_binding
        if not 0 <= idx < len(p.bindings):
            return {'CANCELLED'}
        result = None
        if self.restore and st.last_applied_uid == p.uid:
            result = restore_binding(p.bindings[idx])
        with suspend_callbacks():
            p.bindings.remove(idx)
            p.active_binding = max(0, min(p.active_binding, len(p.bindings) - 1))
        if result and result.status not in {'APPLIED', 'UNCHANGED'}:
            self.report({'WARNING'}, 'Unlinked but original color could not be restored')
        return {'FINISHED'}


class PS_OT_reassign_binding(Operator):
    bl_idname = 'ps.reassign_binding'
    bl_label = 'Reassign Selected Target to Selected Color'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = selected_palette(context.scene.ps_state)
        swatch = selected_swatch(context.scene.ps_state)
        if p is None or swatch is None or not 0 <= p.active_binding < len(p.bindings):
            return {'CANCELLED'}
        binding = p.bindings[p.active_binding]
        previous_uid, previous_enabled = binding.role_uid, binding.enabled
        with suspend_callbacks():
            binding.role_uid = swatch.role_uid
            binding.enabled = True
        result = apply_one_binding(binding, swatch.color)
        if result.status == 'FAILED':
            with suspend_callbacks():
                binding.role_uid = previous_uid
                binding.enabled = previous_enabled
        return {'FINISHED'} if report_assignment(self, result, False) else {'CANCELLED'}


class PS_OT_validate(Operator):
    bl_idname = 'ps.validate'
    bl_label = 'Audit Active Palette Assignments'
    bl_options = {'REGISTER'}

    def execute(self, context):
        st = context.scene.ps_state
        p = selected_palette(st)
        valid = blocked = missing = disabled = 0
        for b in p.bindings:
            if not b.enabled:
                disabled += 1
            else:
                status, _, _ = locate(b)
                valid += status == 'OK'
                blocked += status == 'BLOCKED'
                missing += status == 'MISSING'
        st.last_status = f'{valid} valid, {blocked} connected, {missing} missing, {disabled} disabled'
        self.report({'WARNING'} if blocked or missing else {'INFO'}, st.last_status)
        return {'FINISHED'}


_SOURCE_ENUM_CACHE = []


def available_source_palettes(self, context):
    global _SOURCE_ENUM_CACHE
    if context is None:
        return _SOURCE_ENUM_CACHE or [('-1', 'None', 'No other palettes')]
    st = context.scene.ps_state
    _SOURCE_ENUM_CACHE = [(str(i), str(p.name), f'Copy assignments from {p.name}')
                          for i, p in enumerate(st.palettes) if i != st.active_palette]
    if not _SOURCE_ENUM_CACHE:
        _SOURCE_ENUM_CACHE = [('-1', 'None', 'No other palettes')]
    return _SOURCE_ENUM_CACHE


class PS_OT_copy_targets(Operator):
    bl_idname = 'ps.copy_targets'
    bl_label = 'Copy Assignments from Another Palette'
    bl_description = 'Reuse target links from another palette; preserve current colors and existing links'
    bl_options = {'REGISTER', 'UNDO'}
    source_index: EnumProperty(name='Source Palette', items=available_source_palettes)
    replace: BoolProperty(name='Replace Existing Target Assignments', default=False,
                          description='If target is already assigned here, reassign it to the source color name')

    def invoke(self, context, event):
        if len(context.scene.ps_state.palettes) < 2:
            self.report({'WARNING'}, 'Create another palette to copy from')
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=430)

    def draw(self, context):
        self.layout.prop(self, 'source_index')
        self.layout.prop(self, 'replace')
        self.layout.label(text='New color names are copied; existing names/colors are preserved.', icon='INFO')

    def execute(self, context):
        st = context.scene.ps_state
        dest = selected_palette(st)
        try:
            index = int(self.source_index)
        except (TypeError, ValueError):
            return {'CANCELLED'}
        if not 0 <= index < len(st.palettes) or st.palettes[index].uid == dest.uid:
            return {'CANCELLED'}
        src = st.palettes[index]
        mapping = {}
        added_colors = added_links = replaced = skipped = 0
        with suspend_callbacks():
            for swatch in src.swatches:
                found = next((s for s in dest.swatches if s.name.casefold() == swatch.name.casefold()), None)
                if found is None:
                    found = add_swatch(dest, swatch.name, tuple(swatch.color))
                    added_colors += 1
                mapping[swatch.role_uid] = found.role_uid
            for binding in src.bindings:
                uid = mapping.get(binding.role_uid)
                if uid is None:
                    skipped += 1
                    continue
                ref = binding_ref(binding)
                exists = next((b for b in dest.bindings if _match(b, binding.kind, **ref)), None)
                if exists is not None and not self.replace:
                    skipped += 1
                    continue
                result, created = upsert_binding(context.scene, uid, binding.kind, apply_now=False, **ref)
                if result.status not in {'UNCHANGED', 'APPLIED'}:
                    skipped += 1
                    continue
                receiver = dest.bindings[-1] if created else exists
                receiver.affect_alpha = binding.affect_alpha
                receiver.enabled = binding.enabled
                added_links += int(created)
                replaced += int(not created)
        update_hex_input(st)
        stats = apply_palette(context.scene, switch=True)
        msg = f'{added_colors} colors, {added_links} new links, {replaced} reassigned'
        if skipped:
            msg += f', {skipped} kept/skipped'
        st.last_status = f'{msg}. {stats.short()}'
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class PS_OT_export_json(Operator, ExportHelper):
    bl_idname = 'ps.export_json'
    bl_label = 'Export Independent Palette Library'
    bl_description = 'Export palette-specific colors and names; .blend keeps the actual scene assignments'
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        st = context.scene.ps_state
        data = {
            'format': 'palette-studio', 'version': 2, 'color_space': 'srgb_hex',
            'palettes': [{
                'name': p.name,
                'colors': [{'uid': s.role_uid, 'name': s.name,
                            'hex': rgba_to_hex(s.color, True)} for s in p.swatches],
            } for p in st.palettes],
        }
        try:
            with open(self.filepath, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Exported {len(st.palettes)} palettes (scene links stay in .blend)')
        return {'FINISHED'}


def decode_palette_json(data):
    """Validate a portable library and return independent (name, colors) rows."""
    if not isinstance(data, dict) or data.get('format') != 'palette-studio':
        raise ValueError('Not a Palette Studio JSON library')
    version = data.get('version')
    raw_palettes = data.get('palettes')
    if version not in (1, 2) or not isinstance(raw_palettes, list) or len(raw_palettes) > 300:
        raise ValueError('Unsupported version or too many palettes')
    result = []
    if version == 1:
        raw_roles = data.get('roles')
        if not isinstance(raw_roles, list) or len(raw_roles) > 500:
            raise ValueError('Invalid v1 roles')
        roles = {}
        for role in raw_roles:
            if not isinstance(role, dict) or not isinstance(role.get('uid'), str) or not isinstance(role.get('name'), str):
                raise ValueError('Malformed v1 role')
            if not role['uid'] or not 0 < len(role['name'].strip()) <= 100:
                raise ValueError('Invalid v1 color name')
            roles[role['uid']] = role['name'].strip()
        for p in raw_palettes:
            if not isinstance(p, dict) or not isinstance(p.get('name'), str) or not isinstance(p.get('swatches'), dict):
                raise ValueError('Invalid v1 palette')
            colors = []
            for uid, text in p['swatches'].items():
                if uid not in roles or not isinstance(text, str):
                    raise ValueError('Unknown v1 swatch ID')
                colors.append((roles[uid], hex_to_rgba(text)))
            result.append((p['name'], colors))
    else:
        for p in raw_palettes:
            if not isinstance(p, dict) or not isinstance(p.get('name'), str) or not isinstance(p.get('colors'), list):
                raise ValueError('Invalid v2 palette')
            if len(p['colors']) > 500:
                raise ValueError('Too many colors in palette')
            colors = []
            for entry in p['colors']:
                if (not isinstance(entry, dict) or not isinstance(entry.get('name'), str)
                        or not isinstance(entry.get('hex'), str)):
                    raise ValueError('Invalid palette color')
                if not 0 < len(entry['name'].strip()) <= 100:
                    raise ValueError('Invalid color name')
                colors.append((entry['name'].strip(), hex_to_rgba(entry['hex'])))
            result.append((p['name'], colors))
    for name, _ in result:
        if not 0 < len(name.strip()) <= 100:
            raise ValueError('Invalid palette name')
    return result


class PS_OT_import_json(Operator, ImportHelper):
    bl_idname = 'ps.import_json'
    bl_label = 'Import Palette Library'
    bl_description = 'Create independent palettes from v1 or v2 JSON; scene bindings are not imported'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        st = context.scene.ps_state
        try:
            if Path(self.filepath).stat().st_size > 4_000_000:
                raise ValueError('Palette JSON exceeds 4 MB')
            with open(self.filepath, 'r', encoding='utf-8') as handle:
                decoded = decode_palette_json(json.load(handle))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        with suspend_callbacks():
            for name, colors in decoded:
                p = new_palette(st, name)
                for color_name, color in colors:
                    add_swatch(p, color_name, color)
            if decoded:
                st.active_palette = len(st.palettes) - len(decoded)
        update_hex_input(st)
        if decoded:
            apply_palette(context.scene, switch=True)
        self.report({'INFO'}, f'Imported {len(decoded)} independently editable palettes')
        return {'FINISHED'}


class PS_OT_export_gpl(Operator, ExportHelper):
    bl_idname = 'ps.export_gpl'
    bl_label = 'Export Active Palette (.gpl)'
    filename_ext = '.gpl'
    filter_glob: StringProperty(default='*.gpl', options={'HIDDEN'})

    def execute(self, context):
        p = selected_palette(context.scene.ps_state)
        if p is None:
            return {'CANCELLED'}
        try:
            with open(self.filepath, 'w', encoding='utf-8') as handle:
                handle.write(f'GIMP Palette\nName: {p.name}\nColumns: 4\n# Palette Studio\n')
                for swatch in p.swatches:
                    rgb = [int(round(max(0, min(1, linear_to_srgb(c))) * 255)) for c in swatch.color[:3]]
                    handle.write(f'{rgb[0]:3d} {rgb[1]:3d} {rgb[2]:3d}\t{swatch.name}\n')
        except OSError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class PS_OT_import_color_helper(Operator):
    bl_idname = 'ps.import_color_helper'
    bl_label = 'Import Color Helper Palettes'
    bl_description = 'Import each Color Helper palette with its own swatches, without linking scene objects'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, 'ch_palette_collection')

    def execute(self, context):
        st = context.scene.ps_state
        incoming = []
        for group in context.scene.ch_palette_collection:
            for palette in group.palettes:
                colors = [tuple(col.color) for col in palette.colors]
                incoming.append((f'{group.name} / {palette.name}' if group.name else palette.name, colors))
        if not incoming:
            self.report({'WARNING'}, 'No Color Helper swatches found in this scene')
            return {'CANCELLED'}
        if len(incoming) > 150 or max((len(colors) for _, colors in incoming), default=0) > 128:
            self.report({'ERROR'}, 'Importer limit: 150 palettes, 128 colors each')
            return {'CANCELLED'}
        with suspend_callbacks():
            for name, colors in incoming:
                p = new_palette(st, name)
                for i, color in enumerate(colors):
                    add_swatch(p, f'Color {i + 1:02d}', color)
            st.active_palette = len(st.palettes) - len(incoming)
        update_hex_input(st)
        apply_palette(context.scene, switch=True)
        self.report({'INFO'}, f'Imported {len(incoming)} independent palettes')
        return {'FINISHED'}


def matching_material_targets(context, scope, tolerance):
    swatch = selected_swatch(context.scene.ps_state)
    if swatch is None:
        return []
    source_rgb = [linear_to_srgb(c) for c in swatch.color[:3]]
    objs = context.selected_objects if scope == 'SELECTED' else context.scene.objects
    visited = set()
    found = []
    for obj in objs:
        for slot in getattr(obj, 'material_slots', []):
            mat = slot.material
            if mat is None or mat.as_pointer() in visited:
                continue
            visited.add(mat.as_pointer())
            target = base_color_target(mat)
            if target is None or target[3].is_linked:
                continue
            actual = [linear_to_srgb(c) for c in target[3].default_value[:3]]
            if max(abs(a - b) for a, b in zip(actual, source_rgb)) <= tolerance:
                found.append((mat.name, target))
    return found


class PS_OT_bind_matching(Operator):
    bl_idname = 'ps.bind_matching'
    bl_label = 'Find Matching Materials'
    bl_description = 'Find Base Colors that match selected swatch and add them to this palette'
    bl_options = {'REGISTER', 'UNDO'}
    scope: EnumProperty(name='Search', items=(
        ('SELECTED', 'Selected Objects', 'Search selected object materials only'),
        ('SCENE', 'Entire Scene', 'Search materials in this scene'),
    ), default='SELECTED')
    tolerance: FloatProperty(name='sRGB Color Tolerance', default=.002, min=0, max=.10, precision=3)

    @classmethod
    def poll(cls, context):
        return is_ready(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        self.layout.prop(self, 'scope')
        self.layout.prop(self, 'tolerance')
        matches = matching_material_targets(context, self.scope, self.tolerance)
        self.layout.label(text=f'{len(matches)} matching materials', icon='VIEWZOOM')
        for name, _ in matches[:8]:
            self.layout.label(text=name, icon='MATERIAL')
        if len(matches) > 8:
            self.layout.label(text=f'...and {len(matches) - 8} others')
        self.layout.label(text='No existing node links will be disconnected.', icon='INFO')

    def execute(self, context):
        matches = matching_material_targets(context, self.scope, self.tolerance)
        uid = active_role_uid(context.scene.ps_state)
        created = reused = skipped = 0
        for _, (tree, node, idx, _) in matches:
            result, fresh = link_node_socket(context.scene, uid, tree, node, 'INPUT', idx)
            if result.status in {'APPLIED', 'UNCHANGED'}:
                created += int(fresh)
                reused += int(not fresh)
            else:
                skipped += 1
        self.report({'INFO'} if not skipped else {'WARNING'},
                    f'{created} linked, {reused} reassigned, {skipped} skipped')
        return {'FINISHED'} if created + reused else {'CANCELLED'}


class PS_OT_capture_assigned(Operator):
    bl_idname = 'ps.capture_assigned'
    bl_label = 'Capture Colors from Linked Scene'
    bl_description = 'Read a role color from linked targets; ambiguous roles with different target colors are skipped'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = context.scene.ps_state
        p = selected_palette(st)
        updated = ambiguous = missing = 0
        for swatch in p.swatches:
            values = []
            for b in p.bindings:
                if b.role_uid != swatch.role_uid or not b.enabled:
                    continue
                status, color = target_color(b)
                if status == 'OK':
                    values.append(tuple(color))
            if not values:
                missing += 1
                continue
            # Do not silently choose one from multiple inconsistent target colors.
            if any(max(abs(a - b) for a, b in zip(values[0][:3], v[:3])) > 0.0001 for v in values[1:]):
                ambiguous += 1
                continue
            with suspend_callbacks():
                swatch.color = values[0]
            updated += 1
        update_hex_input(st)
        st.last_status = f'Captured {updated} colors; {ambiguous} ambiguous, {missing} without targets'
        self.report({'WARNING'} if ambiguous else {'INFO'}, st.last_status)
        return {'FINISHED'}


class PS_OT_migrate(Operator):
    bl_idname = 'ps.migrate'
    bl_label = 'Upgrade Previous Palette Data'
    bl_description = 'Copy previous global roles/links into each independent palette'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        changed = migrate_scene(context.scene)
        self.report({'INFO'}, 'Existing palettes upgraded' if changed else 'Data already up to date')
        return {'FINISHED'}


CLASSES = (
    PS_OT_initialize, PS_OT_select_palette, PS_OT_new_palette,
    PS_OT_duplicate_palette, PS_OT_delete_palette, PS_OT_rename_palette,
    PS_OT_create_variant, PS_OT_select_role, PS_OT_add_role,
    PS_OT_rename_role, PS_OT_move_role, PS_OT_delete_role,
    PS_OT_set_hex, PS_OT_clipboard_hex, PS_OT_apply, PS_OT_bind_quick,
    PS_OT_sample_quick, PS_OT_bind_node_socket, PS_OT_bind_ramp_stop,
    PS_OT_remove_binding, PS_OT_reassign_binding, PS_OT_validate,
    PS_OT_copy_targets, PS_OT_export_json, PS_OT_import_json,
    PS_OT_export_gpl, PS_OT_import_color_helper, PS_OT_bind_matching,
    PS_OT_capture_assigned, PS_OT_migrate,
)
