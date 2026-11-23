"""Shank pod lid: same lid as the thigh pod."""
from thigh_pod_lid import build_lid

if __name__ == "__main__":
    print(build_lid(False).bounding_box().size)
