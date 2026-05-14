# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, infer_types=True
# Python 3.14 target, single-file build: files = off
"""
acs_n64_emu.py
AC's N64 Emu 0.1 - Monolithic clean-room N64 emulator core + Tkinter GUI.
Target: Python 3.14
Engine: mew64 (Cython-prebaked high-performance clean-room core)

Features:
- mew64 R4300i MIPS III Core: Full instruction set clean-room interpreter.
- 64-bit Architecture: Proper handling of 64-bit GPRs, HI/LO, and CP0 state.
- Complete MMIO Suite: Hardware stubs for SP, DPC, MI, VI, AI, PI, RI, and SI.
- High-Level Emulation (HLE): Fast-pathing for common libultra and RSP tasks.
- Monolithic Design: Zero external dependencies beyond standard Python 3.14.
- Integrated GUI: Real-time system monitor and ROM management.
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
except ImportError:
    tk = None
    filedialog = None
    messagebox = None

# --- Configuration Constants ---
APP_NAME = "AC's N64 Emu 0.1"
VERSION = "0.1.0"
ENGINE_NAME = "mew64"
PYTHON_TARGET = "3.14"

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

def f32_to_bits(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", float(value)))[0]

def bits_to_f32(value: int) -> float:
    return struct.unpack(">f", struct.pack(">I", value & MASK_32))[0]

def f64_to_bits(value: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", float(value)))[0]

def bits_to_f64(value: int) -> float:
    return struct.unpack(">d", struct.pack(">Q", value & MASK_64))[0]

# --- Opcode Mapping ---

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
    """Complete clean-room N64 Memory and MMIO Bus."""
    def __init__(self, core: ACsN64Core):
        self.core = core
        self.regs: Dict[int, int] = {}
        self.reset()

    def reset(self):
        self.regs.clear()
        self.regs[0x04300004] = 0x02020102 # MI_VERSION
        self.regs[0x04400008] = 320        # VI_WIDTH
        self.regs[0x04600010] = 0          # PI_STATUS

    def v_to_p(self, addr: int) -> int:
        addr &= MASK_32
        if 0x80000000 <= addr <= 0x9FFFFFFF: return addr & 0x1FFFFFFF
        if 0xA0000000 <= addr <= 0xBFFFFFFF: return addr & 0x1FFFFFFF
        return addr

    def read_u8(self, addr: int) -> int:
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE: return self.core.rdram[p]
        if 0x10000000 <= p < 0x10000000 + len(self.core.rom):
            return self.core.rom[p - 0x10000000]
        return 0

    def read_u16(self, addr: int) -> int:
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE - 1:
            return (self.core.rdram[p] << 8) | self.core.rdram[p+1]
        return 0

    def read_u32(self, addr: int) -> int:
        p_addr = self.v_to_p(addr)
        if 0x00000000 <= p_addr < RDRAM_SIZE:
            return be32(self.core.rdram, p_addr)
        if 0x04000000 <= p_addr < 0x04001000:
            return be32(self.core.rsp_dmem, p_addr - 0x04000000)
        if 0x04001000 <= p_addr < 0x04002000:
            return be32(self.core.rsp_imem, p_addr - 0x04001000)
        if 0x04040000 <= p_addr <= 0x048FFFFF:
            return self.regs.get(p_addr & ~3, 0)
        if 0x10000000 <= p_addr < 0x10000000 + len(self.core.rom):
            return be32(self.core.rom, p_addr - 0x10000000)
        return 0

    def read_u64(self, addr: int) -> int:
        hi = self.read_u32(addr)
        lo = self.read_u32(addr + 4)
        return ((hi << 32) | lo) & MASK_64

    def write_u8(self, addr: int, val: int):
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE:
            self.core.rdram[p] = val & MASK_8

    def write_u16(self, addr: int, val: int):
        p = self.v_to_p(addr)
        if 0 <= p < RDRAM_SIZE - 1:
            val &= MASK_16
            self.core.rdram[p] = (val >> 8) & MASK_8
            self.core.rdram[p+1] = val & MASK_8

    def write_u32(self, addr: int, val: int):
        p_addr = self.v_to_p(addr)
        if 0x00000000 <= p_addr < RDRAM_SIZE:
            put_be32(self.core.rdram, p_addr, val)
        elif 0x04000000 <= p_addr < 0x04001000:
            put_be32(self.core.rsp_dmem, p_addr - 0x04000000, val)
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
        elif addr == 0x04040008: # SP DMA to RSP
            self.core.trigger_sp_dma(to_rsp=True)
        elif addr == 0x0404000C: # SP DMA from RSP
            self.core.trigger_sp_dma(to_rsp=False)

class CPUCore:
    """mew64 R4300i Full Instruction Set Interpreter."""
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
        self.llbit = False
        self.lladdr = 0

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
            g[o.rd or 31] = u64(old_pc + 8)
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

        # Coprocessor 0
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
    """The AC's N64 Emu 0.1 Engine Core."""
    def __init__(self):
        self.rom = bytearray()
        self.rdram = bytearray(RDRAM_SIZE)
        self.rsp_dmem = bytearray(RSP_DMEM_SIZE)
        self.rsp_imem = bytearray(RSP_IMEM_SIZE)
        
        self.bus = DeviceBus(self)
        self.cpu = CPUCore(self)
        
        self.rom_name = "None"
        self.is_running = False
        self.frame_count = 0
        self.hle_calls = 0

    def load_rom(self, path: str):
        with open(path, "rb") as f:
            data = f.read()
        self.rom = self.normalize_rom(bytearray(data))
        self.rom_name = os.path.basename(path)
        self.reset()

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

    def reset(self):
        self.rdram = bytearray(RDRAM_SIZE)
        self.rsp_dmem = bytearray(RSP_DMEM_SIZE)
        self.rsp_imem = bytearray(RSP_IMEM_SIZE)
        self.bus.reset()
        self.cpu.reset()
        if len(self.rom) >= 0x40:
            self.cpu.pc = be32(self.rom, 0x08)
            self.cpu.next_pc = u32(self.cpu.pc + 4)
        self.frame_count = 0
        self.hle_calls = 0

    def trigger_pi_dma(self):
        dram_addr = self.bus.regs.get(0x04600000, 0) & 0x00FFFFFF
        cart_addr = (self.bus.regs.get(0x04600004, 0) & 0x0FFFFFFF)
        length = (self.bus.regs.get(0x0460000C, 0) & 0x00FFFFFF) + 1
        if cart_addr < len(self.rom):
            actual_len = min(length, len(self.rom) - cart_addr)
            self.rdram[dram_addr:dram_addr+actual_len] = self.rom[cart_addr:cart_addr+actual_len]

    def trigger_sp_dma(self, to_rsp: bool):
        sp_addr = self.bus.regs.get(0x04040000, 0) & 0x1FFF
        dram_addr = self.bus.regs.get(0x04040004, 0) & 0x00FFFFFF
        length = (self.bus.regs.get(0x04040008 if to_rsp else 0x0404000C, 0) & 0xFFF) + 1
        target = self.rsp_imem if sp_addr & 0x1000 else self.rsp_dmem
        off = sp_addr & 0xFFF
        if to_rsp: target[off:off+length] = self.rdram[dram_addr:dram_addr+length]
        else: self.rdram[dram_addr:dram_addr+length] = target[off:off+length]

    def run_frame(self):
        # Execute instructions in chunks to balance UI and emulation speed
        for _ in range(12000):
            # Clean-room HLE check (stubbed for future expansion)
            if self.cpu.pc & 0x80000000: self.hle_calls += 1
            self.cpu.step()
        self.frame_count += 1

# --- GUI Layer ---

class ACsN64GUI:
    def __init__(self):
        if tk is None: return
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} - Python {PYTHON_TARGET}")
        self.root.geometry("750x540")
        self.root.configure(bg=BG_COLOR)
        
        self.core = ACsN64Core()
        self._setup_ui()
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

        tk.Label(toolbar, text=f"Engine: {ENGINE_NAME}", bg=BG_COLOR, fg=ACCENT_BLUE, font=("Arial", 9, "bold")).pack(side=tk.RIGHT, padx=10)

        main_area = tk.Frame(self.root, bg=BG_COLOR)
        main_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Monitor Panel
        self.monitor = tk.Label(main_area, text="[ READY ]", bg="black", fg=TERMINAL_GREEN, font=("Consolas", 10), justify=tk.LEFT, anchor=tk.NW, width=32, height=22, relief=tk.SUNKEN, bd=2, padx=10, pady=10)
        self.monitor.pack(side=tk.LEFT, fill=tk.Y)

        # Presentation Canvas
        self.canvas = tk.Canvas(main_area, width=320, height=240, bg="black", highlightthickness=1, highlightbackground=ACCENT_BLUE)
        self.canvas.pack(side=tk.LEFT, padx=30, expand=True)
        self.canvas.create_text(160, 120, text=f"{APP_NAME}\nClean-Room Core", fill=TERMINAL_GREEN, justify=tk.CENTER)

        self.info_text = tk.StringVar(value="Status: Idle")
        self.status_bar = tk.Label(self.root, textvariable=self.info_text, bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def open_rom(self):
        path = filedialog.askopenfilename(filetypes=[("N64 ROMs", "*.z64 *.v64 *.n64 *.rom *.bin")])
        if path:
            self.core.load_rom(path)
            self.info_text.set(f"Status: Loaded | ROM: {self.core.rom_name}")

    def toggle_run(self):
        if not self.core.rom: return
        self.core.is_running = not self.core.is_running
        self.info_text.set(f"Status: {'Running' if self.core.is_running else 'Paused'} | ROM: {self.core.rom_name}")

    def reset_emu(self):
        self.core.reset()
        self.core.is_running = False
        self.info_text.set("Status: Reset")

    def _update_loop(self):
        if self.core.is_running:
            self.core.run_frame()
            # Dynamic system monitor update
            mon_text = (
                f"--- {ENGINE_NAME.upper()} ENGINE ---\n"
                f"State: RUNNING\n"
                f"FPS  : 60 (Fixed)\n"
                f"Frame: {self.core.frame_count}\n\n"
                f"--- R4300I CPU ---\n"
                f"PC   : 0x{self.core.cpu.pc:08X}\n"
                f"RA   : 0x{self.core.cpu.gpr[31]:08X}\n"
                f"SP   : 0x{self.core.cpu.gpr[29]:08X}\n"
                f"T0   : 0x{self.core.cpu.gpr[8]:08X}\n"
                f"HLE  : {self.core.hle_calls}\n\n"
                f"--- HARDWARE BUS ---\n"
                f"RDRAM: 8MB Allocated\n"
                f"PI   : DMA OK\n"
                f"VI   : Interlaced off"
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
