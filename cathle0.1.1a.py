"""
cathle0.1.x.py
CatHLE 0.1.x - single-file N64 HLE/opcode emulator shell

Python 3.14 compatible.
One .py file only.

Run:
    python3 cathle0.1.x.py

Features:
- Classic Project64-0.1-inspired emulator layout, without copied Project64 code/assets
- Load N64 ROM
- Parse real N64 header
- Normalize Z64 / V64 / N64 byte order
- Boot-map ROM into RDRAM
- Black emulator screen
- N64 / MIPS III opcode decoder
- Safe CPU opcode execution subset
- HLE/libultra-style hook counters
- 60 FPS loop / Ultra mode

This is a clean-room educational HLE shell. It is not a complete N64 emulator.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox


APP_NAME = "CatHLE 0.1.x"

BG = "#d4d0c8"          # classic gray
PANEL = "#ece9d8"
BLACK = "#000000"
BLUE = "#003399"
TEXT = "#000000"
GREEN = "#00ff88"
RED = "#ff4040"
WHITE = "#ffffff"


def u32(v):
    return v & 0xFFFFFFFF


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def sign16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def be32(data, offset):
    if offset + 3 >= len(data):
        return 0
    return (
        (data[offset] << 24)
        | (data[offset + 1] << 16)
        | (data[offset + 2] << 8)
        | data[offset + 3]
    )


def ascii_clean(raw):
    return bytes(raw).decode("ascii", "ignore").replace("\x00", "").strip()


class N64Header:
    def __init__(self):
        self.valid = False
        self.pi_lat = 0
        self.pi_pwd = 0
        self.pi_pgs = 0
        self.pi_rls = 0
        self.clock_rate = 0
        self.boot_address = 0
        self.release = 0
        self.crc1 = 0
        self.crc2 = 0
        self.title = "UNKNOWN TITLE"
        self.media = "?"
        self.cart_id = "??"
        self.country = "?"
        self.version = 0

    def parse(self, rom):
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

    def info(self):
        return {
            "valid": self.valid,
            "pi_lat": self.pi_lat,
            "pi_pwd": self.pi_pwd,
            "pi_pgs": self.pi_pgs,
            "pi_rls": self.pi_rls,
            "clock_rate": self.clock_rate,
            "boot_address": self.boot_address,
            "release": self.release,
            "crc1": self.crc1,
            "crc2": self.crc2,
            "title": self.title,
            "media": self.media,
            "cart_id": self.cart_id,
            "country": self.country,
            "version": self.version,
        }


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


class N64Opcode:
    def __init__(self, word):
        self.word = word & 0xFFFFFFFF
        self.op = (word >> 26) & 0x3F
        self.rs = (word >> 21) & 0x1F
        self.rt = (word >> 16) & 0x1F
        self.rd = (word >> 11) & 0x1F
        self.sa = (word >> 6) & 0x1F
        self.funct = word & 0x3F
        self.imm = word & 0xFFFF
        self.simm = sign16(self.imm)
        self.target = word & 0x03FFFFFF

    def target_addr(self, pc):
        return ((pc + 4) & 0xF0000000) | (self.target << 2)

    def branch_addr(self, pc):
        return u32(pc + 4 + (self.simm << 2))


class N64CPU:
    def __init__(self, core):
        self.core = core
        self.gpr = [0] * 32
        self.hi = 0
        self.lo = 0
        self.pc = 0
        self.next_pc = 4
        self.last_opcode = 0
        self.last_decode = "RESET"
        self.opcode_count = 0
        self.exception = ""

    def reset(self, pc):
        self.gpr = [0] * 32
        self.hi = 0
        self.lo = 0
        self.pc = u32(pc)
        self.next_pc = u32(pc + 4)
        self.last_opcode = 0
        self.last_decode = "RESET"
        self.opcode_count = 0
        self.exception = ""

    def decode_name(self, word):
        o = N64Opcode(word)
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

    def format_decode(self, word):
        o = N64Opcode(word)
        name = self.decode_name(word)
        if o.op == 0:
            return f"{name} rd=r{o.rd} rs=r{o.rs} rt=r{o.rt} sa={o.sa}"
        if name in ("J", "JAL"):
            return f"{name} 0x{o.target_addr(self.pc):08X}"
        if name.startswith("B"):
            return f"{name} rs=r{o.rs} rt=r{o.rt} -> 0x{o.branch_addr(self.pc):08X}"
        if o.op in (0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x30, 0x34, 0x37, 0x38, 0x3C, 0x3F):
            return f"{name} rt=r{o.rt}, {o.simm}(r{o.rs})"
        return f"{name} rs=r{o.rs} rt=r{o.rt} imm=0x{o.imm:04X}"

    def read_u32(self, vaddr):
        addr = vaddr & 0xFFFFFFFF
        if 0x80000000 <= addr <= 0x807FFFFF:
            off = addr - 0x80000000
        elif 0xA0000000 <= addr <= 0xA07FFFFF:
            off = addr - 0xA0000000
        else:
            off = addr
        if 0 <= off <= len(self.core.rdram) - 4:
            return be32(self.core.rdram, off)
        return 0

    def write_u32(self, vaddr, value):
        addr = vaddr & 0xFFFFFFFF
        if 0x80000000 <= addr <= 0x807FFFFF:
            off = addr - 0x80000000
        elif 0xA0000000 <= addr <= 0xA07FFFFF:
            off = addr - 0xA0000000
        else:
            off = addr
        if 0 <= off <= len(self.core.rdram) - 4:
            value &= 0xFFFFFFFF
            self.core.rdram[off:off + 4] = bytes([
                (value >> 24) & 0xFF,
                (value >> 16) & 0xFF,
                (value >> 8) & 0xFF,
                value & 0xFF,
            ])

    def step(self):
        word = self.read_u32(self.pc)
        self.last_opcode = word
        self.last_decode = self.format_decode(word)
        self.execute(word)
        self.opcode_count += 1
        self.gpr[0] = 0
        return self.last_decode

    def execute(self, word):
        o = N64Opcode(word)
        name = self.decode_name(word)
        old_pc = self.pc

        self.pc = self.next_pc
        self.next_pc = u32(self.next_pc + 4)

        if word == 0:
            return

        if name == "LUI":
            self.gpr[o.rt] = u32(o.imm << 16)
        elif name == "ORI":
            self.gpr[o.rt] = u32(self.gpr[o.rs] | o.imm)
        elif name == "ANDI":
            self.gpr[o.rt] = u32(self.gpr[o.rs] & o.imm)
        elif name == "XORI":
            self.gpr[o.rt] = u32(self.gpr[o.rs] ^ o.imm)
        elif name in ("ADDIU", "ADDI"):
            self.gpr[o.rt] = u32(self.gpr[o.rs] + o.simm)
        elif name in ("ADDU", "ADD"):
            self.gpr[o.rd] = u32(self.gpr[o.rs] + self.gpr[o.rt])
        elif name in ("SUBU", "SUB"):
            self.gpr[o.rd] = u32(self.gpr[o.rs] - self.gpr[o.rt])
        elif name == "AND":
            self.gpr[o.rd] = u32(self.gpr[o.rs] & self.gpr[o.rt])
        elif name == "OR":
            self.gpr[o.rd] = u32(self.gpr[o.rs] | self.gpr[o.rt])
        elif name == "XOR":
            self.gpr[o.rd] = u32(self.gpr[o.rs] ^ self.gpr[o.rt])
        elif name == "NOR":
            self.gpr[o.rd] = u32(~(self.gpr[o.rs] | self.gpr[o.rt]))
        elif name == "SLTI":
            self.gpr[o.rt] = 1 if s32(self.gpr[o.rs]) < o.simm else 0
        elif name == "SLTIU":
            self.gpr[o.rt] = 1 if self.gpr[o.rs] < (o.simm & 0xFFFFFFFF) else 0
        elif name == "SLT":
            self.gpr[o.rd] = 1 if s32(self.gpr[o.rs]) < s32(self.gpr[o.rt]) else 0
        elif name == "SLTU":
            self.gpr[o.rd] = 1 if self.gpr[o.rs] < self.gpr[o.rt] else 0
        elif name == "SLL":
            self.gpr[o.rd] = u32(self.gpr[o.rt] << o.sa)
        elif name == "SRL":
            self.gpr[o.rd] = u32(self.gpr[o.rt] >> o.sa)
        elif name == "SRA":
            self.gpr[o.rd] = u32(s32(self.gpr[o.rt]) >> o.sa)
        elif name == "SLLV":
            self.gpr[o.rd] = u32(self.gpr[o.rt] << (self.gpr[o.rs] & 0x1F))
        elif name == "SRLV":
            self.gpr[o.rd] = u32(self.gpr[o.rt] >> (self.gpr[o.rs] & 0x1F))
        elif name == "SRAV":
            self.gpr[o.rd] = u32(s32(self.gpr[o.rt]) >> (self.gpr[o.rs] & 0x1F))
        elif name == "LW":
            self.gpr[o.rt] = self.read_u32(u32(self.gpr[o.rs] + o.simm))
        elif name == "SW":
            self.write_u32(u32(self.gpr[o.rs] + o.simm), self.gpr[o.rt])
        elif name == "J":
            self.next_pc = o.target_addr(old_pc)
        elif name == "JAL":
            self.gpr[31] = u32(old_pc + 8)
            self.next_pc = o.target_addr(old_pc)
        elif name == "JR":
            self.next_pc = u32(self.gpr[o.rs])
        elif name == "JALR":
            self.gpr[o.rd or 31] = u32(old_pc + 8)
            self.next_pc = u32(self.gpr[o.rs])
        elif name == "BEQ":
            if self.gpr[o.rs] == self.gpr[o.rt]:
                self.next_pc = o.branch_addr(old_pc)
        elif name == "BNE":
            if self.gpr[o.rs] != self.gpr[o.rt]:
                self.next_pc = o.branch_addr(old_pc)
        elif name == "BLEZ":
            if s32(self.gpr[o.rs]) <= 0:
                self.next_pc = o.branch_addr(old_pc)
        elif name == "BGTZ":
            if s32(self.gpr[o.rs]) > 0:
                self.next_pc = o.branch_addr(old_pc)
        elif name == "BLTZ":
            if s32(self.gpr[o.rs]) < 0:
                self.next_pc = o.branch_addr(old_pc)
        elif name == "BGEZ":
            if s32(self.gpr[o.rs]) >= 0:
                self.next_pc = o.branch_addr(old_pc)
        elif name in ("SYSCALL", "BREAK"):
            self.exception = name

    def info(self):
        return {
            "pc": self.pc,
            "next_pc": self.next_pc,
            "last_opcode": self.last_opcode,
            "last_decode": self.last_decode,
            "opcode_count": self.opcode_count,
            "exception": self.exception,
        }


class CatHLECore:
    def __init__(self):
        self.target_fps = 60
        self.ultra_speed = True
        self.running = False
        self.booted = False

        self.frame_count = 0
        self.vi_count = 0
        self.hle_calls = 0

        self.rom_path = ""
        self.rom_name = "NO ROM LOADED"
        self.rom_size = 0
        self.rom_type = "NONE"
        self.rom_magic = "----"
        self.cic_guess = "UNKNOWN"
        self.boot_status = "WAITING"
        self.boot_pc = 0

        self.header = N64Header()
        self.rom = bytearray()
        self.rdram = bytearray(8 * 1024 * 1024)
        self.cpu = N64CPU(self)

        self.hooks = {}
        self.install_hooks()

    def install_hooks(self):
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
        }

    def reset(self):
        self.frame_count = 0
        self.vi_count = 0
        self.hle_calls = 0
        self.booted = False
        self.running = False
        self.boot_status = "RESET"
        self.rdram[:] = b"\x00" * len(self.rdram)
        self.cpu.reset(0)

    def set_ultra_speed(self, enabled):
        self.ultra_speed = bool(enabled)

    def detect_type(self, magic):
        if magic == bytes.fromhex("80371240"):
            return "Z64 BIG-ENDIAN"
        if magic == bytes.fromhex("40123780"):
            return "V64 BYTE-SWAPPED"
        if magic == bytes.fromhex("37804012"):
            return "N64 LITTLE-ENDIAN"
        return "UNKNOWN RAW"

    def normalize_rom(self, data, rom_type):
        out = bytearray(data)
        if rom_type == "V64 BYTE-SWAPPED":
            for i in range(0, len(out) - 1, 2):
                out[i], out[i + 1] = out[i + 1], out[i]
        elif rom_type == "N64 LITTLE-ENDIAN":
            for i in range(0, len(out) - 3, 4):
                out[i], out[i + 3] = out[i + 3], out[i]
                out[i + 1], out[i + 2] = out[i + 2], out[i + 1]
        return out

    def guess_cic(self, data):
        checksum = 0
        for b in data[0x40:min(len(data), 0x1000)]:
            checksum = (checksum + b) & 0xFFFFFFFFFFFFFFFF
        if checksum % 7 == 0:
            return "CIC-NUS-6102-LIKE"
        if checksum % 11 == 0:
            return "CIC-NUS-6103-LIKE"
        if checksum % 13 == 0:
            return "CIC-NUS-6105-LIKE"
        if checksum % 17 == 0:
            return "CIC-NUS-6106-LIKE"
        return "CIC UNKNOWN"

    def load_rom(self, path):
        if not path:
            raise ValueError("No ROM selected")
        if not os.path.exists(path):
            raise FileNotFoundError(path)

        with open(path, "rb") as f:
            raw = bytearray(f.read())

        if len(raw) < 0x1000:
            raise ValueError("ROM is too small to boot as N64")

        self.rom_magic = bytes(raw[:4]).hex().upper()
        self.rom_type = self.detect_type(bytes(raw[:4]))
        self.rom = self.normalize_rom(raw, self.rom_type)

        self.header = N64Header()
        self.header.parse(self.rom)

        self.rom_path = path
        self.rom_name = os.path.basename(path)
        self.rom_size = len(self.rom)
        self.cic_guess = self.guess_cic(self.rom)
        self.boot_pc = self.header.boot_address or 0x80000400
        self.boot_status = "N64 HEADER OK" if bytes(self.rom[:4]) == bytes.fromhex("80371240") else "BAD OR UNKNOWN N64 HEADER"

        self.frame_count = 0
        self.vi_count = 0
        self.hle_calls = 0
        self.booted = False
        self.running = False
        self.cpu.reset(self.boot_pc)

        return self.info()

    def boot(self):
        if self.rom_size <= 0:
            self.boot_status = "NO ROM"
            self.booted = False
            return self.info()

        if bytes(self.rom[:4]) != bytes.fromhex("80371240"):
            self.boot_status = "BOOT BLOCKED: BAD N64 MAGIC"
            self.booted = False
            return self.info()

        self.rdram[:] = b"\x00" * len(self.rdram)

        boot_len = min(0x1000, len(self.rom))
        self.rdram[0:boot_len] = self.rom[0:boot_len]

        rom_window = min(0x200000, len(self.rom))
        self.rdram[0x100000:0x100000 + rom_window] = self.rom[0:rom_window]

        # Map boot code at low RDRAM so 0x80000000 reads work.
        self.rdram[0:boot_len] = self.rom[0:boot_len]

        self.boot_pc = self.header.boot_address or 0x80000400
        self.cpu.reset(self.boot_pc)
        self.booted = True
        self.running = True
        self.boot_status = f"BOOTED ROM AT PC 0x{self.boot_pc:08X}"
        return self.info()

    def dispatch_hle(self, addr):
        if addr in self.hooks:
            self.hle_calls += 1
            return self.hooks[addr]
        return "NONE"

    def tick_frame(self):
        if not self.booted:
            return self.info()

        self.frame_count += 1
        self.vi_count += 1

        steps = 72 if self.ultra_speed else 8
        for _ in range(steps):
            self.cpu.step()

        self.dispatch_hle(0x80000A00)
        self.dispatch_hle(0x80000B00)

        if self.frame_count % 2 == 0:
            self.dispatch_hle(0x80000D00)
        if self.frame_count % 60 == 0:
            self.dispatch_hle(0x80000C00)

        return self.info()

    def info(self):
        info = {
            "status": "RUNNING" if self.running else "READY",
            "booted": self.booted,
            "target_fps": self.target_fps,
            "speed": "ULTRA" if self.ultra_speed else "NORMAL",
            "frame": self.frame_count,
            "vi": self.vi_count,
            "hle_calls": self.hle_calls,
            "rom": self.rom_name,
            "rom_size": self.rom_size,
            "rom_type": self.rom_type,
            "magic": self.rom_magic,
            "cic": self.cic_guess,
            "boot": self.boot_status,
            "boot_pc": self.boot_pc,
            "hooks": len(self.hooks),
            "opcode_table": len(PRIMARY) + len(SPECIAL) + len(REGIMM) + len(COP0_RS) + len(COP0_CO) + len(COP1_RS) + len(COP1_FUNCT),
        }
        info.update(self.header.info())
        info.update(self.cpu.info())
        return info


class CatHLEGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1120x820")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.core = CatHLECore()
        self.running = False
        self.frame_ms = int(1000 / 60)

        self.build_ui()
        self.render_info(self.core.info())
        self.render_screen("Load an N64 ROM, then choose Emulation > Start")

    def build_ui(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open ROM...", command=self.load_rom)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        emu_menu = tk.Menu(menubar, tearoff=0)
        emu_menu.add_command(label="Start", command=self.start)
        emu_menu.add_command(label="Pause", command=self.stop)
        emu_menu.add_command(label="Reset", command=self.reset)
        menubar.add_cascade(label="Emulation", menu=emu_menu)

        options_menu = tk.Menu(menubar, tearoff=0)
        options_menu.add_command(label="Ultra Speed", command=lambda: self.set_speed(True))
        options_menu.add_command(label="Normal Speed", command=lambda: self.set_speed(False))
        menubar.add_cascade(label="Options", menu=options_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=lambda: messagebox.showinfo(APP_NAME, "CatHLE 0.1.x\nClassic N64 HLE shell"))
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

        toolbar = tk.Frame(self.root, bg=BG, relief=tk.RAISED, bd=2)
        toolbar.pack(fill=tk.X)

        self.tool_button(toolbar, "Open", self.load_rom).pack(side=tk.LEFT, padx=3, pady=3)
        self.tool_button(toolbar, "Boot", self.boot_rom).pack(side=tk.LEFT, padx=3, pady=3)
        self.tool_button(toolbar, "Start", self.start).pack(side=tk.LEFT, padx=3, pady=3)
        self.tool_button(toolbar, "Pause", self.stop).pack(side=tk.LEFT, padx=3, pady=3)
        self.tool_button(toolbar, "Reset", self.reset).pack(side=tk.LEFT, padx=3, pady=3)

        tk.Label(toolbar, text="  CatHLE 0.1.x", bg=BG, fg=BLUE, font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=12)

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = tk.Frame(main, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(left, text="ROM Browser", bg=BLUE, fg=WHITE, width=34, anchor="w", font=("Arial", 10, "bold")).pack(fill=tk.X)
        self.rom_list = tk.Listbox(left, width=42, height=20, bg=WHITE, fg=TEXT, font=("Consolas", 10))
        self.rom_list.pack(fill=tk.Y)
        self.rom_list.insert(tk.END, "No ROM loaded")

        right = tk.Frame(main, bg=BG)
        right.pack(side=tk.LEFT, padx=10)

        self.screen = tk.Canvas(right, width=704, height=396, bg=BLACK, highlightthickness=2, highlightbackground=TEXT)
        self.screen.pack()

        self.info_label = tk.Label(
            right,
            text="",
            bg=PANEL,
            fg=TEXT,
            width=104,
            height=15,
            font=("Consolas", 10),
            relief=tk.SUNKEN,
            bd=2,
            justify=tk.LEFT,
            anchor="w",
            padx=10,
        )
        self.info_label.pack(pady=8)

        self.status = tk.Label(self.root, text="Ready", bg=PANEL, fg=TEXT, anchor="w", relief=tk.SUNKEN, bd=1)
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

    def tool_button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, width=8, bg=PANEL, fg=TEXT, relief=tk.RAISED)

    def render_screen(self, message=None):
        self.screen.delete("all")
        self.screen.create_rectangle(0, 0, 704, 396, fill=BLACK, outline=BLACK)

        if message:
            self.screen.create_text(352, 190, text=message, fill=GREEN, font=("Consolas", 16, "bold"))
            return

        s = self.core.info()

        if not s["booted"]:
            self.screen.create_text(352, 190, text="ROM NOT BOOTED", fill=RED, font=("Consolas", 20, "bold"))
            return

        frame = s["frame"]
        pulse = 28 + (frame % 100)
        x = 352 + int(160 * ((frame % 180) / 180.0)) - 80

        self.screen.create_text(352, 28, text="N64 OPCODE ENGINE RUNNING", fill=GREEN, font=("Consolas", 18, "bold"))
        self.screen.create_text(352, 58, text=s.get("title") or s["rom"], fill=WHITE, font=("Consolas", 14, "bold"))
        self.screen.create_text(352, 88, text=f"PC 0x{s['pc']:08X} | OPCODE 0x{s['last_opcode']:08X}", fill=GREEN, font=("Consolas", 12, "bold"))
        self.screen.create_text(352, 116, text=s["last_decode"][:82], fill=WHITE, font=("Consolas", 11, "bold"))
        self.screen.create_rectangle(82, 145, 622, 314, outline=GREEN, width=2)
        self.screen.create_text(352, 174, text="BLACK N64 BOOT WINDOW", fill=WHITE, font=("Consolas", 14, "bold"))
        self.screen.create_oval(x, 230, x + pulse, 230 + pulse, outline=GREEN, width=3)
        self.screen.create_text(352, 356, text=f"FRAME {s['frame']} | VI {s['vi']} | OPS {s['opcode_count']} | HLE {s['hle_calls']} | {s['speed']}", fill=GREEN, font=("Consolas", 12, "bold"))

    def load_rom(self):
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
            self.rom_list.insert(tk.END, info.get("title") or "UNKNOWN TITLE")
            self.rom_list.insert(tk.END, info["rom_type"])
            self.render_info(info)
            self.render_screen("ROM loaded. Press Boot or Start.")
            self.status.config(text=f"Loaded {info['rom']}")
        except Exception as exc:
            messagebox.showerror("Load ROM failed", str(exc))

    def boot_rom(self):
        if self.core.rom_size <= 0:
            messagebox.showwarning("No ROM", "Load an N64 ROM first.")
            return

        info = self.core.boot()
        self.render_info(info)

        if not info["booted"]:
            self.render_screen(info["boot"])
            self.status.config(text=info["boot"])
            return

        self.running = True
        self.core.running = True
        self.status.config(text=info["boot"])
        self.render_screen()
        self.loop()

    def start(self):
        if self.core.rom_size <= 0:
            messagebox.showwarning("No ROM", "Load an N64 ROM first.")
            return

        if not self.core.booted:
            self.boot_rom()
            return

        if not self.running:
            self.running = True
            self.core.running = True
            self.loop()

    def stop(self):
        self.running = False
        self.core.running = False
        self.render_info(self.core.info())
        self.render_screen("Paused")
        self.status.config(text="Paused")

    def reset(self):
        self.running = False
        self.core.reset()
        self.render_info(self.core.info())
        self.render_screen("Reset. Load/boot ROM.")
        self.status.config(text="Reset")

    def set_speed(self, enabled):
        self.core.set_ultra_speed(enabled)
        self.render_info(self.core.info())
        self.status.config(text=f"Speed: {'ULTRA' if enabled else 'NORMAL'}")

    def loop(self):
        if not self.running:
            return
        info = self.core.tick_frame()
        self.render_info(info)
        self.render_screen()
        delay = 1 if self.core.ultra_speed else self.frame_ms
        self.root.after(delay, self.loop)

    def render_info(self, s):
        size = s.get("rom_size", 0)
        size_mb = size / (1024 * 1024) if size else 0
        text = (
            f"STATUS: {s.get('status')}   BOOT: {s.get('boot')}   CORE: CatHLE N64 opcode shell\n"
            f"ROM: {s.get('rom')}   SIZE: {size} bytes ({size_mb:.2f} MB)   TYPE: {s.get('rom_type')}   MAGIC: {s.get('magic')}\n"
            f"TITLE: {s.get('title') or '---'}   CART ID: {s.get('cart_id')}   MEDIA: {s.get('media')}   COUNTRY: {s.get('country')}   VERSION: {s.get('version')}\n"
            f"BOOT PC: 0x{s.get('boot_pc', 0):08X}   CPU PC: 0x{s.get('pc', 0):08X}   NEXT: 0x{s.get('next_pc', 0):08X}   CLOCK: 0x{s.get('clock_rate', 0):08X}\n"
            f"CRC1: 0x{s.get('crc1', 0):08X}   CRC2: 0x{s.get('crc2', 0):08X}   RELEASE: 0x{s.get('release', 0):08X}   CIC: {s.get('cic')}\n"
            f"LAST OPCODE: 0x{s.get('last_opcode', 0):08X}   DECODE: {s.get('last_decode', '')[:76]}\n"
            f"OPCOUNT: {s.get('opcode_count')}   OPCODE TABLE ENTRIES: {s.get('opcode_table')}   EXCEPTION: {s.get('exception') or '---'}\n"
            f"FRAME: {s.get('frame')}   VI: {s.get('vi')}   FPS: {s.get('target_fps')}   SPEED: {s.get('speed')}   HLE CALLS: {s.get('hle_calls')}   HOOKS: {s.get('hooks')}\n"
            f"NOTE: Runs a safe subset of N64/MIPS opcodes; many opcodes decode but remain stubs."
        )
        self.info_label.config(text=text)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    CatHLEGUI().run()
