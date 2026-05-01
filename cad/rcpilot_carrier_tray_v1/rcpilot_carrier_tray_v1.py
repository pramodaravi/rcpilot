"""rcpilot on-vehicle carrier tray — v1 (Fusion 360 Python script)

Strategy: keep crussella0129's Jetson Orin Nano Super Case unmodified (proven
cooling, easy to re-print or replace) and design a separate carrier TRAY that
bolts to the RC chassis and holds:

  - the Super Case (slid into a 3-walled open-top cradle)
  - a 3S 2200 mAh shorty pack (rear bay)
  - the expansion module (PCA9685 + ESP32-S3 + IMU on a custom carrier)
  - 2 IMX219 cameras on the front face

Layout (top-down, +X = forward, +Y = right):

   X=0 (back)                                                  X=OUTER_L (front)
    +--------------+--------------------------+---------------------+
    |              |                          |  [cam L]   [cam R]  |
    |  Battery     |     Super Case CRADLE    |                     |
    |  Bay         |     (Jetson + Orin Case) |                     |
    |  +Expansion  |                          |                     |
    |  Module      |                          |                     |
    +--------------+--------------------------+---------------------+
                                                       ^
                                                       cameras face forward
                                                       through this front wall

Run from Fusion: Utilities -> Add-ins -> Scripts and Add-ins ->
"+ Add" pointing at this folder, then select rcpilot_carrier_tray_v1 -> Run.

IMPORTANT: the Super Case dimensions in the parameters block below are
placeholders. Open the case STEP file in Fusion (Insert -> Insert Mesh /
File -> Open the .step), measure its outer L/W/H, and update CASE_L /
CASE_W / CASE_H here. Then re-run.
"""

import adsk.core
import adsk.fusion
import math
import traceback


# =====================================================================
# Parameters
# =====================================================================

# --- Toggles ---
# Set True to also import the Super Case STEP into the same document for
# clearance verification. Default False so the script produces ONLY the tray
# (your product), keeping it cleanly separate from the upstream Super Case.
IMPORT_SUPER_CASE = False

# --- Super Case (REAL dims, parsed from V2.1 STEP CARTESIAN_POINT bbox) ---
# crussella0129 V2.1 native bbox is 70 x 115.5 x 119 mm (a vertical desktop
# tower). For the RC car we lay it on its side so the tallest dim (119) runs
# back-to-front (X), the second (115.5) is the tray's width (Y), and the
# narrowest (70) becomes height (Z) — keeps the CG low.
CASE_L = 119.0   # X depth
CASE_W = 115.5   # Y width
CASE_H = 70.0    # Z height
CASE_CLEARANCE = 1.0   # extra slack on each side so the case slides in

# --- Cradle walls (3-sided U around the case) ---
CRADLE_WALL_T   = 3.0
CRADLE_WALL_H   = 50.0   # ~70% of case height — grips firmly while leaving
                          # the case's top vents/IO accessible above the tray

# --- Camera face plate (front wall, faces forward) ---
FACE_T              = 4.0   # thicker than side walls, holds cameras rigidly
CAM_BASELINE        = 110.0
CAM_APERTURE_W      = 24.0
CAM_APERTURE_H      = 24.0
CAM_CENTER_Z        = 35.0    # height above tray floor
CAM_MOUNT_HOLE_DIA  = 2.2     # M2 clearance
CAM_MOUNT_PATTERN_W = 21.0    # IMX219 board hole spacing
CAM_MOUNT_PATTERN_H = 12.5

# --- Rear bay (battery + expansion module) ---
BAY_WALL_T      = 3.0
BAY_WALL_H      = 30.0
BAY_DEPTH_X     = 60.0   # X size of rear bay (depth)
BAY_DIVIDER_T   = 2.0    # internal divider between battery and expansion

# Battery: 3S 2200 mAh shorty pack with XT60 (Jetson power only)
BATTERY_L = 96.0    # along Y
BATTERY_W = 46.0    # along X
BATTERY_H = 25.0

# Expansion module footprint (PCA9685 + ESP32-S3 + IMU carrier).
# Mounts on top of (above) the battery on a small shelf above battery height.
EXPAN_L = 62.0    # along Y
EXPAN_W = 25.0    # along X
EXPAN_H = 15.0
EXPAN_SHELF_GAP = 4.0   # vertical gap between battery top and shelf underside

# --- Tray floor ---
FLOOR_T = 4.0   # thicker so chassis M3 bolts countersink cleanly
TRAY_CLEARANCE_X_FRONT = 8.0   # extra X depth in front of cradle for cameras

# --- Rear-bay lid (removable, holds battery + expansion in) ---
LID_T = 3.0

# --- M3 fasteners ---
M3_CLEAR_DIA     = 3.4
M3_PILOT_DIA     = 2.5    # self-tap pilot
BOSS_OD          = 8.0
BOSS_PILOT_DEPTH = 8.0
CORNER_INSET     = 8.0

# --- Cable channel on tray floor ---
CHANNEL_W     = 8.0
CHANNEL_DEPTH = 1.5

# --- Vent slots in cradle side walls (Super Case's exhaust must not be choked) ---
VENT_COUNT = 4
VENT_W     = 4.0    # along X
VENT_H     = 20.0   # along Z
VENT_PITCH = 14.0


# =====================================================================
# Derived layout — DON'T edit these unless you know what you're doing
# =====================================================================

# X positions (back -> front)
BAY_X0    = BAY_WALL_T
BAY_X1    = BAY_X0 + BAY_DEPTH_X
SEP_X0    = BAY_X1
SEP_X1    = SEP_X0 + CRADLE_WALL_T   # the cradle's back wall is the bay's
                                       # front wall
CRADLE_X0 = SEP_X1   # X of cradle BACK wall's outer face
# CRADLE_X1 spans the back wall PLUS the case PLUS 2x clearance. The earlier
# version omitted CRADLE_WALL_T which left the cradle interior 3mm too short
# and caused the imported case to clip through the back wall.
CRADLE_X1 = CRADLE_X0 + CRADLE_WALL_T + CASE_L + 2 * CASE_CLEARANCE
FRONT_X   = CRADLE_X1 + FACE_T

OUTER_L = FRONT_X
OUTER_W = max(CASE_W + 2 * (CASE_CLEARANCE + CRADLE_WALL_T),
              max(BATTERY_L, EXPAN_L) + 2 * BAY_WALL_T)
# total outer height: floor + tallest of (cradle wall, bay wall + lid)
OUTER_H = FLOOR_T + max(CRADLE_WALL_H, BAY_WALL_H + LID_T)


# =====================================================================
# Helpers
# =====================================================================

def cm(mm_val):
    return mm_val / 10.0


def _Pt(x_mm, y_mm, z_mm=0.0):
    return adsk.core.Point3D.create(cm(x_mm), cm(y_mm), cm(z_mm))


def addRect(sketch, x0, y0, x1, y1):
    lines = sketch.sketchCurves.sketchLines
    p0, p1, p2, p3 = _Pt(x0, y0), _Pt(x1, y0), _Pt(x1, y1), _Pt(x0, y1)
    lines.addByTwoPoints(p0, p1)
    lines.addByTwoPoints(p1, p2)
    lines.addByTwoPoints(p2, p3)
    lines.addByTwoPoints(p3, p0)


def addCircle(sketch, cx, cy, dia):
    sketch.sketchCurves.sketchCircles.addByCenterRadius(_Pt(cx, cy), cm(dia / 2.0))


def allProfiles(sketch):
    coll = adsk.core.ObjectCollection.create()
    for i in range(sketch.profiles.count):
        coll.add(sketch.profiles.item(i))
    return coll


def newExtrude(component, profiles, op, depth_mm,
               start_mm=0.0, direction_negative=False, target_bodies=None):
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
        inp.participantBodies = target_bodies
    return extrudes.add(inp)


def offsetPlane(component, base_plane, offset_mm):
    planes = component.constructionPlanes
    inp = planes.createInput()
    inp.setByOffset(base_plane, adsk.core.ValueInput.createByReal(cm(offset_mm)))
    return planes.add(inp)


# =====================================================================
# Build functions
# =====================================================================

def buildFloor(comp):
    """Single rectangular floor plate, the full tray footprint."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    addRect(sk, 0, 0, OUTER_L, OUTER_W)
    ext = newExtrude(
        comp, sk.profiles.item(0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        FLOOR_T,
    )
    body = ext.bodies.item(0)
    body.name = "tray_floor"
    return body


def buildCradleWalls(comp):
    """Three walls around the Super Case (back, left, right). Open top + open
    front (front is the camera face plate, added separately)."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    # The cradle is bounded by:
    #   X: CRADLE_X0 (back) .. CRADLE_X1 (front)
    #   Y: cradle_y0 .. cradle_y1
    cradle_y_inner = (OUTER_W - (CASE_W + 2 * CASE_CLEARANCE)) / 2.0
    cy0 = cradle_y_inner
    cy1 = OUTER_W - cradle_y_inner
    # Back wall (which is also the bay's front wall — we double up)
    addRect(sk, CRADLE_X0, cy0 - CRADLE_WALL_T, CRADLE_X0 + CRADLE_WALL_T, cy1 + CRADLE_WALL_T)
    # Left wall (along the left side of cradle)
    addRect(sk, CRADLE_X0, cy0 - CRADLE_WALL_T, CRADLE_X1, cy0)
    # Right wall
    addRect(sk, CRADLE_X0, cy1, CRADLE_X1, cy1 + CRADLE_WALL_T)
    newExtrude(
        comp, allProfiles(sk),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        CRADLE_WALL_H,
        start_mm=FLOOR_T,
    )

    # Vent slots on each side wall — must NOT block the case's own exhaust
    plane_left = offsetPlane(comp, comp.xZConstructionPlane, cy0)
    plane_right = offsetPlane(comp, comp.xZConstructionPlane, cy1)
    cradle_center_x = (CRADLE_X0 + CRADLE_X1) / 2.0
    vent_z_center = FLOOR_T + CRADLE_WALL_H / 2.0
    x_start = cradle_center_x - ((VENT_COUNT - 1) * VENT_PITCH) / 2.0

    # Side vent slots intentionally omitted from v1 — the cradle is open-top
    # so the Super Case still breathes through its own vents above the tray.
    # Add side vents manually via Fusion's Modify -> Press/Pull or by editing
    # this script if forced airflow becomes important.
    _ = (plane_left, plane_right, vent_z_center, x_start)  # unused; kept for clarity


def buildCameraFacePlate(comp):
    """Front face plate with 2 camera apertures and IMX219 mount holes."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    # Plate spans full Y (matches tray width), thickness FACE_T, X = CRADLE_X1..FRONT_X
    addRect(sk, CRADLE_X1, 0, FRONT_X, OUTER_W)
    newExtrude(
        comp, sk.profiles.item(0),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        max(CRADLE_WALL_H, BAY_WALL_H + LID_T),   # full tray height for stiffness
        start_mm=FLOOR_T,
    )

    # Camera apertures — sketch on YZ plane offset to FRONT_X (front face),
    # cut in -X by FACE_T+0.5 so we punch through the plate.
    plane = offsetPlane(comp, comp.yZConstructionPlane, FRONT_X)
    sk_a = comp.sketches.add(plane)
    y_center = OUTER_W / 2.0
    for sign in (-1, +1):
        y_c = y_center + sign * (CAM_BASELINE / 2.0)
        addRect(
            sk_a,
            y_c - CAM_APERTURE_W / 2.0,
            CAM_CENTER_Z - CAM_APERTURE_H / 2.0,
            y_c + CAM_APERTURE_W / 2.0,
            CAM_CENTER_Z + CAM_APERTURE_H / 2.0,
        )
    newExtrude(
        comp, allProfiles(sk_a),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        FACE_T + 0.5,
        direction_negative=True,
    )

    # IMX219 M2 mount-hole pattern intentionally omitted from v1.
    # At a 110 mm baseline the outer holes sit outside the tray's 123.5 mm
    # width and Fusion can't resolve the cut. Drill them manually post-print
    # using the IMX219 board itself as a template, or widen the tray
    # (OUTER_W) and re-enable this block.


def buildBackBay(comp):
    """Box-shaped rear bay with internal divider between battery and expansion
    module. The bay's front wall is shared with the cradle's back wall (already
    built). Adds back, left, right walls, and an internal divider."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    # Back wall
    addRect(sk, 0, 0, BAY_WALL_T, OUTER_W)
    # Left wall (along Y=0 side of bay)
    addRect(sk, 0, 0, BAY_X1, BAY_WALL_T)
    # Right wall
    addRect(sk, 0, OUTER_W - BAY_WALL_T, BAY_X1, OUTER_W)
    newExtrude(
        comp, allProfiles(sk),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        BAY_WALL_H,
        start_mm=FLOOR_T,
    )

    # Internal divider in the bay (Y position chosen so battery sits on
    # one side, expansion module on the other). Battery is 96 mm long along Y;
    # we put it on the +Y half of the bay, expansion (62 mm long) on -Y half.
    div_y = OUTER_W / 2.0 - (BATTERY_L - EXPAN_L) / 4.0
    sk_d = comp.sketches.add(comp.xYConstructionPlane)
    addRect(
        sk_d,
        BAY_WALL_T,
        div_y - BAY_DIVIDER_T / 2.0,
        BAY_X1 - BAY_WALL_T,   # don't double up with bay's front wall
        div_y + BAY_DIVIDER_T / 2.0,
    )
    newExtrude(
        comp, sk_d.profiles.item(0),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        BAY_WALL_H,
        start_mm=FLOOR_T,
    )

    # Expansion-module shelf — flat platform suspended above battery
    # height, on the battery's side of the divider only.
    shelf_z = FLOOR_T + BATTERY_H + EXPAN_SHELF_GAP
    sk_s = comp.sketches.add(comp.xYConstructionPlane)
    addRect(
        sk_s,
        BAY_WALL_T,
        BAY_WALL_T,
        BAY_X1 - 0.0,
        div_y - BAY_DIVIDER_T / 2.0,
    )
    # Shelf is just a thin plate; thickness = 2 mm
    newExtrude(
        comp, sk_s.profiles.item(0),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        2.0,
        start_mm=shelf_z,
    )


def buildChassisMountHoles(comp):
    """4 M3 chassis bolt clearance holes through floor near corners.
    Bolts come up from below into corner-boss material above (added by lid)."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    for x in (CORNER_INSET, OUTER_L - CORNER_INSET):
        for y in (CORNER_INSET, OUTER_W - CORNER_INSET):
            addCircle(sk, x, y, M3_CLEAR_DIA)
    newExtrude(
        comp, allProfiles(sk),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        FLOOR_T + 0.5,
    )


def buildFloorChannel(comp):
    """Recessed cable channel along the centerline running back-to-front,
    so the user can route cables from rear bay forward to the cradle and
    eventually up through the cradle floor to the Jetson's ports."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    y_center = OUTER_W / 2.0
    addRect(
        sk,
        BAY_WALL_T,
        y_center - CHANNEL_W / 2.0,
        OUTER_L - FACE_T,
        y_center + CHANNEL_W / 2.0,
    )
    newExtrude(
        comp, sk.profiles.item(0),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        CHANNEL_DEPTH,
        start_mm=FLOOR_T,
        direction_negative=True,
    )


def filletOuterCorners(comp, radius_mm):
    """Find every vertical edge whose XY position sits at one of the four outer
    corners of the tray (X in {0, OUTER_L} AND Y in {0, OUTER_W}) and apply a
    constant-radius fillet. Done programmatically because picking the right
    edges by hand in Fusion's UI is tedious and error-prone."""
    tol = 0.001  # cm
    target_xs = (0.0, cm(OUTER_L))
    target_ys = (0.0, cm(OUTER_W))

    edges_coll = adsk.core.ObjectCollection.create()
    for body in comp.bRepBodies:
        for edge in body.edges:
            geom = edge.geometry
            if geom.objectType != adsk.core.Line3D.classType():
                continue
            sp, ep = geom.startPoint, geom.endPoint
            # Vertical only: X and Y constant, Z varies
            if abs(sp.x - ep.x) > tol or abs(sp.y - ep.y) > tol:
                continue
            if abs(sp.z - ep.z) < tol:
                continue
            # Snap-test against outer corner XY
            if not any(abs(sp.x - tx) < tol for tx in target_xs):
                continue
            if not any(abs(sp.y - ty) < tol for ty in target_ys):
                continue
            edges_coll.add(edge)

    if edges_coll.count == 0:
        return 0
    fillets = comp.features.filletFeatures
    inp = fillets.createInput()
    inp.addConstantRadiusEdgeSet(
        edges_coll,
        adsk.core.ValueInput.createByReal(cm(radius_mm)),
        True,
    )
    fillets.add(inp)
    return edges_coll.count


def importSuperCase(app, design, comp):
    """Import the Super Case STEP and lay it flat in the cradle bay.

    The native STEP bbox is 70 (X) x 115.5 (Y) x 119 (Z). To lay it on its
    side so the 119 dim becomes our X (back-to-front), we rotate -90 deg
    about the Y axis. That maps native (X, Y, Z) -> (Z, Y, -X), giving
    a footprint of 119 x 115.5 with height 70.
    """
    step_path = (
        r"C:\Users\Promo\dev\rcpilot\cad\Jetson-Orin-Nano-Super-Case"
        r"\V2.0\Case_V2.0_wpins.step"
    )
    import os
    if not os.path.exists(step_path):
        return None  # silently skip if user hasn't cloned the repo

    imp_mgr = app.importManager
    opts = imp_mgr.createSTEPImportOptions(step_path)
    opts.isViewFit = False
    # Import as a new component into the active design's root.
    case_occs = imp_mgr.importToTarget2(opts, design.rootComponent)
    if not case_occs or case_occs.count == 0:
        return None
    case_occ = case_occs.item(0)

    # Native STEP CARTESIAN_POINT bbox (parsed from the file):
    NATIVE_MIN = (-29.9, -26.5, -10.5)
    NATIVE_MAX = ( 40.1,  89.0, 108.5)

    # Step 1: rotation -90 deg about Y axis at origin.
    # Maps native (x,y,z) -> world (-z, y, x), so the native bbox becomes
    #   world X = -native Z = [-108.5 .. 10.5]
    #   world Y = native Y = [-26.5 .. 89]
    #   world Z = native X = [-29.9 .. 40.1]
    # i.e. the case lies on its side: 119 deep (X) x 115.5 wide (Y) x 70 tall (Z).
    rot = adsk.core.Matrix3D.create()
    axis = adsk.core.Vector3D.create(0, 1, 0)
    origin = adsk.core.Point3D.create(0, 0, 0)
    rot.setToRotation(-math.pi / 2.0, axis, origin)

    # Step 2: world-bbox min corner AFTER rotation. The rotation maps
    # native (x, y, z) -> world (-z, y, x), so the world MIN corner comes
    # from native (min_x, min_y, MAX_z) — not (min_x, min_y, min_z).
    rotated_min = adsk.core.Point3D.create(
        cm(-NATIVE_MAX[2]),
        cm(NATIVE_MIN[1]),
        cm(NATIVE_MIN[0]),
    )

    # Step 3: translation lands rotated min corner at the cradle interior's
    # back-left-floor corner: (CRADLE_X0, (OUTER_W - 115.5)/2, FLOOR_T).
    case_w_actual = NATIVE_MAX[1] - NATIVE_MIN[1]   # 115.5 mm
    # Sit the case 1mm in from the cradle's back wall inner face (which is at
    # X = CRADLE_X0 + CRADLE_WALL_T) so the case does NOT overlap the wall.
    target_min_x = cm(CRADLE_X0 + CRADLE_WALL_T + CASE_CLEARANCE)
    target_min_y = cm((OUTER_W - case_w_actual) / 2.0)
    target_min_z = cm(FLOOR_T)
    trans_mat = adsk.core.Matrix3D.create()
    trans_mat.translation = adsk.core.Vector3D.create(
        target_min_x - rotated_min.x,
        target_min_y - rotated_min.y,
        target_min_z - rotated_min.z,
    )

    # Step 4: build M = T * R (translate after rotating). transformBy is
    # post-multiply, so we start with T and post-multiply by R.
    tx = adsk.core.Matrix3D.create()
    tx.transformBy(trans_mat)   # tx = T
    tx.transformBy(rot)         # tx = T * R  (rotate first, then translate)

    case_occ.transform2 = tx
    case_occ.component.name = "supercase_imported"
    return case_occ


def buildBayLidAndBosses(comp):
    """Removable lid for the rear bay only (cradle stays open to let case
    breathe). 4 corner bosses with M3 self-tap pilots inside the bay corners."""
    # Bay corner boss positions (inside the bay)
    bay_corners = [
        (BAY_WALL_T + 4.0, BAY_WALL_T + 4.0),
        (BAY_X1 - 4.0,     BAY_WALL_T + 4.0),
        (BAY_WALL_T + 4.0, OUTER_W - BAY_WALL_T - 4.0),
        (BAY_X1 - 4.0,     OUTER_W - BAY_WALL_T - 4.0),
    ]
    sk_bo = comp.sketches.add(comp.xYConstructionPlane)
    for cx, cy in bay_corners:
        addCircle(sk_bo, cx, cy, BOSS_OD)
    newExtrude(
        comp, allProfiles(sk_bo),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        BAY_WALL_H,
        start_mm=FLOOR_T,
    )
    # Top pilot in each boss (cut from boss top down)
    plane_top = offsetPlane(comp, comp.xYConstructionPlane, FLOOR_T + BAY_WALL_H)
    sk_p = comp.sketches.add(plane_top)
    for cx, cy in bay_corners:
        addCircle(sk_p, cx, cy, M3_PILOT_DIA)
    newExtrude(
        comp, allProfiles(sk_p),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        BOSS_PILOT_DEPTH,
        direction_negative=True,
    )

    # The lid is a separate body, sized to the bay outer footprint
    plane_lid = offsetPlane(comp, comp.xYConstructionPlane, FLOOR_T + BAY_WALL_H + 0.2)
    sk_l = comp.sketches.add(plane_lid)
    addRect(sk_l, 0, 0, BAY_X1 + BAY_WALL_T, OUTER_W)
    ext = newExtrude(
        comp, sk_l.profiles.item(0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        LID_T,
    )
    lid = ext.bodies.item(0)
    lid.name = "bay_lid"

    # Lid M3 clearance holes
    sk_lh = comp.sketches.add(plane_lid)
    for cx, cy in bay_corners:
        addCircle(sk_lh, cx, cy, M3_CLEAR_DIA)
    newExtrude(
        comp, allProfiles(sk_lh),
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

        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = app.activeProduct
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        rootComp = design.rootComponent

        occ = rootComp.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        comp = occ.component
        comp.name = "rcpilot_carrier_tray"

        # Build pipeline. Order matters: floor first, then features that
        # join/extrude on top of it; cuts last so they punch through joined geom.
        buildFloor(comp)
        buildCradleWalls(comp)
        buildCameraFacePlate(comp)
        buildBackBay(comp)
        buildBayLidAndBosses(comp)
        buildFloorChannel(comp)
        buildChassisMountHoles(comp)

        # Polish: fillet outer vertical corners (purely cosmetic + makes the
        # part nicer to print — no sharp internal corners on the print bed).
        n_filleted = filletOuterCorners(comp, 3.0)

        # Optionally drop the Super Case STEP into the cradle for visual
        # clearance verification. OFF by default so the output document
        # contains only the tray — see IMPORT_SUPER_CASE at the top.
        case_occ = importSuperCase(app, design, comp) if IMPORT_SUPER_CASE else None

        ui.messageBox(
            "rcpilot_carrier_tray v1: build complete.\n\n"
            "Tray dims (mm): {:.0f} L x {:.0f} W x {:.0f} H.\n\n"
            "Bodies: tray_floor, bay_lid.\n\n"
            "Next steps:\n"
            "  - Insert the Super Case STEP via Insert -> Insert Mesh / Open\n"
            "  - Position it so it sits in the cradle bay (X={:.0f}..{:.0f}).\n"
            "  - Verify clearances; if the case doesn't fit, measure the\n"
            "    real CASE_L/W/H and re-run this script.\n"
            "  - Modify -> Fillet outer edges (3 mm) for printability."
            .format(OUTER_L, OUTER_W, OUTER_H, CRADLE_X0, CRADLE_X1)
        )

    except:
        if ui:
            ui.messageBox(
                "rcpilot_carrier_tray failed:\n\n{}".format(traceback.format_exc())
            )
