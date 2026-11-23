"""Create a SYNTHETIC two-strap CSV for interface testing, not model validation."""
import argparse
import csv
import math
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/interim/two_imu_demo.csv"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    header = ["time_ms"] + [f"{site}_{axis}" for site in ("thigh", "shank")
                            for axis in ("ax", "ay", "az", "gx", "gy", "gz")]
    with args.output.open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        for index in range(500):
            t = index / 50
            row = [index * 20]
            for amplitude in (0.15, 0.6):
                theta = amplitude * math.sin(2 * math.pi * 0.5 * t)
                omega = amplitude * math.pi * math.cos(math.pi * t)
                row.extend([0, -9.80665 * math.cos(theta), 9.80665 * math.sin(theta), omega, 0, 0])
            writer.writerow(row)
    print(f"Wrote SYNTHETIC interface-only data to {args.output}")


if __name__ == "__main__":
    main()
