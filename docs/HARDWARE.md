# S3 knee pods: hardware

Status: **draft, not validated on hardware.** The schematics were checked by ERC and by reading the netlist back. Nothing here has been powered, measured or worn. This is a research prototype, not a medical device (see [Safety](#safety)).

The ESP32-C3 pod in `tech/designs/` (see `tech/designs/README_electronics.md`) is legacy. It is superseded by the two-pod ESP32-S3 design below and is not reused.

## 1. System overview

Two wireless pods, a thigh pod and a shank pod, each with a LOLIN S3 Mini, a GY-521 (MPU6050) IMU on I2C, and a 1S LiPo with a TP4056 + DW01 charger module. The thigh pod also has a microSD SPI breakout (the SD log store), a tactile button and a slide switch. The shank pod has no SD card and no button. The firmware and ESP-NOW link are described in `plan.md` (Phases 2 and 3).

```
  THIGH POD                                    SHANK POD
  +------------------------------------+       +------------------------------+
  | 1S LiPo -> TP4056+DW01 (OUT+)      |       | 1S LiPo -> TP4056+DW01 (OUT+)|
  |   USB-C port: charge only          |       |   USB-C port: charge only    |
  |   OUT+ -> slide SW -> SS14 -> 5V   |       |   OUT+ -> slide SW -> SS14   |
  |   USB 5V -> SS14 -> 5V             |       |   USB 5V -> SS14 -> 5V       |
  |                                    |       |                              |
  |  LOLIN S3 Mini (ESP32-S3)          | ESP-NOW  LOLIN S3 Mini (ESP32-S3)    |
  |   IO8/IO9 I2C -> GY-521 (0x68)     |<------>|   IO8/IO9 I2C -> GY-521 (0x69)|
  |   IO10-13 SPI -> microSD (SD log)  |       |                              |
  |   IO4 <- button (internal pull-up) |       |   IO4 unused                 |
  |   IO5 <- battery divider 100k/100k |       |   IO5 <- battery divider     |
  +------------------------------------+       +------------------------------+
```

The thigh pod owns the SD card and runs inference (Phase 3). Pod-to-pod data goes over ESP-NOW.

## 2. Power architecture

Schematics: `tech/electronics_s3/thigh_pod.kicad_sch` and `tech/electronics_s3/shank_pod.kicad_sch`. These are wiring-level block schematics, not PCB layouts. The netlist was read back with `kicad_sch.read` and ERC is clean for both sheets (0 errors, 0 warnings).

### Battery path (cell powers the board)

```
cell (B+ / B-) -> TP4056 module B+/B- -> DW01 protection -> OUT+ (BAT_OUT)
  -> slide switch COM -> T1 (BAT_SW) -> SS14 (D2) anode
  -> SS14 cathode = SYS_5V -> LOLIN 5V pin -> on-board LDO -> 3V3
OUT- -> GND
```

The battery divider (R1 100k from BAT_OUT to ADC_BAT, R2 100k from ADC_BAT to GND) feeds the ADC pin (IO5, ADC1_CH4). It reads half the cell voltage (`BATTERY_DIVIDER_RATIO` 2.0 in `pins.h`).

### USB path (charge and run)

```
USB-C (TP4056 port) -> IN+ (VBUS) -> TP4056 charger -> B+ (cell)
VBUS -> SS14 (D1) anode -> cathode = SYS_5V -> LOLIN 5V pin
```

### The rule: charge via the TP4056 port only

**Charge the battery only through the TP4056 module's USB-C port.** Do not charge through the LOLIN's own USB-C port, and do not feed an unregulated 5 V into the 5V pin, SYS_5V or the battery pads. The TP4056 provides the constant-current / constant-voltage charge profile and the DW01 protection. The LOLIN port is for programming and the serial monitor.

### Why there is no back-feed

- With USB connected, SYS_5V sits at about VBUS minus one diode drop (about 4.7 V). The battery diode D2 is then reverse-biased (cell at about 4.2 V or lower), so USB does not push current into the cell through the load path.
- D1 blocks SYS_5V from flowing back into VBUS. The battery cannot reach VBUS either.
- If the LOLIN's own USB-C 5V net is tied to SYS_5V (check the board schematic), D1 and D2 still stop that supply from reaching the TP4056 input or the cell.

These are reasoned from the schematic and the diode directions. They are not measured. Measure on the bench before first use (see section 6).

### Brownout risk (issue, see section 9)

The LOLIN's on-board LDO needs roughly 3.4 V or more at its input to hold 3.3 V (plan figure, verify against the board's LDO datasheet). D2 adds about 0.3-0.4 V at load. So the battery must stay above roughly **3.7 V under load** for the 3V3 rail to hold. Below that the pod browns out, which can corrupt an SD write. Treat the usable window as roughly 3.7-4.2 V.

## 3. Pin table

Source of truth: `firmware/knee_pod_s3/include/pins.h`. The schematics use these same numbers. Both pods use the same I2C and battery-ADC pins. Only the thigh pod uses the SD and button pins.

| Signal | LOLIN pin | Thigh pod | Shank pod | Notes |
|---|---|---|---|---|
| I2C SDA | IO8 | GY-521 SDA | GY-521 SDA | Both pods |
| I2C SCL | IO9 | GY-521 SCL | GY-521 SCL | Both pods |
| Battery ADC | IO5 (ADC1_CH4) | divider midpoint | divider midpoint | `PIN_BATTERY_ADC`; ADC1 is readable while Wi-Fi/ESP-NOW is on |
| Button | IO4 | tactile button to GND, internal pull-up | not used (no-connect) | `PIN_BUTTON`; thigh only |
| SD CS | IO10 | microSD CS | not used (no-connect) | `PIN_SD_CS`; thigh only |
| SD MOSI | IO11 | microSD MOSI | not used (no-connect) | `PIN_SD_MOSI`; thigh only |
| SD SCK | IO12 | microSD SCK | not used (no-connect) | `PIN_SD_SCK`; thigh only |
| SD MISO | IO13 | microSD MISO | not used (no-connect) | `PIN_SD_MISO`; thigh only |
| IMU address | (strap) | GY-521 AD0 to GND (0x68) | GY-521 AD0 to 3V3 (0x69) | Set by wiring, not a GPIO |
| Power | 5V pin | SYS_5V (D1 and D2) | SYS_5V (D1 and D2) | See section 2 |
| Power | 3V3 pin | GY-521 VCC, microSD VCC | GY-521 VCC | LDO output |
| Ground | GND (x2) | common GND | common GND | |

Not wired, on the board: the RGB LED (`PIN_RGB_LED`, GPIO47) is the LOLIN's on-board WS2812, driven by firmware. The BOOT button (GPIO0) is also on-board.

Pins avoided by `pins.h`: GPIO0, 3, 45, 46 (strapping); GPIO19, 20 (native USB); GPIO26-37 (in-package flash/PSRAM bus); GPIO39-42 (JTAG, kept free).

## 4. Bill of materials

Full list with quantities, example source types and rough prices: **`tech/BOM_S3.csv`**.

- Columns: `kit` (thigh, shank or both), `qty_per_kit`, `total_qty_set` (both pods, or the set for shared items), `part`, `spec`, `example_source`, `unit_price_estimate_usd`, `line_total_estimate_usd`, `notes`.
- Prices are **rough estimates** for planning only. Verify before ordering.
- Rough total for the set: about **$134** including tools, or about **$79** without tools (estimate, not a quote).
- The count of M2 inserts and screws is a **placeholder** until the Phase 5 CAD fixes the hole positions.

## 5. Assembly and wiring steps

1. **Bench first.** Do not connect a battery until the wiring is checked with a multimeter (continuity and no short between 3V3 and GND, and between SYS_5V and GND).
2. Solder the LOLIN S3 Mini header pins to the wires listed in section 3, or use female headers if you want the board removable.
3. Wire the GY-521: VCC to 3V3, GND to GND, SCL to IO9, SDA to IO8, AD0 to GND (thigh) or 3V3 (shank). Leave INT, XDA and XCL unconnected.
4. Wire the battery divider: 100k from BAT_OUT (the TP4056 OUT+ pad) to ADC_BAT, 100k from ADC_BAT to GND, ADC_BAT to IO5.
5. Wire the slide switch between OUT+ and D2's anode. Wire D2's cathode and D1's cathode to SYS_5V, and to the LOLIN 5V pin. Wire D1's anode to the TP4056 IN+ (VBUS).
6. Wire the battery through a JST-PH pigtail: red to TP4056 B+, black to B-. Confirm polarity with a multimeter before connecting.
7. **Thigh pod only:** wire the microSD breakout (VCC to 3V3, GND to GND, CS to IO10, MOSI to IO11, SCK to IO12, MISO to IO13). Wire the button from IO4 to GND. No external pull-up: IO4 uses the firmware's internal pull-up.
8. Do not add I2C pull-ups. Most GY-521 boards carry 4.7k pull-ups already (`pins.h`), and adding more makes them too strong. Check the microSD breakout the same way.
9. Mechanical: heat-set the M2 inserts into the printed pod (Phase 5), mount the modules, and secure the cell away from the electronics. Insulate the cell.
10. Secure all wires with strain relief. Do not run a cell or charge lead under the strap or across the skin.

## 6. Bring-up checks

Do these before any firmware or any wearing:

- Battery unplugged, USB only: 5V pin at about 4.7-5.0 V, 3V3 at 3.3 V.
- Battery only (switch on, USB off): SYS_5V about 0.3 V below the cell; 3V3 holds 3.3 V down to about 3.7 V cell.
- Cell voltage at ADC_BAT is about half the cell voltage.
- USB plugged in with the battery connected: no reverse current into the cell from the load path (measure the current in the B+ lead, which should be the charge current only).
- Confirm the TP4056 charge current setting (section 9, issue 1).
- I2C scan finds 0x68 (thigh) or 0x69 (shank).
- Serial monitor works on the LOLIN USB-C port.

## 7. Safety

- **Not medical-grade.** These pods are a research prototype. They are not cleared or validated for clinical use. Do not use the outputs to decide how far to bend a surgically repaired knee or whether to keep exercising. Follow the treating clinician's prescribed range, load and protocol. See `docs/TWO_STRAP_TEAM_GUIDE.md` section 7.
- **Protected cells only.** Use a cell with built-in protection (or the DW01 module). Never use an unprotected cell. Never short, puncture, crush, heat or overcharge a LiPo.
- **Do not charge unattended.** Charge on a non-combustible surface, away from flammables, and supervise the charge.
- **Charge via the TP4056 port only** (section 2).
- **Do not put the pods on broken skin, on or near the incision, or over wounds.** Check skin under the straps regularly. Stop use if there is irritation or pain.
- **Battery handling.** Store cells at room temperature and partly charged. Dispose of damaged cells as hazardous waste.
- **Data.** SD logs contain motion recordings. Keep them on a controlled computer and do not commit patient recordings to git (see the team guide).

## 8. Files

| File | Purpose |
|---|---|
| `tech/electronics_s3/thigh_pod.kicad_sch` | Thigh pod wiring schematic (KiCad 9) |
| `tech/electronics_s3/shank_pod.kicad_sch` | Shank pod wiring schematic (KiCad 9) |
| `tech/electronics_s3/parts_s3.kicad_sym` | Project symbol library for module blocks |
| `tech/electronics_s3/out/*.pdf`, `*.svg` | Exported sheets |
| `tech/BOM_S3.csv` | Bill of materials for both pods |

## 9. Issues and verify before ordering

1. **TP4056 charge current (high priority).** Many TP4056 modules ship with a 1 A charge setting, which is about 2C for a 500 mAh cell. Set the charge current to 1C or below (about 500 mA or less, and lower is safer) by changing the PROG resistor, and check the datasheet value. This is not yet checked on the purchased module.
2. **Brownout window.** The SS14 battery path plus the LDO leaves about 3.7 V as the effective cut-off under load. Options to evaluate: a regulated boost to 3.3 V, or a lower-drop ideal-diode path. This is a design decision, not yet made.
3. **SS14 count.** The plan lists 2 SS14 diodes in total. This design needs two per pod (4 in total): one on the USB path and one on the battery path.
4. **LOLIN 5V net.** Check on the board's schematic whether the 5V pin is directly tied to its own USB-C VBUS. If it is, the USB-C port can also power SYS_5V. The diodes keep that from reaching the cell or the TP4056 input, but confirm it.
5. **Battery divider drain.** The divider draws about 21 uA continuously from the cell, because it sits before the slide switch. Move it behind the switch if zero drain when off matters more than reading the battery when off.
6. **Module pinout.** Verify the TP4056 module's pad labels (B+, B-, IN+, IN-, OUT+, OUT-) against the actual board. The DW01 output (OUT+/OUT-) is the protected side used for the load.
7. **GY-521 pull-ups.** Confirm the purchased module has its 4.7k pull-ups, and add none (`pins.h`).
8. **microSD breakout voltage.** Confirm the breakout is 3.3 V-safe on its logic lines.
9. **Protected cell.** Confirm the 503035 cell is protected (or rely on the TP4056 DW01 module) and that its size fits the Phase 5 enclosure.
10. **Pins.** The schematics match `pins.h` as it stood when this was written. Re-check if `pins.h` changes, and check the GPIO choices against the LOLIN silkscreen before soldering.
11. **Schematic layout.** The sheets are wiring-level blocks with net labels. They are correct (ERC clean, netlist checked), but the drawing is not tidy. Redraw if they go to a team review.
12. **M2 insert and screw counts** are placeholders (section 4).
