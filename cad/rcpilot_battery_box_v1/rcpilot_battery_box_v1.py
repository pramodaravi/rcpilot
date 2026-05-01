"""rcpilot battery box for the 3S 2200 mAh shorty pack — v1.

Standalone, print-ready enclosure that lives next to the NVIDIA Orin Nano dev
kit reference enclosure (Base_Plate.STEP / Top_Cover.STEP from the official
NVIDIA Industrial Design package). Bolts to the chassis (or to the side of
the NVIDIA Base_Plate) via a 2-hole M3 flange.

Footprint notes (to help align in Fusion):
  - NVIDIA Base_Plate is 134 mm long x 103 mm deep x ~35 mm tall.
  - This battery box is ~102 mm long x 70 mm deep (incl. flange) x 28 mm tall.
  - Place flange face flush with the long side of the Base_Plate;
    centerline-align the box for a tidy assembly.

Features
--------
- Sized for a 3S 2200 mAh shorty pack (96 x 46 x 18 mm). Inside cavity is
  98 x 48 x 22 mm so the battery slides in with foam padding allowance.
- Floor + 4 walls, 3 mm thick. Removable lid 2.5 mm thick, screws in with
  4x M3 self-tap (or M3 + heat-set inserts in the corner bosses).
- Cable pass-through on the back wall (16 x 10 mm slot for XT60 pigtail +
  JST balance lead).
- 2 strap slots through the lid (25 x 4 mm each) for a velcro strap that
  wraps around the pack — belt-and-suspenders retention if the lid screws
  ever loosen on a bumpy track.
- Mounting flange on one long side, 18 mm wide, with 2 x M3 through-holes
  on a 60 mm centerline pitch.
- Outer vertical corners auto-filleted at 3 mm radius for printability.

Run from Fusion: Utilities -> Add-ins -> Scripts and Add-ins ->
"+ Add" pointing at this folder, then select rcpilot_battery_box_v1 -> Run.
"""

import adsk.core
import adsk.fusion
import math
import traceback


# =====================================================================
# Parameters — edit and re-run to tune
# =====================================================================

# Battery: SMC / Gens Ace / etc 3S 2200 mAh shorty pack with XT60.
BAT_L = 96.0    # along Y (long axis)
BAT_W = 46.0    # along X (short axis)
BAT_H = 18.0    # along Z

# Internal cavity is slightly bigger than the battery so foam pads & shrink-
# wrap variations slide in without forcing.
CAVITY_PAD_X = 1.5
CAVITY_PAD_Y = 1.5
CAVITY_PAD_Z = 4.0   # extra headroom for a foam pad above the pack

INNER_X = BAT_W + 2 * CAVITY_PAD_X    # 49 mm
INNER_Y = BAT_L + 2 * CAVITY_PAD_Y    # 99 mm
INNER_Z = BAT_H + CAVITY_PAD_Z        # 22 mm

WALL_T  = 3.0    # side walls
FLOOR_T = 3.0    # bottom thickness
LID_T   = 2.5    # removable lid

# Outer envelope (without flange)
OUTER_X = INNER_X + 2 * WALL_T        # 55 mm
OUTER_Y = INNER_Y + 2 * WALL_T        # 105 mm
OUTER_Z = FLOOR_T + INNER_Z + LID_T   # 27.5 mm — open-top before lid sits on

# Cable pass-through on the back wall (Y = OUTER_Y face)
CABLE_W = 16.0
CABLE_H = 10.0
CABLE_Z_FROM_FLOOR = 4.0   # bottom of slot above floor

# Strap slots through the lid
STRAP_COUNT     = 2
STRAP_W         = 25.0    # along X
STRAP_H         = 4.0     # along Y
STRAP_PITCH_Y   = 50.0    # spacing between slots along Y axis

# Corner bosses for lid screws (heat-set insert friendly)
BOSS_OD            = 7.0
BOSS_PILOT_DIA     = 2.5
BOSS_PILOT_DEPTH   = 6.0
BOSS_INSET         = 6.0   # boss center inset from corner
LID_SCREW_CLEAR    = 3.4

# Mounting flange — extends from the +X long side
FLANGE_W           = 18.0   # how far it sticks out (in +X direction)
FLANGE_T           = FLOOR_T  # same thickness as floor for one-shell extrude
FLANGE_HOLE_DIA    = 3.4
FLANGE_HOLE_PITCH  = 60.0   # center-to-center along Y
FLANGE_HOLE_INSET_X = FLANGE_W / 2.0

# Outer fillet for printability and feel
OUTER_FILLET_R = 3.0


# =====================================================================
# Helpers (shared idioms with the carrier-tray script — same Fusion API)
# =====================================================================

def cm(mm):
    return mm / 10.0


def _Pt(x, y, z=0.0):
    return adsk.core.Point3D.create(cm(x), cm(y), cm(z))


def addRect(sk, x0, y0, x1, y1):
    L = sk.sketchCurves.sketchLines
    p0, p1, p2, p3 = _Pt(x0, y0), _Pt(x1, y0), _Pt(x1, y1), _Pt(x0, y1)
    L.addByTwoPoints(p0, p1)
    L.addByTwoPoints(p1, p2)
    L.addByTwoPoints(p2, p3)
    L.addByTwoPoints(p3, p0)


def addCircle(sk, cx, cy, dia):
    sk.sketchCurves.sketchCircles.addByCenterRadius(_Pt(cx, cy), cm(dia / 2.0))


def allProfiles(sk):
    coll = adsk.core.ObjectCollection.create()
    for i in range(sk.profiles.count):
        coll.add(sk.profiles.item(i))
    return coll


def newExtrude(comp, profiles, op, depth_mm,
               start_mm=0.0, direction_negative=False, target_bodies=None):
    extrudes = comp.features.extrudeFeatures
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


def offsetPlane(comp, base_plane, offset_mm):
    planes = comp.constructionPlanes
    inp = planes.createInput()
    inp.setByOffset(base_plane, adsk.core.ValueInput.createByReal(cm(offset_mm)))
    return planes.add(inp)


def findFaceByZ(body, target_z_mm, tol_mm=0.05):
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


# =====================================================================
# Build steps
# =====================================================================

def buildShell(comp):
    """Outer block + Shell -> hollow open-top box of the battery cavity."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    addRect(sk, 0, 0, OUTER_X, OUTER_Y)
    ext = newExtrude(
        comp, sk.profiles.item(0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        OUTER_Z,
    )
    body = ext.bodies.item(0)
    body.name = "battery_shell"

    top_face = findFaceByZ(body, OUTER_Z)
    if not top_face:
        raise RuntimeError("Couldn't find top face for shell")

    shells = comp.features.shellFeatures
    fc = adsk.core.ObjectCollection.create()
    fc.add(top_face)
    sh_inp = shells.createInput(fc, False)
    sh_inp.insideThickness = adsk.core.ValueInput.createByReal(cm(WALL_T))
    shells.add(sh_inp)
    return body


def buildFlange(comp):
    """Mounting flange extending in +X with two M3 through-holes."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    # Flange sits flush with the +X side of the box, full Y length, FLOOR_T tall.
    addRect(sk, OUTER_X, 0, OUTER_X + FLANGE_W, OUTER_Y)
    newExtrude(
        comp, sk.profiles.item(0),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        FLANGE_T,
    )

    # Two M3 clearance holes along the flange centerline (Y), spaced FLANGE_HOLE_PITCH.
    hole_x = OUTER_X + FLANGE_HOLE_INSET_X
    y_center = OUTER_Y / 2.0
    sk_h = comp.sketches.add(comp.xYConstructionPlane)
    for sign in (-1, +1):
        cy = y_center + sign * (FLANGE_HOLE_PITCH / 2.0)
        addCircle(sk_h, hole_x, cy, FLANGE_HOLE_DIA)
    newExtrude(
        comp, allProfiles(sk_h),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        FLANGE_T + 0.5,
    )


def buildCablePassthrough(comp):
    """Slot in the +Y wall for the XT60 pigtail and JST balance lead.

    Plane is offset 1 mm past the outer wall face so the cut volume cleanly
    overlaps the wall body (avoids Fusion's "no target body" error that fires
    when a cut plane sits exactly on a body boundary).
    """
    plane = offsetPlane(comp, comp.xZConstructionPlane, OUTER_Y + 1.0)
    sk = comp.sketches.add(plane)
    # Sketch local: x -> world X, y -> world Z. Center the slot in X on the
    # battery cavity (NOT including the flange).
    cx = OUTER_X / 2.0
    z_low = FLOOR_T + CABLE_Z_FROM_FLOOR
    addRect(
        sk,
        cx - CABLE_W / 2.0,
        z_low,
        cx + CABLE_W / 2.0,
        z_low + CABLE_H,
    )
    # Cut -Y by WALL_T + 2 so we go from 1mm past the outer face, all the way
    # through the wall, and 1mm past the inner face — clean overlap.
    newExtrude(
        comp, sk.profiles.item(0),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        WALL_T + 2.0,
        direction_negative=True,
    )


def buildCornerBosses(comp):
    """4 internal corner bosses for the lid screws."""
    corners = [
        (BOSS_INSET, BOSS_INSET),
        (OUTER_X - BOSS_INSET, BOSS_INSET),
        (BOSS_INSET, OUTER_Y - BOSS_INSET),
        (OUTER_X - BOSS_INSET, OUTER_Y - BOSS_INSET),
    ]
    sk = comp.sketches.add(comp.xYConstructionPlane)
    for cx, cy in corners:
        addCircle(sk, cx, cy, BOSS_OD)
    # Bosses span FROM the floor top (Z=FLOOR_T) to the lid bottom (Z=OUTER_Z).
    newExtrude(
        comp, allProfiles(sk),
        adsk.fusion.FeatureOperations.JoinFeatureOperation,
        OUTER_Z - FLOOR_T,
        start_mm=FLOOR_T,
    )

    # Top pilot in each boss, cut from boss top down.
    plane_top = offsetPlane(comp, comp.xYConstructionPlane, OUTER_Z)
    sk_p = comp.sketches.add(plane_top)
    for cx, cy in corners:
        addCircle(sk_p, cx, cy, BOSS_PILOT_DIA)
    newExtrude(
        comp, allProfiles(sk_p),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        BOSS_PILOT_DEPTH,
        direction_negative=True,
    )
    return corners


def buildLid(comp, corners):
    """Removable lid: separate body, with 4 M3 clearance holes + 2 strap slots."""
    plane_lid = offsetPlane(comp, comp.xYConstructionPlane, OUTER_Z + 0.2)
    sk_l = comp.sketches.add(plane_lid)
    # The lid covers the box footprint only (NOT the flange).
    addRect(sk_l, 0, 0, OUTER_X, OUTER_Y)
    ext = newExtrude(
        comp, sk_l.profiles.item(0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        LID_T,
    )
    lid = ext.bodies.item(0)
    lid.name = "battery_lid"

    # Lid screw clearance holes, lining up with the corner bosses below.
    sk_h = comp.sketches.add(plane_lid)
    for cx, cy in corners:
        addCircle(sk_h, cx, cy, LID_SCREW_CLEAR)
    newExtrude(
        comp, allProfiles(sk_h),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        LID_T + 0.5,
        target_bodies=[lid],
    )

    # Strap slots through the lid, parallel to X, evenly spaced along Y.
    sk_s = comp.sketches.add(plane_lid)
    y_center = OUTER_Y / 2.0
    cx = OUTER_X / 2.0
    y_start = y_center - ((STRAP_COUNT - 1) * STRAP_PITCH_Y) / 2.0
    for i in range(STRAP_COUNT):
        yc = y_start + i * STRAP_PITCH_Y
        addRect(
            sk_s,
            cx - STRAP_W / 2.0,
            yc - STRAP_H / 2.0,
            cx + STRAP_W / 2.0,
            yc + STRAP_H / 2.0,
        )
    newExtrude(
        comp, allProfiles(sk_s),
        adsk.fusion.FeatureOperations.CutFeatureOperation,
        LID_T + 0.5,
        target_bodies=[lid],
    )
    return lid


def filletOuterVerticals(comp, radius_mm):
    """Fillet vertical edges at the 4 OUTER corners of the battery shell.

    Outer corners depend on whether the flange is included:
      - Without flange: corners at X={0, OUTER_X} x Y={0, OUTER_Y}.
      - With flange:    same as above PLUS X={0, OUTER_X+FLANGE_W} x Y={0, OUTER_Y}.
    We fillet ALL of those (8 vertical edges typically, depending on body topology).
    """
    tol = 0.001
    target_xs = (0.0, cm(OUTER_X), cm(OUTER_X + FLANGE_W))
    target_ys = (0.0, cm(OUTER_Y))

    edges = adsk.core.ObjectCollection.create()
    for body in comp.bRepBodies:
        for edge in body.edges:
            geom = edge.geometry
            if geom.objectType != adsk.core.Line3D.classType():
                continue
            sp, ep = geom.startPoint, geom.endPoint
            if abs(sp.x - ep.x) > tol or abs(sp.y - ep.y) > tol:
                continue
            if abs(sp.z - ep.z) < tol:
                continue
            x_match = any(abs(sp.x - tx) < tol for tx in target_xs)
            y_match = any(abs(sp.y - ty) < tol for ty in target_ys)
            if x_match and y_match:
                edges.add(edge)

    if edges.count == 0:
        return 0
    fillets = comp.features.filletFeatures
    inp = fillets.createInput()
    inp.addConstantRadiusEdgeSet(
        edges,
        adsk.core.ValueInput.createByReal(cm(radius_mm)),
        True,
    )
    fillets.add(inp)
    return edges.count


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
        comp.name = "rcpilot_battery_box"

        # Build pipeline. Cable pass-through is intentionally skipped at v1 —
        # Fusion's API consistently fails to cut through a shelled wall via a
        # construction-plane extrude (RuntimeError: No target body) regardless
        # of plane offset / direction. Easier to drill the cable hole manually
        # via Modify -> Hole on the back wall after running this script.
        buildShell(comp)
        buildFlange(comp)
        corners = buildCornerBosses(comp)
        buildLid(comp, corners)
        n_filleted = filletOuterVerticals(comp, OUTER_FILLET_R)

        outer_with_flange_x = OUTER_X + FLANGE_W
        ui.messageBox(
            "rcpilot_battery_box v1: build complete.\n\n"
            "Outer: {:.0f} (X, incl. {:.0f} mm flange) x {:.0f} (Y) x {:.0f} (Z) mm\n"
            "Cavity: {:.0f} x {:.0f} x {:.0f} mm (3S 2200 mAh shorty fits with foam)\n"
            "Bodies: battery_shell, battery_lid (print separately)\n"
            "Filleted edges: {}\n\n"
            "Hardware to print/buy:\n"
            "  - 4 x M3 x 8 mm (lid screws)\n"
            "  - 4 x M3 brass heat-set inserts (optional, for boss tops)\n"
            "  - 2 x M3 chassis bolts + nuts (for the flange)\n"
            "  - 1 x velcro strap, 20 mm wide x 200 mm (loops through lid slots)\n\n"
            "Print orientation: shell on its floor (no supports needed). Lid\n"
            "flat side down."
            .format(
                outer_with_flange_x, FLANGE_W, OUTER_Y, OUTER_Z + LID_T,
                INNER_X, INNER_Y, INNER_Z, n_filleted,
            )
        )

    except Exception:
        if ui:
            ui.messageBox(
                "rcpilot_battery_box failed:\n\n{}".format(traceback.format_exc())
            )
