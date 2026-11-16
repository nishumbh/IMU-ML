# Build board.kicad_pcb: 40 mm diameter, outline, import netlist, place footprints by ref.
# Run:  cad run build_board.py
from cad import kicad_pcb as kpcb
from kicode.geometry import Vector2
from kicode.board_types import BoardCircle, BoardLayer, Angle

BRD = "board.kicad_pcb"
NETLIST = "fab/board.net"

# CPL placement (mm, y down) from placement_DRAFT_cpl.csv
CPL = {
    "U1": (3.0, 7.0), "J1": (16.7, 0.0), "J2": (-11.5, 0.0), "U2": (4.0, -6.5),
    "U3": (-2.5, -12.5), "U4": (-11.0, -11.5), "Q1": (4.5, -13.0), "D1": (9.5, -12.0),
    "D2": (12.0, -8.0), "J3": (-1.0, -5.0), "R1": (-6.5, -16.0), "R2": (12.0, -5.8),
    "R3": (8.0, -9.0), "R4": (-1.5, -2.2), "C1": (13.0, -12.0), "C2": (-7.2, -9.4),
    "C3": (0.0, -16.0), "C4": (-14.8, -9.5), "C5": (0.8, -2.2), "C6": (7.2, -6.5),
    "C7": (7.0, -3.5), "C8": (-9.0, -15.0),
}
ROT = {"J2": 180.0}

FOOTPRINT = {
    "U1": "custom:ESP32_C3_MINI_1", "U2": "Package_LGA:Bosch_LGA-14_3x2.5mm_P0.5mm",
    "U3": "Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.514x3.2mm",
    "U4": "Package_TO_SOT_SMD:SOT-89-5", "Q1": "Package_TO_SOT_SMD:SOT-23-3",
    "D1": "Diode_SMD:D_SOD-123", "D2": "LED_SMD:LED_0603_1608Metric",
    "J1": "Connector_USB:USB_Micro-B_Molex-105133-0001",
    "J2": "Connector_Card:microSD_HC_Wuerth_693072010801",
    "J3": "Connector_JST:JST_SH_SM02B-SRSS-TB_1x02-1MP_P1.00mm_Horizontal",
    "R1": "Resistor_SMD:R_0603_1608Metric", "R2": "Resistor_SMD:R_0603_1608Metric",
    "R3": "Resistor_SMD:R_0603_1608Metric", "R4": "Resistor_SMD:R_0603_1608Metric",
    "R5": "Resistor_SMD:R_0603_1608Metric", "R6": "Resistor_SMD:R_0603_1608Metric",
    "R7": "Resistor_SMD:R_0603_1608Metric",
    "C1": "Capacitor_SMD:C_0805_2012Metric", "C2": "Capacitor_SMD:C_0805_2012Metric",
    "C3": "Capacitor_SMD:C_0805_2012Metric", "C4": "Capacitor_SMD:C_0805_2012Metric",
    "C5": "Capacitor_SMD:C_0603_1608Metric", "C6": "Capacitor_SMD:C_0603_1608Metric",
    "C7": "Capacitor_SMD:C_0603_1608Metric", "C8": "Capacitor_SMD:C_0603_1608Metric",
}

with kpcb.edit(BRD) as b:
    # 1) Board outline: 40 mm diameter circle on Edge.Cuts, centred at origin.
    outline = BoardCircle()
    outline.layer = BoardLayer.BL_Edge_Cuts
    outline.center = Vector2.from_xy_mm(0, 0)
    outline.radius_point = Vector2.from_xy_mm(20, 0)
    b.create_items(outline)

    # 2) Import netlist (brings components + nets, no footprints yet).
    rep = b.import_netlist(NETLIST, dry_run=False, match_mode=1)  # NMM_REFERENCE
    print("netlist import:", getattr(rep, "report", str(rep))[:200])

    # 3) Place each footprint by reference.
    placed = {fp.reference_field.text.value: fp for fp in b.get_footprints()}
    moved = []
    for ref, (x, y) in CPL.items():
        if ref in placed:
            fp = placed[ref]
            # Set correct footprint if not already set
            if hasattr(fp, 'lib_id'):
                pass
            # Actually need to update footprint via library - use place from library or update
            # For now just move existing footprints
            fp.position = Vector2.from_xy_mm(x, y)
            if ref in ROT:
                fp.orientation = Angle.from_degrees(ROT[ref])
            moved.append(fp)
    b.update_items(moved)

    # 4) For footprints that didn't import (missing), place them from library
    for ref, (x, y) in CPL.items():
        if ref not in placed:
            fp_lib = FOOTPRINT.get(ref)
            if fp_lib:
                fp = b.place_footprint_from_library(fp_lib, Vector2.from_xy_mm(x, y))
                if ref in ROT:
                    fp.orientation = Angle.from_degrees(ROT[ref])
                print(f"placed {ref} from {fp_lib}")
            else:
                print(f"WARNING: no footprint for {ref}")

print("board created. footprints:", len(CPL))