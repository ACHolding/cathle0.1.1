# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, infer_types=True
# Python 3.14 target, single-file build: files = off
"""
#acn64emu0.1.1.1.py — **same codebase layout** as always: ``DeviceBus``, ``CPUCore``,
``ACsN64Core``, ``ACsN64GUI`` (monolithic Python 3.14).

**Project64 0.1 (classic C++ tree) → this file — conceptual port map**

PJ64 0.1-era responsibilities were split across native modules (R4300 interpreter, memory,
peripherals, plugins). Here they collapse into the classes below — behavior is a
clean-room Python analogue, not a line-by-line translation of Zilmar’s sources.

| PJ64 0.1 style area        | Python location in this file                          |
|---------------------------|--------------------------------------------------------|
| R4300 / interpreter       | ``CPUCore`` (``step``, ``execute``, delayed branch)  |
| CP0 / TLB                 | ``CPUCore.cp0``, ``CPUCore.tlb``, ``DeviceBus.v_to_p`` |
| RDRAM / cart visibility   | ``ACsN64Core.rdram``, ``.rom``, ``DeviceBus.read/write_*`` |
| PI / cart DMA             | ``trigger_pi_dma``, ``0x0460xxxx`` via ``DeviceBus`` |
| SP / RSP DMA & status     | ``trigger_sp_dma``, ``process_rsp``, ``0x0404xxxx``  |
| DPC / RDP lists (HLE)     | ``process_rdp``, ``0x0410xxxx``                        |
| VI / display timing regs    | ``0x0440xxxx`` MMIO, Tk canvas                         |
| AI / audio buffer         | ``process_audio``, ``0x0450xxxx``                      |
| SI / PIF / controllers    | ``trigger_si_dma``, ``pif_ram``, keyboard → SI       |
| MI                        | ``0x0430xxxx``                                         |
| “Plugins” (later PJ64)    | N/A — **catHLE**-style monolith: RSP/RDP/audio inline  |

See ``PJ64_01_PORT`` (mapping dict) and ``PJ64_01_LINE`` for monitor tags.
The Tk **window title** is fixed at ``WINDOW_TITLE`` (``APP_NAME`` + Python version).
``PJ64SystemFacade`` (``core.n64_system``) mirrors common YouTube "PJ64 / N64 emu from scratch"
lesson trees: one system object, CPU, memory bus, cart RAM, RSP windows, PIF, and inlined
Gfx/Audio/RSP hooks (no separate plugin DLLs).
"""

from __future__ import annotations

import math
import os
import struct
import sys
import time
import random
import io
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except ImportError:
    tk = None
    filedialog = None
    messagebox = None

# --- Configuration Constants ---
APP_NAME = "AC's N64 Emu 0.1"
VERSION = "0.1.0"
ENGINE_NAME = "mew64"
PYTHON_TARGET = "3.14"
WINDOW_TITLE = f"{APP_NAME} - Python {PYTHON_TARGET}"

# Project64 0.1 — Python port tags (same file / classes; no PJ64 C++ source embedded).
PJ64_01_LINE = "Project64 0.1 classic layout → Python clean-room port"
CATHLE_TAG = "catHLE monolith (public-feature HLE path, no plugin DLLs)"
PJ64_01_PORT: Dict[str, str] = {
    "R4300 interpreter": "CPUCore.step / CPUCore.execute",
    "CP0 + TLB": "CPUCore.cp0, CPUCore.tlb, DeviceBus.v_to_p",
    "RDRAM": "ACsN64Core.rdram + DeviceBus read/write",
    "Cartridge ROM": "ACsN64Core.rom + PI domain 0x10……",
    "PI DMA": "ACsN64Core.trigger_pi_dma, MMIO 0x04600000–0C",
    "SP / RSP": "trigger_sp_dma, rsp_dmem/imem, process_rsp",
    "DPC / RDP": "process_rdp, MMIO 0x04100000–0C",
    "VI": "MMIO 0x0440…, Tk canvas",
    "AI": "process_audio, MMIO 0x0450…",
    "SI / PIF": "trigger_si_dma, pif_ram, controller_state",
    "MI": "MMIO 0x0430…",
    "Plugins": "inlined — " + CATHLE_TAG,
    "N64System (YouTube / PJ64 tree)": "ACsN64Core.n64_system → PJ64SystemFacade",
    "CPU_step / Emulate one instr": "PJ64SystemFacade.step_cpu_instruction → CPUCore.step",
    "GFX_ProcessDList (lesson name)": "PJ64SystemFacade.run_rdp_hle → ACsN64Core.process_rdp",
    "RSP_Process (lesson name)": "PJ64SystemFacade.run_rsp_hle → ACsN64Core.process_rsp",
    "AI_DMA / audio pump (lesson name)": "PJ64SystemFacade.run_ai_hle → ACsN64Core.process_audio",
    "Plugin slots (Gfx,Audio,RSP,Ctrl)": "pj64_plugin_slots_monolith → ACsN64Core.pj64_plugin_slots",
}


def pj64_port_note(subsystem: str) -> Optional[str]:
    """Return where a PJ64-style subsystem name lives in this port, or None."""
    return PJ64_01_PORT.get(subsystem)


@dataclass(frozen=True)
class PJ64PluginSlot:
    """YouTube PJ64 lesson: Gfx / Audio / RSP / Controller plugins — here all HLE-inlined."""
    name: str
    role: str


def pj64_plugin_slots_monolith() -> Tuple[PJ64PluginSlot, ...]:
    return (
        PJ64PluginSlot("Gfx", "RDP display lists → Tk Canvas (process_rdp)"),
        PJ64PluginSlot("Audio", "AI DMA drain counter (process_audio)"),
        PJ64PluginSlot("RSP", "SP DMA + immediate HLE (process_rsp)"),
        PJ64PluginSlot("Controller", "SI PIF + keyboard → controller_state"),
    )


class PJ64SystemFacade:
    """
    Early-Project64 / YouTube course layout: ``N64System`` owns CPU, physical memory, MMIO,
    and "plugin" responsibilities folded into ``ACsN64Core`` methods (no ``.dll`` loads).
    """
    __slots__ = ("_core",)

    def __init__(self, core: "ACsN64Core") -> None:
        self._core = core

    @property
    def m_Cpu(self) -> "CPUCore":
        return self._core.cpu

    @property
    def m_Bus(self) -> "DeviceBus":
        return self._core.bus

    @property
    def m_RDRAM(self) -> bytearray:
        return self._core.rdram

    @property
    def m_CartRom(self) -> bytearray:
        return self._core.rom

    @property
    def m_RSP_DMEM(self) -> bytearray:
        return self._core.rsp_dmem

    @property
    def m_RSP_IMEM(self) -> bytearray:
        return self._core.rsp_imem

    @property
    def m_PIF_RAM(self) -> bytearray:
        return self._core.pif_ram

    @property
    def m_PluginSlots(self) -> Tuple[PJ64PluginSlot, ...]:
        return self._core.pj64_plugin_slots

    def step_cpu_instruction(self) -> None:
        """Courseware ``CPU_step()`` / ``Emulate()`` one instruction."""
        self._core.cpu.step()

    def run_rsp_hle(self) -> None:
        """Courseware ``RSP_Process()`` fast path."""
        self._core.process_rsp()

    def run_rdp_hle(self) -> None:
        """Courseware ``GFX_ProcessDList()`` stand-in."""
        self._core.process_rdp()

    def run_ai_hle(self) -> None:
        """Courseware ``AI_DMA()`` / buffer pump stand-in."""
        self._core.process_audio()

# UI Aesthetics
BG_COLOR = "#d4d0c8"
PANEL_COLOR = "#ece9d8"
TEXT_COLOR = "#000000"
ACCENT_BLUE = "#003399"
TERMINAL_GREEN = "#00ff88"
STATUS_RED = "#ff4040"
WHITE = "#ffffff"

# Hardware Constraints
RDRAM_SIZE = 8 * 1024 * 1024
RSP_DMEM_SIZE = 0x1000
RSP_IMEM_SIZE = 0x1000
PIF_RAM_SIZE = 0x40

# Video Interface (n64brew) — framebuffer readback for Tk preview
VI_ORIGIN_REG = 0x04400004  # VI_ORIGIN — RDRAM physical base of 16bpp buffer
VI_WIDTH_REG = 0x04400008   # VI_WIDTH — pixels per line

# Bit Masks
MASK_8 = 0xFF
MASK_16 = 0xFFFF
MASK_32 = 0xFFFFFFFF
MASK_64 = 0xFFFFFFFFFFFFFFFF

# CP0 Registers
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
CP0_ERROREPC = 30

FCR31_COND_BIT = 23

# --- Utility Functions ---
def u8(v: int) -> int: return v & MASK_8
def u16(v: int) -> int: return v & MASK_16
def u32(v: int) -> int: return v & MASK_32
def u64(v: int) -> int: return v & MASK_64

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

def sx8_to_64(v: int) -> int: return u64(sign8(v))
def sx16_to_64(v: int) -> int: return u64(sign16(v))
def sx32_to_64(v: int) -> int: return u64(sign32(v))

def be32(data: bytearray | bytes, offset: int) -> int:
    if offset < 0 or offset + 3 >= len(data): return 0
    return struct.unpack_from(">I", data, offset)[0]

def put_be32(data: bytearray, offset: int, value: int) -> None:
    if offset < 0 or offset + 3 >= len(data): return
    struct.pack_into(">I", data, offset, value & MASK_32)

def rdram_rgb5551_to_ppm(rdram: bytearray, origin: int, width: int, height: int) -> bytes | None:
    """Pack N64 big-endian RGBA5551 RDRAM into a binary P6 PPM for tk.PhotoImage."""
    origin &= 0xFFFFFF
    width = max(1, min(width, 320))
    height = max(1, min(height, 240))
    stride = width * 2
    need = origin + stride * height
    if origin < 0 or need > len(rdram):
        return None
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    out = bytearray(width * height * 3)
    mv = memoryview(rdram)
    o = 0
    for y in range(height):
        row = origin + y * stride
        for x in range(0, stride, 2):
            px = (mv[row + x] << 8) | mv[row + x + 1]
            out[o] = ((px >> 11) & 0x1F) << 3
            out[o + 1] = ((px >> 6) & 0x1F) << 3
            out[o + 2] = ((px >> 1) & 0x1F) << 3
            o += 3
    return header + bytes(out)

def f32_to_bits(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", float(value)))[0]

def bits_to_f32(value: int) -> float:
    return struct.unpack(">f", struct.pack(">I", value & MASK_32))[0]

def f64_to_bits(value: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", float(value)))[0]

def bits_to_f64(value: int) -> float:
    return struct.unpack(">d", struct.pack(">Q", value & MASK_64))[0]

def normalize_commercial_entry(addr: int) -> int:
    """Map ROM header PC to a sensible KSEG0 entry (commercial carts vary encoding)."""
    addr = u32(addr)
    if addr == 0:
        return 0x80000400
    hi = addr >> 24
    if hi in (0x80, 0xA0, 0xB0):
        if hi == 0xB0:
            return 0x80000000 | (addr & 0x1FFFFFFF)
        return addr
    # Physical offset into RDRAM (IPL leaves PC as offset after some loaders)
    if addr < RDRAM_SIZE:
        return 0x80000000 | addr
    if hi == 0 and addr < 0x04000000:
        return 0x80000000 | addr
    return addr


def seed_commercial_pif_ram(pif: bytearray) -> None:
    """Minimal PIF RAM seed so libultra/OS does not hang before first SI DMA (no real CIC)."""
    pif[:] = b"\xff" * PIF_RAM_SIZE
    # Typical joybus idle / presence stub for slot 0 (real handshake is much richer).
    pif[0] = 0xFF
    pif[1] = 0xFF
    pif[2] = 0xFF
    pif[3] = 0xFF


PRIMARY_OPS = {
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

SPECIAL_OPS = {
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

REGIMM_OPS = {
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

# --- Core Hardware Logic ---

class N64Header:
    def __init__(self, data: bytearray):
        if len(data) >= 0x40:
            self.pi_bsd_dom1_lat = data[0]
            self.pi_bsd_dom1_pwd = data[1]
            self.pi_bsd_dom1_pgs = data[2]
            self.pi_bsd_dom1_rls = data[3]
            self.clock_rate = be32(data, 0x04)
            self.boot_address = be32(data, 0x08)
            self.release = be32(data, 0x0C)
            self.crc1 = be32(data, 0x10)
            self.crc2 = be32(data, 0x14)
            self.title = data[0x20:0x34].decode('ascii', 'ignore').strip('\x00').strip()
            self.cart_id = data[0x3C:0x3E].decode('ascii', 'ignore')
        else:
            self.clock_rate = 0
            self.boot_address = 0x80000400
            self.release = 0
            self.crc1 = 0
            self.crc2 = 0
            self.title = "UNKNOWN"
            self.cart_id = "??"

@dataclass
class TLBEntry:
    mask: int = 0
    vpn2: int = 0
    g: bool = False
    asid: int = 0
    pfn0: int = 0
    c0: int = 0
    d0: bool = False
    v0: bool = False
    pfn1: int = 0
    c1: int = 0
    d1: bool = False
    v1: bool = False

class N64Opcode:
    __slots__ = ("word", "op", "rs", "rt", "rd", "sa", "funct", "imm", "simm", "target")
    def __init__(self, word: int):
        self.word = word & MASK_32
        self.op = (self.word >> 26) & 0x3F
        self.rs = (self.word >> 21) & 0x1F
        self.rt = (self.word >> 16) & 0x1F
        self.rd = (self.word >> 11) & 0x1F
        self.sa = (self.word >> 6) & 0x1F
        self.funct = self.word & 0x3F
        self.imm = self.word & MASK_16
        self.simm = sign16(self.imm)
        self.target = self.word & 0x03FFFFFF
        
    def target_addr(self, pc: int) -> int:
        return u32(((pc + 4) & 0xF0000000) | (self.target << 2))

    def branch_addr(self, pc: int) -> int:
        return u32(pc + 4 + (self.simm << 2))

class DeviceBus:
    """N64 physical map + MMIO: Python analogue of PJ64 0.1 memory / IO dispatch."""
    def __init__(self, core: ACsN64Core):
        self.core = core
        self.regs: Dict[int, int] = {}
        self.reset()

    def reset(self):
        self.regs.clear()
        self.regs[0x04300004] = 0x02020102 # MI_VERSION
        self.regs[VI_ORIGIN_REG] = 0      # VI_ORIGIN (game/libultra sets framebuffer pointer)
        self.regs[VI_WIDTH_REG] = 320      # VI_WIDTH
        self.regs[0x04600010] = 0          # PI_STATUS
        self.regs[0x0450000C] = 0          # AI_STATUS
        self.regs[0x04800018] = 0          # SI_STATUS
        self.regs[0x04040010] = 1          # SP_STATUS (Halted)

    def v_to_p(self, addr: int) -> int:
        addr &= MASK_32
        segment = addr >> 29
        
        # Unmapped, Uncached (KSEG1) or Unmapped, Cached (KSEG0)
        if segment == 0b101 or segment == 0b100: 
            return addr & 0x1FFFFFFF
            
        # TLB Translation for KUSEG, KSSEG, KSEG3
        tlb = self.core.cpu.tlb
        asid = self.core.cpu.cp0[CP0_ENTRYHI] & 0xFF
        vpn2 = (addr >> 13) & 0x7FFFF
        
        for entry in tlb:
            if (entry.vpn2 == vpn2) and (entry.g or entry.asid == asid):
                # Valid TLB match found
                even_odd = (addr >> 12) & 1
                if even_odd == 0:
                    if not entry.v0: break
                    return (entry.pfn0 << 12) | (addr & 0xFFF)
                else:
                    if not entry.v1: break
                    return (entry.pfn1 << 12) | (addr & 0xFFF)
        
        # TLB Miss / Invalid (Simplification: return masked physical if missed)
        return addr & 0x1FFFFFFF

    def read_u8(self, addr: int) -> int:
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE: return self.core.rdram[p]
        if 0x10000000 <= p < 0x10000000 + len(self.core.rom):
            return self.core.rom[p - 0x10000000]
        if 0x1FC007C0 <= p < 0x1FC007C0 + PIF_RAM_SIZE:
            return self.core.pif_ram[p - 0x1FC007C0]
        return 0

    def read_u16(self, addr: int) -> int:
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE - 1:
            return (self.core.rdram[p] << 8) | self.core.rdram[p+1]
        return 0

    def read_u32(self, addr: int) -> int:
        p_addr = self.v_to_p(addr)
        if 0x00000000 <= p_addr <= RDRAM_SIZE - 4:
            return be32(self.core.rdram, p_addr)
        if 0x04000000 <= p_addr <= 0x04001000 - 4:
            return be32(self.core.rsp_dmem, p_addr - 0x04000000)
        if 0x04001000 <= p_addr <= 0x04002000 - 4:
            return be32(self.core.rsp_imem, p_addr - 0x04001000)
        if 0x04040000 <= p_addr <= 0x048FFFFF:
            return self.regs.get(p_addr & ~3, 0)
        rom_len = len(self.core.rom)
        roff = p_addr - 0x10000000
        if 0 <= roff <= rom_len - 4:
            return be32(self.core.rom, roff)
        return 0

    def read_u64(self, addr: int) -> int:
        hi = self.read_u32(addr)
        lo = self.read_u32(addr + 4)
        return ((hi << 32) | lo) & MASK_64

    def write_u8(self, addr: int, val: int):
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE:
            self.core.rdram[p] = val & MASK_8
        elif 0x1FC007C0 <= p < 0x1FC007C0 + PIF_RAM_SIZE:
            self.core.pif_ram[p - 0x1FC007C0] = val & MASK_8

    def write_u16(self, addr: int, val: int):
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE - 1:
            val &= MASK_16
            self.core.rdram[p] = (val >> 8) & MASK_8
            self.core.rdram[p+1] = val & MASK_8

    def write_u32(self, addr: int, val: int):
        p_addr = self.v_to_p(addr)
        if 0x00000000 <= p_addr <= RDRAM_SIZE - 4:
            put_be32(self.core.rdram, p_addr, val)
        elif 0x04000000 <= p_addr <= 0x04001000 - 4:
            put_be32(self.core.rsp_dmem, p_addr - 0x04000000, val)
        elif 0x04001000 <= p_addr <= 0x04002000 - 4:
            put_be32(self.core.rsp_imem, p_addr - 0x04001000, val)
        elif 0x04040000 <= p_addr <= 0x048FFFFF:
            aligned = p_addr & ~3
            self.regs[aligned] = val
            self.handle_mmio(aligned, val)

    def write_u64(self, addr: int, val: int):
        val &= MASK_64
        self.write_u32(addr, (val >> 32) & MASK_32)
        self.write_u32(addr + 4, val & MASK_32)

    def handle_mmio(self, addr: int, val: int):
        if addr == 0x0460000C: # PI DMA Write
            self.core.trigger_pi_dma()
            self.regs[0x04600010] = 0 # Clear PI Status
        elif addr == 0x04040008: # SP DMA to RSP
            self.core.trigger_sp_dma(to_rsp=True)
        elif addr == 0x0404000C: # SP DMA from RSP
            self.core.trigger_sp_dma(to_rsp=False)
        elif addr == 0x04040010: # SP_STATUS
            if val & 1: self.regs[0x04040010] &= ~1
            if val & 2: self.regs[0x04040010] |= 1
            if (self.regs[0x04040010] & 1) == 0:
                self.core.process_rsp()
        elif addr == 0x0410000C: # DPC_END (RDP Display List Trigger)
            self.core.process_rdp()
        elif addr == 0x04500004: # AI_LEN (Audio DMA)
            self.core.process_audio()
        elif addr == 0x04800004: # SI_PIF_ADDR_RD64B
            self.core.trigger_si_dma(read_pif=True)
        elif addr == 0x04800010: # SI_PIF_ADDR_WR64B
            self.core.trigger_si_dma(read_pif=False)

class CPUCore:
    """R4300i: Python analogue of PJ64 0.1 R4300 interpreter path (mew64 dispatch)."""
    def __init__(self, core: ACsN64Core):
        self.core = core
        self.gpr = [0] * 32
        self.fpr = [0] * 32
        self.cp0 = [0] * 32
        self.fcr0 = 0x00000511
        self.fcr31 = 0
        self.hi = 0
        self.lo = 0
        self.pc = 0
        self.next_pc = 4
        self.llbit = False
        self.lladdr = 0
        self.tlb: List[TLBEntry] = [TLBEntry() for _ in range(32)]
        self.reset()

    def reset(self):
        self.gpr = [0] * 32
        self.fpr = [0] * 32
        self.cp0 = [0] * 32
        self.fcr0 = 0x00000511
        self.fcr31 = 0
        self.hi = 0
        self.lo = 0
        self.pc = 0
        self.next_pc = 4
        self.cp0[CP0_PRID] = 0x00000B00 
        self.cp0[CP0_STATUS] = 0x34000000
        self.cp0[CP0_CONFIG] = 0x0006E463
        self.cp0[CP0_WIRED] = 0
        self.llbit = False
        self.lladdr = 0
        self.tlb = [TLBEntry() for _ in range(32)]

    def step(self):
        word = self.core.bus.read_u32(self.pc)
        i = N64Opcode(word)
        self.execute(i)
        self.gpr[0] = 0 # Hardware wired to zero
        self.cp0[CP0_COUNT] = u32(self.cp0[CP0_COUNT] + 1)

    def decode_name(self, o: N64Opcode) -> str:
        if o.op == 0: return SPECIAL_OPS.get(o.funct, "UNKNOWN")
        if o.op == 1: return REGIMM_OPS.get(o.rt, "UNKNOWN")
        if o.op == 0x10:
            if o.rs == 0x10: return COP0_CO.get(o.funct, "UNKNOWN")
            return COP0_RS.get(o.rs, "UNKNOWN")
        if o.op == 0x11:
            base = COP1_RS.get(o.rs, "UNKNOWN")
            if base in ("S", "D", "W", "L"):
                return f"{COP1_FUNCT.get(o.funct, 'UNKNOWN')}.{base}"
            return base
        return PRIMARY_OPS.get(o.op, "UNKNOWN")

    def _branch(self, target: int):
        self.next_pc = u32(target)

    def _skip_likely(self):
        self.pc = u32(self.pc + 4)
        self.next_pc = u32(self.pc + 4)

    def _write_tlb_entry(self, index: int):
        idx = index % 32
        hi = self.cp0[CP0_ENTRYHI]
        lo0 = self.cp0[CP0_ENTRYLO0]
        lo1 = self.cp0[CP0_ENTRYLO1]
        pagemask = self.cp0[CP0_PAGEMASK]
        
        self.tlb[idx].mask = pagemask
        self.tlb[idx].vpn2 = (hi >> 13) & 0x7FFFF
        self.tlb[idx].asid = hi & 0xFF
        self.tlb[idx].g = bool((lo0 & 1) and (lo1 & 1))
        
        self.tlb[idx].pfn0 = (lo0 >> 6) & 0xFFFFF
        self.tlb[idx].c0 = (lo0 >> 3) & 7
        self.tlb[idx].d0 = bool((lo0 >> 2) & 1)
        self.tlb[idx].v0 = bool((lo0 >> 1) & 1)
        
        self.tlb[idx].pfn1 = (lo1 >> 6) & 0xFFFFF
        self.tlb[idx].c1 = (lo1 >> 3) & 7
        self.tlb[idx].d1 = bool((lo1 >> 2) & 1)
        self.tlb[idx].v1 = bool((lo1 >> 1) & 1)

    def execute(self, o: N64Opcode):
        name = self.decode_name(o)
        old_pc = self.pc
        self.pc = self.next_pc
        self.next_pc = u32(self.next_pc + 4)
        g = self.gpr

        # Load/Store Math Constants
        if name == "LUI": g[o.rt] = sx32_to_64(o.imm << 16)
        elif name == "ORI": g[o.rt] = u64(g[o.rs] | o.imm)
        elif name == "ANDI": g[o.rt] = u64(g[o.rs] & o.imm)
        elif name == "XORI": g[o.rt] = u64(g[o.rs] ^ o.imm)
        elif name == "ADDI": g[o.rt] = sx32_to_64((g[o.rs] + o.simm) & MASK_32)
        elif name == "ADDIU": g[o.rt] = sx32_to_64((g[o.rs] + o.simm) & MASK_32)
        elif name == "DADDI": g[o.rt] = u64(sign64(g[o.rs]) + o.simm)
        elif name == "DADDIU": g[o.rt] = u64(g[o.rs] + o.simm)
        elif name == "SLTI": g[o.rt] = 1 if sign64(g[o.rs]) < o.simm else 0
        elif name == "SLTIU": g[o.rt] = 1 if g[o.rs] < u64(o.simm) else 0

        # Memory Loads
        elif name == "LW": g[o.rt] = sx32_to_64(self.core.bus.read_u32(g[o.rs] + o.simm))
        elif name == "LWU": g[o.rt] = self.core.bus.read_u32(g[o.rs] + o.simm)
        elif name == "LH": g[o.rt] = sx16_to_64(self.core.bus.read_u16(g[o.rs] + o.simm))
        elif name == "LHU": g[o.rt] = self.core.bus.read_u16(g[o.rs] + o.simm)
        elif name == "LB": g[o.rt] = sx8_to_64(self.core.bus.read_u8(g[o.rs] + o.simm))
        elif name == "LBU": g[o.rt] = self.core.bus.read_u8(g[o.rs] + o.simm)
        elif name == "LD": g[o.rt] = self.core.bus.read_u64(g[o.rs] + o.simm)
        elif name == "LL":
            addr = u32(g[o.rs] + o.simm)
            g[o.rt] = sx32_to_64(self.core.bus.read_u32(addr))
            self.llbit = True
            self.lladdr = addr & ~3
        elif name == "LLD":
            addr = u32(g[o.rs] + o.simm)
            g[o.rt] = self.core.bus.read_u64(addr)
            self.llbit = True
            self.lladdr = addr & ~7
        
        # Memory Stores
        elif name == "SW": self.core.bus.write_u32(g[o.rs] + o.simm, u32(g[o.rt]))
        elif name == "SH": self.core.bus.write_u16(g[o.rs] + o.simm, u16(g[o.rt]))
        elif name == "SB": self.core.bus.write_u8(g[o.rs] + o.simm, u8(g[o.rt]))
        elif name == "SD": self.core.bus.write_u64(g[o.rs] + o.simm, g[o.rt])
        elif name == "SC":
            addr = u32(g[o.rs] + o.simm)
            if self.llbit and (addr & ~3) == self.lladdr:
                self.core.bus.write_u32(addr, u32(g[o.rt]))
                g[o.rt] = 1
            else: g[o.rt] = 0
            self.llbit = False
        elif name == "SCD":
            addr = u32(g[o.rs] + o.simm)
            if self.llbit and (addr & ~7) == self.lladdr:
                self.core.bus.write_u64(addr, g[o.rt])
                g[o.rt] = 1
            else: g[o.rt] = 0
            self.llbit = False

        # ALU Special Ops
        elif name == "ADD": g[o.rd] = sx32_to_64((g[o.rs] + g[o.rt]) & MASK_32)
        elif name == "ADDU": g[o.rd] = sx32_to_64((g[o.rs] + g[o.rt]) & MASK_32)
        elif name == "SUB": g[o.rd] = sx32_to_64((g[o.rs] - g[o.rt]) & MASK_32)
        elif name == "SUBU": g[o.rd] = sx32_to_64((g[o.rs] - g[o.rt]) & MASK_32)
        elif name == "DADD": g[o.rd] = u64(sign64(g[o.rs]) + sign64(g[o.rt]))
        elif name == "DADDU": g[o.rd] = u64(g[o.rs] + g[o.rt])
        elif name == "DSUB": g[o.rd] = u64(sign64(g[o.rs]) - sign64(g[o.rt]))
        elif name == "DSUBU": g[o.rd] = u64(g[o.rs] - g[o.rt])
        elif name == "AND": g[o.rd] = u64(g[o.rs] & g[o.rt])
        elif name == "OR": g[o.rd] = u64(g[o.rs] | g[o.rt])
        elif name == "XOR": g[o.rd] = u64(g[o.rs] ^ g[o.rt])
        elif name == "NOR": g[o.rd] = u64(~(g[o.rs] | g[o.rt]))
        elif name == "SLT": g[o.rd] = 1 if sign64(g[o.rs]) < sign64(g[o.rt]) else 0
        elif name == "SLTU": g[o.rd] = 1 if g[o.rs] < g[o.rt] else 0

        # Shifts
        elif name == "SLL": g[o.rd] = sx32_to_64((g[o.rt] & MASK_32) << o.sa)
        elif name == "SRL": g[o.rd] = sx32_to_64((g[o.rt] & MASK_32) >> o.sa)
        elif name == "SRA": g[o.rd] = sx32_to_64(sign32(g[o.rt]) >> o.sa)
        elif name == "SLLV": g[o.rd] = sx32_to_64((g[o.rt] & MASK_32) << (g[o.rs] & 0x1F))
        elif name == "SRLV": g[o.rd] = sx32_to_64((g[o.rt] & MASK_32) >> (g[o.rs] & 0x1F))
        elif name == "SRAV": g[o.rd] = sx32_to_64(sign32(g[o.rt]) >> (g[o.rs] & 0x1F))
        elif name == "DSLL": g[o.rd] = u64(g[o.rt] << o.sa)
        elif name == "DSRL": g[o.rd] = u64(g[o.rt] >> o.sa)
        elif name == "DSRA": g[o.rd] = u64(sign64(g[o.rt]) >> o.sa)
        elif name == "DSLLV": g[o.rd] = u64(g[o.rt] << (g[o.rs] & 0x3F))
        elif name == "DSRLV": g[o.rd] = u64(g[o.rt] >> (g[o.rs] & 0x3F))
        elif name == "DSRAV": g[o.rd] = u64(sign64(g[o.rt]) >> (g[o.rs] & 0x3F))
        elif name == "DSLL32": g[o.rd] = u64(g[o.rt] << (o.sa + 32))
        elif name == "DSRL32": g[o.rd] = u64(g[o.rt] >> (o.sa + 32))
        elif name == "DSRA32": g[o.rd] = u64(sign64(g[o.rt]) >> (o.sa + 32))

        # HI/LO / Math
        elif name == "MFHI": g[o.rd] = self.hi
        elif name == "MTHI": self.hi = u64(g[o.rs])
        elif name == "MFLO": g[o.rd] = self.lo
        elif name == "MTLO": self.lo = u64(g[o.rs])
        elif name == "MULT":
            prod = sign32(g[o.rs]) * sign32(g[o.rt])
            self.lo = sx32_to_64(prod & MASK_32)
            self.hi = sx32_to_64((prod >> 32) & MASK_32)
        elif name == "MULTU":
            prod = (g[o.rs] & MASK_32) * (g[o.rt] & MASK_32)
            self.lo = sx32_to_64(prod & MASK_32)
            self.hi = sx32_to_64((prod >> 32) & MASK_32)
        elif name == "DMULT":
            prod = sign64(g[o.rs]) * sign64(g[o.rt])
            self.lo = u64(prod)
            self.hi = u64(prod >> 64)
        elif name == "DMULTU":
            prod = g[o.rs] * g[o.rt]
            self.lo = u64(prod)
            self.hi = u64(prod >> 64)
        elif name == "DIV" or name == "DIVU":
            a = g[o.rs] & MASK_32
            b = g[o.rt] & MASK_32
            if b != 0:
                if name == "DIV":
                    q, r = int(sign32(a) / sign32(b)), sign32(a) % sign32(b)
                else:
                    q, r = a // b, a % b
                self.lo = sx32_to_64(q)
                self.hi = sx32_to_64(r)
        elif name == "DDIV" or name == "DDIVU":
            a = g[o.rs]
            b = g[o.rt]
            if b != 0:
                if name == "DDIV":
                    q, r = int(sign64(a) / sign64(b)), sign64(a) % sign64(b)
                else:
                    q, r = a // b, a % b
                self.lo = u64(q)
                self.hi = u64(r)

        # Unaligned Memory (Simplistic implementation)
        elif name in ("LWL", "LWR", "LDL", "LDR", "SWL", "SWR", "SDL", "SDR"):
            # Clean-room stub for unaligned operations to prevent crash
            pass 

        # Branches / Jumps
        elif name == "J": self._branch(o.target_addr(old_pc))
        elif name == "JAL":
            g[31] = u64(old_pc + 8)
            self._branch(o.target_addr(old_pc))
        elif name == "JR": self._branch(g[o.rs])
        elif name == "JALR":
            g[o.rd] = u64(old_pc + 8)
            self._branch(g[o.rs])
        elif name == "BEQ":
            if g[o.rs] == g[o.rt]: self._branch(o.branch_addr(old_pc))
        elif name == "BNE":
            if g[o.rs] != g[o.rt]: self._branch(o.branch_addr(old_pc))
        elif name == "BLEZ":
            if sign64(g[o.rs]) <= 0: self._branch(o.branch_addr(old_pc))
        elif name == "BGTZ":
            if sign64(g[o.rs]) > 0: self._branch(o.branch_addr(old_pc))
        elif name == "BEQL":
            if g[o.rs] == g[o.rt]: self._branch(o.branch_addr(old_pc))
            else: self._skip_likely()
        elif name == "BNEL":
            if g[o.rs] != g[o.rt]: self._branch(o.branch_addr(old_pc))
            else: self._skip_likely()
        elif name == "BLEZL":
            if sign64(g[o.rs]) <= 0: self._branch(o.branch_addr(old_pc))
            else: self._skip_likely()
        elif name == "BGTZL":
            if sign64(g[o.rs]) > 0: self._branch(o.branch_addr(old_pc))
            else: self._skip_likely()

        elif name == "BLTZ":
            if sign64(g[o.rs]) < 0: self._branch(o.branch_addr(old_pc))
        elif name == "BGEZ":
            if sign64(g[o.rs]) >= 0: self._branch(o.branch_addr(old_pc))
        elif name == "BLTZL":
            if sign64(g[o.rs]) < 0: self._branch(o.branch_addr(old_pc))
            else: self._skip_likely()
        elif name == "BGEZL":
            if sign64(g[o.rs]) >= 0: self._branch(o.branch_addr(old_pc))
            else: self._skip_likely()
        elif name == "BLTZAL":
            g[31] = u64(old_pc + 8)
            if sign64(g[o.rs]) < 0: self._branch(o.branch_addr(old_pc))
        elif name == "BGEZAL":
            g[31] = u64(old_pc + 8)
            if sign64(g[o.rs]) >= 0: self._branch(o.branch_addr(old_pc))
        elif name == "BLTZALL":
            g[31] = u64(old_pc + 8)
            if sign64(g[o.rs]) < 0: self._branch(o.branch_addr(old_pc))
            else: self._skip_likely()
        elif name == "BGEZALL":
            g[31] = u64(old_pc + 8)
            if sign64(g[o.rs]) >= 0: self._branch(o.branch_addr(old_pc))
            else: self._skip_likely()

        # Coprocessor 0 & TLB
        elif name == "MFC0": g[o.rt] = sx32_to_64(self.cp0[o.rd])
        elif name == "DMFC0": g[o.rt] = u64(self.cp0[o.rd])
        elif name == "MTC0":
            self.cp0[o.rd] = u32(g[o.rt])
            if o.rd == CP0_COMPARE: self.cp0[CP0_CAUSE] &= ~(1 << 15)
        elif name == "DMTC0": self.cp0[o.rd] = u64(g[o.rt])
        elif name == "ERET":
            target = self.cp0[CP0_ERROREPC] if (self.cp0[CP0_STATUS] & 0x4) else self.cp0[CP0_EPC]
            self.pc = u32(target)
            self.next_pc = u32(self.pc + 4)
            self.cp0[CP0_STATUS] &= ~0x6
        elif name == "TLBWI":
            idx = self.cp0[CP0_INDEX] & 0x1F
            self._write_tlb_entry(idx)
        elif name == "TLBWR":
            w = self.cp0[CP0_WIRED] & 0x1F
            idx = random.randint(w, 31)
            self._write_tlb_entry(idx)
        elif name == "TLBP":
            hi = self.cp0[CP0_ENTRYHI]
            vpn2 = (hi >> 13) & 0x7FFFF
            asid = hi & 0xFF
            match = -1
            for i, entry in enumerate(self.tlb):
                if entry.vpn2 == vpn2 and (entry.g or entry.asid == asid):
                    match = i
                    break
            if match >= 0: self.cp0[CP0_INDEX] = match
            else: self.cp0[CP0_INDEX] = 0x80000000
        elif name == "TLBR":
            idx = self.cp0[CP0_INDEX] & 0x1F
            entry = self.tlb[idx]
            self.cp0[CP0_PAGEMASK] = entry.mask
            self.cp0[CP0_ENTRYHI] = (entry.vpn2 << 13) | entry.asid
            self.cp0[CP0_ENTRYLO0] = (entry.pfn0 << 6) | (entry.c0 << 3) | (entry.d0 << 2) | (entry.v0 << 1) | entry.g
            self.cp0[CP0_ENTRYLO1] = (entry.pfn1 << 6) | (entry.c1 << 3) | (entry.d1 << 2) | (entry.v1 << 1) | entry.g
        
        # COP1 (FPU Operations Stub / Direct)
        elif name == "MFC1": g[o.rt] = sx32_to_64(self.fpr[o.rd] & MASK_32)
        elif name == "DMFC1": g[o.rt] = self.fpr[o.rd]
        elif name == "CFC1": g[o.rt] = sx32_to_64(self.fcr31 if o.rd == 31 else self.fcr0)
        elif name == "MTC1": self.fpr[o.rd] = u64((self.fpr[o.rd] & 0xFFFFFFFF00000000) | (g[o.rt] & MASK_32))
        elif name == "DMTC1": self.fpr[o.rd] = g[o.rt]
        elif name == "CTC1":
            if o.rd == 31: self.fcr31 = u32(g[o.rt])
            elif o.rd == 0: self.fcr0 = u32(g[o.rt])
        elif name == "BC1":
            tf = o.rt & 1
            likely = bool(o.rt & 2)
            cond = bool((self.fcr31 >> FCR31_COND_BIT) & 1)
            if cond == bool(tf): self._branch(o.branch_addr(old_pc))
            elif likely: self._skip_likely()
            
        elif "." in name: # FPU Arithmetics mapping safely handled natively or skipped if complex
            pass

class ACsN64Core:
    """Engine core: PJ64 0.1-style subsystems folded into one Python object (no plugin DLLs)."""
    def __init__(self):
        self.rom = bytearray()
        self.rdram = bytearray(RDRAM_SIZE)
        self.rsp_dmem = bytearray(RSP_DMEM_SIZE)
        self.rsp_imem = bytearray(RSP_IMEM_SIZE)
        self.pif_ram = bytearray(PIF_RAM_SIZE)
        
        self.bus = DeviceBus(self)
        self.cpu = CPUCore(self)
        self.pj64_plugin_slots: Tuple[PJ64PluginSlot, ...] = pj64_plugin_slots_monolith()
        self.n64_system = PJ64SystemFacade(self)

        self.rom_name = "None"
        self.is_running = False
        self.has_booted = False
        self.frame_count = 0
        self.hle_calls = 0
        
        # IO States
        self.controller_state = 0x0000
        self.rdp_draw_commands = []
        self.audio_samples_played = 0

    def load_rom(self, path: str):
        with open(path, "rb") as f:
            data = f.read()
        self.rom = self.normalize_rom(bytearray(data))
        self.rom_name = os.path.basename(path)
        self.header = N64Header(self.rom)

        self.reset()
        self.has_booted = False

    def normalize_rom(self, data: bytearray) -> bytearray:
        if len(data) < 4: return data
        magic = data[0:4]
        if magic == b'\x80\x37\x12\x40': return data # Z64
        elif magic == b'\x37\x80\x40\x12': # V64
            for i in range(0, len(data) - 1, 2):
                data[i], data[i+1] = data[i+1], data[i]
        elif magic == b'\x40\x12\x37\x80': # N64
            for i in range(0, len(data) - 3, 4):
                data[i], data[i+3] = data[i+3], data[i]
                data[i+1], data[i+2] = data[i+2], data[i+1]
        return data

    def boot(self) -> bool:
        """Boot path tuned for commercial carts: IPL-style mirrors, entry PC, stack, PIF stub."""
        if len(self.rom) < 0x1000:
            return False

        self.reset()
        self.header = N64Header(self.rom)
        seed_commercial_pif_ram(self.pif_ram)

        # IPL3-style: mirror cart into RDRAM (up to 4 MiB linear — helps PI-less probing).
        linear_cap = min(len(self.rom), 4 * 1024 * 1024, RDRAM_SIZE)
        if linear_cap > 0:
            self.rdram[0:linear_cap] = self.rom[0:linear_cap]

        # Classic compatibility window (many IPLs touch 0x00100000 region).
        rom_window = min(0x200000, len(self.rom), RDRAM_SIZE - 0x100000)
        if rom_window > 0:
            self.rdram[0x100000:0x100000 + rom_window] = self.rom[0:rom_window]

        # osMemSize-style hook: advertise full 8 MiB RDRAM (virtual 0x80000318 → phys 0x318).
        put_be32(self.rdram, 0x318, 0x00800000)

        entry = normalize_commercial_entry(self.header.boot_address)
        self.cpu.pc = entry
        self.cpu.next_pc = u32(entry + 4)

        # Post-IPL GPR convention: stack in upper RDRAM (libultra-safe default).
        self.cpu.gpr[29] = u64(0x803FA800)
        self.cpu.gpr[30] = u64(0x803FA800)

        self.cpu.cp0[CP0_STATUS] = 0x34000000
        self.cpu.cp0[CP0_CONFIG] = 0x0006E463

        self.bus.regs[0x04600010] = 0

        self.has_booted = True
        self.is_running = True
        return True

    def reset(self):
        self.rdram = bytearray(RDRAM_SIZE)
        self.rsp_dmem = bytearray(RSP_DMEM_SIZE)
        self.rsp_imem = bytearray(RSP_IMEM_SIZE)
        self.pif_ram = bytearray(PIF_RAM_SIZE)
        self.bus.reset()
        self.cpu.reset()
        self.frame_count = 0
        self.hle_calls = 0
        self.rdp_draw_commands.clear()
        self.audio_samples_played = 0

    def trigger_pi_dma(self):
        dram_addr = self.bus.regs.get(0x04600000, 0) & 0x00FFFFFF
        cart_addr = (self.bus.regs.get(0x04600004, 0) & 0x0FFFFFFF)
        length = (self.bus.regs.get(0x0460000C, 0) & 0x00FFFFFF) + 1
        if cart_addr >= len(self.rom) or dram_addr >= RDRAM_SIZE:
            return
        actual_len = min(length, len(self.rom) - cart_addr, RDRAM_SIZE - dram_addr)
        if actual_len > 0:
            self.rdram[dram_addr:dram_addr + actual_len] = self.rom[cart_addr:cart_addr + actual_len]

    def trigger_sp_dma(self, to_rsp: bool):
        sp_addr = self.bus.regs.get(0x04040000, 0) & 0x1FFF
        dram_addr = self.bus.regs.get(0x04040004, 0) & 0x00FFFFFF
        length = (self.bus.regs.get(0x04040008 if to_rsp else 0x0404000C, 0) & 0xFFF) + 1
        target = self.rsp_imem if sp_addr & 0x1000 else self.rsp_dmem
        off = sp_addr & 0xFFF
        length = min(length, 0x1000 - off, max(0, RDRAM_SIZE - dram_addr))
        if length <= 0:
            return
        if to_rsp:
            target[off:off + length] = self.rdram[dram_addr:dram_addr + length]
        else:
            self.rdram[dram_addr:dram_addr + length] = target[off:off + length]

    def trigger_si_dma(self, read_pif: bool):
        """Serial Interface: Communicate with PIF-RAM for controllers."""
        dram_addr = self.bus.regs.get(0x04800000, 0) & 0x00FFFFFF
        xfer = min(64, max(0, RDRAM_SIZE - dram_addr))
        if xfer <= 0:
            self.bus.regs[0x04800018] = 0
            return
        if read_pif: # PIF RAM -> RDRAM
            # Format controller response into PIF RAM before copying
            self.pif_ram[0:4] = struct.pack(">I", self.controller_state << 16)
            self.rdram[dram_addr:dram_addr + xfer] = self.pif_ram[0:xfer]
        else: # RDRAM -> PIF RAM
            self.pif_ram[0:xfer] = self.rdram[dram_addr:dram_addr + xfer]
            if xfer < 64:
                self.pif_ram[xfer:64] = bytearray(64 - xfer)
        self.bus.regs[0x04800018] = 0 # SI Status Clear

    def process_rsp(self):
        """HLE representation of the Reality Signal Processor executing."""
        self.hle_calls += 1
        # RSP finishes execution immediately in HLE
        self.bus.regs[0x04040010] |= 1 # Set Halt bit

    def process_rdp(self):
        """HLE representation of the Reality Display Processor Parsing."""
        start_addr = self.bus.regs.get(0x04100000, 0) & 0x00FFFFFF
        end_addr = self.bus.regs.get(0x04100004, 0) & 0x00FFFFFF
        
        # Clear out old draw commands for this frame
        self.rdp_draw_commands.clear()
        
        while start_addr < end_addr:
            cmd = self.bus.read_u64(start_addr)
            cmd_id = (cmd >> 56) & 0x3F
            
            # 0x3F: Fill Rectangle, 0x36: Fill Triangle (Simulated mapping)
            if cmd_id == 0x3F or cmd_id == 0x36:
                # Basic graphical representation to feed to Tkinter Canvas
                x = (cmd >> 12) & 0x3FF
                y = cmd & 0x3FF
                color = "#" + hex(random.randint(0x100000, 0xFFFFFF))[2:]
                self.rdp_draw_commands.append({"type": "rect", "x": x, "y": y, "color": color})
                
            start_addr += 8

    def process_audio(self):
        """HLE representation of the Audio Interface (AI)."""
        length = self.bus.regs.get(0x04500004, 0)
        # Simulate draining the audio buffer
        self.audio_samples_played += length
        self.bus.regs[0x0450000C] = 0 # Clear AI full status

    def vi_framebuffer_phys_origin(self) -> int:
        """Physical RDRAM offset for 16bpp framebuffer (VI_ORIGIN), with boot-time fallback."""
        reg = self.bus.regs.get(VI_ORIGIN_REG, 0) & 0xFFFFFF
        if reg != 0:
            return reg
        # IPL / libultra often uses 0x00100000 before VI_ORIGIN is programmed
        return 0x00100000

    def vi_display_width_height(self) -> Tuple[int, int]:
        w = self.bus.regs.get(VI_WIDTH_REG, 320) & 0xFFF
        if w < 64 or w > 1024:
            w = 320
        return w, 240

    def vi_framebuffer_ppm(self) -> bytes | None:
        """Raw PPM (P6) bytes for the preview window (320×240 max)."""
        w, h = self.vi_display_width_height()
        ow = min(320, w)
        oh = min(240, h)
        return rdram_rgb5551_to_ppm(self.rdram, self.vi_framebuffer_phys_origin(), ow, oh)

    def run_frame(self):
        # Execute instructions in chunks to balance UI and emulation speed
        for _ in range(12000):
            # Clean-room HLE check (stubbed for future expansion)
            if self.cpu.pc & 0x80000000: self.hle_calls += 1
            self.n64_system.step_cpu_instruction()
        self.frame_count += 1

# --- GUI Layer ---

class ACsN64GUI:
    def __init__(self):
        if tk is None: return
        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.geometry("750x540")
        self.root.configure(bg=BG_COLOR)
        
        self.core = ACsN64Core()
        self._fb_photo = None
        self._setup_ui()
        self._bind_controls()
        self._update_loop()

    def _setup_ui(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open ROM...", command=self.open_rom)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menubar)

        toolbar = tk.Frame(self.root, bg=BG_COLOR, relief=tk.RAISED, bd=1)
        toolbar.pack(fill=tk.X)
        
        for txt, cmd in [("Run", self.toggle_run), ("Reset", self.reset_emu)]:
            tk.Button(toolbar, text=txt, command=cmd, width=10, bg=PANEL_COLOR).pack(side=tk.LEFT, padx=2, pady=2)

        tk.Label(toolbar, text=f"{ENGINE_NAME} | PJ640.1→Py", bg=BG_COLOR, fg=ACCENT_BLUE, font=("Arial", 9, "bold")).pack(side=tk.RIGHT, padx=10)

        main_area = tk.Frame(self.root, bg=BG_COLOR)
        main_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Monitor Panel
        self.monitor = tk.Label(main_area, text="[ READY ]", bg="black", fg=TERMINAL_GREEN, font=("Consolas", 10), justify=tk.LEFT, anchor=tk.NW, width=32, height=22, relief=tk.SUNKEN, bd=2, padx=10, pady=10)
        self.monitor.pack(side=tk.LEFT, fill=tk.Y)

        # Presentation Canvas
        self.canvas = tk.Canvas(main_area, width=320, height=240, bg="black", highlightthickness=1, highlightbackground=ACCENT_BLUE)
        self.canvas.pack(side=tk.LEFT, padx=30, expand=True)
        self.canvas.create_text(
            160, 120,
            text=f"{APP_NAME}\nPJ64 0.1 layout (Py)\n{CATHLE_TAG}",
            fill=TERMINAL_GREEN, justify=tk.CENTER,
            tags="splash",
        )

        self.info_text = tk.StringVar(value="Status: Idle")
        self.status_bar = tk.Label(self.root, textvariable=self.info_text, bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _bind_controls(self):
        """Map Keyboard inputs to SI Controller states."""
        self.root.bind("<KeyPress>", self._on_key_press)
        self.root.bind("<KeyRelease>", self._on_key_release)
        
        self.key_map = {
            'Up': 0x0800, 'Down': 0x0400, 'Left': 0x0200, 'Right': 0x0100, # D-Pad
            'Return': 0x1000, # Start
            'z': 0x8000, # A Button
            'x': 0x4000, # B Button
        }

    def _on_key_press(self, event):
        if event.keysym in self.key_map:
            self.core.controller_state |= self.key_map[event.keysym]

    def _on_key_release(self, event):
        if event.keysym in self.key_map:
            self.core.controller_state &= ~self.key_map[event.keysym]

    def open_rom(self):
        path = filedialog.askopenfilename(filetypes=[("N64 ROMs", "*.z64 *.v64 *.n64 *.rom *.bin")])
        if path:
            self.core.load_rom(path)
            self.info_text.set(f"Status: Loaded | ROM: {self.core.rom_name}")

    def toggle_run(self):
        if not self.core.rom: return
        if not self.core.has_booted:
            success = self.core.boot()
            if not success:
                self.info_text.set("Status: Boot Failed - Invalid ROM")
                return
        else:
            self.core.is_running = not self.core.is_running
            
        rom_display_name = self.core.header.title if hasattr(self.core, 'header') and self.core.header.title else self.core.rom_name
        self.info_text.set(f"Status: {'Running' if self.core.is_running else 'Paused'} | ROM: {rom_display_name}")

    def reset_emu(self):
        self.core.reset()
        self.core.has_booted = False
        self.core.is_running = False
        self.canvas.delete("all")
        self.canvas.create_text(
            160, 120,
            text=f"{APP_NAME}\nPJ64 0.1 layout (Py)\n{CATHLE_TAG}",
            fill=TERMINAL_GREEN, justify=tk.CENTER,
            tags="splash",
        )
        self.info_text.set("Status: Reset")
        self._fb_photo = None

    def _refresh_vi_framebuffer(self) -> None:
        """Show RDRAM as RGB5551 using VI_ORIGIN (or 0x00100000 ROM mirror) in the black canvas."""
        ppm = self.core.vi_framebuffer_ppm()
        if not ppm:
            return
        photo = None
        try:
            stream = io.BytesIO(ppm)
            photo = tk.PhotoImage(master=self.root, file=stream, format="ppm")
        except tk.TclError:
            try:
                from PIL import Image, ImageTk
                photo = ImageTk.PhotoImage(Image.open(io.BytesIO(ppm)), master=self.root)
            except Exception:
                return
        self._fb_photo = photo
        self.canvas.delete("fb")
        self.canvas.delete("splash")
        self.canvas.create_image(0, 0, anchor="nw", image=self._fb_photo, tags="fb")

    def _update_loop(self):
        if self.core.is_running:
            self.core.run_frame()
            
            # Render RDP Display Lists to Canvas
            if self.core.frame_count % 2 == 0:
                self._refresh_vi_framebuffer()
                self.canvas.delete("overlay")
                for cmd in self.core.rdp_draw_commands:
                    # Drawing generic rectangles as a basic interpretation of the RDP
                    self.canvas.create_rectangle(cmd["x"], cmd["y"], cmd["x"]+10, cmd["y"]+10, fill=cmd["color"], tags="overlay")

            # Dynamic system monitor update
            mon_text = (
                f"--- {ENGINE_NAME.upper()} | PJ640.1→PY ---\n"
                f"{PJ64_01_LINE}\n"
                f"{CATHLE_TAG}\n"
                f"State: RUNNING\n"
                f"FPS  : 60 (Fixed)\n"
                f"Frame: {self.core.frame_count}\n\n"
                f"--- R4300I CPU ---\n"
                f"PC   : 0x{self.core.cpu.pc:08X}\n"
                f"TLB  : Active (32 Entries)\n"
                f"HLE  : {self.core.hle_calls}\n\n"
                f"--- HARDWARE BUS ---\n"
                f"VI   : reg 0x{(self.core.bus.regs.get(VI_ORIGIN_REG, 0) & 0xFFFFFF):06X} "
                f"prev 0x{self.core.vi_framebuffer_phys_origin():06X}\n"
                f"RDP  : {len(self.core.rdp_draw_commands)} CMDs rendered\n"
                f"RSP  : HLE Online\n"
                f"SI   : Ctrl (0x{self.core.controller_state:04X})\n"
                f"AI   : {self.core.audio_samples_played} bytes\n"
                f"PJ64 : n64_system + {len(self.core.pj64_plugin_slots)} plugin slots (HLE)"
            )
            self.monitor.config(text=mon_text)
            
        self.root.after(16, self._update_loop)

    def run(self):
        self.root.mainloop()

def main():
    if tk is None:
        print("Fatal: Tkinter is required for this GUI.")
        sys.exit(1)
    app = ACsN64GUI()
    app.run()

if __name__ == "__main__":
    main()
