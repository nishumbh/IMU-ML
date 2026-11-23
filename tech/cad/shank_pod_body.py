"""Shank pod body: same tray as the thigh pod, without the microSD slot and button."""
from thigh_pod_body import build_body

if __name__ == "__main__":
    print(build_body(False).bounding_box().size)
