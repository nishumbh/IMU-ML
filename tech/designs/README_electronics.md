# Electronics status: NOT fab-ready
Produced: draft BOM, draft top-side placement (CPL), circuit definition below.
NOT produced: schematic capture, routed copper, Gerbers. A routed, DRC/ERC-checked board was not
generated in this session (no EDA/PCB tool available). Do not send these files to a fab.

## Placement (board origin = centre, 40 mm dia, all parts TOP)
Checked by script: all parts inside r=19.4 mm (connector fronts at the edge), no courtyard overlaps (0.25 mm gap).
USB-B at +X edge, microSD at -X edge, LED D2 at (12,-8), ESP32 antenna end toward +Y (keep copper/ground clear under it).

## Power path
USB VBUS -> TP4056 IN (C1). TP4056 BAT <-> battery (C2) and Q1 drain.
VSYS = VBUS --D1(B5819W, anode VBUS, cathode VSYS)--> VSYS
Q1 AO3401A: Drain=BAT, Source=VSYS, Gate=VBUS with R3 100k gate-to-GND.
 USB present: gate high, Q1 off, system runs from USB via D1.  USB absent: gate low, Q1 on, battery -> VSYS.
 (Drain=BAT orients the body diode BAT->VSYS so VSYS can't back-feed the cell.)
VSYS -> HT7833 (U4) -> 3V3 (C4). RPROG R1=10k -> ~120 mA (set for ~300 mAh pack; 1200/R).
TP4056 has no cell protection: use cells with built-in PCM, or add DW01A+FS8205A.
CHRG/STDBY left to optional GPIO sensing; single status LED D2 on a GPIO via R2.

## Interfaces
I2C: IMU SDA/SCL to ESP32-C3 GPIO (e.g. IO4/IO5) w/ 4.7k pull-ups (add 2x 0603; not in BOM yet).
SPI microSD: SCK/MOSI/MISO/CS to free GPIO (e.g. IO6/IO7/IO2/IO10), 10k pull-up on CS, 100nF at socket.
USB-B is power only (C3 native USB pins IO18/19 could also be routed to it - optional).

## Known issues to resolve before layout
1. Missing in BOM: I2C pull-ups, SD pull-ups; add them (placement area is tight but available near U2/J2).
2. Antenna: module sits ~3 mm inside a TPU puck on top of a LiPo; expect range loss. Test it.
3. Back-side battery: bottom layer has no parts, but keep the solder-mask clean and tape/insulate vs the cell.
4. Quiescent leakage of R3 etc. is negligible; HT7833 Iq ~ 6 uA, fine.
