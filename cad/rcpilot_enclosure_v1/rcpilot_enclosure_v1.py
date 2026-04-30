"""rcpilot on-vehicle enclosure — v2 (Fusion 360 Python script)

Run from Fusion: Utilities -> Add-ins -> Scripts and Add-ins ->
"+ Add" pointing at this folder, then select rcpilot_enclosure_v1 -> Run.

Each run creates a fresh document, so iterating is cheap: edit parameters
at the top, close the old doc (or leave it), re-Run.

----------------------------------------------------------------------
Layout (top-down, +X = forward / cameras, +Y = right):

   X=0 (back)                                              X=OUTER_L (front)
    +--------------+----------------------------------------+
    |  BATTERY +   |                                        |
    |  EXPANSION   |   JETSON COMPARTMENT                   |
    |  COMPARTMENT |   - 4x M3 standoffs                    |
    |              |   - cable channel on floor centerline  |
    |              |   - zip-tie hole pairs                 |
    +-----DIVIDER--+----------------------------------------+
                |     ^
                |     +-- cable pass-through slot through divider
                |
              X=DIVIDER_X
----------------------------------------------------------------------

Two compartments separated by an internal divider. Cables route via the
slot through the divider, then along a channel on the floor centerline.

Front face: 2 camera apertures with M2 mount-hole pattern, baseline tunable.
Rear face: USB pass-through slot.
Floor: 4x M3 chassis bolt holes, plus 4 zip-tie hole pairs.
Lid: separate body, vent slots, M3 clearance holes.

All dims in mm. Fusion API uses cm internally — see cm() helper.
"""

import adsk.core
import adsk.fusion
import math
import traceback


# =====================================================================
# Parameters
# =====================================================================

# --- Outer envelope (mm) ---
# 200 x 160 stays on common 220 mm printer beds while leaving real room for:
# Jetson Orin Nano dev kit, 3S shorty pack, PWM board, and wide camera spacing.
OUTER_L = 200.0   # X depth (back <-> front)
OUTER_W = 160.0   # Y width (left <-> right)
OUTER_H = 75.0    # Z height (chassis <-> lid)

WALL_T  = 3.0
LID_T   = 3.0

# --- Divider wall (separates Jetson from battery+expansion) ---
DIVIDER_X        = 70.0   # X position of back face of divider
DIVIDER_T        = 3.0
DIVIDER_H        = 55.0   # height (leaves cable space above)
CABLE_SLOT_W     = 30.0   # cable pass-through slot width (Y)
CABLE_SLOT_H     = 15.0   # cable pass-through slot height (Z)
CABLE_SLOT_Z_TOP = 45.0   # top of slot above floor

# --- Floor cable channel + zip-tie hole pairs ---
CHANNEL_W       = 8.0     # width of recessed channel on floor centerline
CHANNEL_DEPTH   = 1.5     # how deep the channel cuts into the floor
ZIP_HOLE_DIA    = 2.5     # zip-tie pass-through hole diameter
ZIP_PAIR_DX     = 5.0     # within a pair, 2 holes are this far apart
ZIP_PAIRS_X     = [28.0, 92.0, 138.0, 178.0]  # X positions of zip-tie pairs

# --- 3S 2200 mAh shorty battery ---
# Common shorty packs are roughly 96 x 47 x 25 mm. Measure your exact pack and
# tweak these constants before printing if needed.
BATTERY_L        = 96.0    # along Y
BATTERY_W        = 47.0    # along X
BATTERY_H        = 25.0
BATTERY_CX       = 36.5
BATTERY_CY       = OUTER_W / 2.0
BATTERY_RAIL_H   = 3.0
BATTERY_RAIL_T   = 2.0
BATTERY_ENDSTOP_T = 3.0
BATTERY_STRAP_SLOT_W = 4.0
BATTERY_STRAP_SLOT_L = 24.0
BATTERY_STRAP_Y  = [BATTERY_CY - 26.0, BATTERY_CY + 26.0]
BATTERY_STRAP_MARGIN_X = 5.0

# --- Jetson Orin Nano dev kit ---
# Mounting hole pattern on the carrier: 86 x 58 mm, M3
JETSON_HOLE_DX  = 86.0
JETSON_HOLE_DY  = 58.0
JETSON_CX       = 136.0   # X center of Jetson in box coords
JETSON_CY       = OUTER_W / 2.0
STANDOFF_OD     = 6.0
STANDOFF_H      = 6.0
JETSON_PILOT    = 2.5     # M3 tap pilot diameter

# --- 16-channel PWM servo driver (PCA9685-style board) ---
PWM_BOARD_L      = 62.0    # visual/clearance footprint, along X
PWM_BOARD_W      = 26.0    # visual/clearance footprint, along Y
PWM_HOLE_DX      = 55.0
PWM_HOLE_DY      = 19.0
PWM_CX           = 136.0
PWM_CY           = 21.5    # side strip next to Jetson footprint
PWM_STANDOFF_OD  = 5.0
PWM_STANDOFF_H   = 5.0
PWM_PILOT        = 2.0     # M2.5-ish pilot; enlarge for through bolts

# --- Cameras (IMX219 module + lens) ---
CAM_BASELINE        = 110.0   # center-to-center on the front face
CAM_APERTURE_W      = 24.0
CAM_APERTURE_H      = 24.0
CAM_CENTER_Z        = 35.0    # height above floor
CAM_MOUNT_HOLE_DIA  = 2.2     # M2 clearance
CAM_MOUNT_PATTERN_W = 21.0
CAM_MOUNT_PATTERN_H = 12.5
CAM_PAD_W           = 36.0
CAM_PAD_H           = 30.0
CAM_PAD_T           = 4.0     # extra plastic behind front face for camera screws

# --- Rear face USB pass-through ---
USB_SLOT_W  = 80.0
USB_SLOT_H  = 15.0
USB_SLOT_Z0 = 18.0

# --- Lid features ---
VENT_COUNT     = 5
VENT_SLOT_LEN  = 60.0   # along Y
VENT_SLOT_W    =  4.0   # along X
VENT_PITCH_X   = 14.0
LID_CLEARANCE  = 0.35   # per side clearance for the alignment lip
LID_LIP_T      = 2.0
LID_LIP_H      = 5.0

# --- M3 fasteners ---
M3_CLEAR_DIA = 3.4
CORNER_INSET = 10.0

# --- Corner bosses (M3 machine screws + captured nuts for the lid) ---
BOSS_OD          = 10.0   # outer diameter of each corner pillar
M3_NUT_AF        = 6.4    # across flats, with print clearance
M3_NUT_DEPTH     = 3.2    # nut pocket depth, with print clearance
M3_SCREW_RELIEF_DEPTH = 9.0


# =====================================================================
# Helpers
# =====================================================================

def cm(mm_val):
    """Fusion's API uses cm internally. Wrap mm so dimensions stay readable."""
    return mm_val / 10.0


def _Pt(x_mm, y_mm, z_mm=0.0):
    return adsk.core.Point3D.create(cm(x_mm), cm(y_mm), cm(z_mm))


def addRect(sketch, x0, y0, x1, y1):
    """Draw an axis-aligned rectangle on `sketch` (mm)."""
    lines = sketch.sketchCurves.sketchLines
    p0 = _Pt(x0, y0); p1 = _Pt(x1, y0); p2 = _Pt(x1, y1); p3 = _Pt(x0, y1)
    lines.addByTwoPoints(p0, p1)
    lines.addByTwoPoints(p1, p2)
    lines.addByTwoPoints(p2, p3)
    lines.addByTwoPoints(p3, p0)


def addCircle(sketch, cx, cy, dia):
    sketch.sketchCurves.sketchCircles.addByCenterRadius(_Pt(cx, cy), cm(dia / 2.0))


def addPolygon(sketch, cx, cy, radius, sides=6, rotation_deg=30.0):
    """Draw a regular polygon on `sketch` (mm)."""
    lines = sketch.sketchCurves.sketchLines
    pts = []
    rot = math.radians(rotation_deg)
    for i in range(sides):
        a = rot + (2.0 * math.pi * i / sides)
        pts.append(_Pt(cx + radius * math.cos(a), cy + radius * math.sin(a)))
    for i in range(sides):
        lines.addByTwoPoints(pts[i], pts[(i + 1) % sides])


def allProfiles(sketch):
    """Return an ObjectCollection of every profile on the sketch."""
    coll = adsk.core.ObjectCollection.create()
    for i in range(sketch.profiles.count):
        coll.add(sketch.profiles.item(i))
    return coll


def newExtrude(component, profiles, op, depth_mm,
               start_mm=0.0, direction_negative=False, target_bodies=None):
    """One-shot extrude/cut helper.

    profiles: a single Profile or an ObjectCollection of profiles.
    op:       FeatureOperations.* enum value.
    depth_mm: positive distance.
    start_mm: offset from the sketch plane to start the extrude.
    direction_negative: extrude in -normal direction instead of +normal.
    target_bodies: optional list to limit cut/intersect to these bodies.
    """
    extrudes = component.features.extrudeFeatures
    inp = extrudes.createInput(profiles, op)

    if start_mm != 0.0:
        inp.startExtent = adsk.fusion.OffsetStartDefinition.create(
            adsk.core.ValueInput.createByReal(cm(start_mm))
        )

    distance = cm(depth_mm) * (-1.0 if direction_negative else 1.0)
    inp.setDistanceExtent(False, adsk.core.ValueInput.createByReal(distance))

    if target_bodies and op in (
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        adsk.fusion.FeatureOperations.IntersectFeatureOperation,
    ):
        bodies = adsk.core.ObjectCollection.create()
        for body in target_bodies:
            bodies.add(body)
        inp.participantBodies = bodies

    return extrudes.add(inp)


def offsetPlane(component, base_plane, offset_mm):
    planes = component.constructionPlanes
    inp = planes.createInput()
    inp.setByOffset(base_plane, adsk.core.ValueInput.createByReal(cm(offset_mm)))
    return planes.add(inp)


def offsetPlaneFromFace(component, face, offset_mm):
    planes = component.constructionPlanes
    inp = planes.createInput()
    inp.setByOffset(face, adsk.core.ValueInput.createByReal(cm(offset_mm)))
    return planes.add(inp)


def findFaceByZ(body, target_z_mm, tol_mm=0.05):
    """Find a planar horizontal face whose centroid is at the given Z (mm)."""
    target_z_cm = cm(target_z_mm)
    tol_cm = cm(tol_mm)
    for face in body.faces:
        if face.geometry.objectType != adsk.core.Plane.classType():
            continue
        if abs(face.centroid.z - target_z_cm) <= tol_cm:
            n = face.geometry.normal
            if abs(n.z) > 0.99:
                return face
    return None


def validateParameters():
    """Fail early if a parameter edit makes hardware features leave the box."""
    cam_edge = OUTER_W / 2.0 + CAM_BASELINE / 2.0 + CAM_PAD_W / 2.0
    if cam_edge > OUTER_W - WALL_T:
        raise RuntimeError(
            "camera baseline/pad is too wide for OUTER_W; reduce CAM_BASELINE "
            "or increase OUTER_W"
        )

    batt_x0 = BATTERY_CX - BATTERY_W / 2.0
    batt_x1 = BATTERY_CX + BATTERY_W / 2.0
    if batt_x0 < WALL_T or batt_x1 > DIVIDER_X - WALL_T:
        raise RuntimeError("battery tray does not fit in the rear compartment")

    pwm_x0 = PWM_CX - PWM_HOLE_DX / 2.0 - PWM_STANDOFF_OD / 2.0
    pwm_x1 = PWM_CX + PWM_HOLE_DX / 2.0 + PWM_STANDOFF_OD / 2.0
    pwm_y0 = PWM_CY - PWM_HOLE_DY / 2.0 - PWM_STANDOFF_OD / 2.0
    pwm_y1 = PWM_CY + PWM_HOLE_DY / 2.0 + PWM_STANDOFF_OD / 2.0
    if pwm_x0 < DIVIDER_X + DIVIDER_T or pwm_x1 > OUTER_L - WALL_T:
        raise RuntimeError("PWM driver standoffs do not fit in the Jetson compartment")
    if pwm_y0 < WALL_T or pwm_y1 > OUTER_W - WALL_T:
        raise RuntimeError("PWM driver standoffs do not fit across the enclosure width")


# =====================================================================
# Build
# =====================================================================

def buildOuterAndShell(encl):
    """Outer block + Shell feature → hollow open-top box."""
    sk = encl.sketches.add(encl.xYConstructionPlane)
    addRect(sk, 0, 0, OUTER_L, OUTER_W)
    ext = newExtrude(
        encl,
        sk.profiles.item(0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        OUTER_H,
    )
    body = ext.bodies.item(0)
    body.name = "enclosure_shell"

    plane_top = offsetPlane(encl, encl.xYConstructionPlane, OUTER_H)
    sk2 = encl.sketches.add(plane_top)
    addRect(sk2, WALL_T, WALL_T, OUTER_L - WALL_T, OUTER_W - WALL_T)
    newExtrude(
        encl,
        sk2.profiles.item(0),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        OUTER_H - WALL_T,
        direction_negative=True,
        target_bodies=[body],
    )
    return body


def buildDivider(encl, shell_body):
    """Internal divider wall splitting Jetson and battery compartments."""
    sk = encl.sketches.add(encl.xYConstructionPlane)
    # Divider is a thin rectangle spanning the full inner Y, full Y wall-to-wall.
    addRect(
        sk,
        DIVIDER_X,
        WALL_T,
        DIVIDER_X + DIVIDER_T,
        OUTER_W - WALL_T,
    )
    newExtrude(
        encl,
        sk.profiles.item(0),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        DIVIDER_H,
        start_mm=WALL_T,    # start from top of floor
    )

    # Cable pass-through slot in the divider — sketched on a YZ plane offset
    # at X = DIVIDER_X (back face of divider). Cut +X through DIVIDER_T+1.
    plane = offsetPlane(encl, encl.yZConstructionPlane, DIVIDER_X)
    sk2 = encl.sketches.add(plane)
    # Sketch local coords: x → world Y, y → world Z
    y_center = OUTER_W / 2.0
    addRect(
        sk2,
        y_center - CABLE_SLOT_W / 2.0,
        CABLE_SLOT_Z_TOP - CABLE_SLOT_H,
        y_center + CABLE_SLOT_W / 2.0,
        CABLE_SLOT_Z_TOP,
    )
    newExtrude(
        encl,
        sk2.profiles.item(0),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        DIVIDER_T + 0.5,   # punch through divider thickness
    )


def buildCornerBosses(encl):
    """4 internal corner pillars that span floor-to-lid.

    Both the chassis bolts (from below) and the lid screws (from above) thread
    into solid plastic in these bosses. Top of the boss has a 2.5 mm pilot for
    self-tap M3 lid screws; bottom is left solid so the chassis bolt self-taps
    into it as it passes through the floor clearance hole.
    """
    sk = encl.sketches.add(encl.xYConstructionPlane)
    for x in (CORNER_INSET, OUTER_L - CORNER_INSET):
        for y in (CORNER_INSET, OUTER_W - CORNER_INSET):
            addCircle(sk, x, y, BOSS_OD)
    newExtrude(
        encl,
        allProfiles(sk),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        OUTER_H - WALL_T,
        start_mm=WALL_T,
    )

    # Captured M3 nut pockets for lid screws, open at the top of each boss.
    plane_top = offsetPlane(encl, encl.xYConstructionPlane, OUTER_H)
    sk2 = encl.sketches.add(plane_top)
    nut_radius = M3_NUT_AF / math.sqrt(3.0)
    for x in (CORNER_INSET, OUTER_L - CORNER_INSET):
        for y in (CORNER_INSET, OUTER_W - CORNER_INSET):
            addPolygon(sk2, x, y, nut_radius, sides=6, rotation_deg=30.0)
    newExtrude(
        encl,
        allProfiles(sk2),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        M3_NUT_DEPTH,
        direction_negative=True,
    )

    # Screw relief below the captured nut so long M3 screws do not bottom out.
    sk3 = encl.sketches.add(plane_top)
    for x in (CORNER_INSET, OUTER_L - CORNER_INSET):
        for y in (CORNER_INSET, OUTER_W - CORNER_INSET):
            addCircle(sk3, x, y, M3_CLEAR_DIA)
    newExtrude(
        encl,
        allProfiles(sk3),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        M3_NUT_DEPTH + M3_SCREW_RELIEF_DEPTH,
        direction_negative=True,
    )


def buildJetsonStandoffs(encl):
    """4 standoffs centered on JETSON_CX, JETSON_CY with M3 pilot holes."""
    # Posts
    sk = encl.sketches.add(encl.xYConstructionPlane)
    for sx in (-1, +1):
        for sy in (-1, +1):
            cx = JETSON_CX + sx * JETSON_HOLE_DX / 2.0
            cy = JETSON_CY + sy * JETSON_HOLE_DY / 2.0
            addCircle(sk, cx, cy, STANDOFF_OD)
    newExtrude(
        encl,
        allProfiles(sk),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        STANDOFF_H,
        start_mm=WALL_T,
    )

    # Pilot holes through standoff + floor (so we can tap M3 from above
    # AND the screw could pass through the floor if needed)
    sk2 = encl.sketches.add(encl.xYConstructionPlane)
    for sx in (-1, +1):
        for sy in (-1, +1):
            cx = JETSON_CX + sx * JETSON_HOLE_DX / 2.0
            cy = JETSON_CY + sy * JETSON_HOLE_DY / 2.0
            addCircle(sk2, cx, cy, JETSON_PILOT)
    newExtrude(
        encl,
        allProfiles(sk2),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        WALL_T + STANDOFF_H + 0.5,
    )


def buildPwmDriverMount(encl):
    """Standoffs for a 16-channel PCA9685-style PWM servo driver board."""
    sk = encl.sketches.add(encl.xYConstructionPlane)
    for sx in (-1, +1):
        for sy in (-1, +1):
            cx = PWM_CX + sx * PWM_HOLE_DX / 2.0
            cy = PWM_CY + sy * PWM_HOLE_DY / 2.0
            addCircle(sk, cx, cy, PWM_STANDOFF_OD)
    newExtrude(
        encl,
        allProfiles(sk),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        PWM_STANDOFF_H,
        start_mm=WALL_T,
    )

    sk2 = encl.sketches.add(encl.xYConstructionPlane)
    for sx in (-1, +1):
        for sy in (-1, +1):
            cx = PWM_CX + sx * PWM_HOLE_DX / 2.0
            cy = PWM_CY + sy * PWM_HOLE_DY / 2.0
            addCircle(sk2, cx, cy, PWM_PILOT)
    newExtrude(
        encl,
        allProfiles(sk2),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        WALL_T + PWM_STANDOFF_H + 0.5,
    )

    # Light board outline engraved into the floor for placement reference.
    sk3 = encl.sketches.add(encl.xYConstructionPlane)
    addRect(
        sk3,
        PWM_CX - PWM_BOARD_L / 2.0,
        PWM_CY - PWM_BOARD_W / 2.0,
        PWM_CX + PWM_BOARD_L / 2.0,
        PWM_CY + PWM_BOARD_W / 2.0,
    )
    newExtrude(
        encl,
        sk3.profiles.item(0),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        0.35,
        start_mm=WALL_T,
        direction_negative=True,
    )


def buildBatteryTrayAndStrapSlots(encl):
    """Shorty-pack tray rails plus two through-floor strap slot pairs."""
    batt_x0 = BATTERY_CX - BATTERY_W / 2.0
    batt_x1 = BATTERY_CX + BATTERY_W / 2.0
    batt_y0 = BATTERY_CY - BATTERY_L / 2.0
    batt_y1 = BATTERY_CY + BATTERY_L / 2.0

    # Low rails and end stops locate the battery without trapping it.
    sk = encl.sketches.add(encl.xYConstructionPlane)
    addRect(sk, batt_x0 - BATTERY_RAIL_T, batt_y0, batt_x0, batt_y1)
    addRect(sk, batt_x1, batt_y0, batt_x1 + BATTERY_RAIL_T, batt_y1)
    addRect(sk, batt_x0, batt_y0 - BATTERY_ENDSTOP_T, batt_x1, batt_y0)
    addRect(sk, batt_x0, batt_y1, batt_x1, batt_y1 + BATTERY_ENDSTOP_T)
    newExtrude(
        encl,
        allProfiles(sk),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        BATTERY_RAIL_H,
        start_mm=WALL_T,
    )

    # Two strap bands: each pair of slots lets Velcro pass up from below,
    # over the battery, and back through the floor.
    sk2 = encl.sketches.add(encl.xYConstructionPlane)
    slot_left_cx = batt_x0 - BATTERY_STRAP_MARGIN_X
    slot_right_cx = batt_x1 + BATTERY_STRAP_MARGIN_X
    for y in BATTERY_STRAP_Y:
        addRect(
            sk2,
            slot_left_cx - BATTERY_STRAP_SLOT_W / 2.0,
            y - BATTERY_STRAP_SLOT_L / 2.0,
            slot_left_cx + BATTERY_STRAP_SLOT_W / 2.0,
            y + BATTERY_STRAP_SLOT_L / 2.0,
        )
        addRect(
            sk2,
            slot_right_cx - BATTERY_STRAP_SLOT_W / 2.0,
            y - BATTERY_STRAP_SLOT_L / 2.0,
            slot_right_cx + BATTERY_STRAP_SLOT_W / 2.0,
            y + BATTERY_STRAP_SLOT_L / 2.0,
        )
    newExtrude(
        encl,
        allProfiles(sk2),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        WALL_T + 0.5,
    )


def buildCameraApertures(encl):
    """Two square cutouts on the front face + M2 mount-hole pattern."""
    # Interior pads give the camera screws more plastic than the thin front
    # wall alone. This is useful for heat-set inserts, light tapping, or nuts.
    pad_plane = offsetPlane(encl, encl.yZConstructionPlane, OUTER_L - WALL_T)
    sk_pad = encl.sketches.add(pad_plane)
    y_center = OUTER_W / 2.0
    for sign in (-1, +1):
        y_c = y_center + sign * (CAM_BASELINE / 2.0)
        addRect(
            sk_pad,
            y_c - CAM_PAD_W / 2.0,
            CAM_CENTER_Z - CAM_PAD_H / 2.0,
            y_c + CAM_PAD_W / 2.0,
            CAM_CENTER_Z + CAM_PAD_H / 2.0,
        )
    newExtrude(
        encl,
        allProfiles(sk_pad),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        CAM_PAD_T,
        direction_negative=True,
    )

    # Front face is at X = OUTER_L. Sketch on YZ plane offset to that X
    # and cut in -X direction (into the box wall).
    plane = offsetPlane(encl, encl.yZConstructionPlane, OUTER_L)
    sk = encl.sketches.add(plane)
    # Sketch: x → world Y, y → world Z
    for sign in (-1, +1):
        y_c = y_center + sign * (CAM_BASELINE / 2.0)
        addRect(
            sk,
            y_c - CAM_APERTURE_W / 2.0,
            CAM_CENTER_Z - CAM_APERTURE_H / 2.0,
            y_c + CAM_APERTURE_W / 2.0,
            CAM_CENTER_Z + CAM_APERTURE_H / 2.0,
        )
    newExtrude(
        encl,
        allProfiles(sk),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        WALL_T + CAM_PAD_T + 0.5,
        direction_negative=True,
    )

    # M2 mount holes — separate sketch for clean profile counts
    sk2 = encl.sketches.add(plane)
    for sign in (-1, +1):
        y_c = y_center + sign * (CAM_BASELINE / 2.0)
        for dx in (-CAM_MOUNT_PATTERN_W / 2.0, CAM_MOUNT_PATTERN_W / 2.0):
            for dy in (-CAM_MOUNT_PATTERN_H / 2.0, CAM_MOUNT_PATTERN_H / 2.0):
                addCircle(sk2, y_c + dx, CAM_CENTER_Z + dy, CAM_MOUNT_HOLE_DIA)
    newExtrude(
        encl,
        allProfiles(sk2),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        WALL_T + CAM_PAD_T + 0.5,
        direction_negative=True,
    )


def buildRearCutouts(encl):
    """USB pass-through on the rear face (X = 0)."""
    sk = encl.sketches.add(encl.yZConstructionPlane)
    y_center = OUTER_W / 2.0
    addRect(
        sk,
        y_center - USB_SLOT_W / 2.0,
        USB_SLOT_Z0,
        y_center + USB_SLOT_W / 2.0,
        USB_SLOT_Z0 + USB_SLOT_H,
    )
    # Cut into the box (+X direction)
    newExtrude(
        encl,
        sk.profiles.item(0),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        WALL_T + 0.5,
    )


def buildFloorFeatures(encl):
    """Cable channel groove + zip-tie hole pairs + M3 chassis-bolt holes."""
    # Cable channel groove on centerline, runs whole length, both compartments
    sk = encl.sketches.add(encl.xYConstructionPlane)
    y_center = OUTER_W / 2.0
    addRect(
        sk,
        WALL_T,
        y_center - CHANNEL_W / 2.0,
        OUTER_L - WALL_T,
        y_center + CHANNEL_W / 2.0,
    )
    # Cut DOWN into the floor by CHANNEL_DEPTH, starting at top of floor (Z=WALL_T)
    newExtrude(
        encl,
        sk.profiles.item(0),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        CHANNEL_DEPTH,
        start_mm=WALL_T,
        direction_negative=True,
    )

    # Zip-tie hole pairs along channel
    sk2 = encl.sketches.add(encl.xYConstructionPlane)
    for x in ZIP_PAIRS_X:
        addCircle(sk2, x, y_center - ZIP_PAIR_DX / 2.0, ZIP_HOLE_DIA)
        addCircle(sk2, x, y_center + ZIP_PAIR_DX / 2.0, ZIP_HOLE_DIA)
    newExtrude(
        encl,
        allProfiles(sk2),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        WALL_T + 0.5,
    )

    # M3 chassis-bolt holes near 4 corners
    sk3 = encl.sketches.add(encl.xYConstructionPlane)
    for x in (CORNER_INSET, OUTER_L - CORNER_INSET):
        for y in (CORNER_INSET, OUTER_W - CORNER_INSET):
            addCircle(sk3, x, y, M3_CLEAR_DIA)
    newExtrude(
        encl,
        allProfiles(sk3),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        WALL_T + 0.5,
    )


def buildLid(encl, shell_body):
    """Removable lid — separate body, plus vent slots + screw holes."""
    plane = offsetPlane(encl, encl.xYConstructionPlane, OUTER_H + 0.2)
    sk = encl.sketches.add(plane)
    addRect(sk, 0, 0, OUTER_L, OUTER_W)
    ext = newExtrude(
        encl,
        sk.profiles.item(0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        LID_T,
    )
    lid = ext.bodies.item(0)
    lid.name = "enclosure_lid"

    # Underside alignment lip: drops inside the open shell so the lid locates
    # itself before the M3 screws are tightened.
    lip_x0 = WALL_T + LID_CLEARANCE
    lip_x1 = OUTER_L - WALL_T - LID_CLEARANCE
    lip_y0 = WALL_T + LID_CLEARANCE
    lip_y1 = OUTER_W - WALL_T - LID_CLEARANCE
    sk_lip = encl.sketches.add(plane)
    addRect(sk_lip, lip_x0, lip_y0, lip_x1, lip_y0 + LID_LIP_T)
    addRect(sk_lip, lip_x0, lip_y1 - LID_LIP_T, lip_x1, lip_y1)
    addRect(sk_lip, lip_x0, lip_y0 + LID_LIP_T, lip_x0 + LID_LIP_T, lip_y1 - LID_LIP_T)
    addRect(sk_lip, lip_x1 - LID_LIP_T, lip_y0 + LID_LIP_T, lip_x1, lip_y1 - LID_LIP_T)
    newExtrude(
        encl,
        allProfiles(sk_lip),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        LID_LIP_H,
        direction_negative=True,
    )

    # Lid screw clearance holes (4 corners)
    sk2 = encl.sketches.add(plane)
    for x in (CORNER_INSET, OUTER_L - CORNER_INSET):
        for y in (CORNER_INSET, OUTER_W - CORNER_INSET):
            addCircle(sk2, x, y, M3_CLEAR_DIA)
    newExtrude(
        encl,
        allProfiles(sk2),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        LID_T + 0.5,
        target_bodies=[lid],
    )

    # Vent slots — 5 slots along X axis, parallel to Y, in the Jetson half
    sk3 = encl.sketches.add(plane)
    # Place vents above the Jetson compartment (X > DIVIDER_X)
    jetson_compartment_center = (DIVIDER_X + DIVIDER_T + OUTER_L - WALL_T) / 2.0
    x_start = jetson_compartment_center - ((VENT_COUNT - 1) * VENT_PITCH_X) / 2.0
    y_center = OUTER_W / 2.0
    for i in range(VENT_COUNT):
        xc = x_start + i * VENT_PITCH_X
        addRect(
            sk3,
            xc - VENT_SLOT_W / 2.0,
            y_center - VENT_SLOT_LEN / 2.0,
            xc + VENT_SLOT_W / 2.0,
            y_center + VENT_SLOT_LEN / 2.0,
        )
    newExtrude(
        encl,
        allProfiles(sk3),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        LID_T + 0.5,
        target_bodies=[lid],
    )


# =====================================================================
# Entry point
# =====================================================================

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        validateParameters()

        # Fresh document
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = app.activeProduct
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        rootComp = design.rootComponent

        # Child component for the enclosure
        occ = rootComp.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        encl = occ.component
        encl.name = "rcpilot_enclosure"

        # Build pipeline
        shell_body = buildOuterAndShell(encl)
        buildDivider(encl, shell_body)
        buildCornerBosses(encl)
        buildBatteryTrayAndStrapSlots(encl)
        buildJetsonStandoffs(encl)
        buildPwmDriverMount(encl)
        buildCameraApertures(encl)
        buildRearCutouts(encl)
        buildFloorFeatures(encl)
        buildLid(encl, shell_body)

        ui.messageBox(
            "rcpilot_enclosure v2: build complete.\n\n"
            "Bodies: enclosure_shell, enclosure_lid.\n\n"
            "Things to verify visually:\n"
            "  - Two compartments separated by an internal divider\n"
            "  - 3S shorty battery tray with two strap-slot pairs\n"
            "  - Cable pass-through slot at top of divider\n"
            "  - Recessed cable channel along floor centerline\n"
            "  - 4 M3 standoffs in Jetson compartment\n"
            "  - 4 PWM driver board standoffs in side strip\n"
            "  - 2 camera apertures on front face with M2 mount holes\n"
            "  - USB slot on rear face\n"
            "  - 4 M3 chassis bolt holes in floor corners\n"
            "  - Lid with 5 vent slots, alignment lip, and M3 screw holes\n"
            "  - Captured M3 nut pockets in the enclosure corner bosses\n\n"
            "Edit the constants at the top of the .py and re-run to iterate."
        )

    except:
        if ui:
            ui.messageBox(
                "rcpilot_enclosure failed:\n\n{}".format(traceback.format_exc())
            )
