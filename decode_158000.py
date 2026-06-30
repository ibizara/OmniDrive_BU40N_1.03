#!/usr/bin/env python3
"""
decode_158000_v3.py

Experimental BU40N / MT1959 decoder for the packed block at 0x158000.

Status as of v3:

  * header[0] appears to be a nominal/decompressed upper size, not the packed size
  * header[1] appears to be the packed partition span from 0x158000
  * literal/length table: 288 one-byte canonical Huffman code lengths
  * distance table:       32 one-byte canonical Huffman code lengths
  * bitstream starts at:  offset + 0x148
  * bitstream physical bit order is MSB-first
  * Huffman lookup uses bit-reversed canonical codes
  * symbols 0..255 are literal bytes
  * symbols 256..287 are LZ copy lengths
  * length mapping is linear:
        length = symbol - 253
        therefore 256 -> 3, 257 -> 4, ..., 287 -> 34
  * distance mapping is:
        prefix = Huffman-coded distance symbol
        low7   = next 7 raw MSB-first bits
        distance = (prefix << 7) | low7

Important: the RAM dump appears to contain an extra runtime relocation/fixup pass
for Thumb-2 BL-like instructions. The raw decoded stream stores the branch
immediate as an absolute target/address-like value. The RAM image stores the
normal PC-relative branch encoding.

This script does NOT blindly patch every BL-looking word, because data tables
can contain values that look like BL instructions. Instead, when --compare is
provided, it counts and optionally applies only those branch fixups that are
confirmed by the RAM/oracle file.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path


class BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.bitpos = 0

    def _read_physical_bit(self) -> int:
        if self.bitpos >= len(self.data) * 8:
            raise EOFError("ran out of compressed input")
        byte = self.data[self.bitpos >> 3]
        bit = (byte >> (7 - (self.bitpos & 7))) & 1
        self.bitpos += 1
        return bit

    def read_huffman_bits(self, n: int) -> int:
        # Physical bits are read MSB-first, but accumulated into bit 0 upwards
        # for the reversed canonical-code lookup.
        value = 0
        for i in range(n):
            value |= self._read_physical_bit() << i
        return value

    def read_raw_msb(self, n: int) -> int:
        value = 0
        for _ in range(n):
            value = (value << 1) | self._read_physical_bit()
        return value


def reverse_bits(value: int, width: int) -> int:
    out = 0
    for _ in range(width):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def build_canonical_table(lengths: bytes) -> dict[tuple[int, int], int]:
    counts: dict[int, int] = {}

    for length in lengths:
        if length:
            counts[length] = counts.get(length, 0) + 1

    code = 0
    next_code: dict[int, int] = {}

    for bits in range(1, max(counts.keys(), default=0) + 1):
        code = (code + counts.get(bits - 1, 0)) << 1
        next_code[bits] = code

    table: dict[tuple[int, int], int] = {}

    for symbol, length in enumerate(lengths):
        if not length:
            continue

        canonical = next_code[length]
        next_code[length] += 1
        stored_code = reverse_bits(canonical, length)
        table[(stored_code, length)] = symbol

    return table


def decode_symbol(br: BitReader, table: dict[tuple[int, int], int]) -> int:
    code = 0

    for length in range(1, 32):
        code |= br.read_huffman_bits(1) << (length - 1)

        symbol = table.get((code, length))
        if symbol is not None:
            return symbol

    raise ValueError(f"bad Huffman code at bit {br.bitpos}")


def decode_partition(
    firmware: bytes,
    offset: int = 0x158000,
    output_limit: int | None = None,
) -> tuple[bytes, dict[str, int | str]]:
    nominal_output_size, packed_span = struct.unpack_from("<II", firmware, offset)

    lit_table_off = offset + 8
    dist_table_off = lit_table_off + 288
    stream_off = dist_table_off + 32
    packed_end = offset + packed_span

    if packed_end > len(firmware):
        raise ValueError(
            f"packed span extends beyond input file: end={packed_end:#x}, "
            f"file={len(firmware):#x}"
        )

    lit_lengths = firmware[lit_table_off:lit_table_off + 288]
    dist_lengths = firmware[dist_table_off:dist_table_off + 32]
    stream = firmware[stream_off:packed_end]

    lit_tree = build_canonical_table(lit_lengths)
    dist_tree = build_canonical_table(dist_lengths)

    if output_limit is None:
        output_limit = nominal_output_size

    br = BitReader(stream)
    out = bytearray()
    status = "ok"

    try:
        while len(out) < output_limit:
            symbol = decode_symbol(br, lit_tree)

            if symbol < 256:
                out.append(symbol)
                continue

            # v3 correction: linear length mapping, not DEFLATE-style bases.
            length = symbol - 253

            if length < 3:
                raise ValueError(
                    f"bad length symbol {symbol} at output={len(out):#x}, "
                    f"bit={br.bitpos}"
                )

            distance_prefix = decode_symbol(br, dist_tree)
            distance_low7 = br.read_raw_msb(7)
            distance = (distance_prefix << 7) | distance_low7

            if distance <= 0 or distance > len(out):
                raise ValueError(
                    f"invalid distance {distance:#x} at output={len(out):#x}, "
                    f"prefix={distance_prefix:#x}, low7={distance_low7:#x}, "
                    f"bit={br.bitpos}"
                )

            for _ in range(length):
                out.append(out[-distance])
                if len(out) >= output_limit:
                    break

    except EOFError:
        status = f"EOF at output={len(out):#x}, bit={br.bitpos}"

    stats: dict[str, int | str] = {
        "nominal_output_size": nominal_output_size,
        "packed_span": packed_span,
        "stream_size": len(stream),
        "bits_consumed": br.bitpos,
        "bytes_consumed_rounded": (br.bitpos + 7) // 8,
        "unused_stream_bytes": len(stream) - ((br.bitpos + 7) // 8),
        "status": status,
    }

    return bytes(out), stats


def thumb_bl_imm(h1: int, h2: int) -> int:
    # Thumb-2 BL-style immediate decode.
    s = (h1 >> 10) & 1
    imm10 = h1 & 0x3ff
    j1 = (h2 >> 13) & 1
    j2 = (h2 >> 11) & 1
    imm11 = h2 & 0x7ff

    i1 = (~(j1 ^ s)) & 1
    i2 = (~(j2 ^ s)) & 1

    imm = (
        (s << 24)
        | (i1 << 23)
        | (i2 << 22)
        | (imm10 << 12)
        | (imm11 << 1)
    )

    if s:
        imm -= 1 << 25

    return imm


def encode_thumb_bl_imm(imm: int, h1_orig: int, h2_orig: int) -> tuple[int, int]:
    if imm & 1:
        raise ValueError(f"odd Thumb BL immediate: {imm:#x}")

    val = imm & ((1 << 25) - 1)

    s = (val >> 24) & 1
    i1 = (val >> 23) & 1
    i2 = (val >> 22) & 1
    imm10 = (val >> 12) & 0x3ff
    imm11 = (val >> 1) & 0x7ff

    j1 = (~i1 ^ s) & 1
    j2 = (~i2 ^ s) & 1

    h1 = (h1_orig & 0xf800) | (s << 10) | imm10
    h2 = (h2_orig & 0xd000) | (j1 << 13) | (j2 << 11) | imm11

    return h1, h2


def branch_relocation_candidate(decoded: bytes, oracle: bytes, off: int) -> bool:
    if off + 4 > len(decoded) or off + 4 > len(oracle):
        return False

    h1, h2 = struct.unpack_from("<HH", decoded, off)

    # Thumb-2 BL/B.W-looking instruction. This can occur in data, so this
    # function is only safe because it checks the resulting bytes against oracle.
    if (h1 & 0xf800) != 0xf000 or (h2 & 0xd000) != 0xd000:
        return False

    imm = thumb_bl_imm(h1, h2)
    pc = off + 4
    new_imm = imm - pc

    if not (-(1 << 24) <= new_imm < (1 << 24)):
        return False

    new_h1, new_h2 = encode_thumb_bl_imm(new_imm, h1, h2)
    return struct.pack("<HH", new_h1, new_h2) == oracle[off:off + 4]


def compare_accounting_for_branch_fixups(
    decoded: bytes,
    oracle: bytes,
) -> tuple[bytes, dict[str, int]]:
    patched = bytearray(decoded)
    compare_len = min(len(decoded), len(oracle))
    branch_sites = 0

    for off in range(0, compare_len - 3, 2):
        if decoded[off:off + 4] == oracle[off:off + 4]:
            continue

        if branch_relocation_candidate(decoded, oracle, off):
            h1, h2 = struct.unpack_from("<HH", decoded, off)
            imm = thumb_bl_imm(h1, h2)
            new_h1, new_h2 = encode_thumb_bl_imm(imm - (off + 4), h1, h2)
            struct.pack_into("<HH", patched, off, new_h1, new_h2)
            branch_sites += 1

    mismatches = 0
    first_mismatch = -1

    for i in range(compare_len):
        if patched[i] != oracle[i]:
            mismatches += 1
            if first_mismatch < 0:
                first_mismatch = i

    raw_mismatches = sum(
        1 for i in range(compare_len) if decoded[i] != oracle[i]
    )

    stats = {
        "compare_len": compare_len,
        "raw_mismatching_bytes": raw_mismatches,
        "branch_fixup_sites": branch_sites,
        "post_fixup_mismatching_bytes": mismatches,
        "first_post_fixup_mismatch": first_mismatch,
    }

    return bytes(patched), stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Experimental BU40N/MT1959 packed block decoder"
    )
    parser.add_argument("firmware", help="input firmware .bin")
    parser.add_argument("-o", "--output", default="decoded_158000_v3_raw.bin")
    parser.add_argument("--offset", default="0x158000")
    parser.add_argument("--output-limit", default=None)
    parser.add_argument("--compare", help="optional RAM dump/oracle")
    parser.add_argument(
        "--write-oracle-patched",
        help=(
            "optional output path for a RAM-style image patched only at "
            "branch sites confirmed by --compare"
        ),
    )

    args = parser.parse_args()

    firmware = Path(args.firmware).read_bytes()
    offset = int(args.offset, 0)
    output_limit = int(args.output_limit, 0) if args.output_limit else None

    decoded, stats = decode_partition(firmware, offset, output_limit)
    Path(args.output).write_bytes(decoded)

    print(f"partition offset:          {offset:#x}")
    print(f"nominal output size:       {stats['nominal_output_size']:#x}")
    print(f"packed span:               {stats['packed_span']:#x}")
    print(f"stream size:               {stats['stream_size']:#x}")
    print(f"decoded size:              {len(decoded):#x}")
    print(f"bits consumed:             {stats['bits_consumed']}")
    print(f"bytes consumed rounded:    {stats['bytes_consumed_rounded']:#x}")
    print(f"unused stream bytes:       {stats['unused_stream_bytes']:#x}")
    print(f"status:                    {stats['status']}")
    print(f"md5(decoded raw):          {hashlib.md5(decoded).hexdigest()}")
    print(f"wrote raw:                 {args.output}")

    if args.compare:
        oracle = Path(args.compare).read_bytes()
        patched, cstats = compare_accounting_for_branch_fixups(decoded, oracle)

        print()
        print(f"compare file:              {args.compare}")
        print(f"compare size:              {len(oracle):#x}")
        print(f"compare length used:       {cstats['compare_len']:#x}")
        print(f"raw mismatching bytes:     {cstats['raw_mismatching_bytes']}")
        print(f"branch fixup sites:        {cstats['branch_fixup_sites']}")
        print(f"post-fixup mismatches:     {cstats['post_fixup_mismatching_bytes']}")

        if cstats["first_post_fixup_mismatch"] >= 0:
            off = cstats["first_post_fixup_mismatch"]
            print(f"first post-fixup mismatch: {off:#x}")
            print(f"decoded bytes:             {patched[off:off + 16].hex(' ')}")
            print(f"oracle bytes:              {oracle[off:off + 16].hex(' ')}")
        else:
            print("post-fixup result:         exact match over compare length")

        if len(decoded) > len(oracle):
            print(f"decoded extends past RAM:  {len(decoded) - len(oracle):#x}")
        elif len(oracle) > len(decoded):
            print(f"RAM extends past decoded:  {len(oracle) - len(decoded):#x}")

        if args.write_oracle_patched:
            Path(args.write_oracle_patched).write_bytes(patched)
            print(f"wrote oracle-patched:      {args.write_oracle_patched}")
            print(f"md5(oracle-patched):       {hashlib.md5(patched).hexdigest()}")


if __name__ == "__main__":
    main()
