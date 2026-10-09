#!/usr/bin/env python3
"""Name the program and signal behind a Linux ELF core file, without binutils.

Reads the NT_PRPSINFO and NT_SIGINFO notes (which hold only the process name,
argument line, and signal number); it never prints the memory image, which can
contain credentials. Usage: core-info.py <core-file>
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

NT_PRPSINFO = 3
NT_SIGINFO = 0x53494749
PT_NOTE = 4


def main(path: str) -> int:
    data = Path(path).read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[16] != 4:
        print(f"{path}: not a 64-bit ELF core file", file=sys.stderr)
        return 2
    (phoff,) = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    found = False
    for index in range(phnum):
        p_type, _, p_offset, _, _, p_filesz = struct.unpack_from(
            "<IIQQQQ", data, phoff + index * phentsize
        )
        if p_type != PT_NOTE:
            continue
        offset, end = p_offset, p_offset + p_filesz
        while offset + 12 <= end:
            namesz, descsz, ntype = struct.unpack_from("<III", data, offset)
            offset += 12
            name = data[offset : offset + namesz].rstrip(b"\0")
            offset += (namesz + 3) & ~3
            desc = data[offset : offset + descsz]
            offset += (descsz + 3) & ~3
            if name != b"CORE":
                continue
            if ntype == NT_PRPSINFO and len(desc) >= 136:
                # struct elf_prpsinfo: pr_fname at 40 (16 bytes), pr_psargs at 56 (80 bytes).
                program = desc[40:56].split(b"\0")[0].decode(errors="replace")
                args = desc[56:136].split(b"\0")[0].decode(errors="replace")
                print(f"program: {program}\nargs: {args}")
                found = True
            elif ntype == NT_SIGINFO and len(desc) >= 4:
                (signo,) = struct.unpack_from("<i", desc)
                print(f"signal: {signo}")
    if not found:
        print(f"{path}: no NT_PRPSINFO note found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
