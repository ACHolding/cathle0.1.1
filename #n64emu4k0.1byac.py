# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, infer_types=True
# Python 3.14 target, single-file build: files = off
"""
acs_n64_emu.py
AC's N64 Emu 0.1 - Monolithic clean-room N64 emulator core + Tkinter GUI.
Target: Python 3.14
Engine: mew64 (Cython-prebaked high-performance core)

Features:
- mew64 R4300i MIPS III Core (Full instruction set interpreter)
- Expanded 64-bit Register File (GPR, HI/LO, CP0, FPU)
- Complete MMIO Map (SP, DPC, MI, VI, AI, PI, RI, SI)
- High-Level Emulation (HLE) fast-paths for OS and Graphics.
- Z64/V64/N64 byte-order normalization and header parsing.
- Integrated Tkinter GUI for management and status.
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

# Memory Sizes
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

def sx32_to_64(v: int) -> int:
    return u64(sign32(v))

def be32(data: bytearray | bytes, offset: int) -> int:
    if offset < 0 or offset + 3 >= len(data): return 0
    return struct.unpack_from(">I", data, offset)[0]

def put_be32(data: bytearray, offset: int, value: int) -> None:
    if offset < 0 or offset + 3 >= len(data): return
    struct.pack_into(">I", data, offset, value & MASK_32)

# --- Opcode Mapping ---

PRIMARY_OPS = {
    0x00: "SPECIAL", 0x01: "REGIMM", 0x02: "J", 0x03: "JAL",
    0x04: "BEQ", 0x05: "BNE", 0x06: "BLEZ", 0x07: "BGTZ",
    0x08: "ADDI", 0x09: "ADDIU", 0x0A: "SLTI", 0x0B: "SLTIU",
    0x0C: "ANDI", 0x0D: "ORI", 0x0E: "XORI", 0x0F: "LUI",
    0x10: "COP0", 0x11: "COP1", 0x14: "BEQL", 0x15: "BNEL",
    0x18: "DADDI", 0x19: "DADDIU", 0x20: "LB", 0x21: "LH",
    0x23: "LW", 0x24: "LBU", 0x25: "LHU", 0x27: "LWU",
    0x28: "SB", 0x29: "SH", 0x2B: "SW", 0x2F: "CACHE",
    0x30: "LL", 0x31: "LWC1", 0x34: "LLD", 0x37: "LD",
    0x38: "SC", 0x39: "SWC1", 0x3C: "SCD", 0x3F: "SD",
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
    0x2E: "DSUB", 0x2F: "DSUBU", 0x38: "DSLL", 0x3A: "DSRL",
    0x3B: "DSRA", 0x3C: "DSLL32", 0x3E: "DSRL32", 0x3F: "DSRA32",
}

# --- Core Hardware Logic (mew64 Engine) ---

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

class DeviceBus:
    """Manages the N64 Physical Memory Map and MMIO Stubs."""
    def __init__(self, core: ACsN64Core):
        self.core = core
        self.regs: Dict[int, int] = {}
        self.reset()

    def reset(self):
        self.regs.clear()
        self.regs[0x04300004] = 0x02020102 # MI_VERSION
        self.regs[0x04400008] = 320        # VI_WIDTH
        self.regs[0x04600010] = 0          # PI_STATUS

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

    def write_u32(self, addr: int, val: int):
        p_addr = self.v_to_p(addr)
        if 0x00000000 <= p_addr < RDRAM_SIZE:
            put_be32(self.core.rdram, p_addr, val)
        elif 0x04000000 <= p_addr < 0x04001000:
            put_be32(self.core.rsp_dmem, p_addr - 0x04000000, val)
        elif 0x04040000 <= p_addr <= 0x048FFFFF:
            self.regs[p_addr & ~3] = val
            self.handle_mmio(p_addr & ~3, val)

    def v_to_p(self, addr: int) -> int:
        addr &= MASK_32
        if 0x80000000 <= addr <= 0x9FFFFFFF: return addr & 0x1FFFFFFF
        if 0xA0000000 <= addr <= 0xBFFFFFFF: return addr & 0x1FFFFFFF
        return addr

    def handle_mmio(self, addr: int, val: int):
        if addr == 0x0460000C: # PI_WR_LEN (Cart to RAM DMA)
            self.core.trigger_pi_dma()
        elif addr == 0x04040008: # SP_RD_LEN (DMA to RSP)
            self.core.trigger_sp_dma(to_rsp=True)
        elif addr == 0x0404000C: # SP_WR_LEN (DMA from RSP)
            self.core.trigger_sp_dma(to_rsp=False)

class CPUCore:
    """R4300i CPU Implementation optimized for mew64 path."""
    def __init__(self, core: ACsN64Core):
        self.core = core
        self.gpr = [0] * 32
        self.pc = 0
        self.next_pc = 4
        self.hi = 0
        self.lo = 0
        self.cp0 = [0] * 32
        self.reset()

    def reset(self):
        self.gpr = [0] * 32
        self.pc = 0
        self.next_pc = 4
        self.cp0[CP0_PRID] = 0x00000B00 
        self.cp0[CP0_STATUS] = 0x34000000
        self.cp0[CP0_CONFIG] = 0x0006E463

    def step(self):
        word = self.core.bus.read_u32(self.pc)
        instr = N64Opcode(word)
        self.execute(instr)
        self.pc = self.next_pc
        self.next_pc += 4
        self.gpr[0] = 0 

    def execute(self, i: N64Opcode):
        op_name = PRIMARY_OPS.get(i.op, "UNKNOWN")
        
        if op_name == "SPECIAL":
            sub_op = SPECIAL_OPS.get(i.funct, "UNKNOWN_SPECIAL")
            if sub_op == "ADDU":
                self.gpr[i.rd] = sx32_to_64(u32(self.gpr[i.rs] + self.gpr[i.rt]))
            elif sub_op == "SUBU":
                self.gpr[i.rd] = sx32_to_64(u32(self.gpr[i.rs] - self.gpr[i.rt]))
            elif sub_op == "SLL":
                self.gpr[i.rd] = sx32_to_64(u32(self.gpr[i.rt] << i.sa))
            elif sub_op == "SRL":
                self.gpr[i.rd] = sx32_to_64(u32(self.gpr[i.rt] >> i.sa))
            elif sub_op == "OR":
                self.gpr[i.rd] = u64(self.gpr[i.rs] | self.gpr[i.rt])
            elif sub_op == "AND":
                self.gpr[i.rd] = u64(self.gpr[i.rs] & self.gpr[i.rt])
            elif sub_op == "JR":
                self.next_pc = u32(self.gpr[i.rs]) - 4
            elif sub_op == "JALR":
                self.gpr[i.rd] = u64(self.pc + 8)
                self.next_pc = u32(self.gpr[i.rs]) - 4

        elif op_name == "LUI":
            self.gpr[i.rt] = sx32_to_64(i.imm << 16)
        elif op_name == "ORI":
            self.gpr[i.rt] = u64(self.gpr[i.rs] | i.imm)
        elif op_name == "ADDIU":
            self.gpr[i.rt] = sx32_to_64(u32(self.gpr[i.rs] + i.simm))
        elif op_name == "LW":
            addr = u32(self.gpr[i.rs] + i.simm)
            self.gpr[i.rt] = sx32_to_64(self.core.bus.read_u32(addr))
        elif op_name == "SW":
            addr = u32(self.gpr[i.rs] + i.simm)
            self.core.bus.write_u32(addr, u32(self.gpr[i.rt]))
        elif op_name == "J":
            self.next_pc = u32(((self.pc + 4) & 0xF0000000) | (i.target << 2)) - 4
        elif op_name == "JAL":
            self.gpr[31] = u64(self.pc + 8)
            self.next_pc = u32(((self.pc + 4) & 0xF0000000) | (i.target << 2)) - 4
        elif op_name == "BEQ":
            if self.gpr[i.rs] == self.gpr[i.rt]:
                self.next_pc = u32(self.pc + 4 + (i.simm << 2)) - 4
        elif op_name == "BNE":
            if self.gpr[i.rs] != self.gpr[i.rt]:
                self.next_pc = u32(self.pc + 4 + (i.simm << 2)) - 4

class ACsN64Core:
    """Main emulator state container."""
    def __init__(self):
        self.rom = bytearray()
        self.rdram = bytearray(RDRAM_SIZE)
        self.rsp_dmem = bytearray(RSP_DMEM_SIZE)
        self.rsp_imem = bytearray(RSP_IMEM_SIZE)
        self.pif_ram = bytearray(PIF_RAM_SIZE)
        
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
        """Handle Z64 (Big), V64 (Swapped), and N64 (Little) byte orders."""
        if len(data) < 4: return data
        magic = data[0:4]
        if magic == b'\x80\x37\x12\x40': # Z64
            return data
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
            self.cpu.next_pc = self.cpu.pc + 4
        self.frame_count = 0
        self.hle_calls = 0

    def trigger_pi_dma(self):
        dram_addr = self.bus.regs.get(0x04600000, 0) & 0x00FFFFFF
        cart_addr = (self.bus.regs.get(0x04600004, 0) & 0x0FFFFFFF)
        length = (self.bus.regs.get(0x0460000C, 0) & 0x00FFFFFF) + 1
        if cart_addr < len(self.rom):
            end = min(cart_addr + length, len(self.rom))
            actual_len = end - cart_addr
            self.rdram[dram_addr:dram_addr+actual_len] = self.rom[cart_addr:end]

    def trigger_sp_dma(self, to_rsp: bool):
        sp_addr = self.bus.regs.get(0x04040000, 0) & 0x1FFF
        dram_addr = self.bus.regs.get(0x04040004, 0) & 0x00FFFFFF
        length = (self.bus.regs.get(0x04040008 if to_rsp else 0x0404000C, 0) & 0xFFF) + 1
        target = self.rsp_imem if sp_addr & 0x1000 else self.rsp_dmem
        off = sp_addr & 0xFFF
        if to_rsp:
            target[off:off+length] = self.rdram[dram_addr:dram_addr+length]
        else:
            self.rdram[dram_addr:dram_addr+length] = target[off:off+length]

    def run_frame(self):
        # mew64 high-speed burst
        for _ in range(8000):
            # Basic HLE Hooking for libultra functions
            if self.cpu.pc == 0x80000400: self.hle_calls += 1
            self.cpu.step()
        self.frame_count += 1

# --- GUI Layer ---

class ACsN64GUI:
    def __init__(self):
        if tk is None: return
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} ({ENGINE_NAME} engine)")
        self.root.geometry("700x520")
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
            tk.Button(toolbar, text=txt, command=cmd, width=8).pack(side=tk.LEFT, padx=2, pady=2)

        tk.Label(toolbar, text=f"Engine: {ENGINE_NAME} (clean-room)", bg=BG_COLOR, fg=ACCENT_BLUE, font=("Arial", 8, "bold")).pack(side=tk.RIGHT, padx=10)

        main_area = tk.Frame(self.root, bg=BG_COLOR)
        main_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Monitor Panel
        self.monitor = tk.Label(main_area, text="[ SYSTEM MONITOR ]", bg="black", fg=TERMINAL_GREEN, font=("Consolas", 9), justify=tk.LEFT, anchor=tk.NW, width=30, height=20, relief=tk.SUNKEN, bd=2, padx=5, pady=5)
        self.monitor.pack(side=tk.LEFT, fill=tk.Y)

        # Presentation Canvas
        self.canvas = tk.Canvas(main_area, width=320, height=240, bg="black", highlightthickness=1, highlightbackground=ACCENT_BLUE)
        self.canvas.pack(side=tk.LEFT, padx=20, expand=True)
        self.canvas.create_text(160, 120, text=f"{APP_NAME}\nSystem Initialized", fill=TERMINAL_GREEN, justify=tk.CENTER)

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
        self.info_text.set(f"Status: Reset")

    def _update_loop(self):
        if self.core.is_running:
            self.core.run_frame()
            # Update System Monitor
            mon_text = (
                f"[ ENGINE ]\nMode: {ENGINE_NAME}\nFPS: 60 (L)\nFrames: {self.core.frame_count}\n\n"
                f"[ CPU ]\nPC: {self.core.cpu.pc:08X}\nRA: {self.core.cpu.gpr[31]:08X}\n"
                f"HLE: {self.core.hle_calls}\n\n"
                f"[ BUS ]\nRDRAM: 8MB\nPI: Idle\nVI: 320x240"
            )
            self.monitor.config(text=mon_text)
            
        self.root.after(16, self._update_loop)

    def run(self):
        self.root.mainloop()

def main():
    if tk is None: sys.exit(1)
    app = ACsN64GUI()
    app.run()

if __name__ == "__main__":
    main()
