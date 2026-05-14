"""
hle64.py
emuaihle 1.x / HLE64 single-file UltraHLE-style ROM boot window

Run:
    python3 hle64.py

Features:
- One .py file
- Load ROM
- Boot ROM into a black emulator window
- 60 FPS engine loop
- Ultra speed mode
- UltraHLE-style blue controls
- Black render screen
- HLE core scaffold

This is a starter HLE64 shell, not a complete N64 emulator.
"""

import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox


APP_NAME = "emuaihle 1.x - HLE64"

BG = "#0b1f64"
PANEL = "#102c88"
BLACK = "#000000"
BTN_BG = "#000000"
BTN_TEXT = "#1e90ff"
TEXT = "#4aa3ff"
SOFT_TEXT = "#b9dcff"
GREEN = "#00ff88"


class HLE64Core:
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
        self.game_title = ""
        self.media_format = "?"
        self.country_code = "?"
        self.cic_guess = "UNKNOWN"
        self.boot_status = "WAITING"

        self.rom = bytearray()
        self.rdram = bytearray(8 * 1024 * 1024)

        self.hooks = {}
        self.install_default_hooks()

    def install_default_hooks(self):
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

    def set_ultra_speed(self, enabled: bool):
        self.ultra_speed = enabled

    def load_rom(self, path: str):
        if not path:
            raise ValueError("No ROM selected")

        if not os.path.exists(path):
            raise FileNotFoundError(path)

        with open(path, "rb") as f:
            raw = bytearray(f.read())

        if len(raw) < 0x40:
            raise ValueError("File is too small to be an N64 ROM")

        self.rom_magic = bytes(raw[:4]).hex().upper()
        self.rom_type = self.detect_type(bytes(raw[:4]))
        self.rom = self.normalize_rom(raw, self.rom_type)

        self.rom_path = path
        self.rom_name = os.path.basename(path)
        self.rom_size = len(self.rom)

        title_bytes = bytes(self.rom[0x20:0x34])
        self.game_title = title_bytes.decode("ascii", "ignore").strip() or "UNKNOWN TITLE"
        self.media_format = chr(self.rom[0x3B]) if self.rom[0x3B] else "?"
        self.country_code = chr(self.rom[0x3E]) if self.rom[0x3E] else "?"
        self.cic_guess = self.guess_cic(self.rom)

        self.frame_count = 0
        self.vi_count = 0
        self.hle_calls = 0
        self.booted = False
        self.running = False
        self.boot_status = "ROM LOADED"

        return self.info()

    def detect_type(self, magic: bytes):
        if magic == bytes.fromhex("80371240"):
            return "N64 Z64 / BIG ENDIAN"
        if magic == bytes.fromhex("40123780"):
            return "N64 V64 / BYTE-SWAPPED"
        if magic == bytes.fromhex("37804012"):
            return "N64 N64 / LITTLE ENDIAN"
        return "UNKNOWN / RAW"

    def normalize_rom(self, data: bytearray, rom_type: str):
        normalized = bytearray(data)

        if rom_type == "N64 V64 / BYTE-SWAPPED":
            for i in range(0, len(normalized) - 1, 2):
                normalized[i], normalized[i + 1] = normalized[i + 1], normalized[i]

        elif rom_type == "N64 N64 / LITTLE ENDIAN":
            for i in range(0, len(normalized) - 3, 4):
                normalized[i], normalized[i + 3] = normalized[i + 3], normalized[i]
                normalized[i + 1], normalized[i + 2] = normalized[i + 2], normalized[i + 1]

        return normalized

    def guess_cic(self, data: bytearray):
        end = min(len(data), 0x1000)
        checksum = 0

        for i in range(0x40, end):
            checksum = (checksum + data[i]) & 0xFFFFFFFFFFFFFFFF

        if checksum % 7 == 0:
            return "CIC-NUS-6102-LIKE"
        if checksum % 11 == 0:
            return "CIC-NUS-6103-LIKE"
        if checksum % 13 == 0:
            return "CIC-NUS-6105-LIKE"
        if checksum % 17 == 0:
            return "CIC-NUS-6106-LIKE"
        return "CIC UNKNOWN"

    def boot(self):
        if self.rom_size <= 0:
            self.boot_status = "NO ROM"
            self.booted = False
            return self.info()

        copy_len = min(4096, len(self.rom))
        self.rdram[0:copy_len] = self.rom[0:copy_len]

        self.booted = True
        self.running = True
        self.boot_status = "BOOTED INTO BLACK HLE WINDOW"
        return self.info()

    def dispatch_hle(self, addr: int):
        if addr in self.hooks:
            self.hle_calls += 1
            return self.hooks[addr]
        return "NONE"

    def tick_frame(self):
        if not self.booted:
            return self.info()

        self.frame_count += 1
        self.vi_count += 1

        self.dispatch_hle(0x80000A00)
        self.dispatch_hle(0x80000B00)

        if self.frame_count % 2 == 0:
            self.dispatch_hle(0x80000D00)

        if self.frame_count % 60 == 0:
            self.dispatch_hle(0x80000C00)

        return self.info()

    def info(self):
        return {
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
            "title": self.game_title,
            "media": self.media_format,
            "country": self.country_code,
            "cic": self.cic_guess,
            "boot": self.boot_status,
            "hooks": len(self.hooks),
        }


class EmuAIHLEGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("980x720")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.core = HLE64Core()
        self.running = False
        self.frame_ms = int(1000 / 60)
        self.last_tick = time.perf_counter()

        self.build_ui()
        self.render_info(self.core.info())
        self.render_black_screen("LOAD A ROM, THEN PRESS BOOT ROM")

    def make_button(self, parent, label, command):
        return tk.Button(
            parent,
            text=label,
            command=command,
            bg=BTN_BG,
            fg=BTN_TEXT,
            activebackground="#050505",
            activeforeground="#66b3ff",
            relief=tk.RAISED,
            bd=3,
            width=15,
            height=1,
            font=("Arial", 11, "bold"),
            cursor="hand2",
        )

    def build_ui(self):
        title = tk.Label(
            self.root,
            text="EMUAIHLE 1.X",
            bg=BG,
            fg=TEXT,
            font=("Arial", 31, "bold"),
        )
        title.pack(pady=(16, 2))

        subtitle = tk.Label(
            self.root,
            text="UltraHLE-style GUI • Engine boots ROM into black HLE window • 60 FPS",
            bg=BG,
            fg=SOFT_TEXT,
            font=("Arial", 12, "bold"),
        )
        subtitle.pack(pady=(0, 10))

        self.screen = tk.Canvas(
            self.root,
            width=640,
            height=360,
            bg=BLACK,
            highlightthickness=4,
            highlightbackground=TEXT,
        )
        self.screen.pack(pady=10)

        controls = tk.Frame(self.root, bg=BG)
        controls.pack(pady=8)

        self.make_button(controls, "LOAD ROM", self.load_rom).grid(row=0, column=0, padx=6, pady=4)
        self.make_button(controls, "BOOT ROM", self.boot_rom).grid(row=0, column=1, padx=6, pady=4)
        self.make_button(controls, "START", self.start).grid(row=0, column=2, padx=6, pady=4)
        self.make_button(controls, "STOP", self.stop).grid(row=0, column=3, padx=6, pady=4)
        self.make_button(controls, "RESET", self.reset).grid(row=0, column=4, padx=6, pady=4)

        controls2 = tk.Frame(self.root, bg=BG)
        controls2.pack(pady=2)

        self.make_button(controls2, "ULTRA ON", lambda: self.set_speed(True)).grid(row=0, column=0, padx=6, pady=4)
        self.make_button(controls2, "NORMAL", lambda: self.set_speed(False)).grid(row=0, column=1, padx=6, pady=4)
        self.make_button(controls2, "QUIT", self.root.destroy).grid(row=0, column=2, padx=6, pady=4)

        self.info_label = tk.Label(
            self.root,
            text="",
            bg=PANEL,
            fg=TEXT,
            width=106,
            height=8,
            font=("Consolas", 10),
            relief=tk.SUNKEN,
            bd=3,
            justify=tk.LEFT,
            anchor="w",
            padx=12,
        )
        self.info_label.pack(pady=12)

    def render_black_screen(self, message=None):
        self.screen.delete("all")

        self.screen.create_rectangle(0, 0, 640, 360, fill=BLACK, outline=BLACK)

        if message:
            self.screen.create_text(
                320,
                165,
                text=message,
                fill=TEXT,
                font=("Consolas", 18, "bold"),
                anchor="center",
            )
            return

        s = self.core.info()
        frame = s["frame"]

        # Fake black-window HLE output.
        pulse = 40 + (frame % 120)
        x = 320 + int(120 * ((frame % 180) / 180.0)) - 60

        self.screen.create_text(
            320,
            34,
            text="HLE64 ENGINE BOOTED",
            fill=GREEN,
            font=("Consolas", 18, "bold"),
        )

        self.screen.create_text(
            320,
            72,
            text=s["title"] or s["rom"],
            fill=TEXT,
            font=("Consolas", 15, "bold"),
        )

        self.screen.create_rectangle(
            80,
            110,
            560,
            285,
            outline=TEXT,
            width=2,
        )

        self.screen.create_text(
            320,
            145,
            text="BLACK RENDER WINDOW",
            fill=SOFT_TEXT,
            font=("Consolas", 16, "bold"),
        )

        self.screen.create_oval(
            x,
            205,
            x + pulse,
            205 + pulse,
            outline=GREEN,
            width=3,
        )

        self.screen.create_text(
            320,
            318,
            text=f"FRAME {s['frame']}  |  VI {s['vi']}  |  HLE CALLS {s['hle_calls']}  |  {s['speed']}",
            fill=TEXT,
            font=("Consolas", 12, "bold"),
        )

    def load_rom(self):
        path = filedialog.askopenfilename(
            title="Load N64 ROM",
            filetypes=[
                ("N64 ROMs", "*.z64 *.v64 *.n64 *.rom *.bin"),
                ("All files", "*.*"),
            ],
        )

        if not path:
            return

        try:
            info = self.core.load_rom(path)
            self.render_info(info)
            self.render_black_screen("ROM LOADED - PRESS BOOT ROM")
        except Exception as exc:
            messagebox.showerror("Load ROM failed", str(exc))

    def boot_rom(self):
        if self.core.rom_size <= 0:
            messagebox.showwarning("No ROM", "Load a ROM first.")
            return

        info = self.core.boot()
        self.running = True
        self.core.running = True
        self.render_info(info)
        self.render_black_screen()
        self.loop()

    def start(self):
        if self.core.rom_size <= 0:
            messagebox.showwarning("No ROM", "Load and boot a ROM first.")
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
        self.render_black_screen("STOPPED")

    def reset(self):
        self.running = False
        self.core.reset()
        self.render_info(self.core.info())
        self.render_black_screen("RESET - LOAD/BOOT ROM")

    def set_speed(self, enabled):
        self.core.set_ultra_speed(enabled)
        self.render_info(self.core.info())

    def loop(self):
        if not self.running:
            return

        info = self.core.tick_frame()
        self.render_info(info)
        self.render_black_screen()

        delay = 1 if self.core.ultra_speed else self.frame_ms
        self.root.after(delay, self.loop)

    def render_info(self, s):
        size = s.get("rom_size", 0)
        size_mb = size / (1024 * 1024) if size else 0

        text = (
            f"STATUS: {s.get('status')}   BOOT: {s.get('boot')}   CORE: SINGLE-FILE HLE64 ENGINE\n"
            f"ROM: {s.get('rom')}   TITLE: {s.get('title') or '---'}\n"
            f"SIZE: {size} bytes ({size_mb:.2f} MB)   MAGIC: {s.get('magic')}   TYPE: {s.get('rom_type')}\n"
            f"CIC: {s.get('cic')}   MEDIA: {s.get('media')}   COUNTRY: {s.get('country')}\n"
            f"FRAME: {s.get('frame')}   VI: {s.get('vi')}   FPS: {s.get('target_fps')}   SPEED: {s.get('speed')}\n"
            f"HLE CALLS: {s.get('hle_calls')}   HOOKS: {s.get('hooks')}   WINDOW: BLACK ROM BOOT DISPLAY"
        )
        self.info_label.config(text=text)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    EmuAIHLEGUI().run()
