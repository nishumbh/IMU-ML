"""Shared parameters and component envelopes for the S3 knee pods.

Units: millimetres. Pod-local frame used by every pod file:
  X  = across the leg, along the strap length (circumferential)   pod length 88
  Y  = along the leg axis, across the strap width                  pod width  58
  Z  = outward, from the magnet face (z = 0 at the bottom) up to the lid top

Every envelope below is a deliberate over-estimate or a datasheet-style figure.
Anything not confirmed on the real part is marked VERIFY. Measure every marked
figure with calipers before printing; then edit this file and run build_all.py.
"""

# ---------------------------------------------------------------- process ----
FIT = 0.2             # fit clearance added to every pocket / cavity (mm)
WALL = 2.0            # wall thickness (>= 1.6 mm, i.e. 2 perimeters of 0.4 mm)
FLOOR = 3.2           # floor thickness: 1.6 mm under the 1.6 mm magnet pocket
LID_T = 4.8           # lid plate thickness (2.5 mm IMU pocket, 2.3 mm skin, 0.6 mm engraving)
MIN_WALL = 1.6

# ------------------------------------------------------------ interior -------
INT_X = 84.0          # cavity length, X (clearance included)
INT_Y = 54.0          # cavity width, Y
CAV_Z_TOP = 15.4      # underside of lid (cavity ceiling); LiPo top 15.2 + 0.2
OUT_X = INT_X + 2 * WALL     # 88
OUT_Y = INT_Y + 2 * WALL     # 58
OUT_Z = CAV_Z_TOP + LID_T    # total pod height above the magnet face

# ------------------------------------------------------------ components -----
# LOLIN S3 Mini (ESP32-S3, 4 MB flash). Board: 34.3 x 25.4 mm nominal, ~1.6 mm PCB.
# Board outline is 34.4 x 25.6 to add clearance. Mounting holes are assumed at
# 2.5 mm from the corners, dia 2.5. VERIFY board size, hole positions, header height.
S3_L, S3_W, S3_T = 34.4, 25.6, 1.6
S3_KEEP_TOP = 3.2     # USB-C plus parts above the board (VERIFY)
S3_KEEP_BOT = 2.5     # header pins below the board if headers are fitted (VERIFY)
S3_HOLE_INSET = 2.5
S3_HOLE_D = 2.5
BOARD_BOTTOM_Z = FLOOR + 3.0  # board underside sits on 3 mm standoffs (6.0)
BOARD_CENTER = (-18.0, 0.0)   # X, Y of the S3 board centre in the pod

# GY-521 MPU6050 breakout: about 21 x 16 mm. Lives in a pocket in the lid.
# VERIFY size with calipers (pin headers add height).
MPU_L, MPU_W, MPU_H = 21.0, 16.0, 2.5
MPU_POCKET_DEPTH = 2.5  # pocket into the lid underside; sensor sits flush

# 503035 LiPo, nominal 50 x 35 x 3.5 mm. Envelope takes a 4.0 mm max thickness.
# VERIFY: some 503035 cells are 3.8-4.2 mm thick; calipers the actual cell.
LIPO_L, LIPO_W, LIPO_T = 50.5, 35.5, 4.0
LIPO_CENTER = (-12.0, 0.0)
LIPO_Z = 11.2         # LiPo underside: board top 6.2+1.6, parts to +3.2 = 11.0, +0.2

# TP4056 USB-C charger module, about 26 x 17 mm. USB-C on the short end (+X).
# VERIFY: USB-C connector height above the board is typically 3.2 mm.
TP_L, TP_W, TP_H = 26.5, 17.5, 3.5
TP_CENTER = (25.25, 0.0)

# microSD SPI breakout, about 25 x 15 mm. Card slot faces the -Y wall. Thigh only.
# VERIFY: slot height and whether the module has a 3.3 V level shifter.
SD_L, SD_W, SD_H = 25.0, 15.0, 4.0
SD_CENTER = (22.4, -17.5)

# SS12D00 slide switch: about 12 x 5 x 6 mm body, lever sticks out 1-2 mm.
# Orientation assumed: lever points +Y through the +Y wall. VERIFY orientation.
SW_L, SW_W, SW_H = 12.0, 5.0, 7.0
SW_CENTER = (-20.0, 24.0)   # body sits against the +Y wall
SW_LEVER_SLOT = (6.0, 4.0)  # width along X, height in Z

# 6x6 mm tactile button, 4.5 mm body height (VERIFY stem height and cap).
# Thigh only. Cap faces the +Y wall, with a Ø6.5 hole in the wall.
BTN_L, BTN_W, BTN_H = 6.0, 6.0, 4.5
BTN_CENTER = (-6.0, 23.5)
BTN_HOLE_D = 6.5

# RGB LED light pipe: 1.0 mm clear PETG rod in a Ø1.3 bore through the -X end wall.
LED_PIPE_D = 1.3
LED_PIPE_Z = 8.0
LED_PIPE_Y = 0.0

# ----------------------------------------------------------- LiPo support ----
# Four Ø5 pillars from the floor carry the LiPo over the board.
LIPO_PILLARS = [(-35.5, -16.0), (-35.5, 16.0), (6.0, -16.0), (6.0, 16.0)]
PILLAR_D = 5.0

# ----------------------------------------------------------- fasteners -------
# M2 heat-set inserts (OD 3.2 mm, length about 3.5-4 mm; VERIFY against the datasheet).
INSERT_HOLE_D = 3.2
INSERT_DEPTH = 3.0    # standoffs are only 3 mm tall; lid bosses take 4 mm
BOSS_D = 6.8
BOSS_XY = [(-39.0, -24.0), (-39.0, 24.0), (39.0, -24.0), (39.0, 24.0)]
LID_HOLE_D = 2.4      # M2 clearance hole in the lid

# Board standoffs: four Ø6.8 bosses under the S3 mounting holes (1.8 mm wall around the insert).
S3_STANDOFF_D = 6.8
S3_HOLES = [
    (BOARD_CENTER[0] - S3_L / 2 + S3_HOLE_INSET, BOARD_CENTER[1] - S3_W / 2 + S3_HOLE_INSET),
    (BOARD_CENTER[0] + S3_L / 2 - S3_HOLE_INSET, BOARD_CENTER[1] - S3_W / 2 + S3_HOLE_INSET),
    (BOARD_CENTER[0] - S3_L / 2 + S3_HOLE_INSET, BOARD_CENTER[1] + S3_W / 2 - S3_HOLE_INSET),
    (BOARD_CENTER[0] + S3_L / 2 - S3_HOLE_INSET, BOARD_CENTER[1] + S3_W / 2 - S3_HOLE_INSET),
]

# ------------------------------------------------------- magnets / plate ------
# Pod magnets: 3 x Ø6 x 1.5 mm N52, pocket Ø6.2 x 1.6 deep, opening at the magnet face.
# Plate magnets: 3 x Ø6 x 1.0 mm N52, pocket Ø6.2 x 1.1 deep from the outer face.
# Both sets sit on the centre column X = 0, Y = -16, 0, +16.
MAG_POS = [(0.0, -16.0), (0.0, 0.0), (0.0, 16.0)]
POD_MAG_D, POD_MAG_T = 6.2, 1.5
POD_MAG_POCKET = 1.6
PLATE_MAG_D, PLATE_MAG_T = 6.2, 1.0
PLATE_MAG_POCKET = 1.1

# Strap plate (TPU 95A). Curvature set per limb: R is the inner radius, the leg surface
# under the strap. Thigh and shank differ. VERIFY both with a tape measure.
PLATE_T = 3.0
PLATE_W = 60.0        # width along Y (strap sits in the 38 mm band)
PLATE_CHORD = 108.0   # length along X measured as chord
PLATE_SLOT_X = 48.0   # slot centre, |X|
PLATE_SLOT_W = 5.0    # slot width in X (two layers of strap)
PLATE_SLOT_L = 40.0   # slot length in Y, for the 38 mm strap
R_THIGH = 80.0        # VERIFY: thigh radius at the strap (about 50 cm circumference)
R_SHANK = 65.0        # VERIFY: shank radius at the strap (about 41 cm circumference)

# Strap: 38 mm hook-and-loop elastic, about 2 x 60 cm.
STRAP_W = 38.0

# --------------------------------------------------------- lid features ------
IMU_POCKET_XY = (26.5, 0.0)   # IMU pocket on the lid underside
AXIS_ARROW_LEN = 14.0
AXIS_ARROW_W = 3.0
AXIS_ARROW_H = 0.6            # engraved depth into the lid top (printed face-down)


def fit_size(length: float, width: float) -> tuple[float, float]:
    """Pocket size: component envelope plus the fit clearance on each side."""
    return length + FIT, width + FIT


# Stack-up sanity (fails at import if the layers stop fitting).
assert LIPO_Z >= BOARD_BOTTOM_Z + S3_T + S3_KEEP_TOP, "LiPo sits on the board components"
assert CAV_Z_TOP >= LIPO_Z + LIPO_T, "LiPo does not fit under the lid"
assert LID_T - MPU_POCKET_DEPTH - AXIS_ARROW_H >= MIN_WALL, "lid skin under arrow too thin"
