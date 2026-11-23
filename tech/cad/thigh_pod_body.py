"""Pod body (tray) for the thigh pod. Also builds the shank body with thigh=False.

Pod-local frame: origin at the bottom face centre, Z up. The magnet face is z = 0.
Features: S3 Mini on Ø6 standoffs with heat-set holes, LiPo on four pillars,
TP4056 with a USB-C opening (+X wall), microSD slot (-Y wall, thigh only),
switch lever slot (+Y wall), button bore (+Y wall, thigh only), LED light-pipe
bore (-X wall), four M2 heat-set bosses for the lid, three magnet pockets.

Print: PETG, bottom face on the bed. See tech/cad/PRINT_NOTES.md.
"""
import build123d as bd
import common as C


def _box(size, center):
    return bd.Box(size[0], size[1], size[2]).moved(bd.Pos(*center))


def _post(r, z0, z1, xy):
    """Vertical cylinder from z0 to z1 at xy. z0 sits inside the floor so it fuses."""
    return _cyl(r, z1 - z0, (xy[0], xy[1], (z0 + z1) / 2))


def _cyl(r, h, center, axis="z"):
    shape = bd.Cylinder(r, h)
    if axis == "x":
        shape = shape.rotate(bd.Axis.Y, 90)
    elif axis == "y":
        shape = shape.rotate(bd.Axis.X, 90)
    return shape.moved(bd.Pos(*center))


def build_body(thigh: bool = True) -> bd.Part:
    h_cav = C.CAV_Z_TOP
    outer = _box((C.OUT_X, C.OUT_Y, h_cav), (0, 0, h_cav / 2))
    cav_h = h_cav - C.FLOOR + 1.0
    cavity = _box((C.INT_X, C.INT_Y, cav_h), (0, 0, C.FLOOR + cav_h / 2))
    body = outer - cavity

    # Board standoffs with M2 heat-set holes (top of standoff at BOARD_BOTTOM_Z).
    for x, y in C.S3_HOLES:
        body = body + _post(C.S3_STANDOFF_D / 2, C.FLOOR - 0.5, C.BOARD_BOTTOM_Z, (x, y))
        hole_h = C.INSERT_DEPTH + 1.0
        body = body - _cyl(C.INSERT_HOLE_D / 2, hole_h,
                           (x, y, C.BOARD_BOTTOM_Z + 0.5 - hole_h / 2))

    # LiPo support pillars, top at LIPO_Z.
    for x, y in C.LIPO_PILLARS:
        body = body + _post(C.PILLAR_D / 2, C.FLOOR - 0.5, C.LIPO_Z, (x, y))

    # Lid bosses (heat-set inserts, 4 mm deep from the top).
    for x, y in C.BOSS_XY:
        body = body + _post(C.BOSS_D / 2, C.FLOOR - 0.5, h_cav, (x, y))
        body = body - _cyl(C.INSERT_HOLE_D / 2, 5.0, (x, y, h_cav - 2.0 + 0.5))

    # Magnet pockets on the bottom face (open at z = 0).
    for x, y in C.MAG_POS:
        body = body - _cyl(C.POD_MAG_D / 2, C.POD_MAG_POCKET + 1.0,
                           (x, y, (C.POD_MAG_POCKET - 1.0) / 2))

    # TP4056 USB-C opening on the +X wall (the module's short end faces +X).
    body = body - _box((6.0, 12.0, 6.8 - C.FLOOR + 0.5),
                       (C.OUT_X / 2 - 1.0, 0, (6.8 + C.FLOOR - 0.5) / 2))
    # Switch lever slot on the +Y wall.
    body = body - _box((6.0, 4.0, 3.5), (C.SW_CENTER[0], C.OUT_Y / 2 - 1.0, 5.75))
    # LED light-pipe bore through the -X wall.
    body = body - _cyl(C.LED_PIPE_D / 2, 6.0, (-C.OUT_X / 2, C.LED_PIPE_Y, C.LED_PIPE_Z), "x")

    if thigh:
        # microSD card slot on the -Y wall (card edge faces out).
        body = body - _box((16.0, 4.0, 4.5), (C.SD_CENTER[0], -C.OUT_Y / 2 + 1.0, C.FLOOR + 2.25))
        # Button bore on the +Y wall.
        body = body - _cyl(C.BTN_HOLE_D / 2, 6.0,
                           (C.BTN_CENTER[0], C.OUT_Y / 2 - 1.0, C.FLOOR + C.BTN_H / 2 + 0.25), "y")

    body.label = "thigh_pod_body" if thigh else "shank_pod_body"
    body.color = "#2f6f8f"
    return body


if __name__ == "__main__":
    b = build_body(True)
    print(b.bounding_box().size)
