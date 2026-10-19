"""Recursive UE3 (ver 832) tagged-property decoder for BL2/TPS exports (heuristic, read-only).

`props(ctx, start, end)` gives an export's properties as lines of text; `ctx_for(pkg)` is the context of a package
loaded with upk.py. The build reads a texture's tags with it (texdump.py); tools/dump.py prints them.
"""
import struct

class Ctx:
    def __init__(self, buf, names, imports, exports, obj_path):
        self.buf, self.names, self.imports, self.exports, self.obj_path = buf, names, imports, exports, obj_path
    def fname(self, off):
        idx, num = struct.unpack_from('<ii', self.buf, off)
        if not (0 <= idx < len(self.names)) or num < 0 or num > 100000:
            raise ValueError('bad name')
        n = self.names[idx]
        return (n if num == 0 else f'{n}_{num-1}'), off + 8
    def ref(self, idx):
        if idx == 0: return 'None'
        try: return self.obj_path(idx, self.imports, self.exports)
        except Exception: return f'<ref {idx}>'

def props(ctx, off, end, depth=0, out=None):
    out = [] if out is None else out
    pad = '  ' * depth
    while off < end:
        name, off = ctx.fname(off)
        if name == 'None':
            return off, out
        typ, off = ctx.fname(off)
        size, aidx = struct.unpack_from('<ii', ctx.buf, off); off += 8
        extra = ''
        if typ == 'StructProperty':
            extra, off = ctx.fname(off)
        elif typ == 'BoolProperty':
            out.append(f'{pad}{name} = {bool(ctx.buf[off])}'); off += 1; continue
        elif typ == 'ByteProperty':
            extra, off = ctx.fname(off)
        vstart, vend = off, off + size
        label = f'{pad}{name}' + (f'[{aidx}]' if aidx else '')
        if typ in ('ObjectProperty', 'ClassProperty', 'ComponentProperty', 'InterfaceProperty'):
            out.append(f'{label} = {ctx.ref(struct.unpack_from("<i", ctx.buf, vstart)[0])}')
        elif typ == 'FloatProperty':
            out.append(f'{label} = {round(struct.unpack_from("<f", ctx.buf, vstart)[0], 4)}')
        elif typ == 'IntProperty':
            out.append(f'{label} = {struct.unpack_from("<i", ctx.buf, vstart)[0]}')
        elif typ == 'NameProperty':
            out.append(f'{label} = {ctx.fname(vstart)[0]}')
        elif typ == 'ByteProperty':
            out.append(f'{label} = {ctx.fname(vstart)[0] if size == 8 else ctx.buf[vstart]}')
        elif typ == 'StrProperty':
            n = struct.unpack_from('<i', ctx.buf, vstart)[0]
            s = ctx.buf[vstart+4:vstart+4+n-1].decode('latin-1') if n > 0 else ctx.buf[vstart+4:vstart+4-2*n-2].decode('utf-16-le')
            out.append(f'{label} = "{s}"')
        elif typ == 'StructProperty':
            out.append(f'{label} <{extra}>:')
            try:
                props(ctx, vstart, vend, depth + 1, out)
            except Exception:
                out.append(f'{pad}  <raw {size} bytes: {ctx.buf[vstart:vstart+min(size,16)].hex()}>')
        elif typ == 'ArrayProperty':
            n = struct.unpack_from('<i', ctx.buf, vstart)[0]
            body = size - 4
            out.append(f'{label} (array n={n}):')
            if n and body == 4 * n:
                refs = [ctx.ref(struct.unpack_from('<i', ctx.buf, vstart + 4 + 4*k)[0]) for k in range(n)]
                out.append(f'{pad}  {refs}')
            else:
                p = vstart + 4
                try:
                    for k in range(n):
                        out.append(f'{pad}  [{k}]')
                        p, _ = props(ctx, p, vend, depth + 2, out)
                except Exception:
                    out.append(f'{pad}  <raw {body} bytes>')
        else:
            out.append(f'{label} <{typ} {size}b>')
        off = vend
    return off, out


def ctx_for(pkg):
    return Ctx(pkg.buf, [n.text for n in pkg.names], None, None, lambda idx, _i, _e: pkg.path_of(idx))
