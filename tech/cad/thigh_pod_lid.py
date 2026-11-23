"""Lid for the thigh pod (and shank with thigh=False).

Flat 4.2 mm plate, printed face-down (outer face on the bed). Four M2 clearance holes
over the heat-set bosses. IMU pocket on the underside (depth 2.5 mm, so the MPU6050
sits inside the lid and is not in the cavity). The axis arrow is engraved 0.6 mm into
the top (not embossed): printed face-down, an embossed arrow would hold the whole outer
face 0.6 mm off the bed. The arrow points along the IMU +X axis.
"""
import build123d as bd
import common as C


def _box(size, center):
    return bd.Box(size[0], size[1], size[2]).moved(bd.Pos(*center))


def _cyl(r, h, center):
    return bd.Cylinder(r, h).moved(bd.Pos(*center))


def _arrow() -> bd.Part:
    L, W = C.AXIS_ARROW_LEN, C.AXIS_ARROW_W
    shaft, head = 0.9, 2.0
    pts = [
        (-L / 2, -shaft / 2), (L / 2 - head, -shaft / 2), (L / 2 - head, -W / 2),
        (L / 2, 0.0), (L / 2 - head, W / 2), (L / 2 - head, shaft / 2), (-L / 2, shaft / 2),
    ]
    with bd.BuildPart() as p:
        with bd.BuildSketch():
            bd.Polygon(*pts)
        bd.extrude(amount=C.AXIS_ARROW_H + 0.5)
    return p.part


def build_lid(thigh: bool = True) -> bd.Part:
    z0 = C.CAV_Z_TOP
    lid = _box((C.OUT_X, C.OUT_Y, C.LID_T), (0, 0, z0 + C.LID_T / 2))
    for x, y in C.BOSS_XY:
        lid = lid - _cyl(C.LID_HOLE_D / 2, C.LID_T + 2.0, (x, y, z0 + C.LID_T / 2))
    px, py = C.IMU_POCKET_XY
    pw, pd = C.MPU_L + C.FIT, C.MPU_W + C.FIT
    lid = lid - _box((pw, pd, C.MPU_POCKET_DEPTH + 0.01),
                     (px, py, z0 - 0.01 + (C.MPU_POCKET_DEPTH + 0.01) / 2))
    # Engraved arrow: cut from the top face, depth AXIS_ARROW_H.
    arrow = _arrow().moved(bd.Pos(px, py, C.OUT_Z - C.AXIS_ARROW_H))
    lid = lid - arrow
    lid.label = "thigh_pod_lid" if thigh else "shank_pod_lid"
    lid.color = "#2f6f8f"
    return lid


if __name__ == "__main__":
    print(build_lid(True).bounding_box().size)
