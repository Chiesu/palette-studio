# SPDX-License-Identifier: GPL-3.0-or-later
"""Reversible, palette-scoped links to Blender color properties.

IMPORTANT: Material/Light/World/Scene/Texture node trees are embedded IDs.
Blender does NOT allow them in a PointerProperty. Store their owning ID
instead, and dereference `.node_tree` when the binding is evaluated.
Only standalone node groups may be stored as NodeTree pointers.
"""
from dataclasses import dataclass
from uuid import uuid4

import bpy

from .color_math import nearly_equal
from .model import find_swatch, selected_palette, suspend_callbacks

NODE_UID_KEY = '_palette_studio_node_uid'
TREE_OWNERS = (
    ('MATERIAL', 'material_ref', 'materials'),
    ('LIGHT', 'light_ref', 'lights'),
    ('WORLD', 'world_ref', 'worlds'),
    ('SCENE', 'scene_ref', 'scenes'),
    ('TEXTURE', 'texture_ref', 'textures'),
)


@dataclass
class Result:
    status: str
    message: str = ''


@dataclass
class SyncStats:
    applied: int = 0
    unchanged: int = 0
    restored: int = 0
    blocked: int = 0
    missing: int = 0
    failed: int = 0
    skipped: int = 0

    def short(self):
        text = f'{self.applied} updated, {self.unchanged} already current'
        if self.restored:
            text += f', {self.restored} reset to original'
        if self.blocked:
            text += f', {self.blocked} connected/blocked'
        if self.missing:
            text += f', {self.missing} missing'
        if self.failed:
            text += f', {self.failed} errors'
        return text


def _identity(item):
    if item is None:
        return None
    try:
        return item.as_pointer()
    except (AttributeError, ReferenceError):
        return id(item)


def _matches(a, b):
    return a is not None and b is not None and _identity(a) == _identity(b)


def is_color_socket(socket):
    if getattr(socket, 'type', None) != 'RGBA':
        return False
    try:
        return len(socket.default_value) == 4
    except (AttributeError, TypeError, ValueError):
        return False


def ensure_node_uid(node):
    """Node names can change; custom UUIDs survive node renames."""
    try:
        current = node.get(NODE_UID_KEY)
        if current:
            tree = getattr(node, 'id_data', None)
            matches = sum(1 for n in getattr(tree, 'nodes', ()) if n.get(NODE_UID_KEY) == current)
            if matches <= 1:
                return str(current)
        uid = uuid4().hex
        node[NODE_UID_KEY] = uid
        return uid
    except (TypeError, RuntimeError, AttributeError):
        return ''


def _owner_tree(candidate):
    """Read through owner, never persist an embedded NodeTree itself."""
    try:
        return getattr(candidate, 'node_tree', None) if candidate is not None else None
    except (ReferenceError, AttributeError):
        return None


def _owner_descriptor(owner):
    for kind, field, _ in TREE_OWNERS:
        # Check actual RNA classes (not the coincidentally named datablock).
        cls = getattr(bpy.types, kind.title(), None)
        if cls is not None and isinstance(owner, cls):
            return {'tree_owner': kind, field: owner}
    return None


def tree_descriptor(tree, owner_hint=None):
    """Stable RNA fields to reference a tree, without embedded ID pointers."""
    if tree is None:
        return None
    if owner_hint is not None and _matches(_owner_tree(owner_hint), tree):
        descriptor = _owner_descriptor(owner_hint)
        if descriptor:
            return descriptor

    # Node groups are independent IDs. Material.node_tree etc. are not.
    try:
        if not getattr(tree, 'is_embedded_data', False):
            groups = getattr(bpy.data, 'node_groups', ())
            if any(_matches(group, tree) for group in groups):
                return {'tree_owner': 'GROUP', 'tree_ref': tree}
    except (ReferenceError, RuntimeError):
        pass

    # Prefer the ownership hint; fall back to a one-time lookup when linking.
    # Once linked, apply uses ONLY the saved owner (no repeated scene scans).
    for kind, field, collection in TREE_OWNERS:
        for owner in getattr(bpy.data, collection, ()):
            if _matches(_owner_tree(owner), tree):
                return {'tree_owner': kind, field: owner}

    # Allow external, standalone NodeTrees even if not in bpy.data.node_groups.
    # An embedded ID must never reach this path.
    if not getattr(tree, 'is_embedded_data', True):
        return {'tree_owner': 'GROUP', 'tree_ref': tree}
    return None


def resolve_tree(binding):
    kind = getattr(binding, 'tree_owner', 'LEGACY')
    if kind == 'GROUP':
        return getattr(binding, 'tree_ref', None)
    for owner_kind, field, _ in TREE_OWNERS:
        if kind == owner_kind:
            return _owner_tree(getattr(binding, field, None))
    # A saved 0.2 binding to a standalone group may have no owner enum yet.
    return getattr(binding, 'tree_ref', None)


def _ref_tree(ref):
    kind = ref.get('tree_owner', 'LEGACY')
    if kind == 'GROUP' or kind == 'LEGACY':
        return ref.get('tree_ref')
    for owner_kind, field, _ in TREE_OWNERS:
        if kind == owner_kind:
            return _owner_tree(ref.get(field))
    return None


def resolve_node(binding):
    tree = resolve_tree(binding)
    if tree is None:
        return None
    try:
        candidate = tree.nodes.get(binding.node_name)
        if candidate is not None and (not binding.node_uid or
                                      candidate.get(NODE_UID_KEY) == binding.node_uid):
            return candidate
        if binding.node_uid:
            matches = [n for n in tree.nodes if n.get(NODE_UID_KEY) == binding.node_uid]
            if len(matches) == 1:
                return matches[0]
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    return None


def resolve_socket(binding, node):
    sockets = node.inputs if binding.direction == 'INPUT' else node.outputs
    idx = binding.socket_index
    if 0 <= idx < len(sockets):
        sock = sockets[idx]
        if (binding.socket_id and sock.identifier == binding.socket_id) or (
                not binding.socket_id and sock.name == binding.socket_name):
            return sock
    if binding.socket_id:
        found = [s for s in sockets if s.identifier == binding.socket_id]
    else:
        found = [s for s in sockets if s.name == binding.socket_name]
    return found[0] if len(found) == 1 else None


def locate(binding):
    """Return (status, target, property). NEVER edits the node graph."""
    try:
        kind = binding.kind
        simple = {
            'OBJECT_COLOR': ('object_ref', 'color'),
            'MATERIAL_COLOR': ('material_ref', 'diffuse_color'),
            'LIGHT_COLOR': ('light_ref', 'color'),
            'WORLD_COLOR': ('world_ref', 'color'),
        }
        if kind in simple:
            field, prop = simple[kind]
            target = getattr(binding, field)
            return ('OK', target, prop) if target else ('MISSING', None, None)
        if kind in ('NODE_SOCKET', 'RAMP_STOP'):
            node = resolve_node(binding)
            if node is None:
                return 'MISSING', None, None
            if kind == 'RAMP_STOP':
                ramp = getattr(node, 'color_ramp', None)
                i = binding.ramp_index
                if ramp is None or not 0 <= i < len(ramp.elements):
                    return 'MISSING', None, None
                return 'OK', ramp.elements[i], 'color'
            socket = resolve_socket(binding, node)
            if socket is None or not is_color_socket(socket):
                return 'MISSING', None, None
            if binding.direction == 'INPUT' and socket.is_linked:
                return 'BLOCKED', socket, 'default_value'
            return 'OK', socket, 'default_value'
    except (ReferenceError, AttributeError, RuntimeError):
        pass
    return 'MISSING', None, None


def target_token(binding):
    _, target, prop = locate(binding)
    return (_identity(target), prop) if target is not None else None


def target_color(binding):
    status, target, prop = locate(binding)
    if status != 'OK':
        return status, None
    try:
        values = tuple(getattr(target, prop))
        if len(values) not in (3, 4):
            return 'MISSING', None
        return 'OK', values + (1.0,) if len(values) == 3 else values
    except (TypeError, AttributeError, ReferenceError, RuntimeError):
        return 'MISSING', None


def _set_color(target, prop, rgba, affect_alpha=False):
    try:
        before = tuple(getattr(target, prop))
        if len(before) == 3:
            new = tuple(rgba[:3])
        elif len(before) == 4:
            new = tuple(rgba[:3]) + (rgba[3] if affect_alpha else before[3],)
        else:
            return Result('MISSING', 'Unsupported target color dimension')
        if nearly_equal(before, new):
            return Result('UNCHANGED')
        setattr(target, prop, new)
        return Result('APPLIED')
    except (ValueError, TypeError, AttributeError, ReferenceError, RuntimeError) as exc:
        return Result('FAILED', str(exc)[:180])


def apply_one_binding(binding, color):
    if not binding.enabled:
        return Result('SKIPPED', 'Assignment disabled')
    status, target, prop = locate(binding)
    if status == 'BLOCKED':
        return Result('BLOCKED', 'Input is connected; existing node links are protected')
    if status != 'OK':
        return Result('MISSING', 'Color target was removed or changed')
    return _set_color(target, prop, color, binding.affect_alpha)


def restore_binding(binding):
    status, target, prop = locate(binding)
    if status != 'OK':
        return Result(status, 'Cannot restore missing or externally connected target')
    return _set_color(target, prop, tuple(binding.original_color), affect_alpha=True)


def exclusive_bindings(previous, incoming):
    next_tokens = {target_token(b) for b in incoming.bindings if b.enabled}
    next_tokens.discard(None)
    return [b for b in previous.bindings if b.enabled and target_token(b) not in next_tokens]


def restore_exclusive(previous, incoming):
    restored = 0
    for binding in exclusive_bindings(previous, incoming):
        if restore_binding(binding).status == 'APPLIED':
            restored += 1
    return restored


def apply_palette(scene, only_role_uid=None, update_status=True, switch=False):
    state = scene.ps_state
    palette = selected_palette(state)
    stats = SyncStats()
    if palette is None:
        return stats
    if switch and state.switch_unassigned == 'RESTORE' and state.last_applied_uid != palette.uid:
        previous = next((p for p in state.palettes if p.uid == state.last_applied_uid), None)
        if previous is not None:
            stats.restored = restore_exclusive(previous, palette)
    colors = {s.role_uid: tuple(s.color) for s in palette.swatches}
    for binding in palette.bindings:
        if only_role_uid is not None and binding.role_uid != only_role_uid:
            continue
        rgba = colors.get(binding.role_uid)
        if rgba is None:
            stats.missing += 1
            continue
        result = apply_one_binding(binding, rgba)
        field = {'APPLIED': 'applied', 'UNCHANGED': 'unchanged', 'BLOCKED': 'blocked',
                 'MISSING': 'missing', 'FAILED': 'failed', 'SKIPPED': 'skipped'}[result.status]
        setattr(stats, field, getattr(stats, field) + 1)
    state.last_applied_uid = palette.uid
    if update_status:
        state.last_status = stats.short()
    return stats


def _match(binding, kind, **ref):
    if binding.kind != kind:
        return False
    simple = {
        'OBJECT_COLOR': 'object_ref', 'MATERIAL_COLOR': 'material_ref',
        'LIGHT_COLOR': 'light_ref', 'WORLD_COLOR': 'world_ref',
    }
    if kind in simple:
        field = simple[kind]
        return _matches(getattr(binding, field), ref.get(field))
    if kind in ('NODE_SOCKET', 'RAMP_STOP'):
        if not _matches(resolve_tree(binding), _ref_tree(ref)):
            return False
        uid = ref.get('node_uid')
        if binding.node_uid and uid:
            if binding.node_uid != uid:
                return False
        elif binding.node_name != ref.get('node_name'):
            return False
        if kind == 'RAMP_STOP':
            return binding.ramp_index == ref.get('ramp_index')
        return (binding.direction == ref.get('direction') and
                binding.socket_index == ref.get('socket_index') and
                binding.socket_id == ref.get('socket_id'))
    return False


def binding_ref(binding):
    """Safe, copyable target descriptor. Owners never contain embedded IDs."""
    keys = ('object_ref', 'material_ref', 'light_ref', 'world_ref', 'scene_ref',
            'texture_ref', 'tree_owner', 'tree_ref', 'node_uid', 'node_name',
            'direction', 'socket_id', 'socket_name', 'socket_index', 'ramp_index')
    result = {key: getattr(binding, key) for key in keys}
    if result['tree_ref'] is not None and getattr(result['tree_ref'], 'is_embedded_data', False):
        # Legacy data: resolve to a safe owner before copying.
        result.pop('tree_ref')
        descriptor = tree_descriptor(resolve_tree(binding))
        if descriptor:
            result.update(descriptor)
    return result


def _existing_in_palette(palette, kind, ref):
    return next((b for b in palette.bindings if _match(b, kind, **ref)), None)


def _original_from_other_palettes(state, target):
    token = target_token(target)
    if token is None:
        return None
    for palette in state.palettes:
        for binding in palette.bindings:
            if binding.as_pointer() == target.as_pointer():
                continue
            if target_token(binding) == token:
                return tuple(binding.original_color)
    return None


def upsert_binding(scene, role_uid, kind, apply_now=True, **ref):
    """Add/reassign in active palette. Other palettes are not modified."""
    state = scene.ps_state
    palette = selected_palette(state)
    swatch = find_swatch(palette, role_uid)
    if swatch is None:
        return Result('FAILED', 'Select a color in the active palette'), False
    existing = _existing_in_palette(palette, kind, ref)
    if existing is None:
        with suspend_callbacks():
            binding = palette.bindings.add()
            binding.kind = kind
            binding.role_uid = role_uid
            try:
                for key, value in ref.items():
                    if hasattr(binding, key):
                        setattr(binding, key, value)
                status, original = target_color(binding)
                if status == 'OK':
                    inherited = _original_from_other_palettes(state, binding)
                    binding.original_color = inherited if inherited is not None else original
            except (RuntimeError, AttributeError, TypeError, ValueError) as exc:
                palette.bindings.remove(len(palette.bindings) - 1)
                return Result('FAILED', 'Cannot store this target: ' + str(exc)[:140]), False
        if status != 'OK':
            palette.bindings.remove(len(palette.bindings) - 1)
            message = ('This input is connected to another node; links were not changed' if
                       status == 'BLOCKED' else 'Color target is no longer available')
            return Result(status, message), False
        created = True
    else:
        binding = existing
        created = False
        old_uid, old_enabled = binding.role_uid, binding.enabled
        with suspend_callbacks():
            binding.role_uid = role_uid
            binding.enabled = True
            for key in ('node_name', 'node_uid', 'socket_name'):
                if key in ref:
                    setattr(binding, key, ref[key])
    if not apply_now:
        return Result('UNCHANGED', 'Assignment recorded'), created
    result = apply_one_binding(binding, swatch.color)
    if result.status == 'FAILED':
        if created:
            palette.bindings.remove(len(palette.bindings) - 1)
        else:
            with suspend_callbacks():
                binding.role_uid = old_uid
                binding.enabled = old_enabled
    else:
        state.last_applied_uid = palette.uid
    return result, created


def binding_label(binding):
    try:
        if binding.kind == 'OBJECT_COLOR':
            return f'{binding.object_ref.name} / Object Color' if binding.object_ref else 'Missing Object'
        if binding.kind == 'MATERIAL_COLOR':
            return f'{binding.material_ref.name} / Viewport' if binding.material_ref else 'Missing Material'
        if binding.kind == 'LIGHT_COLOR':
            return f'{binding.light_ref.name} / Light Color' if binding.light_ref else 'Missing Light'
        if binding.kind == 'WORLD_COLOR':
            return f'{binding.world_ref.name} / World Color' if binding.world_ref else 'Missing World'
        tree = resolve_tree(binding)
        if tree:
            node = resolve_node(binding)
            node_name = node.name if node else f'{binding.node_name} [missing]'
            if binding.kind == 'RAMP_STOP':
                return f'{tree.name} / {node_name} / Stop {binding.ramp_index + 1}'
            return f'{tree.name} / {node_name} / {binding.socket_name}'
    except (ReferenceError, AttributeError):
        pass
    return 'Missing Color Target'


def link_node_socket(scene, role_uid, tree, node, direction, socket_index, owner_hint=None):
    sockets = node.inputs if direction == 'INPUT' else node.outputs
    if not 0 <= socket_index < len(sockets):
        return Result('MISSING', 'Socket index is invalid'), False
    socket = sockets[socket_index]
    if not is_color_socket(socket):
        return Result('MISSING', 'This socket is not an editable RGBA value'), False
    if direction == 'INPUT' and socket.is_linked:
        return Result('BLOCKED', 'Input is connected; existing node links are protected'), False
    descriptor = tree_descriptor(tree, owner_hint=owner_hint)
    if descriptor is None:
        return Result('FAILED', 'Cannot identify the owner of this node tree; nothing was linked'), False
    return upsert_binding(scene, role_uid, 'NODE_SOCKET', **descriptor,
                          node_uid=ensure_node_uid(node), node_name=node.name,
                          direction=direction, socket_id=socket.identifier,
                          socket_name=socket.name, socket_index=socket_index)


def link_ramp_stop(scene, role_uid, tree, node, index, owner_hint=None):
    ramp = getattr(node, 'color_ramp', None)
    if ramp is None or not 0 <= index < len(ramp.elements):
        return Result('MISSING', 'ColorRamp stop no longer exists'), False
    descriptor = tree_descriptor(tree, owner_hint=owner_hint)
    if descriptor is None:
        return Result('FAILED', 'Cannot identify the owner of this node tree; nothing was linked'), False
    return upsert_binding(scene, role_uid, 'RAMP_STOP', **descriptor,
                          node_uid=ensure_node_uid(node), node_name=node.name, ramp_index=index)
