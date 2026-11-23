# Print notes: S3 knee pods and strap plates

Generated from `common.py` by `build_all.py`. Change the dimensions in `common.py`, not here.
Exports are in `out/` (STEP, STL, 3MF per part). Every dimension marked VERIFY in `common.py`
must be measured with calipers before printing.

## Parts per kit (one set)

| Part | File stem | Material | Qty |
| --- | --- | --- | --- |
| Thigh pod body | `thigh_pod_body` | PETG | 1 |
| Thigh pod lid | `thigh_pod_lid` | PETG | 1 |
| Shank pod body | `shank_pod_body` | PETG | 1 |
| Shank pod lid | `shank_pod_lid` | PETG | 1 |
| Thigh strap plate | `strap_plate_thigh` | TPU 95A | 1 |
| Shank strap plate | `strap_plate_shank` | TPU 95A | 1 |

## Materials
- **Pods: PETG.** Heat- and sweat-tolerant, rigid, non-metallic (the IMU and the antenna
  are not shielded by the housing).
- **Strap plates: TPU 95A.** Flexible, comfortable against the skin, and it can bend to the
  limb under the strap tension.
- Tolerance on PETG pockets: 0.2 mm fit clearance is already in the model. Expect PETG
  shrink of about 0.2 to 0.3 %; print one test coupon (magnet pocket, Ø3.2 insert hole, the
  IMU pocket) and adjust `FIT` in `common.py` if a pocket is tight or loose.

## Orientation
- **Pod bodies:** bottom face (the magnet face, z = 0) on the bed. Cavity opens upward, so
  the walls and standoffs print vertically.
- **Lids:** outer face on the bed (flat, face-down). The axis arrow is engraved, not embossed,
  so the outer face is flat on the bed. The IMU pocket opens upward during printing.
- **Strap plates:** stand the plate on one of its curved ends (108 mm tall, 22 mm footprint).
  Face-down on its convex face needs about 6,300 mm^2 of support; on an end it needs about
  490 mm^2 (thigh) and 1,000 mm^2 (shank) of support.

## Supports
- **Pods:** none for walls and bosses. Bridges: 3 magnet pockets (Ø6.2 over the bed, about
  30 mm^2 each) and wall-opening lintels (USB, microSD slot, switch slot, button bore). If
  a lintel sags, add a support inside that opening only.
- **Lids:** none.
- **Strap plates:** support under the arc on the open side only (not on the magnet face).
  Use normal (not tree) supports, 0.2 mm Z gap, 60 % density. Remove by hand; the TPU
  flexes.

## Layers, walls, infill
- **Layer height:** 0.20 mm (PETG); 0.20 mm (TPU 95A), with the first layer at 0.25 mm.
- **Perimeters:** 3 for PETG pods (1.2 mm of perimeter, above the 1.6 mm minimum wall in the
  model's 2.0 mm walls); 3 for the TPU plate.
- **Infill:** pods 25 % gyroid; lids 30 %; strap plate 25 % gyroid.
- **Speeds:** TPU 95A 20 to 25 mm/s with direct drive; PETG at the default profile.

## Wall thickness check (as modelled)

| Rule | Value | Result |
| --- | --- | --- |
| Outer wall | 2.00 mm | ok (two perimeters minimum; printed with three) |
| Boss / standoff wall around Ø3.2 insert | 1.80 mm | ok |
| Lid skin over the IMU pocket | 2.30 mm | ok |
| Magnet floor under a 1.6 mm pocket | 1.60 mm | ok (floor is 3.2 mm) |
| Plate thickness | 3.00 mm | ok |
| Plate magnet skin | 1.90 mm | ok |

## Tolerances
- Pod magnets: Ø6.2 mm pocket for Ø6 mm x 1.5 mm N52 (0.1 mm radial). Pocket depth 1.6 mm.
- Plate magnets: Ø6.2 mm pocket for Ø6 mm x 1.0 mm N52, depth 1.1 mm.
- Heat-set inserts: Ø3.2 mm holes; the insert length is 3.0 mm in the standoffs (VERIFY the
  insert datasheet; the standoffs are 3.0 mm tall, the lid bosses take 4 mm).
- Lid screws: Ø2.4 mm clearance, M2 x 6 mm into the inserts.
- Strap slots: 5 mm wide x 40 mm long, for the 38 mm hook-and-loop strap.
- The pod's magnet face is flush with the plate's outer face at X = 0 (zero gap in the model; the magnet pockets hold the magnets).

## Magnets and assembly
- The pod and plate magnets must attract across the flush joint: mark the pole of each
  magnet with a marker, check that each pod pocket attracts its plate pocket, and glue all
  six with CA or 2-part epoxy after the test.
- Heat-set inserts: melt in with the iron at about 200 to 220 °C (VERIFY the datasheet
  for the chosen insert). Use the pilot hole in the boss, not a self-tap.

## Why heat-set inserts, not a snap-fit lid
The lid is opened often: to swap the LiPo, reflash the S3, and service the microSD card.
A PETG snap-fit lid with 2 mm walls loses its clip after repeated cycles and can crack at
the clip root, and a snap-fit lid holds no torque for the IMU pocket. M2 heat-set inserts
(four per pod) make the lid repeatable, and the lid is printed flat so the inserts sit
straight. The cost is one soldering-iron step per pod, which is already in the BOM.

## Known layout risks (see the design notes)
- The LiPo (50.5 x 35.5 mm) sits over the whole S3 footprint on pillars. Check the Wi-Fi /
  ESP-NOW range with the antenna end at the -X side. The LiPo covers the board, so the
  antenna sees metal foil.
- The pod is 88 x 58 mm. Its magnets are on the centre column (X = 0), and its underside is
  flat. The plate curves to the limb, so the pod ends lift off the plate by up to about
  12 mm (thigh, R = 80 mm). A curved pod underside is the next iteration.
- The microSD card slot sits on the -Y wall. It is reachable from outside only if the
  plate's strap does not cover the wall.

## Verify with calipers before printing
- LOLIN S3 Mini: board size (34.4 x 25.6), mounting-hole position and diameter, header and
  USB-C heights.
- GY-521 MPU6050: 21 x 16 x 2.5 mm.
- LiPo 503035: thickness (4.0 mm used; the cell may be 3.8 to 4.2 mm).
- TP4056 USB-C module: 26.5 x 17.5 mm, USB-C height, and the USB end.
- microSD SPI module: 25 x 15 x 4 mm, card-slot height and edge.
- SS12D00 switch: body 12 x 5 x 7 mm, lever orientation (the model puts the lever through the
  +Y wall).
- 6 x 6 tactile button: height 4.5 mm and the cap height.
- Heat-set insert OD and length.
- Thigh and shank radii at the strap (R_THIGH 80 mm, R_SHANK 65 mm).
- Strap width 38 mm; the plate's chord and width (108 x 60 mm) are placeholders.
