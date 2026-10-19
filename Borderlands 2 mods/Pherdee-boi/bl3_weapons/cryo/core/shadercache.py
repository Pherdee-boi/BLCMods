"""Compiled shaders kept between launches (0.4.19; RESEARCH.md §6.71).

Her materials are built and compiled at every start (shaders.py): 19 to 21 of them, some 600 shaders, and on the dev
PC 29 seconds of the game's own thread, in hitches of up to 2.7 s over the first half minute at the main menu.
Players: "a spike during start up time". Nine tenths of it is spent inside the compiler itself, d3dx9_43's
`D3DXCompileShader` (measured: 48 calls and 2.47 of the 2.67 s of one particle material's compile). The engine has a
cache for this, its local shader cache; a cooked game never writes it, and shaders.py sees to it that ours doesn't.

So what the compiler returns is kept here instead. For the length of one of our compiles (`kept()`, around the
console command in `shaders.compile_material`), the exe's import of `D3DXCompileShader` points at `_compile`:
- a shader whose whole input was seen before is handed back from our file without the compiler: the code in a
  buffer of d3dx's own (`D3DXCreateBuffer`), its constant table read out of the code again
  (`D3DXGetShaderConstantTable`: the table is part of the code);
- anything else goes to the compiler, and what it returns is kept for the next start.

"The whole input" is the source text, every file it includes (taken from the engine's own include handler: its
shader files, and the two it makes for each material and vertex factory, `Material.usf` and `VertexFactory.usf`),
the macros, the entry point, the profile and the flags, as one SHA-1. A change to her HLSL, to a material's
settings or to the game's shader files is another key: that shader compiles once more, and its old code is never
handed back.

The files the engine makes are taken without their comments. One comment of the engine's material template ("to
start fading out 80% of the way") goes through a formatted print on its way into `Material.usf`, and its "% o"
comes out as a number off the stack, another one at every start (measured: `80272520604f the way` and
`80272513104f the way` for the same material): with it no key was ever the same twice.

Nothing here is on the path of anything but our own compiles: the import is put back the moment the console
command returns, and the game's own shaders come from its cooked cache as always. Whatever fails (the import not
found, the file damaged, an include not readable, d3dx refusing a buffer) is the compiler's to do, as before 0.4.19.
A file named `aurelia_shaders.off` in the settings folder turns the keeping off.

The file is `aurelia_shaders.bin` in the mod's settings folder (about 0.4 MB): safe to delete, it is made again.

**The constant table above 2 GB (0.6.7; RESEARCH.md §6.94).** With each shader the compiler returns a table of its
constants, which the engine reads straight away: `GetConstant(NULL, i)`, then `GetConstantDesc(handle)`. D3DX takes
a handle to be either a name or one of its own, and tells them apart by the sign: its own are the address negated.
That holds while the table lies in the lower 2 GB. BL2 is large-address-aware, and once that half of its address
space is taken (late in a long session; at the main menu already under some DXVK versions, which take address space
early), the table lands above it, the negated address reads as a name, and the engine stops the game:
"D3D9SafeGetConstantDesc(...) failed ... with error D3DERR_INVALIDCALL" (a player's start-up crash with DXVK,
2026-10-06). D3DX has a table that takes every handle as its own, `D3DXGetShaderConstantTableEx` with
`D3DXCONSTTABLE_LARGEADDRESSAWARE`; `D3DXCompileShader` refuses that flag, so the table it returns is swapped here
for one made that way from the code (`_own_table`), and a kept shader's table is made that way too. This is why the
import is ours for every one of our compiles, also with the keeping turned off.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import re
import struct
import zlib
from collections.abc import Iterator
from ctypes import wintypes
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base.settings import SETTINGS_DIR

def _migrated(new: Path, old_name: str) -> Path:
    """`new` (a file of this mod in the settings folder), copied once from the Aurelia mod's file of that name when it is not there yet: what
    Aurelia recorded (the saves' layouts, the compiled shaders) is what this mod starts from."""
    if not new.exists():
        old = new.with_name(old_name)
        if old.is_file():
            try:
                import shutil  # noqa: PLC0415

                shutil.copyfile(old, new)
            except OSError:
                pass
    return new


FILE = _migrated(SETTINGS_DIR / "cryo_shaders.bin", "aurelia_shaders.bin")
OFF = SETTINGS_DIR / "cryo_shaders.off"
MAGIC = b"AURELIA-SHADERS-1\n"
DLL, FUNCTION = b"d3dx9_43.dll", b"D3DXCompileShader"
KEEP = 4000             # shaders kept at most (her materials make about 600): past it, those not asked for go
_COMMENT = re.compile(rb"/[*].*?[*]/|//[^\r\n]*", re.DOTALL)
_INCLUDE = re.compile(rb'^[ \t]*#[ \t]*include[ \t]+"([^"]+)"', re.MULTILINE)
_READWRITE = 0x04       # PAGE_READWRITE
_LARGE = 1 << 17        # D3DXCONSTTABLE_LARGEADDRESSAWARE

# HRESULT D3DXCompileShader(src, length, macros, include handler, entry point, profile, flags, code**, errors**, table**)
_COMPILE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p)
# ID3DXInclude: Open(this, type, name, parent, data**, bytes*), Close(this, data)
_OPEN = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint))
_CLOSE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
# ID3DXBuffer, after IUnknown's three: GetBufferPointer(this), GetBufferSize(this)
_RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_POINTER = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)
_SIZE = ctypes.WINFUNCTYPE(ctypes.c_uint, ctypes.c_void_p)

stats = {"asked": 0, "kept": 0, "compiled": 0}       # this session: compiles asked of us, handed back, sent on
_shaders: dict[bytes, bytes] | None = None           # key -> compiled code (None: not read yet, or the keeping is off)
_used: set[bytes] = set()
_fresh = 0                                           # kept since the file was last written
_files: dict[bytes, tuple[bytes, tuple[bytes, ...]]] = {}    # an engine shader file: its digest, the files it includes
_made: dict[bytes, tuple[bytes, tuple[bytes, ...]]] = {}     # a file the engine made, by its digest as it came: its
                                                             # digest without comments, and the files it includes
_engine_files: set[bytes] | None = None
_slot: int | None = None                             # the exe's import slot (0: not found)
_real_at = 0
_real: Any = None
_hooked: Any = None                                  # kept alive: the game calls it
_d3dx: Any = None
_said: set[str] = set()


def _once(key: str, msg: str) -> None:
    if key not in _said:
        _said.add(key)
        unrealsdk.logging.info(f"[Aurelia] shaders: {msg}")


def _u32(at: int) -> int:
    return ctypes.c_uint32.from_address(at).value


def _method(obj: int, index: int, proto: Any) -> Any:
    """The `index`-th function of a COM-style object's table."""
    return proto(_u32(_u32(obj) + 4 * index))


# ---------------------------------------------------------------- the file
def _load() -> dict[bytes, bytes]:
    out: dict[bytes, bytes] = {}
    try:
        raw = FILE.read_bytes()
        if not raw.startswith(MAGIC):
            return out
        body = zlib.decompress(raw[len(MAGIC):])
        at = 0
        while at + 24 <= len(body):
            key = body[at:at + 20]
            (size,) = struct.unpack_from("<I", body, at + 20)
            at += 24
            if at + size > len(body):
                break
            out[key] = body[at:at + size]
            at += size
    except (OSError, zlib.error, struct.error):       # none, or damaged: everything compiles once more
        return {}
    return out


def _save() -> None:
    global _fresh  # noqa: PLW0603
    if not _fresh or _shaders is None:
        return
    keep = _shaders if len(_shaders) <= KEEP else {key: code for key, code in _shaders.items() if key in _used}
    body = b"".join(key + struct.pack("<I", len(code)) + code for key, code in keep.items())
    try:
        tmp = FILE.with_suffix(".tmp")
        tmp.write_bytes(MAGIC + zlib.compress(body, 6))
        tmp.replace(FILE)
        _fresh = 0
    except OSError as ex:
        _once("save", f"the compiled shaders couldn't be written ({type(ex).__name__}: {ex}); they compile again next time")


# ---------------------------------------------------------------- a compile's whole input, as one key
def _read(include: int, name: bytes) -> bytes:
    """A file as the engine's include handler gives it: a shader file of its own, or one it made for this compile."""
    data, size = ctypes.c_void_p(), ctypes.c_uint()
    if _method(include, 0, _OPEN)(include, 0, name, None, ctypes.byref(data), ctypes.byref(size)) < 0 or not data.value:
        raise LookupError(name.decode("latin-1"))
    try:
        return ctypes.string_at(data.value, size.value)
    finally:
        _method(include, 1, _CLOSE)(include, data)


def _on_disk() -> set[bytes]:
    """The engine's shader files (by name, lower case): what one of them holds is the same all session."""
    global _engine_files  # noqa: PLW0603
    if _engine_files is None:
        folder = Path.cwd().parents[1] / "Engine" / "Shaders"       # the game runs from Binaries/Win32
        try:
            _engine_files = {p.name.lower().encode("latin-1") for p in folder.iterdir()}
        except OSError:
            _engine_files = set()
    return _engine_files


def _fold(digest: Any, include: int, names: tuple[bytes, ...] | list[bytes], seen: set[bytes]) -> None:
    for name in names:
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        if low in _on_disk():
            known = _files.get(low)
            if known is None:
                body = _read(include, name)
                known = _files[low] = (hashlib.sha1(body).digest(), tuple(_INCLUDE.findall(body)))   # noqa: S324
            digest.update(known[0])
            _fold(digest, include, known[1], seen)
        else:                                   # made for this material or vertex factory: read every time
            body = _read(include, name)
            as_it_came = hashlib.sha1(body).digest()  # noqa: S324
            known = _made.get(as_it_came)       # the same for every shader of one material: stripped once
            if known is None:
                if len(_made) > 256:
                    _made.clear()
                code = _COMMENT.sub(b"", body)
                known = _made[as_it_came] = (hashlib.sha1(code).digest(), tuple(_INCLUDE.findall(code)))  # noqa: S324
            digest.update(known[0])
            _fold(digest, include, known[1], seen)


def _key(src: int, size: int, macros: int, include: int, entry: bytes, profile: bytes, flags: int) -> bytes:
    text = ctypes.string_at(src, size)
    digest = hashlib.sha1(MAGIC)  # noqa: S324 - a content fingerprint, not security
    digest.update(struct.pack("<II", flags, size) + (entry or b"") + b"\0" + (profile or b"") + b"\0")
    at = macros
    while at and _u32(at):                      # pairs of name and definition, ended by a null name
        value = _u32(at + 4)
        digest.update(ctypes.string_at(_u32(at)) + b"=" + (ctypes.string_at(value) if value else b"") + b"\0")
        at += 8
    digest.update(text)
    names = _INCLUDE.findall(text)
    if names and not include:
        raise LookupError("includes, and no handler to read them by")
    _fold(digest, include, names, set())
    return digest.digest()


# ---------------------------------------------------------------- in the compiler's place
def _dll() -> Any:
    global _d3dx  # noqa: PLW0603
    if _d3dx is None:
        _d3dx = ctypes.WinDLL(DLL.decode())
        _d3dx.D3DXCreateBuffer.argtypes = (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        _d3dx.D3DXCreateBuffer.restype = ctypes.c_long
        _d3dx.D3DXGetShaderConstantTableEx.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        _d3dx.D3DXGetShaderConstantTableEx.restype = ctypes.c_long
    return _d3dx


def _table(code_at: int) -> int:
    """A constant table read out of compiled code whose handles hold above the 2 GB line too; 0 when d3dx refuses."""
    table = ctypes.c_void_p()
    if _dll().D3DXGetShaderConstantTableEx(code_at, _LARGE, ctypes.byref(table)) < 0:
        return 0
    return table.value or 0


def _hand_back(code: bytes, out_code: int, out_errors: int, out_table: int) -> bool:
    """What the compiler would return for this shader, made from the kept code."""
    d3dx = _dll()
    buffer = ctypes.c_void_p()
    if d3dx.D3DXCreateBuffer(len(code), ctypes.byref(buffer)) < 0 or not buffer.value:
        return False
    at = _method(buffer.value, 3, _POINTER)(buffer.value)
    ctypes.memmove(at, code, len(code))
    if out_table:
        table = _table(at)
        if not table:
            _method(buffer.value, 2, _RELEASE)(buffer.value)
            return False
        ctypes.c_void_p.from_address(out_table).value = table
    ctypes.c_void_p.from_address(out_code).value = buffer.value
    if out_errors:
        ctypes.c_void_p.from_address(out_errors).value = None
    return True


def _own_table(out_code: int, out_table: int) -> None:
    """The compiler's constant table swapped for one whose handles hold above the 2 GB line too (the module's text)."""
    buffer = ctypes.c_void_p.from_address(out_code).value
    theirs = ctypes.c_void_p.from_address(out_table).value
    if not buffer or not theirs:
        return
    ours = _table(_method(buffer, 3, _POINTER)(buffer))
    if ours:
        ctypes.c_void_p.from_address(out_table).value = ours
        _method(theirs, 2, _RELEASE)(theirs)


def _compile(src: int, size: int, macros: int, include: int, entry: bytes, profile: bytes, flags: int,
             out_code: int, out_errors: int, out_table: int) -> int:
    """`D3DXCompileShader`, for the length of one of our compiles."""
    global _fresh  # noqa: PLW0603
    key = None
    try:
        stats["asked"] += 1
        if out_code and _shaders is not None:
            key = _key(src, size, macros, include, entry, profile, flags)
            code = _shaders.get(key)
            if code is not None and _hand_back(code, out_code, out_errors, out_table):
                _used.add(key)
                stats["kept"] += 1
                return 0
    except Exception as ex:  # noqa: BLE001 - whatever it is, the compiler does this one
        key = None
        _once(f"key:{type(ex).__name__}", f"a shader isn't kept ({type(ex).__name__}: {ex}); it compiles each time")
    result = _real(src, size, macros, include, entry, profile, flags, out_code, out_errors, out_table)
    try:
        stats["compiled"] += 1
        if result >= 0 and out_code and out_table:
            _own_table(out_code, out_table)
    except Exception as ex:  # noqa: BLE001 - then the engine reads the compiler's own table, as before 0.6.7
        _once(f"table:{type(ex).__name__}", f"a constant table stays the compiler's own ({type(ex).__name__}: {ex})")
    try:
        if key is not None and result >= 0 and _shaders is not None:
            buffer = ctypes.c_void_p.from_address(out_code).value
            if buffer:
                at = _method(buffer, 3, _POINTER)(buffer)
                _shaders[key] = ctypes.string_at(at, _method(buffer, 4, _SIZE)(buffer))
                _used.add(key)
                _fresh += 1
    except Exception as ex:  # noqa: BLE001 - it is compiled; only the keeping failed
        _once(f"keep:{type(ex).__name__}", f"a compiled shader couldn't be kept ({type(ex).__name__}: {ex})")
    return result


# ---------------------------------------------------------------- the exe's import
def _find_slot() -> int:
    """Where the running exe keeps the address of d3dx9_43's D3DXCompileShader (its import table), or 0."""
    kernel = ctypes.windll.kernel32
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    base = kernel.GetModuleHandleW(None)
    header = base + _u32(base + 0x3C)
    if ctypes.string_at(header, 4) != b"PE\0\0" or ctypes.c_uint16.from_address(header + 0x18).value != 0x10B:
        return 0                                # not a 32-bit image as BL2's is
    entry = base + _u32(header + 0x18 + 0x68)   # the optional header's import directory
    for _ in range(512):
        lookup, _stamp, _chain, name, addresses = struct.unpack("<5I", ctypes.string_at(entry, 20))
        if not name:
            return 0
        if ctypes.string_at(base + name).lower() == DLL:
            for i in range(4096):
                thunk = _u32(base + (lookup or addresses) + 4 * i)
                if not thunk:
                    break
                if not thunk & 0x80000000 and ctypes.string_at(base + thunk + 2) == FUNCTION:
                    return base + addresses + 4 * i
        entry += 20
    return 0


def _point(slot: int, address: int) -> None:
    kernel = ctypes.windll.kernel32
    old = wintypes.DWORD()
    if not kernel.VirtualProtect(ctypes.c_void_p(slot), ctypes.c_size_t(4), _READWRITE, ctypes.byref(old)):
        raise OSError("the import table can't be written")
    ctypes.c_uint32.from_address(slot).value = address
    kernel.VirtualProtect(ctypes.c_void_p(slot), ctypes.c_size_t(4), old.value, ctypes.byref(old))


def _ready() -> int:
    """The import slot, with everything behind it in place; 0 when the compiler can't be stood in for in this game."""
    global _slot, _real_at, _real, _hooked, _shaders  # noqa: PLW0603
    if _slot is None:
        _slot = 0
        try:
            slot = _find_slot()
            if not slot:
                _once("slot", "the compiler's import isn't found in this exe: compiled shaders are not kept")
                return 0
            _dll()
            _real_at = _u32(slot)
            _real, _hooked = _COMPILE(_real_at), _COMPILE(_compile)
            if OFF.exists():
                _once("off", f"compiled shaders are not kept ({OFF.name} is in the settings folder)")
            else:
                _shaders = _load()
            _slot = slot
        except Exception as ex:  # noqa: BLE001 - then every start compiles, as before 0.4.19
            _once("ready", f"compiled shaders are not kept ({type(ex).__name__}: {ex})")
    return _slot


@contextlib.contextmanager
def kept() -> Iterator[None]:
    """Around one of our compiles: shaders compiled before come from the file, new ones are added to it, and every
    shader's constant table is one the engine can read wherever it lies."""
    slot = _ready()
    pointed = False
    if slot:
        try:
            _point(slot, ctypes.cast(_hooked, ctypes.c_void_p).value)
            pointed = True
        except OSError as ex:
            _once("point", f"compiled shaders are not kept ({ex})")
    try:
        yield
    finally:
        if pointed:
            try:
                _point(slot, _real_at)
            except OSError as ex:               # it stays ours: every compile then goes by the file, which is right too
                _once("back", f"the compiler's import couldn't be put back ({ex})")
            _save()
