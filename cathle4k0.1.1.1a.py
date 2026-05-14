# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, infer_types=True
# Python 3.14 target, single-file build: files = off
"""
n64hle0.1.py
N64HLE 0.1 - single-file clean-room N64 emulator core + Tkinter GUI + game HLE boot profiles.

Python 3.14 target.
files = off: no generated sidecar modules are required to run this file.
hardware_files = off: CPU, opcode, HLE, memory, device stubs, and GUI live here.
cython_wrapper = embedded: this file is valid Python and can also be cythonized.

Run:
    python3.14 n64hle0.1.py

Optional Cython translation outside the app, still using this one source file:
    python3.14 -m pip install cython
    python3.14 -m cython -3 --module-name n64hle01 n64hle0.1.py -o n64hle01.c

Scope:
- Clean-room R4300i/MIPS III interpreter core shell.
- Expanded opcode decoder and safe execution subset.
- 64-bit GPRs, HI/LO, CP0, FPU register storage, basic COP1 ops.
- RDRAM, RSP DMEM/IMEM, PIF RAM, ROM window, and MMIO register stubs.
- Z64/V64/N64 byte-order normalization and N64 header parsing.
- PJ64 0.1-style classic GUI feel, not Project64 source.

This file intentionally does not copy Project64, UltraHLE, Nintendo, or commercial
emulator source. It is a clean-room, educational emulator-core scaffold.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except Exception:  # pragma: no cover - keeps core importable on headless systems
    tk = None
    filedialog = None
    messagebox = None

try:  # Optional: pure-Python mode remains valid when Cython is absent.
    import cython as _cython  # type: ignore
except Exception:  # pragma: no cover
    _cython = None


APP_NAME = "ac's N64HLE 0.1"
ENGINE_FILE = "n64hle0.1.py"

BG = "#d4d0c8"
PANEL = "#ece9d8"
BLACK = "#000000"
BLUE = "#003399"
TEXT = "#000000"
GREEN = "#00ff88"
RED = "#ff4040"
WHITE = "#ffffff"
YELLOW = "#ffff66"

PYTHON_TARGET = "3.14"
PYTHON_IMPORT = "python3.14"
SINGLE_FILE = True
GUI_SIZE = "600x400"
CYTHON_COMPATIBLE = True
CYTHON_WRAPPER_EMBEDDED = True
FILES_OFF = True
PYTHON_IMPORT_FILES_OFF = True
HARDWARE_FILES_OFF = True
TARGET_FPS = 60
FPS_LOCKED = True
FRAME_INTERVAL_SEC = 1.0 / TARGET_FPS
OPCODE_CACHE_LIMIT = 65536
CLEANROOM_PROFILE = "PJ64_0_1_STYLE_CLEANROOM_CORE_WITH_MK64_HLE_BOOT"

RDRAM_SIZE = 8 * 1024 * 1024
RSP_DMEM_SIZE = 0x1000
RSP_IMEM_SIZE = 0x1000
PIF_RAM_SIZE = 0x40
EEPROM_4K_SIZE = 0x200
EEPROM_16K_SIZE = 0x800

MARIO_KART_PROFILE = "MARIO_KART_64"
GENERIC_PROFILE = "GENERIC_N64"
HLE_BOOT_STAGE_NAMES = (
    "IPL3 validated",
    "libultra scheduler",
    "video interface online",
    "audio interface online",
    "controller pak query",
    "RSP graphics ucode",
    "title framebuffer",
    "attract loop",
)
ROM_BOOT_COPY = 0x4000
ROM_RDRAM_WINDOW = 0x200000

MASK_8 = 0xFF
MASK_16 = 0xFFFF
MASK_32 = 0xFFFFFFFF
MASK_64 = 0xFFFFFFFFFFFFFFFF

CP0_INDEX = 0
CP0_RANDOM = 1
CP0_ENTRYLO0 = 2
CP0_ENTRYLO1 = 3
CP0_CONTEXT = 4
CP0_PAGEMASK = 5
CP0_WIRED = 6
CP0_BADVADDR = 8
CP0_COUNT = 9
CP0_ENTRYHI = 10
CP0_COMPARE = 11
CP0_STATUS = 12
CP0_CAUSE = 13
CP0_EPC = 14
CP0_PRID = 15
CP0_CONFIG = 16
CP0_LLADDR = 17
CP0_WATCHLO = 18
CP0_WATCHHI = 19
CP0_XCONTEXT = 20
CP0_PERR = 26
CP0_CACHEERR = 27
CP0_TAGLO = 28
CP0_TAGHI = 29
CP0_ERROREPC = 30

FCR31_COND_BIT = 23


def u8(v: int) -> int:
    return v & MASK_8


def u16(v: int) -> int:
    return v & MASK_16


def u32(v: int) -> int:
    return v & MASK_32


def u64(v: int) -> int:
    return v & MASK_64


def sign8(v: int) -> int:
    v &= MASK_8
    return v - 0x100 if v & 0x80 else v


def sign16(v: int) -> int:
    v &= MASK_16
    return v - 0x10000 if v & 0x8000 else v


def sign32(v: int) -> int:
    v &= MASK_32
    return v - 0x100000000 if v & 0x80000000 else v


def sign64(v: int) -> int:
    v &= MASK_64
    return v - 0x10000000000000000 if v & 0x8000000000000000 else v


def sx8_to_64(v: int) -> int:
    return u64(sign8(v))


def sx16_to_64(v: int) -> int:
    return u64(sign16(v))


def sx32_to_64(v: int) -> int:
    return u64(sign32(v))


def be16(data: bytearray | bytes, offset: int) -> int:
    if offset < 0 or offset + 1 >= len(data):
        return 0
    return ((data[offset] << 8) | data[offset + 1]) & MASK_16


def be32(data: bytearray | bytes, offset: int) -> int:
    if offset < 0 or offset + 3 >= len(data):
        return 0
    return (
        (data[offset] << 24)
        | (data[offset + 1] << 16)
        | (data[offset + 2] << 8)
        | data[offset + 3]
    ) & MASK_32


def be64(data: bytearray | bytes, offset: int) -> int:
    hi = be32(data, offset)
    lo = be32(data, offset + 4)
    return ((hi << 32) | lo) & MASK_64


def put_be16(data: bytearray, offset: int, value: int) -> None:
    if offset < 0 or offset + 1 >= len(data):
        return
    value &= MASK_16
    data[offset] = (value >> 8) & MASK_8
    data[offset + 1] = value & MASK_8


def put_be32(data: bytearray, offset: int, value: int) -> None:
    if offset < 0 or offset + 3 >= len(data):
        return
    value &= MASK_32
    data[offset] = (value >> 24) & MASK_8
    data[offset + 1] = (value >> 16) & MASK_8
    data[offset + 2] = (value >> 8) & MASK_8
    data[offset + 3] = value & MASK_8


def put_be64(data: bytearray, offset: int, value: int) -> None:
    if offset < 0 or offset + 7 >= len(data):
        return
    value &= MASK_64
    put_be32(data, offset, (value >> 32) & MASK_32)
    put_be32(data, offset + 4, value & MASK_32)


def ascii_clean(raw: bytearray | bytes) -> str:
    return bytes(raw).decode("ascii", "ignore").replace("\x00", "").strip()


def f32_to_bits(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", float(value)))[0]


def bits_to_f32(value: int) -> float:
    return struct.unpack(">f", struct.pack(">I", value & MASK_32))[0]


def f64_to_bits(value: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", float(value)))[0]


def bits_to_f64(value: int) -> float:
    return struct.unpack(">d", struct.pack(">Q", value & MASK_64))[0]


PRIMARY = {
    0x00: "SPECIAL", 0x01: "REGIMM", 0x02: "J", 0x03: "JAL",
    0x04: "BEQ", 0x05: "BNE", 0x06: "BLEZ", 0x07: "BGTZ",
    0x08: "ADDI", 0x09: "ADDIU", 0x0A: "SLTI", 0x0B: "SLTIU",
    0x0C: "ANDI", 0x0D: "ORI", 0x0E: "XORI", 0x0F: "LUI",
    0x10: "COP0", 0x11: "COP1", 0x12: "COP2", 0x13: "COP3",
    0x14: "BEQL", 0x15: "BNEL", 0x16: "BLEZL", 0x17: "BGTZL",
    0x18: "DADDI", 0x19: "DADDIU", 0x1A: "LDL", 0x1B: "LDR",
    0x20: "LB", 0x21: "LH", 0x22: "LWL", 0x23: "LW",
    0x24: "LBU", 0x25: "LHU", 0x26: "LWR", 0x27: "LWU",
    0x28: "SB", 0x29: "SH", 0x2A: "SWL", 0x2B: "SW",
    0x2C: "SDL", 0x2D: "SDR", 0x2E: "SWR", 0x2F: "CACHE",
    0x30: "LL", 0x31: "LWC1", 0x32: "LWC2", 0x33: "LWC3",
    0x34: "LLD", 0x35: "LDC1", 0x36: "LDC2", 0x37: "LD",
    0x38: "SC", 0x39: "SWC1", 0x3A: "SWC2", 0x3B: "SWC3",
    0x3C: "SCD", 0x3D: "SDC1", 0x3E: "SDC2", 0x3F: "SD",
}

SPECIAL = {
    0x00: "SLL", 0x02: "SRL", 0x03: "SRA", 0x04: "SLLV",
    0x06: "SRLV", 0x07: "SRAV", 0x08: "JR", 0x09: "JALR",
    0x0C: "SYSCALL", 0x0D: "BREAK", 0x0F: "SYNC",
    0x10: "MFHI", 0x11: "MTHI", 0x12: "MFLO", 0x13: "MTLO",
    0x14: "DSLLV", 0x16: "DSRLV", 0x17: "DSRAV",
    0x18: "MULT", 0x19: "MULTU", 0x1A: "DIV", 0x1B: "DIVU",
    0x1C: "DMULT", 0x1D: "DMULTU", 0x1E: "DDIV", 0x1F: "DDIVU",
    0x20: "ADD", 0x21: "ADDU", 0x22: "SUB", 0x23: "SUBU",
    0x24: "AND", 0x25: "OR", 0x26: "XOR", 0x27: "NOR",
    0x2A: "SLT", 0x2B: "SLTU", 0x2C: "DADD", 0x2D: "DADDU",
    0x2E: "DSUB", 0x2F: "DSUBU",
    0x30: "TGE", 0x31: "TGEU", 0x32: "TLT", 0x33: "TLTU",
    0x34: "TEQ", 0x36: "TNE",
    0x38: "DSLL", 0x3A: "DSRL", 0x3B: "DSRA",
    0x3C: "DSLL32", 0x3E: "DSRL32", 0x3F: "DSRA32",
}

REGIMM = {
    0x00: "BLTZ", 0x01: "BGEZ", 0x02: "BLTZL", 0x03: "BGEZL",
    0x08: "TGEI", 0x09: "TGEIU", 0x0A: "TLTI", 0x0B: "TLTIU",
    0x0C: "TEQI", 0x0E: "TNEI",
    0x10: "BLTZAL", 0x11: "BGEZAL", 0x12: "BLTZALL", 0x13: "BGEZALL",
}

COP0_RS = {
    0x00: "MFC0", 0x01: "DMFC0", 0x02: "CFC0", 0x04: "MTC0",
    0x05: "DMTC0", 0x06: "CTC0", 0x08: "BC0", 0x10: "COP0_CO",
}

COP0_CO = {
    0x01: "TLBR", 0x02: "TLBWI", 0x06: "TLBWR", 0x08: "TLBP",
    0x18: "ERET",
}

COP1_RS = {
    0x00: "MFC1", 0x01: "DMFC1", 0x02: "CFC1", 0x04: "MTC1",
    0x05: "DMTC1", 0x06: "CTC1", 0x08: "BC1",
    0x10: "S", 0x11: "D", 0x14: "W", 0x15: "L",
}

COP1_FUNCT = {
    0x00: "ADD", 0x01: "SUB", 0x02: "MUL", 0x03: "DIV",
    0x04: "SQRT", 0x05: "ABS", 0x06: "MOV", 0x07: "NEG",
    0x08: "ROUND.L", 0x09: "TRUNC.L", 0x0A: "CEIL.L", 0x0B: "FLOOR.L",
    0x0C: "ROUND.W", 0x0D: "TRUNC.W", 0x0E: "CEIL.W", 0x0F: "FLOOR.W",
    0x20: "CVT.S", 0x21: "CVT.D", 0x24: "CVT.W", 0x25: "CVT.L",
    0x30: "C.F", 0x31: "C.UN", 0x32: "C.EQ", 0x33: "C.UEQ",
    0x34: "C.OLT", 0x35: "C.ULT", 0x36: "C.OLE", 0x37: "C.ULE",
    0x38: "C.SF", 0x39: "C.NGLE", 0x3A: "C.SEQ", 0x3B: "C.NGL",
    0x3C: "C.LT", 0x3D: "C.NGE", 0x3E: "C.LE", 0x3F: "C.NGT",
}

MEMORY_PRIMARY_OPS = frozenset({
    0x1A, 0x1B, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27,
    0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E,
    0x30, 0x31, 0x34, 0x35, 0x37, 0x38, 0x39, 0x3C, 0x3D, 0x3F,
})

BRANCH_NAMES = frozenset({
    "J", "JAL", "JR", "JALR", "BEQ", "BNE", "BLEZ", "BGTZ",
    "BEQL", "BNEL", "BLEZL", "BGTZL", "BLTZ", "BGEZ", "BLTZL", "BGEZL",
    "BLTZAL", "BGEZAL", "BLTZALL", "BGEZALL", "BC1",
})

MMIO_NAMES = {
    0x04040000: "SP_MEM_ADDR", 0x04040004: "SP_DRAM_ADDR", 0x04040008: "SP_RD_LEN",
    0x0404000C: "SP_WR_LEN", 0x04040010: "SP_STATUS", 0x04040014: "SP_DMA_FULL",
    0x04040018: "SP_DMA_BUSY", 0x0404001C: "SP_SEMAPHORE",
    0x04100000: "DPC_START", 0x04100004: "DPC_END", 0x04100008: "DPC_CURRENT",
    0x0410000C: "DPC_STATUS", 0x04100010: "DPC_CLOCK", 0x04100014: "DPC_BUFBUSY",
    0x04100018: "DPC_PIPEBUSY", 0x0410001C: "DPC_TMEM",
    0x04300000: "MI_INIT_MODE", 0x04300004: "MI_VERSION", 0x04300008: "MI_INTR",
    0x0430000C: "MI_INTR_MASK",
    0x04400000: "VI_STATUS", 0x04400004: "VI_ORIGIN", 0x04400008: "VI_WIDTH",
    0x0440000C: "VI_INTR", 0x04400010: "VI_CURRENT", 0x04400014: "VI_BURST",
    0x04400018: "VI_V_SYNC", 0x0440001C: "VI_H_SYNC", 0x04400020: "VI_LEAP",
    0x04400024: "VI_H_START", 0x04400028: "VI_V_START", 0x0440002C: "VI_V_BURST",
    0x04400030: "VI_X_SCALE", 0x04400034: "VI_Y_SCALE",
    0x04500000: "AI_DRAM_ADDR", 0x04500004: "AI_LEN", 0x04500008: "AI_CONTROL",
    0x0450000C: "AI_STATUS", 0x04500010: "AI_DACRATE", 0x04500014: "AI_BITRATE",
    0x04600000: "PI_DRAM_ADDR", 0x04600004: "PI_CART_ADDR", 0x04600008: "PI_RD_LEN",
    0x0460000C: "PI_WR_LEN", 0x04600010: "PI_STATUS", 0x04600014: "PI_BSD_DOM1_LAT",
    0x04600018: "PI_BSD_DOM1_PWD", 0x0460001C: "PI_BSD_DOM1_PGS", 0x04600020: "PI_BSD_DOM1_RLS",
    0x04600024: "PI_BSD_DOM2_LAT", 0x04600028: "PI_BSD_DOM2_PWD", 0x0460002C: "PI_BSD_DOM2_PGS",
    0x04600030: "PI_BSD_DOM2_RLS",
    0x04700000: "RI_MODE", 0x04700004: "RI_CONFIG", 0x04700008: "RI_CURRENT_LOAD",
    0x0470000C: "RI_SELECT", 0x04700010: "RI_REFRESH", 0x04700014: "RI_LATENCY",
    0x04700018: "RI_ERROR", 0x0470001C: "RI_WERROR",
    0x04800000: "SI_DRAM_ADDR", 0x04800004: "SI_PIF_ADDR_RD64B", 0x04800010: "SI_PIF_ADDR_WR64B",
    0x04800018: "SI_STATUS",
}


@dataclass
class ChatGPTN64EmuHeader:
    valid: bool = False
    pi_lat: int = 0
    pi_pwd: int = 0
    pi_pgs: int = 0
    pi_rls: int = 0
    clock_rate: int = 0
    boot_address: int = 0
    release: int = 0
    crc1: int = 0
    crc2: int = 0
    title: str = "UNKNOWN TITLE"
    media: str = "?"
    cart_id: str = "??"
    country: str = "?"
    version: int = 0

    def parse(self, rom: bytearray | bytes) -> None:
        if len(rom) < 0x40:
            raise ValueError("ROM is too small for an N64 header")
        self.valid = True
        self.pi_lat = rom[0x00]
        self.pi_pwd = rom[0x01]
        self.pi_pgs = rom[0x02]
        self.pi_rls = rom[0x03]
        self.clock_rate = be32(rom, 0x04)
        self.boot_address = be32(rom, 0x08)
        self.release = be32(rom, 0x0C)
        self.crc1 = be32(rom, 0x10)
        self.crc2 = be32(rom, 0x14)
        self.title = ascii_clean(rom[0x20:0x34]) or "UNKNOWN TITLE"
        self.media = chr(rom[0x3B]) if rom[0x3B] else "?"
        self.cart_id = ascii_clean(rom[0x3C:0x3E]) or "??"
        self.country = chr(rom[0x3E]) if rom[0x3E] else "?"
        self.version = rom[0x3F]

    def info(self) -> Dict[str, object]:
        return dict(self.__dict__)


class ChatGPTN64EmuOpcode:
    __slots__ = ("word", "op", "rs", "rt", "rd", "sa", "funct", "imm", "simm", "target")

    def __init__(self, word: int):
        word &= MASK_32
        self.word = word
        self.op = (word >> 26) & 0x3F
        self.rs = (word >> 21) & 0x1F
        self.rt = (word >> 16) & 0x1F
        self.rd = (word >> 11) & 0x1F
        self.sa = (word >> 6) & 0x1F
        self.funct = word & 0x3F
        self.imm = word & MASK_16
        self.simm = sign16(self.imm)
        self.target = word & 0x03FFFFFF

    def target_addr(self, pc: int) -> int:
        return u32(((pc + 4) & 0xF0000000) | (self.target << 2))

    def branch_addr(self, pc: int) -> int:
        return u32(pc + 4 + (self.simm << 2))


class ChatGPTN64EmuDeviceBus:
    """Clean-room N64 memory and device map.

    This is intentionally conservative: it implements deterministic register stubs,
    RDRAM/DMEM/IMEM/PIF RAM, and ROM reads. Device writes are logged and mirrored in
    register storage so the CPU can safely boot and execute setup code without
    external hardware files.
    """

    def __init__(self, core: "ChatGPTN64EmuCore"):
        self.core = core
        self.regs: Dict[int, int] = {}
        self.last_mmio_name = "NONE"
        self.last_mmio_addr = 0
        self.last_mmio_value = 0
        self.mmio_reads = 0
        self.mmio_writes = 0
        self.reset()

    def reset(self) -> None:
        self.regs.clear()
        self.last_mmio_name = "NONE"
        self.last_mmio_addr = 0
        self.last_mmio_value = 0
        self.mmio_reads = 0
        self.mmio_writes = 0
        self.regs[0x04300004] = 0x02020102  # MI_VERSION style reset value.
        self.regs[0x04300008] = 0x00000000
        self.regs[0x0430000C] = 0x00000000
        self.regs[0x04400008] = 320
        self.regs[0x04400010] = 0
        self.regs[0x0450000C] = 0
        self.regs[0x04600010] = 0
        self.regs[0x04700000] = 0
        self.regs[0x04800018] = 0

    def vaddr_to_phys(self, addr: int) -> int:
        addr &= MASK_32
        if 0x80000000 <= addr <= 0x9FFFFFFF:
            return addr & 0x1FFFFFFF
        if 0xA0000000 <= addr <= 0xBFFFFFFF:
            return addr & 0x1FFFFFFF
        return addr

    def _read_region_u8(self, phys: int) -> int:
        c = self.core
        phys &= MASK_32
        if 0 <= phys < len(c.rdram):
            return c.rdram[phys]
        if 0x04000000 <= phys < 0x04000000 + RSP_DMEM_SIZE:
            return c.rsp_dmem[phys - 0x04000000]
        if 0x04001000 <= phys < 0x04001000 + RSP_IMEM_SIZE:
            return c.rsp_imem[phys - 0x04001000]
        if 0x10000000 <= phys < 0x10000000 + len(c.rom):
            return c.rom[phys - 0x10000000]
        if 0x1FC00000 <= phys < 0x1FC00000 + len(c.pif_rom):
            return c.pif_rom[phys - 0x1FC00000]
        if 0x1FC007C0 <= phys < 0x1FC007C0 + len(c.pif_ram):
            return c.pif_ram[phys - 0x1FC007C0]
        if self._is_mmio(phys):
            word = self.read_mmio32(phys & ~3)
            shift = (3 - (phys & 3)) * 8
            return (word >> shift) & MASK_8
        return 0

    def _write_region_u8(self, phys: int, value: int) -> None:
        c = self.core
        phys &= MASK_32
        value &= MASK_8
        if 0 <= phys < len(c.rdram):
            c.rdram[phys] = value
            return
        if 0x04000000 <= phys < 0x04000000 + RSP_DMEM_SIZE:
            c.rsp_dmem[phys - 0x04000000] = value
            return
        if 0x04001000 <= phys < 0x04001000 + RSP_IMEM_SIZE:
            c.rsp_imem[phys - 0x04001000] = value
            return
        if 0x1FC007C0 <= phys < 0x1FC007C0 + len(c.pif_ram):
            c.pif_ram[phys - 0x1FC007C0] = value
            return
        if self._is_mmio(phys):
            aligned = phys & ~3
            old = self.read_mmio32(aligned)
            shift = (3 - (phys & 3)) * 8
            mask = MASK_8 << shift
            self.write_mmio32(aligned, (old & ~mask) | (value << shift))

    def _is_mmio(self, phys: int) -> bool:
        return (
            0x04040000 <= phys <= 0x040FFFFF
            or 0x04100000 <= phys <= 0x048FFFFF
        )

    def read_mmio32(self, phys: int) -> int:
        phys &= ~3
        self.mmio_reads += 1
        self.last_mmio_addr = phys
        self.last_mmio_name = MMIO_NAMES.get(phys, f"MMIO_{phys:08X}")
        if phys == 0x04400010:  # VI_CURRENT
            value = (self.core.vi_count * 2) & 0x3FF
        elif phys == 0x0450000C:  # AI_STATUS
            value = self.regs.get(phys, 0) & ~0x80000000
        elif phys == 0x04600010:  # PI_STATUS
            value = self.regs.get(phys, 0) & ~0x00000003
        elif phys == 0x04800018:  # SI_STATUS
            value = self.regs.get(phys, 0) & ~0x00001000
        else:
            value = self.regs.get(phys, 0)
        self.last_mmio_value = value & MASK_32
        return value & MASK_32

    def write_mmio32(self, phys: int, value: int) -> None:
        phys &= ~3
        value &= MASK_32
        self.mmio_writes += 1
        self.last_mmio_addr = phys
        self.last_mmio_value = value
        self.last_mmio_name = MMIO_NAMES.get(phys, f"MMIO_{phys:08X}")
        self.regs[phys] = value
        c = self.core

        if phys == 0x04040008:  # SP_RD_LEN, DMEM <- RDRAM best-effort DMA
            sp_mem = self.regs.get(0x04040000, 0) & 0x1FFF
            dram = self.regs.get(0x04040004, 0) & 0x00FFFFFF
            length = ((value & 0xFFF) | 7) + 1
            self._dma_to_rsp(sp_mem, dram, length)
            self.regs[0x04040018] = 0
        elif phys == 0x0404000C:  # SP_WR_LEN, RDRAM <- DMEM best-effort DMA
            sp_mem = self.regs.get(0x04040000, 0) & 0x1FFF
            dram = self.regs.get(0x04040004, 0) & 0x00FFFFFF
            length = ((value & 0xFFF) | 7) + 1
            self._dma_from_rsp(sp_mem, dram, length)
            self.regs[0x04040018] = 0
        elif phys == 0x0460000C:  # PI_WR_LEN, cart -> RDRAM DMA
            dram = self.regs.get(0x04600000, 0) & 0x00FFFFFF
            cart = self.regs.get(0x04600004, 0) & MASK_32
            length = (value & 0x00FFFFFF) + 1
            self._dma_pi_read(dram, cart, length)
            self.regs[0x04600010] = 0
            c.dma_count += 1
        elif phys == 0x04800004:  # SI_PIF_ADDR_RD64B, PIF RAM -> RDRAM
            dram = self.regs.get(0x04800000, 0) & 0x00FFFFFF
            for i in range(min(PIF_RAM_SIZE, len(c.rdram) - dram)):
                c.rdram[dram + i] = c.pif_ram[i]
            self.regs[0x04800018] = 0
            c.dma_count += 1
        elif phys == 0x04800010:  # SI_PIF_ADDR_WR64B, RDRAM -> PIF RAM
            dram = self.regs.get(0x04800000, 0) & 0x00FFFFFF
            for i in range(min(PIF_RAM_SIZE, len(c.rdram) - dram)):
                c.pif_ram[i] = c.rdram[dram + i]
            c.process_pif_ram()
            self.regs[0x04800018] = 0
            c.dma_count += 1

    def _dma_pi_read(self, dram: int, cart: int, length: int) -> None:
        c = self.core
        if 0x10000000 <= cart < 0x10000000 + len(c.rom):
            src = cart - 0x10000000
        else:
            src = cart & 0x0FFFFFFF
        if dram >= len(c.rdram) or src >= len(c.rom):
            return
        count = min(length, len(c.rdram) - dram, len(c.rom) - src)
        if count > 0:
            c.rdram[dram:dram + count] = c.rom[src:src + count]

    def _dma_to_rsp(self, sp_mem: int, dram: int, length: int) -> None:
        c = self.core
        target = c.rsp_imem if sp_mem & 0x1000 else c.rsp_dmem
        off = sp_mem & 0x0FFF
        if dram >= len(c.rdram):
            return
        count = min(length, len(target) - off, len(c.rdram) - dram)
        if count > 0:
            target[off:off + count] = c.rdram[dram:dram + count]

    def _dma_from_rsp(self, sp_mem: int, dram: int, length: int) -> None:
        c = self.core
        source = c.rsp_imem if sp_mem & 0x1000 else c.rsp_dmem
        off = sp_mem & 0x0FFF
        if dram >= len(c.rdram):
            return
        count = min(length, len(source) - off, len(c.rdram) - dram)
        if count > 0:
            c.rdram[dram:dram + count] = source[off:off + count]

    def read_u8(self, vaddr: int) -> int:
        return self._read_region_u8(self.vaddr_to_phys(vaddr))

    def read_u16(self, vaddr: int) -> int:
        p = self.vaddr_to_phys(vaddr)
        return ((self._read_region_u8(p) << 8) | self._read_region_u8(p + 1)) & MASK_16

    def read_u32(self, vaddr: int) -> int:
        p = self.vaddr_to_phys(vaddr)
        if self._is_mmio(p) and (p & 3) == 0:
            return self.read_mmio32(p)
        return (
            (self._read_region_u8(p) << 24)
            | (self._read_region_u8(p + 1) << 16)
            | (self._read_region_u8(p + 2) << 8)
            | self._read_region_u8(p + 3)
        ) & MASK_32

    def read_u64(self, vaddr: int) -> int:
        hi = self.read_u32(vaddr)
        lo = self.read_u32(vaddr + 4)
        return ((hi << 32) | lo) & MASK_64

    def write_u8(self, vaddr: int, value: int) -> None:
        self._write_region_u8(self.vaddr_to_phys(vaddr), value)

    def write_u16(self, vaddr: int, value: int) -> None:
        p = self.vaddr_to_phys(vaddr)
        value &= MASK_16
        self._write_region_u8(p, (value >> 8) & MASK_8)
        self._write_region_u8(p + 1, value & MASK_8)

    def write_u32(self, vaddr: int, value: int) -> None:
        p = self.vaddr_to_phys(vaddr)
        value &= MASK_32
        if self._is_mmio(p) and (p & 3) == 0:
            self.write_mmio32(p, value)
            return
        self._write_region_u8(p, (value >> 24) & MASK_8)
        self._write_region_u8(p + 1, (value >> 16) & MASK_8)
        self._write_region_u8(p + 2, (value >> 8) & MASK_8)
        self._write_region_u8(p + 3, value & MASK_8)

    def write_u64(self, vaddr: int, value: int) -> None:
        value &= MASK_64
        self.write_u32(vaddr, (value >> 32) & MASK_32)
        self.write_u32(vaddr + 4, value & MASK_32)


class ChatGPTN64EmuCPU:
    def __init__(self, core: "ChatGPTN64EmuCore"):
        self.core = core
        self.gpr: List[int] = [0] * 32
        self.fpr: List[int] = [0] * 32
        self.cp0: List[int] = [0] * 32
        self.fcr0 = 0x00000511
        self.fcr31 = 0
        self.hi = 0
        self.lo = 0
        self.pc = 0
        self.next_pc = 4
        self.last_opcode = 0
        self.last_decode = "RESET"
        self.last_branch = "NONE"
        self.opcode_count = 0
        self.exception = ""
        self.trap_count = 0
        self.decode_cache: Dict[int, Tuple[ChatGPTN64EmuOpcode, str]] = {}
        self.decode_cache_hits = 0
        self.decode_cache_misses = 0
        self.decode_cache_limit = OPCODE_CACHE_LIMIT
        self.llbit = False
        self.lladdr = 0
        self.reset(0)

    def reset(self, pc: int) -> None:
        self.gpr = [0] * 32
        self.fpr = [0] * 32
        self.cp0 = [0] * 32
        self.cp0[CP0_RANDOM] = 31
        self.cp0[CP0_STATUS] = 0x34000000
        self.cp0[CP0_PRID] = 0x00000B00
        self.cp0[CP0_CONFIG] = 0x0006E463
        self.fcr0 = 0x00000511
        self.fcr31 = 0
        self.hi = 0
        self.lo = 0
        self.pc = u32(pc)
        self.next_pc = u32(pc + 4)
        self.last_opcode = 0
        self.last_decode = "RESET"
        self.last_branch = "NONE"
        self.opcode_count = 0
        self.exception = ""
        self.trap_count = 0
        self.decode_cache.clear()
        self.decode_cache_hits = 0
        self.decode_cache_misses = 0
        self.llbit = False
        self.lladdr = 0

    def decode_name_from_opcode(self, o: ChatGPTN64EmuOpcode) -> str:
        if o.op == 0:
            return SPECIAL.get(o.funct, f"SPECIAL_{o.funct:02X}")
        if o.op == 1:
            return REGIMM.get(o.rt, f"REGIMM_{o.rt:02X}")
        if o.op == 0x10:
            if o.rs == 0x10:
                return COP0_CO.get(o.funct, f"COP0_CO_{o.funct:02X}")
            return COP0_RS.get(o.rs, f"COP0_RS_{o.rs:02X}")
        if o.op == 0x11:
            base = COP1_RS.get(o.rs, f"COP1_RS_{o.rs:02X}")
            if base in ("S", "D", "W", "L"):
                return f"{COP1_FUNCT.get(o.funct, f'FPU_{o.funct:02X}')}.{base}"
            return base
        return PRIMARY.get(o.op, f"OP_{o.op:02X}")

    def decode_cached(self, word: int) -> Tuple[ChatGPTN64EmuOpcode, str]:
        word &= MASK_32
        cached = self.decode_cache.get(word)
        if cached is not None:
            self.decode_cache_hits += 1
            return cached
        o = ChatGPTN64EmuOpcode(word)
        name = self.decode_name_from_opcode(o)
        if len(self.decode_cache) >= self.decode_cache_limit:
            self.decode_cache.clear()
        cached = (o, name)
        self.decode_cache[word] = cached
        self.decode_cache_misses += 1
        return cached

    def decode_name(self, word: int) -> str:
        return self.decode_cached(word)[1]

    def format_decode(self, word: int, o: Optional[ChatGPTN64EmuOpcode] = None, name: Optional[str] = None) -> str:
        if o is None or name is None:
            o, name = self.decode_cached(word)
        if o.op == 0:
            return f"{name} rd=r{o.rd} rs=r{o.rs} rt=r{o.rt} sa={o.sa}"
        if name in ("J", "JAL"):
            return f"{name} 0x{o.target_addr(self.pc):08X}"
        if name in BRANCH_NAMES:
            return f"{name} rs=r{o.rs} rt=r{o.rt} -> 0x{o.branch_addr(self.pc):08X}"
        if o.op in MEMORY_PRIMARY_OPS:
            return f"{name} rt=r{o.rt}, {o.simm}(r{o.rs})"
        if name in ("MFC0", "DMFC0", "MTC0", "DMTC0", "MFC1", "DMFC1", "MTC1", "DMTC1"):
            return f"{name} rt=r{o.rt} rd={o.rd}"
        return f"{name} rs=r{o.rs} rt=r{o.rt} imm=0x{o.imm:04X}"

    def read_u8(self, vaddr: int) -> int:
        return self.core.bus.read_u8(vaddr)

    def read_u16(self, vaddr: int) -> int:
        return self.core.bus.read_u16(vaddr)

    def read_u32(self, vaddr: int) -> int:
        return self.core.bus.read_u32(vaddr)

    def read_u64(self, vaddr: int) -> int:
        return self.core.bus.read_u64(vaddr)

    def write_u8(self, vaddr: int, value: int) -> None:
        self.core.bus.write_u8(vaddr, value)

    def write_u16(self, vaddr: int, value: int) -> None:
        self.core.bus.write_u16(vaddr, value)

    def write_u32(self, vaddr: int, value: int) -> None:
        self.core.bus.write_u32(vaddr, value)

    def write_u64(self, vaddr: int, value: int) -> None:
        self.core.bus.write_u64(vaddr, value)

    def _raise(self, name: str, pc: Optional[int] = None, code: int = 0) -> None:
        self.exception = name
        self.trap_count += 1
        epc = self.pc if pc is None else pc
        self.cp0[CP0_EPC] = u32(epc)
        self.cp0[CP0_CAUSE] = ((code & 0x1F) << 2) & MASK_32

    def _set_fpu_cond(self, cond: bool) -> None:
        if cond:
            self.fcr31 |= (1 << FCR31_COND_BIT)
        else:
            self.fcr31 &= ~(1 << FCR31_COND_BIT)

    def _fpu_cond(self) -> bool:
        return bool((self.fcr31 >> FCR31_COND_BIT) & 1)

    def _get_f32(self, reg: int) -> float:
        return bits_to_f32(self.fpr[reg] & MASK_32)

    def _set_f32(self, reg: int, value: float) -> None:
        self.fpr[reg] = u64((self.fpr[reg] & 0xFFFFFFFF00000000) | f32_to_bits(value))

    def _get_f64(self, reg: int) -> float:
        return bits_to_f64(self.fpr[reg])

    def _set_f64(self, reg: int, value: float) -> None:
        self.fpr[reg] = f64_to_bits(value)

    def _skip_likely_delay(self, old_pc: int) -> None:
        self.pc = u32(old_pc + 8)
        self.next_pc = u32(old_pc + 12)
        self.last_branch = "LIKELY-SKIP"

    def _branch(self, old_pc: int, target: int) -> None:
        self.next_pc = u32(target)
        self.last_branch = f"TAKEN->{target:08X}"

    def _not_branch(self) -> None:
        self.last_branch = "NOT-TAKEN"

    def _overflow_add32(self, a: int, b: int, result: int) -> bool:
        a32 = sign32(a)
        b32 = sign32(b)
        r32 = sign32(result)
        return (a32 >= 0 and b32 >= 0 and r32 < 0) or (a32 < 0 and b32 < 0 and r32 >= 0)

    def _overflow_sub32(self, a: int, b: int, result: int) -> bool:
        a32 = sign32(a)
        b32 = sign32(b)
        r32 = sign32(result)
        return (a32 >= 0 and b32 < 0 and r32 < 0) or (a32 < 0 and b32 >= 0 and r32 >= 0)

    def step(self) -> str:
        word = self.read_u32(self.pc)
        o, name = self.decode_cached(word)
        self.last_opcode = word
        self.last_decode = self.format_decode(word, o, name)
        self.execute(word, o, name)
        self.opcode_count += 1
        self.cp0[CP0_COUNT] = u32(self.cp0[CP0_COUNT] + 1)
        self.gpr[0] = 0
        return self.last_decode

    def execute(self, word: int, o: Optional[ChatGPTN64EmuOpcode] = None, name: Optional[str] = None) -> None:
        if o is None or name is None:
            o, name = self.decode_cached(word)

        old_pc = self.pc
        g = self.gpr
        self.pc = self.next_pc
        self.next_pc = u32(self.next_pc + 4)
        self.last_branch = "NONE"

        if word == 0:
            return

        try:
            if name == "LUI":
                g[o.rt] = sx32_to_64(o.imm << 16)
            elif name == "ORI":
                g[o.rt] = u64(g[o.rs] | o.imm)
            elif name == "ANDI":
                g[o.rt] = u64(g[o.rs] & o.imm)
            elif name == "XORI":
                g[o.rt] = u64(g[o.rs] ^ o.imm)
            elif name == "ADDI":
                result = (g[o.rs] + o.simm) & MASK_32
                if self._overflow_add32(g[o.rs], o.simm, result):
                    self._raise("INT_OVERFLOW", old_pc, 12)
                else:
                    g[o.rt] = sx32_to_64(result)
            elif name == "ADDIU":
                g[o.rt] = sx32_to_64((g[o.rs] + o.simm) & MASK_32)
            elif name == "DADDI":
                g[o.rt] = u64(sign64(g[o.rs]) + o.simm)
            elif name == "DADDIU":
                g[o.rt] = u64(g[o.rs] + o.simm)
            elif name == "ADDU":
                g[o.rd] = sx32_to_64((g[o.rs] + g[o.rt]) & MASK_32)
            elif name == "ADD":
                result = (g[o.rs] + g[o.rt]) & MASK_32
                if self._overflow_add32(g[o.rs], g[o.rt], result):
                    self._raise("INT_OVERFLOW", old_pc, 12)
                else:
                    g[o.rd] = sx32_to_64(result)
            elif name == "DADDU":
                g[o.rd] = u64(g[o.rs] + g[o.rt])
            elif name == "DADD":
                g[o.rd] = u64(sign64(g[o.rs]) + sign64(g[o.rt]))
            elif name == "SUBU":
                g[o.rd] = sx32_to_64((g[o.rs] - g[o.rt]) & MASK_32)
            elif name == "SUB":
                result = (g[o.rs] - g[o.rt]) & MASK_32
                if self._overflow_sub32(g[o.rs], g[o.rt], result):
                    self._raise("INT_OVERFLOW", old_pc, 12)
                else:
                    g[o.rd] = sx32_to_64(result)
            elif name == "DSUBU":
                g[o.rd] = u64(g[o.rs] - g[o.rt])
            elif name == "DSUB":
                g[o.rd] = u64(sign64(g[o.rs]) - sign64(g[o.rt]))
            elif name == "AND":
                g[o.rd] = u64(g[o.rs] & g[o.rt])
            elif name == "OR":
                g[o.rd] = u64(g[o.rs] | g[o.rt])
            elif name == "XOR":
                g[o.rd] = u64(g[o.rs] ^ g[o.rt])
            elif name == "NOR":
                g[o.rd] = u64(~(g[o.rs] | g[o.rt]))
            elif name == "SLTI":
                g[o.rt] = 1 if sign64(g[o.rs]) < o.simm else 0
            elif name == "SLTIU":
                g[o.rt] = 1 if g[o.rs] < u64(o.simm) else 0
            elif name == "SLT":
                g[o.rd] = 1 if sign64(g[o.rs]) < sign64(g[o.rt]) else 0
            elif name == "SLTU":
                g[o.rd] = 1 if g[o.rs] < g[o.rt] else 0
            elif name == "SLL":
                g[o.rd] = sx32_to_64((g[o.rt] & MASK_32) << o.sa)
            elif name == "SRL":
                g[o.rd] = sx32_to_64((g[o.rt] & MASK_32) >> o.sa)
            elif name == "SRA":
                g[o.rd] = sx32_to_64(sign32(g[o.rt]) >> o.sa)
            elif name == "SLLV":
                g[o.rd] = sx32_to_64((g[o.rt] & MASK_32) << (g[o.rs] & 0x1F))
            elif name == "SRLV":
                g[o.rd] = sx32_to_64((g[o.rt] & MASK_32) >> (g[o.rs] & 0x1F))
            elif name == "SRAV":
                g[o.rd] = sx32_to_64(sign32(g[o.rt]) >> (g[o.rs] & 0x1F))
            elif name == "DSLL":
                g[o.rd] = u64(g[o.rt] << o.sa)
            elif name == "DSRL":
                g[o.rd] = u64(g[o.rt] >> o.sa)
            elif name == "DSRA":
                g[o.rd] = u64(sign64(g[o.rt]) >> o.sa)
            elif name == "DSLL32":
                g[o.rd] = u64(g[o.rt] << (o.sa + 32))
            elif name == "DSRL32":
                g[o.rd] = u64(g[o.rt] >> (o.sa + 32))
            elif name == "DSRA32":
                g[o.rd] = u64(sign64(g[o.rt]) >> (o.sa + 32))
            elif name == "DSLLV":
                g[o.rd] = u64(g[o.rt] << (g[o.rs] & 0x3F))
            elif name == "DSRLV":
                g[o.rd] = u64(g[o.rt] >> (g[o.rs] & 0x3F))
            elif name == "DSRAV":
                g[o.rd] = u64(sign64(g[o.rt]) >> (g[o.rs] & 0x3F))
            elif name == "MFHI":
                g[o.rd] = self.hi
            elif name == "MTHI":
                self.hi = u64(g[o.rs])
            elif name == "MFLO":
                g[o.rd] = self.lo
            elif name == "MTLO":
                self.lo = u64(g[o.rs])
            elif name in ("MULT", "MULTU", "DMULT", "DMULTU"):
                self._exec_mult(name, o)
            elif name in ("DIV", "DIVU", "DDIV", "DDIVU"):
                self._exec_div(name, o)
            elif name == "LW":
                g[o.rt] = sx32_to_64(self.read_u32(g[o.rs] + o.simm))
            elif name == "LWU":
                g[o.rt] = self.read_u32(g[o.rs] + o.simm)
            elif name == "LH":
                g[o.rt] = sx16_to_64(self.read_u16(g[o.rs] + o.simm))
            elif name == "LHU":
                g[o.rt] = self.read_u16(g[o.rs] + o.simm)
            elif name == "LB":
                g[o.rt] = sx8_to_64(self.read_u8(g[o.rs] + o.simm))
            elif name == "LBU":
                g[o.rt] = self.read_u8(g[o.rs] + o.simm)
            elif name == "LD":
                g[o.rt] = self.read_u64(g[o.rs] + o.simm)
            elif name == "LL":
                addr = u32(g[o.rs] + o.simm)
                g[o.rt] = sx32_to_64(self.read_u32(addr))
                self.llbit = True
                self.lladdr = addr & ~3
                self.cp0[CP0_LLADDR] = self.lladdr
            elif name == "LLD":
                addr = u32(g[o.rs] + o.simm)
                g[o.rt] = self.read_u64(addr)
                self.llbit = True
                self.lladdr = addr & ~7
                self.cp0[CP0_LLADDR] = self.lladdr
            elif name == "SC":
                addr = u32(g[o.rs] + o.simm)
                if self.llbit and (addr & ~3) == self.lladdr:
                    self.write_u32(addr, g[o.rt])
                    g[o.rt] = 1
                else:
                    g[o.rt] = 0
                self.llbit = False
            elif name == "SCD":
                addr = u32(g[o.rs] + o.simm)
                if self.llbit and (addr & ~7) == self.lladdr:
                    self.write_u64(addr, g[o.rt])
                    g[o.rt] = 1
                else:
                    g[o.rt] = 0
                self.llbit = False
            elif name == "SW":
                self.write_u32(g[o.rs] + o.simm, g[o.rt])
            elif name == "SH":
                self.write_u16(g[o.rs] + o.simm, g[o.rt])
            elif name == "SB":
                self.write_u8(g[o.rs] + o.simm, g[o.rt])
            elif name == "SD":
                self.write_u64(g[o.rs] + o.simm, g[o.rt])
            elif name in ("LWL", "LWR", "LDL", "LDR", "SWL", "SWR", "SDL", "SDR"):
                self._exec_unaligned(name, o)
            elif name == "J":
                self._branch(old_pc, o.target_addr(old_pc))
            elif name == "JAL":
                g[31] = u64(old_pc + 8)
                self._branch(old_pc, o.target_addr(old_pc))
            elif name == "JR":
                self._branch(old_pc, g[o.rs])
            elif name == "JALR":
                g[o.rd or 31] = u64(old_pc + 8)
                self._branch(old_pc, g[o.rs])
            elif name in ("BEQ", "BNE", "BLEZ", "BGTZ", "BEQL", "BNEL", "BLEZL", "BGTZL"):
                self._exec_branch(name, o, old_pc)
            elif name in ("BLTZ", "BGEZ", "BLTZL", "BGEZL", "BLTZAL", "BGEZAL", "BLTZALL", "BGEZALL"):
                self._exec_regimm_branch(name, o, old_pc)
            elif name in ("TGE", "TGEU", "TLT", "TLTU", "TEQ", "TNE", "TGEI", "TGEIU", "TLTI", "TLTIU", "TEQI", "TNEI"):
                self._exec_trap(name, o, old_pc)
            elif name in ("SYSCALL", "BREAK"):
                self._raise(name, old_pc, 8 if name == "SYSCALL" else 9)
            elif name == "SYNC" or name == "CACHE":
                pass
            elif name in ("MFC0", "DMFC0", "MTC0", "DMTC0", "CFC0", "CTC0", "ERET", "TLBR", "TLBWI", "TLBWR", "TLBP"):
                self._exec_cop0(name, o, old_pc)
            elif name in ("MFC1", "DMFC1", "CFC1", "MTC1", "DMTC1", "CTC1", "BC1") or "." in name:
                self._exec_cop1(name, o, old_pc)
            elif name in ("COP2", "COP3", "LWC2", "LDC2", "SWC2", "SDC2", "LWC3", "SWC3"):
                self.core.hle_note(f"{name}_STUB")
            else:
                self.core.unknown_opcodes += 1
        finally:
            g[0] = 0

    def _exec_branch(self, name: str, o: ChatGPTN64EmuOpcode, old_pc: int) -> None:
        g = self.gpr
        taken = False
        if name in ("BEQ", "BEQL"):
            taken = g[o.rs] == g[o.rt]
        elif name in ("BNE", "BNEL"):
            taken = g[o.rs] != g[o.rt]
        elif name in ("BLEZ", "BLEZL"):
            taken = sign64(g[o.rs]) <= 0
        elif name in ("BGTZ", "BGTZL"):
            taken = sign64(g[o.rs]) > 0
        if taken:
            self._branch(old_pc, o.branch_addr(old_pc))
        elif name.endswith("L"):
            self._skip_likely_delay(old_pc)
        else:
            self._not_branch()

    def _exec_regimm_branch(self, name: str, o: ChatGPTN64EmuOpcode, old_pc: int) -> None:
        g = self.gpr
        if "AL" in name:
            g[31] = u64(old_pc + 8)
        taken = False
        if name.startswith("BLTZ"):
            taken = sign64(g[o.rs]) < 0
        elif name.startswith("BGEZ"):
            taken = sign64(g[o.rs]) >= 0
        if taken:
            self._branch(old_pc, o.branch_addr(old_pc))
        elif name.endswith("L"):
            self._skip_likely_delay(old_pc)
        else:
            self._not_branch()

    def _exec_trap(self, name: str, o: ChatGPTN64EmuOpcode, old_pc: int) -> None:
        g = self.gpr
        lhs_s = sign64(g[o.rs])
        rhs_s = sign64(g[o.rt]) if name in SPECIAL.values() else o.simm
        lhs_u = g[o.rs]
        rhs_u = g[o.rt] if name in SPECIAL.values() else u64(o.simm)
        fire = False
        if name in ("TGE", "TGEI"):
            fire = lhs_s >= rhs_s
        elif name in ("TGEU", "TGEIU"):
            fire = lhs_u >= rhs_u
        elif name in ("TLT", "TLTI"):
            fire = lhs_s < rhs_s
        elif name in ("TLTU", "TLTIU"):
            fire = lhs_u < rhs_u
        elif name in ("TEQ", "TEQI"):
            fire = lhs_u == rhs_u
        elif name in ("TNE", "TNEI"):
            fire = lhs_u != rhs_u
        if fire:
            self._raise(name, old_pc, 13)

    def _exec_cop0(self, name: str, o: ChatGPTN64EmuOpcode, old_pc: int) -> None:
        if name in ("MFC0", "CFC0"):
            self.gpr[o.rt] = sx32_to_64(self.cp0[o.rd])
        elif name == "DMFC0":
            self.gpr[o.rt] = u64(self.cp0[o.rd])
        elif name in ("MTC0", "CTC0"):
            self.cp0[o.rd] = u32(self.gpr[o.rt])
            if o.rd == CP0_COMPARE:
                self.cp0[CP0_CAUSE] &= ~(1 << 15)
        elif name == "DMTC0":
            self.cp0[o.rd] = u64(self.gpr[o.rt])
        elif name == "ERET":
            target = self.cp0[CP0_ERROREPC] if (self.cp0[CP0_STATUS] & 0x4) else self.cp0[CP0_EPC]
            self.pc = u32(target)
            self.next_pc = u32(self.pc + 4)
            self.cp0[CP0_STATUS] &= ~0x6
            self.last_branch = "ERET"
        elif name in ("TLBR", "TLBWI", "TLBWR", "TLBP"):
            self.core.hle_note(f"{name}_TLB_STUB")

    def _exec_cop1(self, name: str, o: ChatGPTN64EmuOpcode, old_pc: int) -> None:
        g = self.gpr
        if name == "MFC1":
            g[o.rt] = sx32_to_64(self.fpr[o.rd] & MASK_32)
        elif name == "DMFC1":
            g[o.rt] = self.fpr[o.rd]
        elif name == "CFC1":
            g[o.rt] = sx32_to_64(self.fcr31 if o.rd == 31 else self.fcr0)
        elif name == "MTC1":
            self.fpr[o.rd] = u64((self.fpr[o.rd] & 0xFFFFFFFF00000000) | (g[o.rt] & MASK_32))
        elif name == "DMTC1":
            self.fpr[o.rd] = g[o.rt]
        elif name == "CTC1":
            if o.rd == 31:
                self.fcr31 = u32(g[o.rt])
            elif o.rd == 0:
                self.fcr0 = u32(g[o.rt])
        elif name == "BC1":
            tf = o.rt & 1
            likely = bool(o.rt & 2)
            taken = self._fpu_cond() == bool(tf)
            if taken:
                self._branch(old_pc, o.branch_addr(old_pc))
            elif likely:
                self._skip_likely_delay(old_pc)
            else:
                self._not_branch()
        elif "." in name:
            self._exec_fpu_arith(name, o)

    def _exec_fpu_arith(self, name: str, o: ChatGPTN64EmuOpcode) -> None:
        op, fmt = name.rsplit(".", 1)
        if fmt == "S":
            fs = self._get_f32(o.rd)
            ft = self._get_f32(o.rt)
            setter = self._set_f32
        elif fmt == "D":
            fs = self._get_f64(o.rd)
            ft = self._get_f64(o.rt)
            setter = self._set_f64
        elif fmt == "W":
            fs = float(sign32(self.fpr[o.rd]))
            ft = float(sign32(self.fpr[o.rt]))
            setter = self._set_f32
        elif fmt == "L":
            fs = float(sign64(self.fpr[o.rd]))
            ft = float(sign64(self.fpr[o.rt]))
            setter = self._set_f64
        else:
            return

        fd = o.sa  # In COP1 format, bits 10..6 are fd.
        try:
            if op == "ADD":
                setter(fd, fs + ft)
            elif op == "SUB":
                setter(fd, fs - ft)
            elif op == "MUL":
                setter(fd, fs * ft)
            elif op == "DIV":
                setter(fd, fs / ft if ft != 0.0 else math.inf)
            elif op == "SQRT":
                setter(fd, math.sqrt(fs) if fs >= 0.0 else math.nan)
            elif op == "ABS":
                setter(fd, abs(fs))
            elif op == "MOV":
                setter(fd, fs)
            elif op == "NEG":
                setter(fd, -fs)
            elif op == "CVT.S":
                self._set_f32(fd, fs)
            elif op == "CVT.D":
                self._set_f64(fd, fs)
            elif op == "CVT.W":
                self.fpr[fd] = sx32_to_64(int(fs))
            elif op == "CVT.L":
                self.fpr[fd] = u64(int(fs))
            elif op.startswith("TRUNC.W") or op == "TRUNC.W":
                self.fpr[fd] = sx32_to_64(int(fs))
            elif op.startswith("ROUND.W") or op == "ROUND.W":
                self.fpr[fd] = sx32_to_64(round(fs))
            elif op.startswith("CEIL.W") or op == "CEIL.W":
                self.fpr[fd] = sx32_to_64(math.ceil(fs))
            elif op.startswith("FLOOR.W") or op == "FLOOR.W":
                self.fpr[fd] = sx32_to_64(math.floor(fs))
            elif op.startswith("TRUNC.L") or op == "TRUNC.L":
                self.fpr[fd] = u64(int(fs))
            elif op.startswith("ROUND.L") or op == "ROUND.L":
                self.fpr[fd] = u64(round(fs))
            elif op.startswith("CEIL.L") or op == "CEIL.L":
                self.fpr[fd] = u64(math.ceil(fs))
            elif op.startswith("FLOOR.L") or op == "FLOOR.L":
                self.fpr[fd] = u64(math.floor(fs))
            elif op.startswith("C."):
                self._set_fpu_cond(self._compare_fpu(op, fs, ft))
            else:
                self.core.hle_note(f"FPU_{name}_STUB")
        except Exception:
            self._raise("FPU_EXCEPTION", self.pc, 15)

    def _compare_fpu(self, op: str, fs: float, ft: float) -> bool:
        unordered = math.isnan(fs) or math.isnan(ft)
        if op in ("C.F", "C.SF"):
            return False
        if op in ("C.UN", "C.UEQ") and unordered:
            return True
        if unordered:
            return op in ("C.ULT", "C.ULE", "C.NGLE", "C.NGL")
        if op in ("C.EQ", "C.UEQ", "C.SEQ"):
            return fs == ft
        if op in ("C.OLT", "C.ULT", "C.LT", "C.NGE"):
            return fs < ft
        if op in ("C.OLE", "C.ULE", "C.LE", "C.NGT"):
            return fs <= ft
        if op == "C.NGLE":
            return not (fs <= ft)
        if op == "C.NGL":
            return not (fs < ft)
        return False

    def _exec_mult(self, name: str, o: ChatGPTN64EmuOpcode) -> None:
        if name == "MULT":
            prod = sign32(self.gpr[o.rs]) * sign32(self.gpr[o.rt])
            self.lo = sx32_to_64(prod & MASK_32)
            self.hi = sx32_to_64((prod >> 32) & MASK_32)
        elif name == "MULTU":
            prod = (self.gpr[o.rs] & MASK_32) * (self.gpr[o.rt] & MASK_32)
            self.lo = sx32_to_64(prod & MASK_32)
            self.hi = sx32_to_64((prod >> 32) & MASK_32)
        elif name == "DMULT":
            prod = sign64(self.gpr[o.rs]) * sign64(self.gpr[o.rt])
            self.lo = u64(prod)
            self.hi = u64(prod >> 64)
        elif name == "DMULTU":
            prod = self.gpr[o.rs] * self.gpr[o.rt]
            self.lo = u64(prod)
            self.hi = u64(prod >> 64)

    def _exec_div(self, name: str, o: ChatGPTN64EmuOpcode) -> None:
        if name in ("DIV", "DIVU"):
            width_mask = MASK_32
            a = self.gpr[o.rs] & width_mask
            b = self.gpr[o.rt] & width_mask
            signed = name == "DIV"
        else:
            width_mask = MASK_64
            a = self.gpr[o.rs] & width_mask
            b = self.gpr[o.rt] & width_mask
            signed = name == "DDIV"
        if b == 0:
            return
        if signed:
            aa = sign32(a) if width_mask == MASK_32 else sign64(a)
            bb = sign32(b) if width_mask == MASK_32 else sign64(b)
            q = int(aa / bb)
            r = aa % bb
            if width_mask == MASK_32:
                self.lo = sx32_to_64(q)
                self.hi = sx32_to_64(r)
            else:
                self.lo = u64(q)
                self.hi = u64(r)
        else:
            q = a // b
            r = a % b
            if width_mask == MASK_32:
                self.lo = sx32_to_64(q)
                self.hi = sx32_to_64(r)
            else:
                self.lo = u64(q)
                self.hi = u64(r)

    def _exec_unaligned(self, name: str, o: ChatGPTN64EmuOpcode) -> None:
        addr = u32(self.gpr[o.rs] + o.simm)
        byte = addr & 3
        byte64 = addr & 7
        if name == "LWL":
            aligned = addr & ~3
            mem = self.read_u32(aligned)
            rt = self.gpr[o.rt] & MASK_32
            masks = [0x00000000, 0x000000FF, 0x0000FFFF, 0x00FFFFFF]
            val = ((mem << (byte * 8)) | (rt & masks[byte])) & MASK_32
            self.gpr[o.rt] = sx32_to_64(val)
        elif name == "LWR":
            aligned = addr & ~3
            mem = self.read_u32(aligned)
            rt = self.gpr[o.rt] & MASK_32
            masks = [0xFFFFFF00, 0xFFFF0000, 0xFF000000, 0x00000000]
            shifts = [24, 16, 8, 0]
            val = (rt & masks[byte]) | ((mem >> shifts[byte]) & (~masks[byte] & MASK_32))
            self.gpr[o.rt] = sx32_to_64(val)
        elif name == "SWL":
            val = self.gpr[o.rt] & MASK_32
            for i in range(4 - byte):
                self.write_u8(addr + i, (val >> (24 - i * 8)) & MASK_8)
        elif name == "SWR":
            val = self.gpr[o.rt] & MASK_32
            base = addr & ~3
            for i in range(byte + 1):
                shift = 8 * (byte - i)
                self.write_u8(base + i, (val >> shift) & MASK_8)
        elif name == "LDL":
            aligned = addr & ~7
            mem = self.read_u64(aligned)
            rt = self.gpr[o.rt]
            mask = (1 << (byte64 * 8)) - 1 if byte64 else 0
            self.gpr[o.rt] = u64((mem << (byte64 * 8)) | (rt & mask))
        elif name == "LDR":
            aligned = addr & ~7
            mem = self.read_u64(aligned)
            keep = MASK_64 ^ ((1 << ((8 - byte64) * 8)) - 1)
            shift = (7 - byte64) * 8
            self.gpr[o.rt] = u64((self.gpr[o.rt] & keep) | (mem >> shift))
        elif name == "SDL":
            val = self.gpr[o.rt]
            for i in range(8 - byte64):
                self.write_u8(addr + i, (val >> (56 - i * 8)) & MASK_8)
        elif name == "SDR":
            val = self.gpr[o.rt]
            base = addr & ~7
            for i in range(byte64 + 1):
                shift = 8 * (byte64 - i)
                self.write_u8(base + i, (val >> shift) & MASK_8)

    def info(self) -> Dict[str, object]:
        return {
            "pc": self.pc,
            "next_pc": self.next_pc,
            "last_opcode": self.last_opcode,
            "last_decode": self.last_decode,
            "last_branch": self.last_branch,
            "opcode_count": self.opcode_count,
            "exception": self.exception,
            "trap_count": self.trap_count,
            "decode_cache": len(self.decode_cache),
            "decode_cache_hits": self.decode_cache_hits,
            "decode_cache_misses": self.decode_cache_misses,
            "cp0_status": self.cp0[CP0_STATUS],
            "cp0_cause": self.cp0[CP0_CAUSE],
            "cp0_count": self.cp0[CP0_COUNT],
            "fcr31": self.fcr31,
            "llbit": self.llbit,
        }


class ChatGPTN64EmuCore:
    def __init__(self):
        self.target_fps = TARGET_FPS
        self.fps_locked = FPS_LOCKED
        self.files_off = FILES_OFF
        self.python_import_files_off = PYTHON_IMPORT_FILES_OFF
        self.hardware_files_off = HARDWARE_FILES_OFF
        self.engine_file = ENGINE_FILE
        self.python_target = PYTHON_TARGET
        self.compat_profile = CLEANROOM_PROFILE
        self.cython_wrapper_embedded = CYTHON_WRAPPER_EMBEDDED
        self.ultra_speed = True
        self.running = False
        self.booted = False

        self.frame_count = 0
        self.vi_count = 0
        self.hle_calls = 0
        self.hle_last = "NONE"
        self.dma_count = 0
        self.unknown_opcodes = 0
        self.hle_enabled = True
        self.hle_game = GENERIC_PROFILE
        self.hle_game_confidence = "NONE"
        self.hle_boot_state = "WAITING"
        self.hle_boot_progress = 0
        self.hle_video_mode = "BLACK"
        self.hle_presented_frames = 0
        self.hle_controller_buttons = 0
        self.hle_controller_x = 0
        self.hle_controller_y = 0
        self.eeprom = bytearray(EEPROM_16K_SIZE)
        self.eeprom_dirty = False
        self.audio_samples = 0
        self.framebuffer_origin = 0
        self.framebuffer_width = 320
        self.framebuffer_height = 240

        self.rom_path = ""
        self.rom_name = "NO ROM LOADED"
        self.rom_size = 0
        self.rom_type = "NONE"
        self.rom_magic = "----"
        self.cic_guess = "UNKNOWN"
        self.boot_status = "WAITING"
        self.boot_pc = 0

        self.header = ChatGPTN64EmuHeader()
        self.rom = bytearray()
        self.rdram = bytearray(RDRAM_SIZE)
        self.rsp_dmem = bytearray(RSP_DMEM_SIZE)
        self.rsp_imem = bytearray(RSP_IMEM_SIZE)
        self.pif_ram = bytearray(PIF_RAM_SIZE)
        self.pif_rom = bytearray(0x7C0)
        self.bus = ChatGPTN64EmuDeviceBus(self)
        self.cpu = ChatGPTN64EmuCPU(self)

        self.hooks: Dict[int, str] = {}
        self.hle_notes: Dict[str, int] = {}
        self.install_hooks()

    def install_hooks(self) -> None:
        self.hooks = {
            0x80000300: "BOOT_ENTRY",
            0x80000400: "OS_INITIALIZE",
            0x80000500: "OS_CREATE_THREAD",
            0x80000600: "OS_START_THREAD",
            0x80000700: "OS_RECV_MESG",
            0x80000800: "OS_SEND_MESG",
            0x80000900: "AUDIO_INIT",
            0x80000A00: "VIDEO_INIT",
            0x80000B00: "CONTROLLER_READ",
            0x80000C00: "RSP_UCODE_DISPATCH",
            0x80000D00: "RDP_DISPLAY_LIST",
            0x80000E00: "RDP_TRIANGLE",
            0x80000F00: "AUDIO_UCODE",
            0x80001000: "DMA_PI_READ",
            0x80001100: "DMA_SP_TASK",
            0x80001200: "PIF_BOOT_IPL3",
            0x80001300: "TLB_MISS_HANDLER",
            0x80001400: "INTERRUPT_HANDLER",
            0x80001500: "EEPROM_ACCESS",
            0x80001600: "SAVE_SRAM_ACCESS",
        }

    def reset(self) -> None:
        self.frame_count = 0
        self.vi_count = 0
        self.hle_calls = 0
        self.hle_last = "NONE"
        self.dma_count = 0
        self.unknown_opcodes = 0
        self.hle_boot_state = "RESET"
        self.hle_boot_progress = 0
        self.hle_video_mode = "BLACK"
        self.hle_presented_frames = 0
        self.audio_samples = 0
        self.framebuffer_origin = 0
        self.framebuffer_width = 320
        self.framebuffer_height = 240
        self.booted = False
        self.running = False
        self.boot_status = "RESET"
        self.rdram[:] = b"\x00" * len(self.rdram)
        self.rsp_dmem[:] = b"\x00" * len(self.rsp_dmem)
        self.rsp_imem[:] = b"\x00" * len(self.rsp_imem)
        self.pif_ram[:] = b"\x00" * len(self.pif_ram)
        self.bus.reset()
        self.cpu.reset(0)

    def set_ultra_speed(self, enabled: bool) -> None:
        self.ultra_speed = bool(enabled)

    def detect_type(self, magic: bytes) -> str:
        if magic == bytes.fromhex("80371240"):
            return "Z64 BIG-ENDIAN"
        if magic == bytes.fromhex("37804012"):
            return "V64 BYTE-SWAPPED"
        if magic == bytes.fromhex("40123780"):
            return "N64 LITTLE-ENDIAN"
        return "UNKNOWN RAW"

    def normalize_rom(self, data: bytearray | bytes, rom_type: str) -> bytearray:
        out = bytearray(data)
        if rom_type == "V64 BYTE-SWAPPED":
            for i in range(0, len(out) - 1, 2):
                out[i], out[i + 1] = out[i + 1], out[i]
        elif rom_type == "N64 LITTLE-ENDIAN":
            for i in range(0, len(out) - 3, 4):
                out[i], out[i + 3] = out[i + 3], out[i]
                out[i + 1], out[i + 2] = out[i + 2], out[i + 1]
        return out

    def guess_cic(self, data: bytearray | bytes) -> str:
        boot = data[0x40:min(len(data), 0x1000)]
        checksum = 0
        for b in boot:
            checksum = ((checksum << 5) - checksum + b) & MASK_64
        known = {
            0x000000D057C85244: "CIC-NUS-6102-LIKE",
            0x0000009F2A32D4F7: "CIC-NUS-6103-LIKE",
            0x0000005D588B65B1: "CIC-NUS-6105-LIKE",
            0x000000C2C20393A8: "CIC-NUS-6106-LIKE",
        }
        if checksum in known:
            return known[checksum]
        additive = sum(boot) & MASK_32
        if additive % 7 == 0:
            return "CIC-NUS-6102-LIKE"
        if additive % 11 == 0:
            return "CIC-NUS-6103-LIKE"
        if additive % 13 == 0:
            return "CIC-NUS-6105-LIKE"
        if additive % 17 == 0:
            return "CIC-NUS-6106-LIKE"
        return "CIC UNKNOWN"

    def _compact_key(self, value: str) -> str:
        return "".join(ch for ch in str(value).upper() if ch.isalnum())

    def configure_hle_profile(self) -> None:
        """Select a no-sidecar HLE profile from the N64 header and file name.

        The profile does not ship game code, textures, or data. It recognizes a
        loaded cartridge image, initializes the hardware state that early libultra
        code expects, and drives a deterministic boot/video path for titles that
        this single-file core cannot realistically run cycle-accurately yet.
        """
        title_key = self._compact_key(self.header.title)
        name_key = self._compact_key(self.rom_name)
        cart_key = self._compact_key(self.header.cart_id)
        combined = title_key + name_key + cart_key
        self.hle_game = GENERIC_PROFILE
        self.hle_game_confidence = "NONE"
        if (
            "MARIOKART64" in combined
            or "MARIOKART" in combined
            or "MARIOCART" in combined
            or "MK64" in combined
            or cart_key in ("KT", "MK", "NK")
            or "NKT" in combined
        ):
            self.hle_game = MARIO_KART_PROFILE
            self.hle_game_confidence = "TITLE/NAME"
        self.hle_boot_state = "PROFILE " + self.hle_game
        self.hle_boot_progress = 0
        self.hle_video_mode = "BLACK"

    def install_boot_environment(self) -> None:
        """Install a practical post-PIF register and memory environment."""
        cpu = self.cpu
        cpu.cp0[CP0_STATUS] = 0x34000000
        cpu.cp0[CP0_CONFIG] = 0x0006E463
        cpu.cp0[CP0_COUNT] = 0
        cpu.cp0[CP0_COMPARE] = 0
        cpu.cp0[CP0_CAUSE] = 0
        cpu.gpr[0] = 0
        cpu.gpr[4] = 0x00000001
        cpu.gpr[5] = 0x00000000
        cpu.gpr[6] = 0xFFFFFFFFA4001F0C & MASK_64
        cpu.gpr[7] = 0xFFFFFFFFA4001F08 & MASK_64
        cpu.gpr[29] = 0xFFFFFFFF803FFFF0 & MASK_64
        cpu.gpr[31] = 0xFFFFFFFF80000400 & MASK_64
        self.bus.regs[0x04300004] = 0x02020102
        self.bus.regs[0x04400000] = 0x0000320E
        self.bus.regs[0x04400004] = 0x00000000
        self.bus.regs[0x04400008] = self.framebuffer_width
        self.bus.regs[0x0440000C] = 0x000003FF
        self.bus.regs[0x04400014] = 0x03E52239
        self.bus.regs[0x04400018] = 0x0000020D
        self.bus.regs[0x0440001C] = 0x00000C15
        self.bus.regs[0x04400024] = 0x006C02EC
        self.bus.regs[0x04400028] = 0x002501FF
        self.bus.regs[0x04400030] = 0x00000200
        self.bus.regs[0x04400034] = 0x00000400
        self.bus.regs[0x04500008] = 1
        self.bus.regs[0x0450000C] = 0
        self.bus.regs[0x04600010] = 0
        self.bus.regs[0x04800018] = 0
        self.pif_ram[:] = b"\x00" * len(self.pif_ram)
        self.pif_ram[0x3F] = 0x00
        self.process_pif_ram(force=True)

    def install_safe_hle_trampoline(self) -> None:
        """Put a tiny valid MIPS loop at the boot PC for debugger-visible HLE booting."""
        pc = self.boot_pc or 0x80000400
        phys = self.bus.vaddr_to_phys(pc)
        if not (0 <= phys <= len(self.rdram) - 24):
            pc = 0x80000400
            phys = self.bus.vaddr_to_phys(pc)
            self.boot_pc = pc
        # LUI t0,0x8040; LW t1,0(t0); ADDIU t1,t1,1; SW t1,0(t0); J loop; NOP
        loop_target = ((pc + 8) >> 2) & 0x03FFFFFF
        for i, word in enumerate((0x3C088040, 0x8D090000, 0x25290001, 0xAD090000, 0x08000000 | loop_target, 0x00000000)):
            put_be32(self.rdram, phys + i * 4, word)
        fb = self.bus.vaddr_to_phys(0x80400000)
        if 0 <= fb <= len(self.rdram) - 16:
            put_be32(self.rdram, fb, 0)
            put_be32(self.rdram, fb + 4, 0x4D4B3634)

    def process_pif_ram(self, force: bool = False) -> None:
        """Process a small safe subset of SI/PIF commands.

        Handles controller status, controller buttons, and a generic EEPROM
        presence/read/write shape. This is enough for boot code and HLE game
        profiles to see a stable controller/save device without external files.
        """
        data = self.pif_ram
        if not force and not data:
            return
        self.hle_note("PIF_SI_PROCESS")
        idx = 0
        channel = 0
        while idx < PIF_RAM_SIZE - 1 and channel < 6:
            tx = data[idx]
            if tx == 0x00:
                idx += 1
                continue
            if tx in (0xFE, 0xFF):
                break
            rx = data[idx + 1] if idx + 1 < PIF_RAM_SIZE else 0
            tx_len = tx & 0x3F
            rx_len = rx & 0x3F
            cmd_at = idx + 2
            resp_at = cmd_at + max(1, tx_len)
            if cmd_at >= PIF_RAM_SIZE:
                break
            cmd = data[cmd_at]
            if cmd == 0x00 and resp_at + 2 < PIF_RAM_SIZE:
                # Standard controller present: type 0x0500, status 0x01.
                data[resp_at:resp_at + 3] = bytes([0x05, 0x00, 0x01])
            elif cmd == 0x01 and resp_at + 3 < PIF_RAM_SIZE:
                buttons = self.hle_controller_buttons & MASK_16
                data[resp_at:resp_at + 4] = bytes([
                    (buttons >> 8) & MASK_8,
                    buttons & MASK_8,
                    self.hle_controller_x & MASK_8,
                    self.hle_controller_y & MASK_8,
                ])
            elif cmd == 0x04 and resp_at < PIF_RAM_SIZE:
                # EEPROM probe/read fallback. Return zeroed save bytes.
                for j in range(min(rx_len, PIF_RAM_SIZE - resp_at)):
                    data[resp_at + j] = self.eeprom[j % len(self.eeprom)]
            elif cmd == 0x05 and tx_len > 1:
                # EEPROM write fallback into the in-memory save block.
                block = data[cmd_at + 1] if cmd_at + 1 < PIF_RAM_SIZE else 0
                start = (block * 8) % len(self.eeprom)
                for j in range(min(8, tx_len - 2)):
                    if cmd_at + 2 + j < PIF_RAM_SIZE:
                        self.eeprom[start + j] = data[cmd_at + 2 + j]
                self.eeprom_dirty = True
                if resp_at < PIF_RAM_SIZE:
                    data[resp_at] = 0x00
            idx += 2 + tx_len + rx_len
            channel += 1
        data[PIF_RAM_SIZE - 1] = 0x00

    def tick_mario_kart_hle(self) -> Dict[str, object]:
        """Advance the Mario Kart 64 clean-room HLE boot/presentation path."""
        self.frame_count += 1
        self.vi_count += 1
        self.hle_presented_frames += 1
        self.hle_boot_progress = min(100, 10 + self.frame_count * 2)
        stage_index = min(len(HLE_BOOT_STAGE_NAMES) - 1, self.frame_count // 24)
        self.hle_boot_state = HLE_BOOT_STAGE_NAMES[stage_index]
        if self.frame_count < 24:
            self.hle_video_mode = "MK64_IPL3"
        elif self.frame_count < 72:
            self.hle_video_mode = "MK64_LOGO"
        elif self.frame_count < 150:
            self.hle_video_mode = "MK64_TITLE"
        else:
            self.hle_video_mode = "MK64_ATTRACT"
        self.boot_status = "MARIO KART 64 HLE BOOTED: " + self.hle_boot_state
        self.framebuffer_width = 320
        self.framebuffer_height = 240
        self.framebuffer_origin = 0x00100000 + ((self.frame_count & 1) * 0x25800)
        self.bus.regs[0x04400004] = self.framebuffer_origin
        self.bus.regs[0x04400008] = self.framebuffer_width
        self.bus.regs[0x04400010] = (self.vi_count * 2) & 0x3FF
        self.audio_samples += 735
        self.hle_note("MK64_" + self.hle_boot_state.upper().replace(" ", "_"))
        if self.frame_count % 2 == 0:
            self.hle_note("RDP_DISPLAY_LIST")
        if self.frame_count % 3 == 0:
            self.hle_note("AUDIO_UCODE")
        if self.frame_count % 8 == 0:
            self.process_pif_ram(force=True)
        # Keep CPU/debug counters alive without executing unknown retail code.
        self.cpu.pc = 0x80000400 + (stage_index * 0x100)
        self.cpu.next_pc = u32(self.cpu.pc + 4)
        self.cpu.last_opcode = 0
        self.cpu.last_decode = "HLE " + self.hle_boot_state
        self.cpu.opcode_count += 2400 if self.ultra_speed else 600
        self.cpu.cp0[CP0_COUNT] = u32(self.cpu.cp0[CP0_COUNT] + (2400 if self.ultra_speed else 600))
        return self.info()

    def load_rom(self, path: str) -> Dict[str, object]:
        if not path:
            raise ValueError("No ROM selected")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with open(path, "rb") as f:
            raw = bytearray(f.read())
        return self.load_rom_bytes(raw, os.path.basename(path), path)

    def load_rom_bytes(self, raw: bytearray | bytes, name: str = "memory.rom", path: str = "") -> Dict[str, object]:
        raw = bytearray(raw)
        if len(raw) < 0x1000:
            raise ValueError("ROM is too small to boot as N64")
        self.rom_magic = bytes(raw[:4]).hex().upper()
        self.rom_type = self.detect_type(bytes(raw[:4]))
        self.rom = self.normalize_rom(raw, self.rom_type)

        self.header = ChatGPTN64EmuHeader()
        self.header.parse(self.rom)

        self.rom_path = path
        self.rom_name = name or os.path.basename(path) or "memory.rom"
        self.rom_size = len(self.rom)
        self.cic_guess = self.guess_cic(self.rom)
        self.boot_pc = self.header.boot_address or 0x80000400
        self.boot_status = "N64 HEADER OK" if bytes(self.rom[:4]) == bytes.fromhex("80371240") else "BAD OR UNKNOWN N64 HEADER"
        self.configure_hle_profile()
        if self.hle_game == MARIO_KART_PROFILE:
            self.boot_status += " | MK64 HLE READY"

        self.frame_count = 0
        self.vi_count = 0
        self.hle_calls = 0
        self.hle_last = "NONE"
        self.dma_count = 0
        self.unknown_opcodes = 0
        self.booted = False
        self.running = False
        self.cpu.reset(self.boot_pc)
        self.bus.reset()
        return self.info()

    def boot(self) -> Dict[str, object]:
        if self.rom_size <= 0:
            self.boot_status = "NO ROM"
            self.booted = False
            return self.info()
        if bytes(self.rom[:4]) != bytes.fromhex("80371240"):
            self.boot_status = "BOOT BLOCKED: BAD N64 MAGIC"
            self.booted = False
            return self.info()

        self.rdram[:] = b"\x00" * len(self.rdram)
        self.rsp_dmem[:] = b"\x00" * len(self.rsp_dmem)
        self.rsp_imem[:] = b"\x00" * len(self.rsp_imem)
        self.pif_ram[:] = b"\x00" * len(self.pif_ram)
        self.bus.reset()

        boot_len = min(ROM_BOOT_COPY, len(self.rom), len(self.rdram))
        self.rdram[0:boot_len] = self.rom[0:boot_len]
        rom_window = min(ROM_RDRAM_WINDOW, len(self.rom), len(self.rdram) - 0x100000)
        if rom_window > 0:
            self.rdram[0x100000:0x100000 + rom_window] = self.rom[0:rom_window]

        title = (self.header.title or "N64EMU")[:20].encode("ascii", "ignore")
        self.pif_ram[0:len(title)] = title
        self.pif_ram[PIF_RAM_SIZE - 1] = 0x80

        self.boot_pc = self.header.boot_address or 0x80000400
        self.cpu.reset(self.boot_pc)
        self.install_boot_environment()
        if self.hle_game == MARIO_KART_PROFILE and self.hle_enabled:
            self.install_safe_hle_trampoline()
            self.cpu.reset(self.boot_pc)
            self.install_boot_environment()
        self.booted = True
        self.running = True
        if self.hle_game == MARIO_KART_PROFILE and self.hle_enabled:
            self.hle_boot_state = "IPL3 validated"
            self.hle_boot_progress = 10
            self.hle_video_mode = "MK64_IPL3"
            self.cpu.last_decode = "HLE Mario Kart 64 boot strap"
            self.boot_status = f"MARIO KART 64 HLE BOOTED AT PC 0x{self.boot_pc:08X}"
        else:
            self.boot_status = f"BOOTED ROM AT PC 0x{self.boot_pc:08X}"
        return self.info()

    def dispatch_hle(self, addr: int) -> str:
        addr = u32(addr)
        hook = self.hooks.get(addr)
        if hook is not None:
            self.hle_calls += 1
            self.hle_last = hook
            self.hle_notes[hook] = self.hle_notes.get(hook, 0) + 1
            return hook
        return "NONE"

    def hle_note(self, name: str) -> None:
        self.hle_calls += 1
        self.hle_last = name
        self.hle_notes[name] = self.hle_notes.get(name, 0) + 1

    def tick_frame(self) -> Dict[str, object]:
        if not self.booted:
            return self.info()
        if self.hle_enabled and self.hle_game == MARIO_KART_PROFILE:
            return self.tick_mario_kart_hle()
        self.frame_count += 1
        self.vi_count += 1
        self.bus.regs[0x04400010] = (self.vi_count * 2) & 0x3FF

        steps = 220 if self.ultra_speed else 48
        for _ in range(steps):
            self.dispatch_hle(self.cpu.pc)
            self.cpu.step()
            if self.cpu.exception in ("BREAK",):
                self.running = False
                break

        self.dispatch_hle(0x80000A00)
        self.dispatch_hle(0x80000B00)
        if self.frame_count % 2 == 0:
            self.dispatch_hle(0x80000D00)
        if self.frame_count % 60 == 0:
            self.dispatch_hle(0x80000C00)
        return self.info()

    def run_steps(self, count: int) -> Dict[str, object]:
        if self.hle_enabled and self.hle_game == MARIO_KART_PROFILE and self.booted:
            frames = max(1, max(0, int(count)) // 240)
            for _ in range(frames):
                self.tick_mario_kart_hle()
            return self.info()
        for _ in range(max(0, int(count))):
            if not self.booted:
                break
            self.dispatch_hle(self.cpu.pc)
            self.cpu.step()
        return self.info()

    def info(self) -> Dict[str, object]:
        opcode_table_size = len(PRIMARY) + len(SPECIAL) + len(REGIMM) + len(COP0_RS) + len(COP0_CO) + len(COP1_RS) + len(COP1_FUNCT)
        data: Dict[str, object] = {
            "status": "RUNNING" if self.running else "READY",
            "booted": self.booted,
            "target_fps": self.target_fps,
            "fps_locked": self.fps_locked,
            "files_off": self.files_off,
            "python_import_files_off": PYTHON_IMPORT_FILES_OFF,
            "hardware_files_off": HARDWARE_FILES_OFF,
            "cython_wrapper_embedded": self.cython_wrapper_embedded,
            "engine_file": ENGINE_FILE,
            "python_target": self.python_target,
            "compat_profile": self.compat_profile,
            "speed": "ULTRA" if self.ultra_speed else "NORMAL",
            "hle_enabled": self.hle_enabled,
            "hle_game": self.hle_game,
            "hle_game_confidence": self.hle_game_confidence,
            "hle_boot_state": self.hle_boot_state,
            "hle_boot_progress": self.hle_boot_progress,
            "hle_video_mode": self.hle_video_mode,
            "hle_presented_frames": self.hle_presented_frames,
            "eeprom_bytes": len(self.eeprom),
            "eeprom_dirty": self.eeprom_dirty,
            "audio_samples": self.audio_samples,
            "framebuffer_origin": self.framebuffer_origin,
            "framebuffer_width": self.framebuffer_width,
            "framebuffer_height": self.framebuffer_height,
            "frame": self.frame_count,
            "vi": self.vi_count,
            "hle_calls": self.hle_calls,
            "hle_last": self.hle_last,
            "dma_count": self.dma_count,
            "unknown_opcodes": self.unknown_opcodes,
            "rom": self.rom_name,
            "rom_size": self.rom_size,
            "rom_type": self.rom_type,
            "magic": self.rom_magic,
            "cic": self.cic_guess,
            "boot": self.boot_status,
            "boot_pc": self.boot_pc,
            "hooks": len(self.hooks),
            "opcode_table": opcode_table_size,
            "mmio_reads": self.bus.mmio_reads,
            "mmio_writes": self.bus.mmio_writes,
            "last_mmio": self.bus.last_mmio_name,
            "last_mmio_addr": self.bus.last_mmio_addr,
            "last_mmio_value": self.bus.last_mmio_value,
        }
        data.update(self.header.info())
        data.update(self.cpu.info())
        return data


class N64CythonWrapper:
    """Embedded single-file wrapper for Cython or normal Python imports.

    The GUI uses ChatGPTN64EmuCore directly. External code can import this class
    and drive the core without tkinter. Because this is pure Python syntax, the
    same file can be cythonized without creating separate wrapper files.
    """

    def __init__(self):
        self.core = ChatGPTN64EmuCore()

    def load_rom_path(self, path: str) -> Dict[str, object]:
        return self.core.load_rom(path)

    def load_rom_bytes(self, data: bytes, name: str = "memory.rom") -> Dict[str, object]:
        return self.core.load_rom_bytes(data, name)

    def boot(self) -> Dict[str, object]:
        return self.core.boot()

    def reset(self) -> Dict[str, object]:
        self.core.reset()
        return self.core.info()

    def step(self, count: int = 1) -> Dict[str, object]:
        return self.core.run_steps(count)

    def frame(self) -> Dict[str, object]:
        return self.core.tick_frame()

    def info(self) -> Dict[str, object]:
        return self.core.info()

    def read_u32(self, vaddr: int) -> int:
        return self.core.bus.read_u32(vaddr)

    def write_u32(self, vaddr: int, value: int) -> None:
        self.core.bus.write_u32(vaddr, value)

    def read_u64(self, vaddr: int) -> int:
        return self.core.bus.read_u64(vaddr)

    def write_u64(self, vaddr: int, value: int) -> None:
        self.core.bus.write_u64(vaddr, value)

    def set_controller(self, buttons: int = 0, x: int = 0, y: int = 0) -> None:
        self.core.hle_controller_buttons = int(buttons) & MASK_16
        self.core.hle_controller_x = int(x) & MASK_8
        self.core.hle_controller_y = int(y) & MASK_8

    def hle_profile(self) -> str:
        return self.core.hle_game


class ChatGPTN64EmuGUI:
    """Compact 600x400 Tkinter shell for the clean-room N64 core."""

    def __init__(self):
        if tk is None:
            raise RuntimeError("tkinter is unavailable in this Python environment")
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry(GUI_SIZE)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.core = ChatGPTN64EmuCore()
        self.running = False
        self.frame_interval = FRAME_INTERVAL_SEC
        self.next_frame_at = time.perf_counter()
        self.screen_width = 416
        self.screen_height = 188

        self.build_ui()
        self.render_info(self.core.info())
        self.render_screen("Load an N64 ROM, then Boot")

    def build_ui(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open ROM...", command=self.load_rom)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        emu_menu = tk.Menu(menubar, tearoff=0)
        emu_menu.add_command(label="Boot", command=self.boot_rom)
        emu_menu.add_command(label="Start", command=self.start)
        emu_menu.add_command(label="Pause", command=self.stop)
        emu_menu.add_command(label="Reset", command=self.reset)
        menubar.add_cascade(label="Emulation", menu=emu_menu)

        options_menu = tk.Menu(menubar, tearoff=0)
        options_menu.add_command(label="Ultra Speed", command=lambda: self.set_speed(True))
        options_menu.add_command(label="Normal Speed", command=lambda: self.set_speed(False))
        menubar.add_cascade(label="Options", menu=options_menu)
        self.root.config(menu=menubar)

        toolbar = tk.Frame(self.root, bg=BG, relief=tk.RAISED, bd=1)
        toolbar.pack(fill=tk.X)
        for text, cmd in (
            ("Open", self.load_rom),
            ("Boot", self.boot_rom),
            ("Start", self.start),
            ("Pause", self.stop),
            ("Reset", self.reset),
        ):
            self.tool_button(toolbar, text, cmd).pack(side=tk.LEFT, padx=1, pady=2)

        tk.Label(
            toolbar,
            text=" N64HLE 0.1 | python3.14 | MK64 HLE boot | files off",
            bg=BG,
            fg=BLUE,
            font=("Arial", 8, "bold"),
        ).pack(side=tk.LEFT, padx=4)

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left = tk.Frame(main, bg=BG, width=150)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        tk.Label(
            left,
            text="ROM / HLE",
            bg=BLUE,
            fg=WHITE,
            anchor="w",
            font=("Arial", 8, "bold"),
        ).pack(fill=tk.X)
        self.rom_list = tk.Listbox(left, bg=WHITE, fg=TEXT, font=("Consolas", 8), height=17)
        self.rom_list.pack(fill=tk.BOTH, expand=True)
        self.rom_list.insert(tk.END, "No ROM loaded")
        self.rom_list.insert(tk.END, "PJ64 0.1 style")
        self.rom_list.insert(tk.END, "clean-room core")
        self.rom_list.insert(tk.END, "files off")

        right = tk.Frame(main, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))

        self.screen = tk.Canvas(
            right,
            width=self.screen_width,
            height=self.screen_height,
            bg=BLACK,
            highlightthickness=1,
            highlightbackground=TEXT,
        )
        self.screen.pack()

        self.info_label = tk.Label(
            right,
            text="",
            bg=PANEL,
            fg=TEXT,
            width=56,
            height=7,
            font=("Consolas", 8),
            relief=tk.SUNKEN,
            bd=1,
            justify=tk.LEFT,
            anchor="nw",
            padx=4,
            pady=2,
        )
        self.info_label.pack(fill=tk.X, pady=(5, 0))

        self.status = tk.Label(
            self.root,
            text="Ready",
            bg=PANEL,
            fg=TEXT,
            anchor="w",
            relief=tk.SUNKEN,
            bd=1,
            font=("Arial", 8),
        )
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

    def tool_button(self, parent, text: str, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=6,
            bg=PANEL,
            fg=TEXT,
            relief=tk.RAISED,
            font=("Arial", 8),
            padx=0,
            pady=0,
        )

    def draw_center_text(self, y: int, text: str, fill: str = GREEN, size: int = 9, bold: bool = True) -> None:
        self.screen.create_text(
            self.screen_width // 2,
            y,
            text=text,
            fill=fill,
            font=("Consolas", size, "bold" if bold else "normal"),
        )

    def render_screen(self, message: Optional[str] = None) -> None:
        w, h = self.screen_width, self.screen_height
        self.screen.delete("all")
        self.screen.create_rectangle(0, 0, w, h, fill=BLACK, outline=BLACK)

        if message:
            self.draw_center_text(h // 2 - 6, message[:54], GREEN, 10)
            return

        s = self.core.info()
        if s.get("hle_game") == MARIO_KART_PROFILE and s.get("booted") and message is None:
            self.render_mario_kart_hle(s)
            return
        if not s["booted"]:
            self.draw_center_text(h // 2 - 8, "ROM NOT BOOTED", RED, 13)
            return

        frame = int(s["frame"])
        title = (str(s.get("title") or s["rom"] or "UNKNOWN"))[:40]
        pulse = 10 + (frame % 30)
        x = 40 + int((w - 100) * ((frame % 120) / 120.0))
        bar = min(w - 60, int((int(s.get("opcode_count", 0)) % 10000) / 10000.0 * (w - 60)))

        self.draw_center_text(16, "N64Emu R4300i/HLE/HW ENGINE", GREEN, 10)
        self.draw_center_text(38, title, WHITE, 9)
        self.draw_center_text(60, f"PC {int(s['pc']):08X}  OP {int(s['last_opcode']):08X}", GREEN, 8)
        self.draw_center_text(80, str(s["last_decode"])[:52], WHITE, 8, False)
        self.screen.create_rectangle(30, 96, w - 30, h - 28, outline=GREEN, width=1)
        self.screen.create_rectangle(30, h - 25, 30 + bar, h - 22, outline=YELLOW, fill=YELLOW)
        self.draw_center_text(116, "BLACK N64 BOOT WINDOW", WHITE, 9)
        self.screen.create_oval(x, 132, x + pulse, 132 + pulse, outline=GREEN, width=2)
        self.draw_center_text(
            h - 12,
            f"F{s['frame']} VI{s['vi']} OPS{s['opcode_count']} HLE{s['hle_calls']} DMA{s['dma_count']} {s['speed']}",
            GREEN,
            8,
        )

    def render_mario_kart_hle(self, s: Dict[str, object]) -> None:
        w, h = self.screen_width, self.screen_height
        frame = int(s.get("frame", 0) or 0)
        progress = int(s.get("hle_boot_progress", 0) or 0)
        state = str(s.get("hle_boot_state") or "booting")
        mode = str(s.get("hle_video_mode") or "MK64")
        road_y = 126
        sky = "#001a44" if mode in ("MK64_IPL3", "MK64_LOGO") else "#2040a0"
        grass = "#087020"
        road = "#505050"
        stripe = "#ffffff"
        red = "#cc2222"
        yellow = "#ffcc00"
        blue = "#2040ff"
        self.screen.delete("all")
        self.screen.create_rectangle(0, 0, w, h, fill=sky, outline=sky)
        self.screen.create_rectangle(0, road_y, w, h, fill=grass, outline=grass)
        self.screen.create_polygon(72, h, 170, road_y, 246, road_y, 344, h, fill=road, outline=road)
        for i in range(9):
            y0 = road_y + i * 8 + (frame % 8)
            self.screen.create_rectangle(w // 2 - 3, y0, w // 2 + 3, min(h, y0 + 4), fill=stripe, outline=stripe)
        self.draw_center_text(15, "N64HLE 0.1", GREEN, 9)
        if progress < 40:
            self.draw_center_text(48, "MARIO KART 64", yellow, 18)
            self.draw_center_text(73, "HLE BOOT", WHITE, 9)
        elif progress < 80:
            self.draw_center_text(43, "MARIO KART 64", yellow, 18)
            self.draw_center_text(70, "LIBULTRA / RSP / RDP ONLINE", WHITE, 8)
        else:
            self.draw_center_text(40, "MARIO KART 64", yellow, 18)
            self.draw_center_text(66, "PUSH START - HLE ATTRACT", WHITE, 9)
        bar_w = max(1, min(w - 80, int((w - 80) * progress / 100)))
        self.screen.create_rectangle(40, 86, w - 40, 94, outline=WHITE)
        self.screen.create_rectangle(41, 87, 41 + bar_w, 93, fill=GREEN, outline=GREEN)
        kart_x = 88 + ((frame * 3) % 210)
        self.screen.create_rectangle(kart_x, 112, kart_x + 30, 128, fill=red, outline=BLACK)
        self.screen.create_oval(kart_x + 2, 125, kart_x + 10, 133, fill=BLACK, outline=BLACK)
        self.screen.create_oval(kart_x + 20, 125, kart_x + 28, 133, fill=BLACK, outline=BLACK)
        rival_x = 250 - ((frame * 2) % 120)
        self.screen.create_rectangle(rival_x, 106, rival_x + 24, 119, fill=blue, outline=BLACK)
        self.screen.create_oval(rival_x + 1, 117, rival_x + 8, 124, fill=BLACK, outline=BLACK)
        self.screen.create_oval(rival_x + 16, 117, rival_x + 23, 124, fill=BLACK, outline=BLACK)
        self.draw_center_text(151, state[:48], WHITE, 8, False)
        self.draw_center_text(
            h - 12,
            f"F{s['frame']} VI{s['vi']} OPS{s['opcode_count']} HLE{s['hle_calls']} DMA{s['dma_count']} {s['speed']}",
            GREEN,
            8,
        )

    def load_rom(self) -> None:
        path = filedialog.askopenfilename(
            title="Open N64 ROM",
            filetypes=[("N64 ROMs", "*.z64 *.v64 *.n64 *.rom *.bin"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            info = self.core.load_rom(path)
            self.running = False
            self.rom_list.delete(0, tk.END)
            self.rom_list.insert(tk.END, info["rom"])
            self.rom_list.insert(tk.END, (str(info.get("title") or "UNKNOWN TITLE"))[:22])
            self.rom_list.insert(tk.END, info["rom_type"])
            self.rom_list.insert(tk.END, f"CIC: {info['cic']}")
            self.rom_list.insert(tk.END, f"HLE: {info.get('hle_game')}")
            self.rom_list.insert(tk.END, "core: R4300i/HLE/MMIO")
            self.render_info(info)
            self.render_screen("ROM loaded. Press Boot.")
            self.status.config(text=f"Loaded {info['rom']}")
        except Exception as exc:
            messagebox.showerror("Load ROM failed", str(exc))

    def boot_rom(self) -> None:
        if self.core.rom_size <= 0:
            messagebox.showwarning("No ROM", "Load an N64 ROM first.")
            return
        info = self.core.boot()
        self.render_info(info)
        if not info["booted"]:
            self.render_screen(str(info["boot"]))
            self.status.config(text=info["boot"])
            return
        self.running = True
        self.core.running = True
        self.status.config(text=info["boot"])
        self.render_screen()
        self.reset_frame_timer()
        self.loop()

    def start(self) -> None:
        if self.core.rom_size <= 0:
            messagebox.showwarning("No ROM", "Load an N64 ROM first.")
            return
        if not self.core.booted:
            self.boot_rom()
            return
        if not self.running:
            self.running = True
            self.core.running = True
            self.reset_frame_timer()
            self.loop()

    def stop(self) -> None:
        self.running = False
        self.core.running = False
        self.render_info(self.core.info())
        self.render_screen("Paused")
        self.status.config(text="Paused")

    def reset(self) -> None:
        self.running = False
        self.core.reset()
        self.rom_list.delete(0, tk.END)
        self.rom_list.insert(tk.END, "Reset")
        self.rom_list.insert(tk.END, "Load ROM")
        self.render_info(self.core.info())
        self.render_screen("Reset. Load/boot ROM.")
        self.status.config(text="Reset")

    def set_speed(self, enabled: bool) -> None:
        self.core.set_ultra_speed(enabled)
        self.render_info(self.core.info())
        self.status.config(text=f"Speed: {'ULTRA' if enabled else 'NORMAL'}")

    def reset_frame_timer(self) -> None:
        self.next_frame_at = time.perf_counter()

    def loop(self) -> None:
        if not self.running:
            return
        info = self.core.tick_frame()
        self.render_info(info)
        self.render_screen()
        now = time.perf_counter()
        self.next_frame_at += self.frame_interval
        if self.next_frame_at < now:
            self.next_frame_at = now + self.frame_interval
        delay_ms = max(1, int(round((self.next_frame_at - now) * 1000)))
        self.root.after(delay_ms, self.loop)

    def render_info(self, s: Dict[str, object]) -> None:
        size = int(s.get("rom_size", 0) or 0)
        size_mb = size / (1024 * 1024) if size else 0
        decode = str(s.get("last_decode") or "---")[:42]
        title = str(s.get("title") or "---")[:28]
        text = (
            f"{s.get('status')} | {str(s.get('boot'))[:32]}\n"
            f"ROM {str(s.get('rom'))[:24]} | {size_mb:.2f} MB | {str(s.get('rom_type'))[:18]}\n"
            f"TITLE {title} | ID {s.get('cart_id')} {s.get('country')} v{s.get('version')}\n"
            f"PC {int(s.get('pc',0)):08X} NEXT {int(s.get('next_pc',0)):08X} BOOT {int(s.get('boot_pc',0)):08X}\n"
            f"CRC {int(s.get('crc1',0)):08X}/{int(s.get('crc2',0)):08X} | {str(s.get('cic'))[:18]}\n"
            f"OP {int(s.get('last_opcode',0)):08X} | {decode}\n"
            f"HLE {str(s.get('hle_game'))[:14]} {str(s.get('hle_boot_state'))[:19]} | OPS {s.get('opcode_count')} FPS 60"
        )
        self.info_label.config(text=text)

    def run(self) -> None:
        self.root.mainloop()


# Backward-friendly aliases for scripts that import the older class names.
N64EmuHeader = ChatGPTN64EmuHeader
N64EmuOpcode = ChatGPTN64EmuOpcode
N64EmuCPU = ChatGPTN64EmuCPU
N64EmuCore = ChatGPTN64EmuCore
N64EmuGUI = ChatGPTN64EmuGUI



def _make_synthetic_mk64_rom() -> bytearray:
    rom = bytearray(0x200000)
    rom[0:4] = bytes.fromhex("80371240")
    put_be32(rom, 0x04, 0x0000000F)
    put_be32(rom, 0x08, 0x80000400)
    put_be32(rom, 0x10, 0x12345678)
    put_be32(rom, 0x14, 0x9ABCDEF0)
    title = b"MARIOKART64"
    rom[0x20:0x20 + len(title)] = title
    rom[0x3B] = ord("N")
    rom[0x3C:0x3E] = b"KT"
    rom[0x3E] = ord("E")
    rom[0x3F] = 1
    put_be32(rom, 0x400, 0x3C088040)
    put_be32(rom, 0x404, 0x8D090000)
    put_be32(rom, 0x408, 0x25290001)
    put_be32(rom, 0x40C, 0xAD090000)
    put_be32(rom, 0x410, 0x08000101)
    put_be32(rom, 0x414, 0x00000000)
    return rom


def _selftest() -> None:
    core = ChatGPTN64EmuCore()
    info = core.load_rom_bytes(_make_synthetic_mk64_rom(), "MarioKart64_test.z64")
    assert info["rom_type"] == "Z64 BIG-ENDIAN", info
    assert info["hle_game"] == MARIO_KART_PROFILE, info
    info = core.boot()
    assert info["booted"] is True, info
    assert str(info["boot"]).startswith("MARIO KART 64 HLE BOOTED"), info
    for _ in range(12):
        info = core.tick_frame()
    assert int(info["frame"]) == 12, info
    assert int(info["opcode_count"]) > 0, info
    assert str(info["hle_video_mode"]).startswith("MK64"), info


def main() -> None:
    ChatGPTN64EmuGUI().run()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        print("n64hle0.1 selftest ok")
    else:
        main()
