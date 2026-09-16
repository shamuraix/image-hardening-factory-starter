"""Use explicit mapping capabilities for the Debian diagnostic runner's helpers.

Executed as image-build root, never as a host configuration step. libcap2 is
already an installed dependency; its native API avoids downloading extra tools.
"""

import ctypes
import os


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run only as root during the diagnostic image build")
    libcap = ctypes.CDLL("libcap.so.2", use_errno=True)
    libcap.cap_from_text.argtypes = [ctypes.c_char_p]
    libcap.cap_from_text.restype = ctypes.c_void_p
    libcap.cap_set_file.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    libcap.cap_set_file.restype = ctypes.c_int
    libcap.cap_free.argtypes = [ctypes.c_void_p]
    libcap.cap_free.restype = ctypes.c_int
    for helper, capability in (("newuidmap", "cap_setuid=ep"), ("newgidmap", "cap_setgid=ep")):
        path = f"/usr/bin/{helper}"
        cap = libcap.cap_from_text(capability.encode())
        if not cap:
            raise OSError(ctypes.get_errno(), f"Cannot parse {capability}")
        try:
            os.chmod(path, 0o755)
            if libcap.cap_set_file(path.encode(), cap) != 0:
                raise OSError(ctypes.get_errno(), f"Cannot set capabilities on {path}")
        finally:
            libcap.cap_free(cap)
        print(f"{path}: mode=0755, {capability}")


if __name__ == "__main__":
    main()
