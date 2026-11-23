#!/usr/bin/env python3
"""Provision a knee pod over USB serial (NVS keys, see README "Provisioning").

The PC does all key derivation. The device stores what it is given and never runs
PBKDF2. Secrets are never printed. The passphrase comes from an environment variable
or a prompt, never a command-line argument.

Examples (run from the repo root, with the venv that has pyserial):
  python firmware/knee_pod_s3/tools/provision.py status --port /dev/ttyACM0
  python firmware/knee_pod_s3/tools/provision.py pod --port /dev/ttyACM0 --role thigh \
      --dev-id 1 --channel 1 --peer-mac 24:6F:28:AA:BB:02 --pair-file ~/knee_pod/pair.json
  python firmware/knee_pod_s3/tools/provision.py pod --port /dev/ttyACM1 --role shank \
      --channel 1 --peer-mac 24:6F:28:AA:BB:01 --pair-file ~/knee_pod/pair.json

--pair-file holds the ESP-NOW PMK/LMK (secrets, created once with mode 600 and reused for
both pods). Keep it outside the repository.
"""
import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import sys
import time
import unicodedata

PBKDF2_ITERATIONS = 600_000  # docs/SD_LOG_FORMAT.md section 3


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """KBX1 key: PBKDF2-HMAC-SHA256(NFC(passphrase) as UTF-8, salt, 600000, 32)."""
    check_passphrase(passphrase)
    pw = unicodedata.normalize("NFC", passphrase).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", pw, salt, PBKDF2_ITERATIONS, dklen=32)


def check_passphrase(passphrase: str) -> None:
    # Rules from docs/SD_LOG_FORMAT.md section 2.
    norm = unicodedata.normalize("NFC", passphrase)
    if len(norm) < 12:
        raise SystemExit("passphrase must be at least 12 characters")
    if len(norm.encode("utf-8")) > 1024:
        raise SystemExit("passphrase longer than 1024 UTF-8 bytes")
    if re.search(r"[\x00-\x1f\x7f]", norm):
        raise SystemExit("passphrase contains control characters")


def read_passphrase(env_name: str) -> str:
    value = os.environ.get(env_name)
    if value is None:
        value = getpass.getpass("KBX passphrase (not stored): ")
    return value


def load_or_create_pair(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            pair = json.load(fh)
        if len(bytes.fromhex(pair["pmk"])) != 16 or len(bytes.fromhex(pair["lmk"])) != 16:
            raise SystemExit("pair file has wrong key lengths")
        return pair
    pair = {"pmk": secrets.token_hex(16), "lmk": secrets.token_hex(16)}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(pair, fh)
    print(f"created {path} (mode 600): ESP-NOW keys, keep it out of git")
    return pair


class Pod:
    def __init__(self, port: str, baud: int = 115200):
        import serial  # pyserial

        self.ser = serial.Serial(port, baud, timeout=0.2)
        time.sleep(0.5)
        self.ser.reset_input_buffer()

    def send(self, line: str, wait: float = 0.6) -> list[str]:
        self.ser.write((line + "\n").encode("ascii"))
        end = time.time() + wait
        out = []
        while time.time() < end:
            raw = self.ser.readline().decode("utf-8", "replace").strip()
            if raw:
                out.append(raw)
        return out

    def close(self) -> None:
        self.ser.close()


def cmd_status(args) -> int:
    pod = Pod(args.port)
    for line in pod.send("KPOD status", 1.0):
        print(line)
    pod.close()
    return 0


def cmd_pod(args) -> int:
    pair = load_or_create_pair(args.pair_file)
    mac = args.peer_mac.replace(":", "").lower()
    if not re.fullmatch(r"[0-9a-f]{12}", mac):
        raise SystemExit("peer-mac must be six hex bytes")
    settings = [
        ("role", args.role),
        ("channel", str(args.channel)),
        ("peer_mac", mac),
        ("pmk", pair["pmk"]),
        ("lmk", pair["lmk"]),
    ]
    if args.role == "thigh":
        if args.dev_id is None:
            raise SystemExit("--dev-id is required for the thigh pod")
        passphrase = read_passphrase(args.passphrase_env)
        salt = secrets.token_bytes(16)
        key = derive_key(passphrase, salt)  # the device never runs PBKDF2
        settings += [("dev_id", str(args.dev_id)), ("salt", salt.hex()),
                     ("aes_key", key.hex())]
        del passphrase, key
    pod = Pod(args.port)
    failed = False
    for name, value in settings:
        reply = pod.send(f"KPOD set {name} {value}", 0.4)
        ok = any("# OK" in r for r in reply)
        shown = value if name not in ("pmk", "lmk", "aes_key", "salt") else "<hidden>"
        print(f"set {name} {shown}: {'ok' if ok else 'FAILED ' + ' | '.join(reply)}")
        failed = failed or not ok
    for line in pod.send("KPOD status", 1.0):
        print(line)
    pod.close()
    if failed:
        return 1
    print("done. Reset the pod. Restart the shank pod after every thigh reset (see README).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="print NVS status and the pod MAC")
    s.add_argument("--port", required=True)
    s.set_defaults(func=cmd_status)
    p = sub.add_parser("pod", help="write provisioning for one pod")
    p.add_argument("--port", required=True)
    p.add_argument("--role", choices=["thigh", "shank"], required=True)
    p.add_argument("--channel", type=int, default=1, help="Wi-Fi channel 1..13, same on both pods")
    p.add_argument("--peer-mac", required=True, help="MAC of the other pod (from status)")
    p.add_argument("--pair-file", required=True, help="ESP-NOW PMK/LMK JSON, created if missing")
    p.add_argument("--dev-id", type=int, help="thigh only: number in device_id KP-<n>")
    p.add_argument("--passphrase-env", default="KBX_PASSPHRASE")
    p.set_defaults(func=cmd_pod)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
