# Build board.kicad_sch using ONLY global labels (no wires) so no signal wire
# can cross a power pin and short nets. Same-named global labels are one net.
# Run:  cad run build_schematic.py
from cad import kicad_sch as ks
from kicode.geometry import Vector2
from kicode.schematic_types import GlobalLabel, SchematicLabelSpinStyle, NoConnectMarker

SCH = "board.kicad_sch"

PARTS = {
    "U1": ("custom:ESP32_C3_MINI_1", "custom:ESP32_C3_MINI_1", "ESP32-C3-MINI-1-N4"),
    "U2": ("custom:LSM6DSO_LGA14", "Package_LGA:Bosch_LGA-14_3x2.5mm_P0.5mm", "LSM6DSO"),
    "U3": ("custom:TP4056_SOP8EP", "Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.514x3.2mm", "TP4056"),
    "U4": ("custom:HT7833_SOT89", "Package_TO_SOT_SMD:SOT-89-5", "HT7833"),
    "Q1": ("custom:AO3401A_SOT23", "Package_TO_SOT_SMD:SOT-23-3", "AO3401A"),
    "D1": ("Device:D", "Diode_SMD:D_SOD-123", "B5819W"),
    "D2": ("Device:LED", "LED_SMD:LED_0603_1608Metric", "LED_0603_GREEN"),
    "J1": ("Connector:USB_B_Micro", "Connector_USB:USB_Micro-B_Molex-105133-0001", "USB_MICRO_B_SMD"),
    "J2": ("Connector:Micro_SD_Card", "Connector_Card:microSD_HC_Wuerth_693072010801", "MICROSD_PUSH_PUSH"),
    "J3": ("Connector_Generic:Conn_01x02", "Connector_JST:JST_SH_SM02B-SRSS-TB_1x02-1MP_P1.00mm_Horizontal", "JST_SH_2P"),
    "R1": ("Device:R", "Resistor_SMD:R_0603_1608Metric", "10k"),
    "R2": ("Device:R", "Resistor_SMD:R_0603_1608Metric", "1k"),
    "R3": ("Device:R", "Resistor_SMD:R_0603_1608Metric", "100k"),
    "R4": ("Device:R", "Resistor_SMD:R_0603_1608Metric", "10k"),
    "R5": ("Device:R", "Resistor_SMD:R_0603_1608Metric", "4k7"),
    "R6": ("Device:R", "Resistor_SMD:R_0603_1608Metric", "4k7"),
    "R7": ("Device:R", "Resistor_SMD:R_0603_1608Metric", "10k"),
    "C1": ("Device:C", "Capacitor_SMD:C_0805_2012Metric", "10uF"),
    "C2": ("Device:C", "Capacitor_SMD:C_0805_2012Metric", "10uF"),
    "C3": ("Device:C", "Capacitor_SMD:C_0805_2012Metric", "10uF"),
    "C4": ("Device:C", "Capacitor_SMD:C_0805_2012Metric", "10uF"),
    "C5": ("Device:C", "Capacitor_SMD:C_0603_1608Metric", "1uF"),
    "C6": ("Device:C", "Capacitor_SMD:C_0603_1608Metric", "100nF"),
    "C7": ("Device:C", "Capacitor_SMD:C_0603_1608Metric", "100nF"),
    "C8": ("Device:C", "Capacitor_SMD:C_0603_1608Metric", "1uF"),
}

PLACE = {
    "J1": (0, 0), "J3": (0, 60), "U3": (0, 130),
    "R1": (-40, 130), "C1": (40, 130), "C2": (-40, 170),
    "Q1": (70, 130), "R3": (115, 130), "D1": (70, 190),
    "U4": (0, 230), "R4": (-40, 230), "C8": (-40, 270), "C3": (40, 230), "C4": (40, 270),
    "U1": (150, 10), "C7": (200, 50), "C5": (150, 110),
    "U2": (150, 150), "C6": (200, 150), "R5": (100, 150), "R6": (100, 190),
    "J2": (150, 270), "R7": (100, 270),
    "R2": (200, 250), "D2": (240, 250),
}

def pin_at(sym, number):
    pin = next(p for p in sym.definition.pins if p.number == number)
    return sym.position + pin.position

def set_field(sym, name, value):
    getattr(sym, name + "_field").text.value = value

def label_pin(sym, pin_num, net):
    gl = GlobalLabel()
    gl.text.value = net
    gl.position = pin_at(sym, pin_num)
    gl.spin_style = SchematicLabelSpinStyle.SLSS_UP
    return [gl]

with ks.edit(SCH) as s:
    syms = {}
    for ref, (lib_id, fp, value) in PARTS.items():
        x, y = PLACE[ref]
        sym = s.place_symbol_from_library(lib_id, Vector2.from_xy_mm(x, y), reference=ref)
        set_field(sym, "footprint", fp)
        set_field(sym, "value", value)
        syms[ref] = sym

    items = []
    j1, j2, j3 = syms["J1"], syms["J2"], syms["J3"]

    def add(gen):
        items.extend(gen)

    def L(sym, pin, net):
        add(label_pin(sym, pin, net))

    # ---- power nets: label every pin (no wires, so nothing crosses) ----
    # VBUS
    L(j1, "1", "VBUS"); L(syms["U3"], "2", "VBUS"); L(syms["Q1"], "1", "VBUS")
    L(syms["R3"], "1", "VBUS"); L(syms["D1"], "2", "VBUS")   # D1 pin2 = anode
    L(syms["C1"], "1", "VBUS")
    # BAT
    L(j3, "1", "BAT"); L(syms["Q1"], "3", "BAT"); L(syms["U3"], "3", "BAT")
    L(syms["C2"], "1", "BAT")
    # VSYS
    L(syms["Q1"], "2", "VSYS"); L(syms["U4"], "3", "VSYS"); L(syms["U4"], "4", "VSYS")
    L(syms["D1"], "1", "VSYS")   # D1 pin1 = cathode
    L(syms["C3"], "1", "VSYS"); L(syms["C8"], "1", "VSYS")
    # 3V3
    L(syms["U1"], "1", "3V3"); L(syms["U2"], "7", "3V3"); L(syms["U2"], "9", "3V3")
    L(syms["U2"], "4", "3V3");   # CS high => I2C mode
    L(syms["U4"], "1", "3V3")
    L(syms["C4"], "1", "3V3"); L(syms["C6"], "1", "3V3"); L(syms["C7"], "1", "3V3")
    L(j2, "4", "3V3")            # VDD
    L(syms["R4"], "2", "3V3"); L(syms["R5"], "2", "3V3"); L(syms["R6"], "2", "3V3")
    L(syms["R7"], "2", "3V3")
    # GND
    L(syms["U1"], "2", "GND"); L(syms["U1"], "15", "GND")
    L(syms["U2"], "8", "GND"); L(syms["U2"], "1", "GND")    # SA0 low
    L(syms["U3"], "6", "GND"); L(syms["U4"], "2", "GND")
    L(j1, "5", "GND"); L(j1, "6", "GND"); L(j2, "6", "GND"); L(j3, "2", "GND")
    L(syms["C1"], "2", "GND"); L(syms["C2"], "2", "GND"); L(syms["C3"], "2", "GND")
    L(syms["C4"], "2", "GND"); L(syms["C5"], "2", "GND"); L(syms["C6"], "2", "GND")
    L(syms["C7"], "2", "GND"); L(syms["C8"], "2", "GND")
    L(syms["R1"], "2", "GND"); L(syms["R3"], "2", "GND")
    L(syms["D2"], "2", "GND")    # LED cathode
    # EN3V3 (ESP32 EN node)
    L(syms["U1"], "14", "EN3V3"); L(syms["R4"], "1", "EN3V3"); L(syms["C5"], "1", "EN3V3")
    # ---- signals: matching labels, no wires ----
    # I2C: IO4=SDA, IO5=SCL ; pullups R5/R6 to 3V3
    L(syms["U1"], "4", "SDA"); L(syms["U2"], "2", "SDA"); L(syms["R5"], "1", "SDA")
    L(syms["U1"], "5", "SCL"); L(syms["U2"], "3", "SCL"); L(syms["R6"], "1", "SCL")
    # SPI microSD: IO6=CLK, IO7=MOSI, IO8=MISO, IO9=CS ; R7 CS pullup
    L(syms["U1"], "6", "SCK"); L(j2, "5", "SCK")
    L(syms["U1"], "7", "MOSI"); L(j2, "3", "MOSI")
    L(syms["U1"], "8", "MISO"); L(j2, "7", "MISO")
    L(syms["U1"], "9", "CS"); L(j2, "2", "CS"); L(syms["R7"], "1", "CS")
    # TP4056 PROG -> R1 -> GND
    L(syms["U3"], "5", "PROG"); L(syms["R1"], "1", "PROG")
    # LED: IO21 -> R2 -> D2(anode) ; D2 cathode->GND
    L(syms["U1"], "13", "LED"); L(syms["R2"], "1", "LED"); L(syms["R2"], "2", "LED")
    L(syms["D2"], "1", "LED")
    # NC markers for unused pins
    for sym, pns in ((syms["U2"], ("5", "6", "10", "11", "12", "13", "14")),
                     (syms["U3"], ("1", "4", "7", "8")),
                     (syms["U4"], ("5",))):
        for pn in pns:
            nc = NoConnectMarker(); nc.position = pin_at(sym, pn); items.append(nc)
    L(j2, "9", "GND")   # microSD shield to GND
    # PWR_FLAG on off-board driven supplies (VBUS is driven by J1 connector output)
    pwr_pos = {"PWR2":(-14,44),"PWR3":(62,212),"PWR4":(-14,84)}
    for ref, pin, net in (("PWR2","1","BAT"),("PWR3","1","VSYS"),("PWR4","1","3V3")):
        ps = s.place_symbol_from_library("power:PWR_FLAG", Vector2.from_xy_mm(*pwr_pos[ref]), reference=ref)
        L(ps, pin, net)
    # NC markers for USB power-only and free/NC pins
    for sym, pns in ((syms["J1"], ("2", "3", "4")), (syms["J2"], ("1", "8")),
                     (syms["U1"], ("3", "10", "11", "12", "16", "17", "18", "19", "20"))):
        for pn in pns:
            nc = NoConnectMarker(); nc.position = pin_at(sym, pn); items.append(nc)

    items = [it for it in items if it is not None]
    s.create_items(items)

print("schematic built. symbols:", len(syms))
