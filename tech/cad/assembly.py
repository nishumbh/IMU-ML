"""Assemble one pod on its strap plate, place the component envelopes and check fit.

Reports: bounding boxes, every envelope-vs-body and envelope-vs-envelope overlap
(volume), envelopes outside the cavity, and the closest gap between components.
Exits with status 1 if any interference is found.

Run: .venv/bin/python tech/cad/assembly.py
"""
import sys

import build123d as bd
import common as C
from thigh_pod_body import build_body
from thigh_pod_lid import build_lid
from strap_mount import build_plate


def _box(size, lo):
    """Box of `size` whose minimum corner is `lo` (x, y, z)."""
    cx = lo[0] + size[0] / 2
    cy = lo[1] + size[1] / 2
    cz = lo[2] + size[2] / 2
    return bd.Box(size[0], size[1], size[2]).moved(bd.Pos(cx, cy, cz))


def envelopes(thigh: bool) -> dict[str, bd.Part]:
    """Component envelopes in pod-local coordinates (z = 0 magnet face)."""
    z_board = C.BOARD_BOTTOM_Z
    bx, by = C.BOARD_CENTER
    env = {}
    env["S3 board"] = _box((C.S3_L, C.S3_W, C.S3_T),
                           (bx - C.S3_L / 2, by - C.S3_W / 2, z_board))
    env["S3 top parts"] = _box((C.S3_L, C.S3_W, C.S3_KEEP_TOP),
                               (bx - C.S3_L / 2, by - C.S3_W / 2, z_board + C.S3_T))
    lx, ly = C.LIPO_CENTER
    env["LiPo"] = _box((C.LIPO_L, C.LIPO_W, C.LIPO_T),
                       (lx - C.LIPO_L / 2, ly - C.LIPO_W / 2, C.LIPO_Z))
    tx, ty = C.TP_CENTER
    env["TP4056"] = _box((C.TP_L, C.TP_W, C.TP_H),
                         (tx - C.TP_L / 2, ty - C.TP_W / 2, C.FLOOR))
    sx, sy = C.SW_CENTER
    env["switch"] = _box((C.SW_L, C.SW_W, C.SW_H),
                         (sx - C.SW_L / 2, sy - C.SW_W / 2, C.FLOOR))
    if thigh:
        qx, qy = C.SD_CENTER
        env["microSD"] = _box((C.SD_L, C.SD_W, C.SD_H),
                              (qx - C.SD_L / 2, qy - C.SD_W / 2, C.FLOOR))
        bx2, by2 = C.BTN_CENTER
        env["button"] = _box((C.BTN_L, C.BTN_W, C.BTN_H),
                             (bx2 - C.BTN_L / 2, by2 - C.BTN_W / 2, C.FLOOR))
    px, py = C.IMU_POCKET_XY
    env["MPU6050 (in lid)"] = _box((C.MPU_L, C.MPU_W, C.MPU_H),
                                   (px - C.MPU_L / 2, py - C.MPU_W / 2, C.CAV_Z_TOP))
    return env


def check(thigh: bool = True, verbose: bool = True) -> int:
    name = "thigh" if thigh else "shank"
    body = build_body(thigh)
    lid = build_lid(thigh)
    plate = build_plate(C.R_THIGH if thigh else C.R_SHANK, f"strap_plate_{name}")
    env = envelopes(thigh)
    problems = []
    out = print if verbose else (lambda *a, **k: None)

    out(f"== {name} pod ==")
    for label, shape in (("body", body), ("lid", lid), ("strap plate", plate)):
        s = shape.bounding_box().size
        out(f"  {label:12s} bbox {s.X:7.2f} x {s.Y:6.2f} x {s.Z:6.2f} mm")

    # Envelopes against the body and the lid (pocket must be clear).
    for label, e in env.items():
        for other_label, other in (("body", body), ("lid", lid)):
            v = (e & other).volume
            if v > 1e-3:
                problems.append(f"{label} overlaps {other_label}: {v:.3f} mm^3")
        # Envelope must lie inside the cavity (below the lid underside, inside the walls)
        bb = e.bounding_box()
        if bb.min.X < -C.INT_X / 2 or bb.max.X > C.INT_X / 2 \
                or bb.min.Y < -C.INT_Y / 2 or bb.max.Y > C.INT_Y / 2:
            if label != "MPU6050 (in lid)":
                problems.append(f"{label} outside cavity footprint")
        if label != "MPU6050 (in lid)" and bb.max.Z > C.CAV_Z_TOP + 1e-6:
            problems.append(f"{label} above the lid underside ({bb.max.Z:.2f})")

    # Envelope against envelope (pairs that touch only at a face are allowed).
    items = list(env.items())
    min_gaps = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (na, a), (nb, b) = items[i], items[j]
            v = (a & b).volume
            if v > 1e-3:
                problems.append(f"{na} overlaps {nb}: {v:.3f} mm^3")
            try:
                gap = a.distance_to(b)
            except Exception:
                gap = float("nan")
            min_gaps.append((gap, na, nb))

    # Plate against the body (only the touching magnet face is allowed).
    v = (plate & body).volume
    if v > 1e-3:
        problems.append(f"strap plate overlaps body: {v:.3f} mm^3")

    close = sorted(min_gaps, key=lambda t: t[0] if t[0] == t[0] else 1e9)[:4]
    out("  closest component gaps (mm):")
    for g, a, b in close:
        out(f"    {g:6.2f}  {a} / {b}")
    out("  interferences:" + ("  none" if not problems else ""))
    for p in problems:
        out(f"    {p}")
    return len(problems)


if __name__ == "__main__":
    n = check(True) + check(False)
    sys.exit(1 if n else 0)
