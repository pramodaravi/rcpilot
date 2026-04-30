"""Build a race-buggy cockpit interior in Blender.

Run from Blender's Scripting workspace: paste this whole file into a Text
block, then click "Run Script" (Alt+P with the cursor in the text editor).

Conventions
-----------
This script uses Blender's default axes (X right, Y forward, Z up) and
positions the cockpit so its origin is on the floor directly below the
driver. The "driver eye" is at (0, -0.4, 1.15) — matches Unity's
cockpit-camera world position when the imported FBX is placed at world
(0, 0, 0) with no offset.

Blender's FBX exporter converts Z-up → Y-up automatically, so Unity will
see the same proportions you see here.

What gets built
---------------
- Roll cage: 4 windshield bars + roof spine + 2 side rails (8 cylinders)
- Dashboard: tilted slab with subtle bevel
- Steering wheel: torus rim + 3 spokes + boss
- Front shock towers: cylinder body + spring coils (decorative)
- Side panels: angled flanks
- Driver-eye marker: small empty for reference (named DriverEye)

Materials are basic colored Principled BSDF — the FBX importer carries
them into Unity and we adjust there if needed.
"""
from __future__ import annotations

import math
import bpy

# -----------------------------------------------------------------------------
# Setup: clean scene, set units to meters, switch to Cycles for nicer preview.
# -----------------------------------------------------------------------------

def clean_scene() -> None:
    """Delete everything in the current scene; clear orphans."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                  bpy.data.cameras, bpy.data.curves):
        for item in list(block):
            block.remove(item)


def set_units_meters() -> None:
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0
    bpy.context.scene.unit_settings.length_unit = "METERS"


# -----------------------------------------------------------------------------
# Material helpers.
# -----------------------------------------------------------------------------

def make_material(name: str, base_color: tuple[float, float, float],
                  roughness: float = 0.5, metallic: float = 0.0) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    return mat


def set_material(obj: bpy.types.Object, mat: bpy.types.Material) -> None:
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


# -----------------------------------------------------------------------------
# Primitive builders.
# -----------------------------------------------------------------------------

def add_cube(name: str, location, scale, mat) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    set_material(obj, mat)
    return obj


def add_cylinder_tube(name: str, p_from, p_to, radius: float, mat) -> bpy.types.Object:
    """Cylinder spanning two points. Length is computed from the distance and
    the cylinder is rotated so its local Z aligns with the segment direction."""
    fx, fy, fz = p_from
    tx, ty, tz = p_to
    dx, dy, dz = tx - fx, ty - fy, tz - fz
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-6:
        return None
    midpoint = (fx + dx * 0.5, fy + dy * 0.5, fz + dz * 0.5)
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=length,
                                         location=midpoint, vertices=20)
    obj = bpy.context.active_object
    obj.name = name
    # Cylinder's local +Z must point along (dx, dy, dz). Compute Euler.
    direction = (dx / length, dy / length, dz / length)
    # Default cylinder is along +Z, so use track_to-style rotation.
    import mathutils
    up = mathutils.Vector((0.0, 0.0, 1.0))
    target = mathutils.Vector(direction)
    if abs(target.z) < 0.99999:
        rot_quat = up.rotation_difference(target)
        obj.rotation_euler = rot_quat.to_euler()
    set_material(obj, mat)
    return obj


def add_torus(name: str, location, major_radius: float, minor_radius: float,
              tilt_x_deg: float, mat) -> bpy.types.Object:
    bpy.ops.mesh.primitive_torus_add(location=location,
                                     major_radius=major_radius,
                                     minor_radius=minor_radius,
                                     major_segments=32, minor_segments=12)
    obj = bpy.context.active_object
    obj.name = name
    obj.rotation_euler = (math.radians(tilt_x_deg), 0.0, 0.0)
    set_material(obj, mat)
    return obj


def add_empty_marker(name: str, location) -> bpy.types.Object:
    bpy.ops.object.empty_add(type="SPHERE", location=location, radius=0.05)
    obj = bpy.context.active_object
    obj.name = name
    return obj


# -----------------------------------------------------------------------------
# Cockpit assembly.
# -----------------------------------------------------------------------------

def build_cockpit() -> None:
    # Materials
    cage_mat = make_material("CockpitCage",     (0.10, 0.11, 0.12), roughness=0.45, metallic=0.20)
    dash_mat = make_material("Dashboard",       (0.025, 0.025, 0.030), roughness=0.65)
    body_mat = make_material("BodyRed",         (0.78, 0.10, 0.07), roughness=0.30)
    panel_mat = make_material("SidePanel",      (0.045, 0.045, 0.055), roughness=0.55)
    wheel_mat = make_material("WheelRubber",    (0.018, 0.018, 0.020), roughness=0.85)
    metal_mat = make_material("Metal",          (0.50, 0.52, 0.55), roughness=0.30, metallic=0.85)
    coil_mat = make_material("ShockCoil",       (0.06, 0.40, 1.00), roughness=0.30, metallic=0.20)
    accent_mat = make_material("AccentRed",     (0.85, 0.12, 0.14), roughness=0.40)

    # Driver-eye reference (kept as Empty so it doesn't export to Unity as geometry)
    add_empty_marker("DriverEye", (0.0, -0.4, 1.15))

    # ---- Roll cage (built first so it's the visual backbone) ----
    z_windshield = 1.55  # forward axis (toward camera feed); +Y in Blender
    # Windshield top crossbar
    add_cylinder_tube("Cage_WindshieldTop",
                      (-0.55, z_windshield, 1.95), (0.55, z_windshield, 1.95),
                      0.04, cage_mat)
    # A-pillars: from windshield top corners going up-and-back to roof
    add_cylinder_tube("Cage_APillarL",
                      (-0.55, z_windshield, 1.95), (-0.65, 0.10, 2.20),
                      0.045, cage_mat)
    add_cylinder_tube("Cage_APillarR",
                      (0.55, z_windshield, 1.95), (0.65, 0.10, 2.20),
                      0.045, cage_mat)
    # Rear roof crossbar
    add_cylinder_tube("Cage_RoofRear",
                      (-0.65, 0.10, 2.20), (0.65, 0.10, 2.20),
                      0.04, cage_mat)
    # Roof spine (front-back, center)
    add_cylinder_tube("Cage_RoofSpine",
                      (0.0, z_windshield, 1.95), (0.0, 0.10, 2.20),
                      0.035, cage_mat)
    # Side rails (door bars)
    add_cylinder_tube("Cage_SideRailL",
                      (-0.65, -0.30, 0.55), (-0.65, 1.40, 0.85),
                      0.04, cage_mat)
    add_cylinder_tube("Cage_SideRailR",
                      (0.65, -0.30, 0.55), (0.65, 1.40, 0.85),
                      0.04, cage_mat)

    # ---- Side panels (angled cubes flanking the driver) ----
    panel_l = add_cube("SidePanelL", (-0.70, 0.50, 1.00), (0.06, 1.50, 1.20), panel_mat)
    panel_l.rotation_euler = (0.0, 0.0, math.radians(8))
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    panel_r = add_cube("SidePanelR", (0.70, 0.50, 1.00), (0.06, 1.50, 1.20), panel_mat)
    panel_r.rotation_euler = (0.0, 0.0, math.radians(-8))
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    # ---- Dashboard (tilted slab in front of driver) ----
    dash = add_cube("Dashboard", (0.0, 1.15, 0.85), (1.30, 0.42, 0.20), dash_mat)
    dash.rotation_euler = (math.radians(15), 0.0, 0.0)  # top tilts toward driver
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    # Dashboard accent strip (red)
    add_cube("DashAccent", (0.0, 1.05, 1.02), (0.95, 0.020, 0.020), accent_mat)
    # Switches/knobs on dash
    for i, x in enumerate([-0.32, -0.16, 0.0, 0.16, 0.32]):
        knob = add_cube(f"DashKnob{i}", (x, 1.10, 0.95),
                        (0.025, 0.04, 0.025), metal_mat)
        knob.rotation_euler = (math.radians(15), 0.0, 0.0)
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    # ---- Truck-style hood + fenders (replaces the flat slab + shocks combo) ----
    # Long hood extending forward beyond visibility, with a slight rise toward
    # the windshield to read as "F-150-ish" perspective. The hood is split
    # into a center section and two raised fenders flanking it; that gives
    # the driver clear lower-corner peripheral cues just like sitting in a
    # full-size truck.
    hood_mat = make_material("Hood", (0.78, 0.10, 0.07), roughness=0.30)
    fender_mat = make_material("Fender", (0.78, 0.10, 0.07), roughness=0.30)

    # Hood center: wide low slab from base of windshield extending forward
    hood = add_cube("HoodCenter",
                    (0.0, 2.30, 0.55),  # forward of dash, low
                    (0.95, 1.60, 0.16),
                    hood_mat)
    # Slight downward rake so the front edge sits below the windshield line
    hood.rotation_euler = (math.radians(-4), 0.0, 0.0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    # Hood crown (subtle raised center for visual interest)
    crown = add_cube("HoodCrown",
                     (0.0, 2.30, 0.66),
                     (0.55, 1.50, 0.025),
                     hood_mat)
    crown.rotation_euler = (math.radians(-4), 0.0, 0.0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    # Left fender: bulging cube flanking the hood. The driver sees it as a
    # rounded shoulder rising up the side of the hood - the F-150 fender line.
    for sign, name in ((-1, "FenderL"), (1, "FenderR")):
        fender = add_cube(name,
                          (sign * 0.78, 2.20, 0.62),
                          (0.30, 1.80, 0.30),
                          fender_mat)
        # Slight bevel-suggesting tilt: outer edge dips, inner edge rises
        fender.rotation_euler = (math.radians(-3), 0.0, math.radians(-sign * 6))
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        # Fender top "shoulder" - thin ridge that catches the eye
        ridge = add_cube(f"{name}_Ridge",
                         (sign * 0.78, 2.20, 0.78),
                         (0.06, 1.80, 0.020),
                         hood_mat)
        ridge.rotation_euler = (math.radians(-3), 0.0, 0.0)
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    # Front grille hint (dark slab visible at the very forward edge of hood)
    grille_mat = make_material("Grille", (0.05, 0.05, 0.05), roughness=0.55, metallic=0.30)
    add_cube("GrilleHint",
             (0.0, 3.05, 0.50),
             (1.20, 0.10, 0.30),
             grille_mat)

    # ---- Steering wheel (parent empty + rim + boss + 3 spokes + emissive readout) ----
    # We build the wheel as children of an empty so the whole assembly tilts
    # together with one rotation. Driver-side rake is -66 deg around X (top
    # of wheel toward driver eye).
    bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0.0, 0.30, 0.95))
    wheel_root = bpy.context.active_object
    wheel_root.name = "SteeringWheel"

    def parent_to_wheel(child: bpy.types.Object) -> None:
        child.parent = wheel_root
        child.matrix_parent_inverse = wheel_root.matrix_world.inverted()

    # Rim (thicker than before so it reads as a real rim from the driver POV)
    bpy.ops.mesh.primitive_torus_add(location=(0.0, 0.30, 0.95),
                                      major_radius=0.185,
                                      minor_radius=0.028,
                                      major_segments=40, minor_segments=14)
    rim = bpy.context.active_object
    rim.name = "Wheel_Rim"
    set_material(rim, wheel_mat)
    parent_to_wheel(rim)

    # Boss (raised cylindrical hub at the wheel center)
    bpy.ops.mesh.primitive_cylinder_add(radius=0.07, depth=0.04, vertices=24,
                                         location=(0.0, 0.30, 0.95))
    boss = bpy.context.active_object
    boss.name = "Wheel_Boss"
    set_material(boss, metal_mat)
    parent_to_wheel(boss)

    # Three spokes radiating from boss to rim, on the wheel's local XY plane
    # (i.e. before the parent's -66 deg tilt). We pick angles 90/210/330 for
    # a flat-bottom feel (no spoke at -90 deg = clear bottom).
    for spoke_i, angle_deg in enumerate((90, 210, 330)):
        a = math.radians(angle_deg)
        # Spoke endpoint in wheel-local XY (Z=0 plane of the empty)
        end_x = math.cos(a) * 0.155
        end_y = math.sin(a) * 0.155
        # Cylinder default extends along +Z. We want it to lie along the
        # in-plane direction (cos a, sin a, 0). Rotate 90 deg around Y to
        # bring +Z to +X, then rotate around Z by angle_deg.
        bpy.ops.mesh.primitive_cylinder_add(radius=0.012, depth=0.155, vertices=12,
                                             location=(end_x * 0.5, 0.30 + end_y * 0.5, 0.95))
        spoke = bpy.context.active_object
        spoke.name = f"Wheel_Spoke{spoke_i}"
        spoke.rotation_euler = (0.0, math.radians(90), math.radians(angle_deg))
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        set_material(spoke, metal_mat)
        parent_to_wheel(spoke)

    # Emissive readout panel on top of the boss (faces driver after tilt)
    display_mat = make_material("WheelDisplay", (0.015, 0.015, 0.015), roughness=0.20)
    bsdf = display_mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        # Emission inputs in 4.x are "Emission Color" + "Emission Strength"
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (0.05, 1.0, 0.30, 1.0)
        elif "Emission" in bsdf.inputs:
            bsdf.inputs["Emission"].default_value = (0.05, 1.0, 0.30, 1.0)
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = 4.0
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.30, 0.972))
    display = bpy.context.active_object
    display.name = "Wheel_Display"
    display.scale = (0.10, 0.05, 0.005)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    set_material(display, display_mat)
    parent_to_wheel(display)

    # Now tilt the whole wheel toward the driver
    wheel_root.rotation_euler = (math.radians(-66), 0.0, 0.0)

    # ---- Pedals (brake + accelerator on the floor in the foot area) ----
    # Foot area is between driver and dash, ~0.45-0.65 m forward, low to floor.
    # Brake on the left (per right-hand-drive convention; throttle on right).
    pedal_mat = make_material("Pedal", (0.05, 0.05, 0.05), roughness=0.85)
    brake_face_mat = make_material("BrakePedalFace", (0.55, 0.06, 0.06), roughness=0.55)
    # Brake (wider, flatter)
    brake_arm = add_cube("BrakePedalArm",
                         (-0.08, 0.50, 0.20),
                         (0.020, 0.025, 0.36),
                         pedal_mat)
    brake_arm.rotation_euler = (math.radians(-25), 0.0, 0.0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    brake_pad = add_cube("BrakePedalPad",
                         (-0.08, 0.55, 0.07),
                         (0.10, 0.16, 0.022),
                         brake_face_mat)
    brake_pad.rotation_euler = (math.radians(-65), 0.0, 0.0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    # Throttle (narrower, taller, slightly further right)
    throttle_arm = add_cube("ThrottlePedalArm",
                            (0.10, 0.52, 0.18),
                            (0.020, 0.025, 0.32),
                            pedal_mat)
    throttle_arm.rotation_euler = (math.radians(-22), 0.0, 0.0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    throttle_pad = add_cube("ThrottlePedalPad",
                            (0.10, 0.58, 0.07),
                            (0.07, 0.20, 0.020),
                            pedal_mat)
    throttle_pad.rotation_euler = (math.radians(-70), 0.0, 0.0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    # ---- Floor (closure underneath the cockpit) ----
    add_cube("Floor", (0.0, 0.50, 0.04), (1.50, 1.80, 0.03),
             panel_mat)


# -----------------------------------------------------------------------------
# Camera + light setup so the viewport gives a useful preview from the driver
# seat. None of this exports to FBX (cameras and lights are excluded by the
# Unity-bound FBX export step we'll do next).
# -----------------------------------------------------------------------------

def setup_preview_camera() -> None:
    bpy.ops.object.camera_add(location=(0.0, -0.4, 1.15),
                              rotation=(math.radians(90), 0.0, 0.0))
    cam = bpy.context.active_object
    cam.name = "PreviewCam_DriverPOV"
    cam.data.lens = 24  # roughly the FOV Unity uses (55° vertical ≈ 24mm)
    bpy.context.scene.camera = cam

    bpy.ops.object.light_add(type="SUN", location=(2.0, -2.0, 4.0))
    sun = bpy.context.active_object
    sun.data.energy = 2.5
    sun.rotation_euler = (math.radians(45), 0.0, math.radians(-45))


# -----------------------------------------------------------------------------
# Entry point.
# -----------------------------------------------------------------------------

def main():
    clean_scene()
    set_units_meters()
    build_cockpit()
    setup_preview_camera()
    # Fit the viewport to the new geometry
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D":
            for region in area.regions:
                if region.type == "WINDOW":
                    override = {"area": area, "region": region}
                    try:
                        with bpy.context.temp_override(**override):
                            bpy.ops.view3d.view_all()
                    except Exception:
                        pass
    print("[rcpilot] cockpit built. Press Numpad 0 to view from PreviewCam_DriverPOV.")


if __name__ == "__main__":
    main()
