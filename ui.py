# SPDX-License-Identifier: GPL-3.0-or-later
"""Compact workflow UI: palette > its colors > its targets > apply."""
import bpy
from bpy.types import Menu, Panel, UIList

from .bindings import binding_label, is_color_socket, locate
from .color_math import rgba_to_hex
from .model import selected_palette, selected_swatch


def label_for_uid(palette, uid):
    swatch = next((s for s in palette.swatches if s.role_uid == uid), None)
    return swatch.name if swatch else 'Missing Color'


class PS_MT_choose_role(Menu):
    bl_idname = 'PS_MT_choose_role'
    bl_label = 'Color in This Palette'

    def draw(self, context):
        palette = selected_palette(context.scene.ps_state)
        if palette:
            for i, swatch in enumerate(palette.swatches):
                op = self.layout.operator('ps.select_role', text=swatch.name, icon='COLOR')
                op.index = i
        if not palette or not palette.swatches:
            self.layout.label(text='Add a color to this palette')


class PS_MT_choose_palette(Menu):
    bl_idname = 'PS_MT_choose_palette'
    bl_label = 'Select Scene Palette'

    def draw(self, context):
        st = context.scene.ps_state
        for i, palette in enumerate(st.palettes):
            op = self.layout.operator('ps.select_palette',
                                      text=f'{i + 1:02d}  {palette.name}', icon='COLOR')
            op.index = i


class PS_UL_palettes(UIList):
    bl_idname = 'PS_UL_palettes'

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=f'{index + 1:02d}  {item.name}', icon='COLOR')
        row.label(text=f'{len(item.swatches)}c / {len(item.bindings)}t')


class PS_UL_swatches(UIList):
    bl_idname = 'PS_UL_swatches'

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.name, icon='COLOR')
        preview = row.row(align=True)
        preview.ui_units_x = 4.5
        preview.prop(item, 'color', text='')
        count = sum(b.role_uid == item.role_uid for b in data.bindings)
        if count:
            row.label(text=f'{count}', icon='LINKED')

    def filter_items(self, context, data, propname):
        needle = self.filter_name.strip().casefold()
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item if not needle or needle in s.name.casefold() else 0
                 for s in items]
        return flags, []


class PS_UL_bindings(UIList):
    bl_idname = 'PS_UL_bindings'

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        status, _, _ = locate(item)
        icon = 'ERROR' if status == 'MISSING' else 'UNLINKED' if status == 'BLOCKED' else 'LINKED'
        row = layout.row(align=True)
        row.label(text=binding_label(item), icon=icon)
        row.prop(item, 'enabled', text='')

    def filter_items(self, context, data, propname):
        st = context.scene.ps_state
        palette = selected_palette(st)
        chosen = selected_swatch(st)
        needle = self.filter_name.strip().casefold()
        flags = []
        for b in getattr(data, propname):
            matches_role = (st.link_filter != 'ROLE' or (chosen and b.role_uid == chosen.role_uid))
            matches_text = (not needle or needle in binding_label(b).casefold() or
                            needle in label_for_uid(palette, b.role_uid).casefold())
            flags.append(self.bitflag_filter_item if matches_role and matches_text else 0)
        return flags, []


def section(layout, heading, icon, id_name, closed=False):
    header, body = layout.panel(id_name, default_closed=closed)
    header.label(text=heading, icon=icon)
    return body


def palette_controls(layout, context):
    st = context.scene.ps_state
    palette = selected_palette(st)
    if palette is None:
        return
    row = layout.row(align=True)
    row.operator('ps.select_palette', text='', icon='BACK').direction = -1
    row.menu('PS_MT_choose_palette', text=palette.name, icon='COLOR')
    row.operator('ps.select_palette', text='', icon='FORWARD').direction = 1
    row = layout.row(align=True)
    row.scale_y = 1.15
    row.operator('ps.apply', text='APPLY SCENE PALETTE', icon='CHECKMARK')
    row = layout.row(align=True)
    row.prop(st, 'apply_on_switch', text='Auto Switch', toggle=True)
    row.prop(st, 'live_update', text='Live Color Edit', toggle=True)
    layout.prop(st, 'switch_unassigned', text='Unassigned Targets')
    layout.label(text=f'{len(palette.swatches)} colors   /   {len(palette.bindings)} targets   /   '
                      f'{len(st.palettes)} palettes')
    if st.last_applied_uid and st.last_applied_uid != palette.uid:
        layout.label(text='Selected palette not applied yet', icon='INFO')


def palette_manager(layout, context):
    st = context.scene.ps_state
    body = section(layout, '05  PALETTE MANAGEMENT', 'COLOR', 'ps_03_palette_manager', closed=True)
    if body is None:
        return
    row = body.row(align=True)
    row.template_list('PS_UL_palettes', '', st, 'palettes', st, 'active_palette', rows=3)
    tools = row.column(align=True)
    tools.operator('ps.new_palette', text='', icon='ADD')
    tools.operator('ps.duplicate_palette', text='', icon='DUPLICATE')
    tools.operator('ps.rename_palette', text='', icon='GREASEPENCIL')
    tools.operator('ps.delete_palette', text='', icon='REMOVE')
    copy_row = body.row()
    copy_row.enabled = len(st.palettes) > 1
    copy_row.operator('ps.copy_targets', text='Copy Targets from Another Palette', icon='LINKED')
    variants = body.row(align=True)
    for name, label in (('WARM', 'Warm'), ('COOL', 'Cool'), ('MUTED', 'Muted')):
        variants.operator('ps.create_variant', text=label).style = name
    variants = body.row(align=True)
    variants.operator('ps.create_variant', text='Darker').style = 'DARK'
    variants.operator('ps.create_variant', text='Lighter').style = 'LIGHT'
    body.label(text='Duplicate / variations keep the same scene targets.', icon='INFO')


def swatch_manager(layout, context):
    st = context.scene.ps_state
    p = selected_palette(st)
    body = section(layout, f'01  COLORS / {p.name.upper()}', 'BRUSH_DATA', 'ps_03_color_manager')
    if body is None:
        return
    row = body.row(align=True)
    row.template_list('PS_UL_swatches', '', p, 'swatches', p, 'active_swatch', rows=6)
    tools = row.column(align=True)
    tools.operator('ps.add_role', icon='ADD', text='')
    tools.operator('ps.rename_role', icon='GREASEPENCIL', text='')
    tools.operator('ps.delete_role', icon='REMOVE', text='')
    tools.separator()
    tools.operator('ps.move_role', text='', icon='TRIA_UP').direction = -1
    tools.operator('ps.move_role', text='', icon='TRIA_DOWN').direction = 1
    swatch = selected_swatch(st)
    if swatch:
        sub = body.box()
        sub.label(text=f'COLOR  {swatch.name}', icon='COLOR')
        row = sub.row(align=True)
        row.prop(st, 'hex_input', text='HEX')
        row.operator('ps.set_hex', text='', icon='CHECKMARK')
        row = sub.row(align=True)
        row.label(text=rgba_to_hex(swatch.color))
        row.operator('ps.clipboard_hex', text='Copy HEX', icon='COPYDOWN').mode = 'COPY'
        row.operator('ps.clipboard_hex', text='Paste', icon='PASTEDOWN').mode = 'PASTE'
    else:
        body.operator('ps.add_role', text='Add First Color', icon='ADD')
    body.label(text='Colors and names belong ONLY to this palette.', icon='INFO')


def _color_row(body, label, source, prop, bind_kind, sample_kind=None, linked=False):
    """A Blender color picker and one-click bind action on the SAME row."""
    row = body.row(align=True)
    row.label(text=label)
    picker = row.row(align=True)
    picker.ui_units_x = 3.6
    if source is not None and not linked:
        picker.prop(source, prop, text='')
    else:
        picker.label(text='Connected' if linked else 'Unavailable', icon='UNLINKED')
    action = row.row(align=True)
    action.enabled = not linked and source is not None
    action.operator('ps.bind_quick', text='', icon='LINKED').kind = bind_kind
    if sample_kind:
        action.operator('ps.sample_quick', text='', icon='EYEDROPPER').kind = sample_kind


def link_target_panel(layout, context):
    st = context.scene.ps_state
    swatch = selected_swatch(st)
    body = section(layout, '02  LINK COLORS', 'LINKED', 'ps_03_quicklink')
    if body is None:
        return
    if swatch is None:
        body.label(text='Add or select a palette color first.', icon='INFO')
        return
    row = body.row(align=True)
    row.label(text='Selected swatch')
    row.menu('PS_MT_choose_role', text=swatch.name, icon='COLOR')
    body.label(text='Color chip = edit. Chain = link. Dropper = sample.')
    body.label(text='Or right-click a Blender color chip to link it.', icon='INFO')

    obj = context.active_object
    if obj is not None:
        body.label(text=f'OBJECT  {obj.name}', icon='OBJECT_DATA')
        mat = getattr(obj, 'active_material', None)
        if mat is not None:
            from .ops import base_color_target
            target = base_color_target(mat)
            if target is not None:
                socket = target[3]
                _color_row(body, 'Shader Base', socket, 'default_value', 'ACTIVE_BASE',
                           'ACTIVE_BASE', linked=socket.is_linked)
            _color_row(body, 'Material View', mat, 'diffuse_color', 'ACTIVE_VIEWPORT',
                       'ACTIVE_VIEWPORT')
        else:
            body.label(text='No active material (assign one to link Base Color).')
        row = body.row(align=True)
        row.label(text='Object Display')
        preview = row.row(align=True)
        preview.ui_units_x = 3.6
        preview.prop(obj, 'color', text='')
        row.operator('ps.bind_quick', text='', icon='LINKED').kind = 'ACTIVE_OBJECT'
        row.operator('ps.sample_quick', text='', icon='EYEDROPPER').kind = 'OBJECT'
        if obj.type == 'LIGHT' and obj.data is not None:
            _color_row(body, 'Light Color', obj.data, 'color', 'ACTIVE_LIGHT', 'ACTIVE_LIGHT')

    world = context.scene.world
    if world is not None:
        if world.use_nodes:
            from .ops import world_background_target
            target = world_background_target(world)
            if target is not None:
                socket = target[3]
                _color_row(body, 'World BG', socket, 'default_value', 'WORLD_BACKGROUND',
                           'WORLD_BACKGROUND', linked=socket.is_linked)
        else:
            _color_row(body, 'World Color', world, 'color', 'WORLD_BACKGROUND',
                       'WORLD_BACKGROUND')



def batch_link_panel(layout, context):
    body = section(layout, '04  BATCH LINK', 'MOD_ARRAY', 'ps_03_batch', closed=True)
    if body is None:
        return
    body.label(text='Apply the selected swatch to multiple targets.')
    body.operator('ps.bind_quick', text='Selected Materials / Base Color',
                  icon='MATERIAL').kind = 'ALL_MATERIALS'
    body.operator('ps.bind_quick', text='Selected Object Display Colors',
                  icon='OBJECT_DATA').kind = 'SELECTED_OBJECTS'
    body.operator('ps.bind_quick', text='Selected Light Colors',
                  icon='LIGHT').kind = 'SELECTED_LIGHTS'
    body.operator('ps.bind_matching', text='Find Similar Material Colors', icon='VIEWZOOM')


def node_target_panel(layout, context):
    if context.area.type != 'NODE_EDITOR':
        return
    tree = getattr(context.space_data, 'edit_tree', None)
    if tree is None:
        return
    body = section(layout, '03  SELECTED NODE COLORS', 'NODE_MATERIAL', 'ps_03_nodes')
    if body is None:
        return
    if selected_swatch(context.scene.ps_state) is None:
        body.label(text='Select a palette color first.')
        return
    selected = [node for node in tree.nodes if node.select]
    selected.sort(key=lambda node: (0 if tree.nodes.active == node else 1, node.name))
    if not selected:
        body.label(text='Select a node to see its editable color inputs.', icon='INFO')
        return
    for node in selected[:6]:
        group = body.box()
        group.label(text=node.label or node.name, icon='NODE')
        count = 0
        for direction, sockets in (('INPUT', node.inputs), ('OUTPUT', node.outputs)):
            for index, sock in enumerate(sockets):
                if not is_color_socket(sock):
                    continue
                count += 1
                locked = ((direction == 'INPUT' and sock.is_linked) or
                          sock.is_property_readonly('default_value'))
                row = group.row(align=True)
                row.label(text=f'{sock.name} ({"in" if direction == "INPUT" else "out"})')
                if locked:
                    row.label(text='Linked / Read-only', icon='UNLINKED')
                    continue
                picker = row.row(align=True)
                picker.ui_units_x = 3.6
                picker.prop(sock, 'default_value', text='')
                op = row.operator('ps.bind_node_socket', text='', icon='LINKED')
                op.node_name, op.direction, op.socket_index = node.name, direction, index
                op = row.operator('ps.bind_node_socket', text='', icon='EYEDROPPER')
                op.node_name, op.direction, op.socket_index, op.sample = node.name, direction, index, True
        ramp = getattr(node, 'color_ramp', None)
        if ramp:
            for index, stop in enumerate(ramp.elements):
                count += 1
                row = group.row(align=True)
                row.label(text=f'Stop {index + 1} ({stop.position:.2f})')
                picker = row.row(align=True)
                picker.ui_units_x = 3.6
                picker.prop(stop, 'color', text='')
                op = row.operator('ps.bind_ramp_stop', text='', icon='LINKED')
                op.node_name, op.ramp_index = node.name, index
                op = row.operator('ps.bind_ramp_stop', text='', icon='EYEDROPPER')
                op.node_name, op.ramp_index, op.sample = node.name, index, True
        if not count:
            group.label(text='No RGBA color inputs on this node')
    if len(selected) > 6:
        body.label(text=f'Showing 6 of {len(selected)} selected nodes')
    body.label(text='Each color socket can have a different swatch per palette.')


def binding_manager(layout, context):
    st = context.scene.ps_state
    p = selected_palette(st)
    body = section(layout, f'06  ASSIGNMENTS / {p.name.upper()}', 'OUTLINER_DATA_LIGHT',
                   'ps_03_links', closed=True)
    if body is None:
        return
    row = body.row(align=True)
    row.prop(st, 'link_filter', expand=True)
    row = body.row(align=True)
    row.template_list('PS_UL_bindings', '', p, 'bindings', p, 'active_binding', rows=5)
    tools = row.column(align=True)
    tools.operator('ps.validate', text='', icon='VIEWZOOM')
    tools.operator('ps.remove_binding', text='', icon='UNLINKED').restore = False
    tools.operator('ps.remove_binding', text='', icon='LOOP_BACK').restore = True
    if 0 <= p.active_binding < len(p.bindings):
        binding = p.bindings[p.active_binding]
        detail = body.box()
        detail.label(text=binding_label(binding))
        detail.label(text=f'Color: {label_for_uid(p, binding.role_uid)}')
        status, _, _ = locate(binding)
        detail.label(text=f'Status: {status.title()}')
        row = detail.row(align=True)
        row.prop(binding, 'enabled')
        row.prop(binding, 'affect_alpha', text='Alpha')
        detail.operator('ps.reassign_binding', text='Use Selected Color Here', icon='LINKED')
        row = detail.row(align=True)
        row.operator('ps.remove_binding', text='Unassign', icon='UNLINKED').restore = False
        row.operator('ps.remove_binding', text='Restore + Unassign', icon='LOOP_BACK').restore = True
        body.operator('ps.capture_assigned', text='Capture Linked Scene Colors', icon='EYEDROPPER')
    else:
        body.label(text='No targets assigned in this palette.', icon='INFO')
    body.label(text='Other palettes keep their own target assignments.')


def library_manager(layout, context):
    body = section(layout, '07  BACKUP / IMPORT', 'FILE_FOLDER', 'ps_03_library', closed=True)
    if body is None:
        return
    body.label(text='Assignments save inside the .blend file.')
    row = body.row(align=True)
    row.operator('ps.export_json', text='Export Colors', icon='EXPORT')
    row.operator('ps.import_json', text='Import Colors', icon='IMPORT')
    body.operator('ps.export_gpl', text='Export Active .gpl', icon='EXPORT')
    body.label(text='JSON saves colors/names, not target links.', icon='INFO')
    if hasattr(context.scene, 'ch_palette_collection'):
        body.operator('ps.import_color_helper', text='Import from Color Helper', icon='IMPORT')


def draw_palette_studio(layout, context):
    st = context.scene.ps_state
    if st.palettes and st.data_version < 2:
        layout.label(text='Previous palette data detected.', icon='INFO')
        layout.operator('ps.migrate', text='Upgrade to Independent Palettes', icon='FILE_REFRESH')
        return
    if not st.palettes:
        layout.label(text='PALETTE STUDIO', icon='COLOR')
        layout.label(text='Build scene looks with independent colors and targets.')
        layout.operator('ps.initialize', text='Start With 4 Example Palettes', icon='COLOR').mode = 'STARTER'
        layout.operator('ps.initialize', text='Start Empty', icon='ADD').mode = 'EMPTY'
        layout.label(text='Nothing in the scene changes until linked.')
        return
    palette_controls(layout, context)
    layout.separator()
    swatch_manager(layout, context)
    link_target_panel(layout, context)
    node_target_panel(layout, context)
    batch_link_panel(layout, context)
    palette_manager(layout, context)
    binding_manager(layout, context)
    library_manager(layout, context)
    layout.separator()
    layout.label(text=st.last_status, icon='INFO')


class PS_PT_view3d(Panel):
    bl_idname = 'PS_PT_view3d'
    bl_label = 'Palette Studio'
    bl_category = 'Palette'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    def draw(self, context):
        draw_palette_studio(self.layout, context)


class PS_PT_node_editor(Panel):
    bl_idname = 'PS_PT_node_editor'
    bl_label = 'Palette Studio'
    bl_category = 'Palette'
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(space and space.tree_type in {'ShaderNodeTree', 'CompositorNodeTree',
                                                  'GeometryNodeTree'})

    def draw(self, context):
        draw_palette_studio(self.layout, context)


CLASSES = (
    PS_MT_choose_role, PS_MT_choose_palette, PS_UL_palettes, PS_UL_swatches,
    PS_UL_bindings, PS_PT_view3d, PS_PT_node_editor,
)
