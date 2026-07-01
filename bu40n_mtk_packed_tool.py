#!/usr/bin/env python3
"""
bu40n_mtk_packed_tool.py

All-purpose experimental tool for the BU40N / MT1959 packed firmware block
at offset 0x158000.

It can:
  * extract/decompress the packed block from a firmware image
  * write raw pre-fixup and/or RAM-style post-fixup output
  * write a detailed text report and JSON metadata
  * recompress a raw or RAM-style decompressed block
  * insert the recompressed block back into a firmware image
  * verify by immediately decoding the newly written block

Current format assumptions, based on BU40N 1.00 stock and BU40N 1.03MK:
  * header[0] = nominal decompressed/output size / workspace upper bound
  * header[1] = packed partition span from partition offset
  * offset + 0x008: 288-byte literal/length canonical-Huffman table
  * offset + 0x128:  32-byte distance canonical-Huffman table
  * offset + 0x148: compressed bitstream
  * physical bitstream order is MSB-first
  * Huffman lookup uses bit-reversed canonical codes
  * literal symbols 0..255 emit a byte
  * symbols 256..287 are LZ copy lengths, length = symbol - 253
  * distance = (Huffman-coded prefix << 7) | next 7 raw MSB-first bits
  * distance range is 1..4095
  * copy length range is 3..34
  * after decompression, the drive applies a blind THUMB long-branch fixup pass

The compressor intentionally does NOT try to reproduce the original byte stream.
It emits a valid stream using the existing code-length tables from the target
firmware, then verifies that decoding it reproduces the requested raw image.
"""

from __future__ import annotations

import argparse
import binascii
import dataclasses
import hashlib
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_OFFSET = 0x158000
LITLEN_COUNT = 288
DIST_COUNT = 32
HEADER_SIZE = 8
LIT_TABLE_REL = 0x008
DIST_TABLE_REL = 0x128
STREAM_REL = 0x148
MIN_MATCH = 3
MAX_MATCH = 34
MAX_DISTANCE = (DIST_COUNT << 7) - 1  # 4095
NOMINAL_GAP_OBSERVED = 0x1EB


@dataclasses.dataclass(frozen=True)
class Hashes:
    length: int
    crc32: str
    md5: str
    sha1: str


@dataclasses.dataclass(frozen=True)
class PartitionInfo:
    offset: int
    nominal_output_size: int
    packed_span: int
    packed_end: int
    lit_table_offset: int
    dist_table_offset: int
    stream_offset: int
    stream_size: int
    lit_kraft_sum: float
    dist_kraft_sum: float
    firmware_size: int


@dataclasses.dataclass(frozen=True)
class DecodeResult:
    raw: bytes
    ramstyle: bytes
    info: PartitionInfo
    bits_consumed: int
    bytes_consumed_rounded: int
    unused_stream_bytes: int
    status: str
    branch_fixup_sites: int


@dataclasses.dataclass(frozen=True)
class CompressResult:
    stream: bytes
    packed_block: bytes
    nominal_output_size: int
    packed_span: int
    raw_size: int
    bits_written: int
    branch_unfix_sites: int
    branch_fixup_sites: int
    matches: int
    literals: int
    compressed_stream_size: int


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
        value = 0
        for i in range(n):
            value |= self._read_physical_bit() << i
        return value

    def read_raw_msb(self, n: int) -> int:
        value = 0
        for _ in range(n):
            value = (value << 1) | self._read_physical_bit()
        return value


class BitWriter:
    def __init__(self):
        self.buf = bytearray()
        self.bitpos = 0

    def _write_physical_bit(self, bit: int) -> None:
        if self.bitpos % 8 == 0:
            self.buf.append(0)
        if bit & 1:
            self.buf[-1] |= 1 << (7 - (self.bitpos & 7))
        self.bitpos += 1

    def write_huffman_code(self, reversed_code: int, length: int) -> None:
        # Decoder accumulates physical bits into bit 0 upwards, so write the
        # reversed-code integer from LSB to MSB.
        for i in range(length):
            self._write_physical_bit((reversed_code >> i) & 1)

    def write_raw_msb(self, value: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            self._write_physical_bit((value >> i) & 1)

    def finish(self) -> bytes:
        return bytes(self.buf)


def file_hashes(data: bytes) -> Hashes:
    return Hashes(
        length=len(data),
        crc32=f"{binascii.crc32(data) & 0xffffffff:08x}",
        md5=hashlib.md5(data).hexdigest(),
        sha1=hashlib.sha1(data).hexdigest(),
    )


def reverse_bits(value: int, width: int) -> int:
    out = 0
    for _ in range(width):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def kraft_sum(lengths: bytes) -> float:
    return sum(2.0 ** -length for length in lengths if length)


def build_decode_table(lengths: bytes) -> Dict[Tuple[int, int], int]:
    encode = build_encode_table(lengths)
    return {(code, length): symbol for symbol, (code, length) in encode.items()}


def build_encode_table(lengths: bytes) -> Dict[int, Tuple[int, int]]:
    counts: Dict[int, int] = {}
    for length in lengths:
        if length:
            counts[length] = counts.get(length, 0) + 1

    code = 0
    next_code: Dict[int, int] = {}
    for bits in range(1, max(counts.keys(), default=0) + 1):
        code = (code + counts.get(bits - 1, 0)) << 1
        next_code[bits] = code

    table: Dict[int, Tuple[int, int]] = {}
    for symbol, length in enumerate(lengths):
        if not length:
            continue
        canonical = next_code[length]
        next_code[length] += 1
        table[symbol] = (reverse_bits(canonical, length), length)
    return table


def decode_symbol(br: BitReader, table: Dict[Tuple[int, int], int]) -> int:
    code = 0
    for length in range(1, 32):
        code |= br.read_huffman_bits(1) << (length - 1)
        symbol = table.get((code, length))
        if symbol is not None:
            return symbol
    raise ValueError(f"bad Huffman code at bit {br.bitpos}")


def read_partition_info(firmware: bytes, offset: int = DEFAULT_OFFSET) -> PartitionInfo:
    if offset < 0 or offset + STREAM_REL > len(firmware):
        raise ValueError(f"offset {offset:#x} is outside firmware size {len(firmware):#x}")

    nominal_output_size, packed_span = struct.unpack_from("<II", firmware, offset)
    packed_end = offset + packed_span
    lit_table_offset = offset + LIT_TABLE_REL
    dist_table_offset = offset + DIST_TABLE_REL
    stream_offset = offset + STREAM_REL

    if packed_span < STREAM_REL:
        raise ValueError(f"packed span {packed_span:#x} is smaller than header/tables {STREAM_REL:#x}")
    if packed_end > len(firmware):
        raise ValueError(
            f"packed span ends beyond firmware: end={packed_end:#x}, firmware={len(firmware):#x}"
        )

    lit_lengths = firmware[lit_table_offset:lit_table_offset + LITLEN_COUNT]
    dist_lengths = firmware[dist_table_offset:dist_table_offset + DIST_COUNT]

    return PartitionInfo(
        offset=offset,
        nominal_output_size=nominal_output_size,
        packed_span=packed_span,
        packed_end=packed_end,
        lit_table_offset=lit_table_offset,
        dist_table_offset=dist_table_offset,
        stream_offset=stream_offset,
        stream_size=packed_span - STREAM_REL,
        lit_kraft_sum=kraft_sum(lit_lengths),
        dist_kraft_sum=kraft_sum(dist_lengths),
        firmware_size=len(firmware),
    )


def _looks_like_thumb_long_branch(data: bytearray | bytes, i: int) -> bool:
    return i + 4 <= len(data) and (data[i + 1] & 0xF8) == 0xF0 and (data[i + 3] & 0xF8) == 0xF8


def _extract_drive_branch_value(data: bytearray | bytes, i: int) -> int:
    # This is the exact value construction from the ARM fixup routine, expressed
    # in byte terms.
    return (
        data[i + 2]
        | (data[i + 1] << 19)
        | (data[i] << 11)
        | ((data[i + 3] & 7) << 8)
    )


def _store_drive_branch_value(data: bytearray, i: int, value: int) -> None:
    # Re-encode using the exact byte placement used by the drive routine.
    value &= 0xFFFFFFFF
    data[i + 1] = 0xF0 | ((value >> 19) & 7)
    data[i] = (value >> 11) & 0xFF
    data[i + 3] = 0xF8 | ((value >> 8) & 7)
    data[i + 2] = value & 0xFF


def apply_drive_branch_fixups(raw: bytes) -> Tuple[bytes, int]:
    """Apply the drive's blind THUMB long-branch relocation pass."""
    data = bytearray(raw)
    i = 0
    sites = 0
    n = len(data)
    while i + 4 <= n:
        if _looks_like_thumb_long_branch(data, i):
            value = _extract_drive_branch_value(data, i)
            value = (value - (i >> 1) - 2) & 0xFFFFFFFF
            _store_drive_branch_value(data, i, value)
            sites += 1
            i += 2
        i += 2
    return bytes(data), sites


def undo_drive_branch_fixups(ramstyle: bytes) -> Tuple[bytes, int]:
    """Inverse of apply_drive_branch_fixups for preparing RAM-style code for firmware storage."""
    data = bytearray(ramstyle)
    i = 0
    sites = 0
    n = len(data)
    while i + 4 <= n:
        if _looks_like_thumb_long_branch(data, i):
            value = _extract_drive_branch_value(data, i)
            value = (value + (i >> 1) + 2) & 0xFFFFFFFF
            _store_drive_branch_value(data, i, value)
            sites += 1
            i += 2
        i += 2
    return bytes(data), sites


def decode_packed_block(
    firmware: bytes,
    offset: int = DEFAULT_OFFSET,
    output_limit: Optional[int] = None,
) -> DecodeResult:
    """Decode the packed partition and also return the drive/RAM-style fixed image."""
    info = read_partition_info(firmware, offset)
    lit_lengths = firmware[info.lit_table_offset:info.lit_table_offset + LITLEN_COUNT]
    dist_lengths = firmware[info.dist_table_offset:info.dist_table_offset + DIST_COUNT]
    stream = firmware[info.stream_offset:info.packed_end]

    lit_tree = build_decode_table(lit_lengths)
    dist_tree = build_decode_table(dist_lengths)

    if output_limit is None:
        output_limit = info.nominal_output_size

    br = BitReader(stream)
    out = bytearray()
    status = "ok"

    try:
        while len(out) < output_limit:
            symbol = decode_symbol(br, lit_tree)
            if symbol < 256:
                out.append(symbol)
                continue

            length = symbol - 253  # 256 -> 3, ..., 287 -> 34
            if length < MIN_MATCH or length > MAX_MATCH:
                raise ValueError(f"bad length symbol {symbol} at output={len(out):#x}, bit={br.bitpos}")

            distance_prefix = decode_symbol(br, dist_tree)
            distance_low7 = br.read_raw_msb(7)
            distance = (distance_prefix << 7) | distance_low7
            if distance <= 0 or distance > len(out):
                raise ValueError(
                    f"invalid distance {distance:#x} at output={len(out):#x}, "
                    f"prefix={distance_prefix:#x}, low7={distance_low7:#x}, bit={br.bitpos}"
                )

            for _ in range(length):
                out.append(out[-distance])
                if len(out) >= output_limit:
                    break
    except EOFError:
        status = f"EOF at output={len(out):#x}, bit={br.bitpos}"

    raw = bytes(out)
    ramstyle, sites = apply_drive_branch_fixups(raw)
    bytes_used = (br.bitpos + 7) // 8

    return DecodeResult(
        raw=raw,
        ramstyle=ramstyle,
        info=info,
        bits_consumed=br.bitpos,
        bytes_consumed_rounded=bytes_used,
        unused_stream_bytes=len(stream) - bytes_used,
        status=status,
        branch_fixup_sites=sites,
    )


def _write_symbol(bw: BitWriter, enc: Dict[int, Tuple[int, int]], symbol: int) -> None:
    try:
        code, length = enc[symbol]
    except KeyError:
        raise ValueError(f"symbol {symbol} has no Huffman code in target table") from None
    bw.write_huffman_code(code, length)


def _literal_cost(lit_lengths: Sequence[int], data: bytes, pos: int, length: int) -> int:
    return sum(lit_lengths[data[pos + j]] for j in range(length))


def _build_match_candidates(data: bytes, pos: int, chains: Dict[bytes, List[int]], max_chain: int) -> List[int]:
    if pos + MIN_MATCH > len(data):
        return []
    key = data[pos:pos + MIN_MATCH]
    prevs = chains.get(key)
    if not prevs:
        return []
    min_prev = max(0, pos - MAX_DISTANCE)
    candidates: List[int] = []
    # Recent positions tend to produce longer matches and lower distances.
    for p in reversed(prevs):
        if p < min_prev:
            break
        candidates.append(p)
        if len(candidates) >= max_chain:
            break
    return candidates


def _best_match_at(
    data: bytes,
    pos: int,
    chains: Dict[bytes, List[int]],
    lit_lengths: Sequence[int],
    dist_lengths: Sequence[int],
    max_chain: int,
) -> Optional[Tuple[int, int, int]]:
    """Return (length, distance, saving_bits) for the best cost-saving match at pos."""
    n = len(data)
    if pos + MIN_MATCH > n:
        return None

    best: Optional[Tuple[int, int, int, int]] = None  # saving, length, distance, bit_cost
    for prev in _build_match_candidates(data, pos, chains, max_chain):
        distance = pos - prev
        if distance <= 0 or distance > MAX_DISTANCE:
            continue
        dist_prefix = distance >> 7
        if dist_prefix >= len(dist_lengths) or dist_lengths[dist_prefix] == 0:
            continue

        max_len = min(MAX_MATCH, n - pos)
        length = 0
        # LZ77 copy can overlap, but for match discovery comparing source bytes
        # in the already-produced output works because previous bytes are data.
        while length < max_len and data[prev + length] == data[pos + length]:
            length += 1
            # If the match overlaps, data[prev + length] remains valid because
            # it is from the final target buffer.
        if length < MIN_MATCH:
            continue

        # Pick the best length for this distance by actual Huffman cost. There
        # are no length extra bits in this format.
        for l in range(MIN_MATCH, length + 1):
            length_symbol = l + 253
            lit_cost = _literal_cost(lit_lengths, data, pos, l)
            match_cost = lit_lengths[length_symbol] + dist_lengths[dist_prefix] + 7
            saving = lit_cost - match_cost
            if saving <= 0:
                continue
            if best is None or saving > best[0] or (saving == best[0] and l > best[1]):
                best = (saving, l, distance, match_cost)

    if best is None:
        return None
    saving, length, distance, _cost = best
    return length, distance, saving


def _add_position_to_chains(data: bytes, pos: int, chains: Dict[bytes, List[int]]) -> None:
    if pos + MIN_MATCH <= len(data):
        chains[data[pos:pos + MIN_MATCH]].append(pos)


def encode_lz_stream(
    raw: bytes,
    lit_lengths: bytes,
    dist_lengths: bytes,
    *,
    max_chain: int = 256,
    lazy: bool = True,
) -> Tuple[bytes, Dict[str, int]]:
    """Compress raw bytes into a valid BU40N/MT1959 bitstream using target tables."""
    lit_enc = build_encode_table(lit_lengths)
    dist_enc = build_encode_table(dist_lengths)

    required_symbols = list(range(256)) + list(range(256, 288))
    missing = [s for s in required_symbols if s not in lit_enc]
    if missing:
        raise ValueError(f"target literal/length table cannot encode required symbols: {missing[:10]}")
    missing_dist = [s for s in range(DIST_COUNT) if s not in dist_enc]
    if missing_dist:
        raise ValueError(f"target distance table cannot encode prefixes: {missing_dist[:10]}")

    lit_costs = list(lit_lengths)
    dist_costs = list(dist_lengths)
    bw = BitWriter()
    chains: Dict[bytes, List[int]] = defaultdict(list)
    pos = 0
    literals = 0
    matches = 0
    n = len(raw)

    while pos < n:
        best = _best_match_at(raw, pos, chains, lit_costs, dist_costs, max_chain)

        # One-symbol lazy parsing: use a literal now if the next position has a
        # materially better match. This is simple but usually helps size.
        if lazy and best is not None and pos + 1 < n:
            _add_position_to_chains(raw, pos, chains)
            next_best = _best_match_at(raw, pos + 1, chains, lit_costs, dist_costs, max_chain)
            # Undo the temporary chain addition by popping it back off.
            if pos + MIN_MATCH <= n:
                chains[raw[pos:pos + MIN_MATCH]].pop()
            if next_best is not None and next_best[2] > best[2] + lit_costs[raw[pos]]:
                best = None

        if best is None:
            _write_symbol(bw, lit_enc, raw[pos])
            _add_position_to_chains(raw, pos, chains)
            pos += 1
            literals += 1
            continue

        length, distance, _saving = best
        length_symbol = length + 253
        dist_prefix = distance >> 7
        dist_low7 = distance & 0x7F

        _write_symbol(bw, lit_enc, length_symbol)
        _write_symbol(bw, dist_enc, dist_prefix)
        bw.write_raw_msb(dist_low7, 7)

        for p in range(pos, pos + length):
            _add_position_to_chains(raw, p, chains)
        pos += length
        matches += 1

    stream = bw.finish()
    stats = {
        "bits_written": bw.bitpos,
        "bytes_written": len(stream),
        "literals": literals,
        "matches": matches,
    }
    return stream, stats


def make_packed_block(
    firmware: bytes,
    decompressed: bytes,
    offset: int = DEFAULT_OFFSET,
    *,
    input_kind: str = "raw",
    nominal_output_size: Optional[int] = None,
    max_chain: int = 256,
    lazy: bool = True,
) -> CompressResult:
    """
    Create a replacement packed block using tables from firmware.

    input_kind:
      * raw      = decompressed bytes are already pre-fixup firmware form
      * ramstyle = decompressed bytes are runtime/RAM-style and need unfixing
    """
    info = read_partition_info(firmware, offset)
    lit_lengths = firmware[info.lit_table_offset:info.lit_table_offset + LITLEN_COUNT]
    dist_lengths = firmware[info.dist_table_offset:info.dist_table_offset + DIST_COUNT]

    if input_kind == "raw":
        raw = decompressed
        unfix_sites = 0
    elif input_kind == "ramstyle":
        raw, unfix_sites = undo_drive_branch_fixups(decompressed)
    else:
        raise ValueError("input_kind must be 'raw' or 'ramstyle'")

    if nominal_output_size is None:
        # Keep the target firmware's original nominal/workspace bound if it is
        # still large enough. If the edited raw image grows, use the observed
        # firmware convention of actual_size + 0x1eb.
        nominal_output_size = max(info.nominal_output_size, len(raw) + NOMINAL_GAP_OBSERVED)

    stream, estats = encode_lz_stream(
        raw,
        lit_lengths,
        dist_lengths,
        max_chain=max_chain,
        lazy=lazy,
    )
    packed_span = STREAM_REL + len(stream)
    packed_block = (
        struct.pack("<II", nominal_output_size, packed_span)
        + lit_lengths
        + dist_lengths
        + stream
    )

    ramstyle, fix_sites = apply_drive_branch_fixups(raw)

    return CompressResult(
        stream=stream,
        packed_block=packed_block,
        nominal_output_size=nominal_output_size,
        packed_span=packed_span,
        raw_size=len(raw),
        bits_written=estats["bits_written"],
        branch_unfix_sites=unfix_sites,
        branch_fixup_sites=fix_sites,
        matches=estats["matches"],
        literals=estats["literals"],
        compressed_stream_size=len(stream),
    )


def patch_firmware_image(
    firmware: bytes,
    packed_block: bytes,
    offset: int = DEFAULT_OFFSET,
    *,
    allow_grow: bool = False,
) -> bytes:
    old_info = read_partition_info(firmware, offset)
    old_span = old_info.packed_span
    new_span = len(packed_block)
    if new_span > old_span and not allow_grow:
        raise ValueError(
            f"new packed block is larger than old block: new={new_span:#x}, old={old_span:#x}; "
            "use --allow-grow only if you know the surrounding firmware layout/checksums permit it"
        )

    out = bytearray(firmware)
    if new_span <= old_span:
        # Replace the live block and leave trailing bytes untouched; the updated
        # packed-span header makes the decoder ignore that tail.
        out[offset:offset + new_span] = packed_block
    else:
        out[offset:offset + old_span] = packed_block
    return bytes(out)


def _as_dict(obj) -> dict:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    raise TypeError(type(obj).__name__)


def build_report(
    *,
    command: str,
    firmware_path: Path,
    firmware: bytes,
    info: PartitionInfo,
    decoded: Optional[DecodeResult] = None,
    compressed: Optional[CompressResult] = None,
    input_path: Optional[Path] = None,
    input_data: Optional[bytes] = None,
    output_firmware: Optional[bytes] = None,
    notes: Optional[List[str]] = None,
) -> Tuple[str, dict]:
    report: dict = {
        "command": command,
        "firmware_path": str(firmware_path),
        "firmware_hashes": _as_dict(file_hashes(firmware)),
        "partition": _as_dict(info),
        "notes": notes or [],
    }

    if decoded is not None:
        report["decode"] = {
            "status": decoded.status,
            "bits_consumed": decoded.bits_consumed,
            "bytes_consumed_rounded": decoded.bytes_consumed_rounded,
            "unused_stream_bytes": decoded.unused_stream_bytes,
            "nominal_minus_raw_size": info.nominal_output_size - len(decoded.raw),
            "branch_fixup_sites": decoded.branch_fixup_sites,
            "raw_hashes": _as_dict(file_hashes(decoded.raw)),
            "ramstyle_hashes": _as_dict(file_hashes(decoded.ramstyle)),
        }

    if input_path is not None and input_data is not None:
        report["input_decompressed_path"] = str(input_path)
        report["input_decompressed_hashes"] = _as_dict(file_hashes(input_data))

    if compressed is not None:
        report["compress"] = {
            "raw_size": compressed.raw_size,
            "nominal_output_size": compressed.nominal_output_size,
            "packed_span": compressed.packed_span,
            "stream_size": compressed.compressed_stream_size,
            "bits_written": compressed.bits_written,
            "literals": compressed.literals,
            "matches": compressed.matches,
            "branch_unfix_sites": compressed.branch_unfix_sites,
            "branch_fixup_sites": compressed.branch_fixup_sites,
            "fits_original_span": compressed.packed_span <= info.packed_span,
            "old_packed_span": info.packed_span,
            "span_delta_new_minus_old": compressed.packed_span - info.packed_span,
            "stream_hashes": _as_dict(file_hashes(compressed.stream)),
            "packed_block_hashes": _as_dict(file_hashes(compressed.packed_block)),
        }

    if output_firmware is not None:
        report["output_firmware_hashes"] = _as_dict(file_hashes(output_firmware))

    text_lines: List[str] = []
    text_lines.append("BU40N / MT1959 packed block report")
    text_lines.append("=" * 40)
    text_lines.append(f"command:                  {command}")
    text_lines.append(f"firmware:                 {firmware_path}")
    fh = file_hashes(firmware)
    text_lines.append(f"firmware length:          {fh.length:#x}")
    text_lines.append(f"firmware crc32:           {fh.crc32}")
    text_lines.append(f"firmware md5:             {fh.md5}")
    text_lines.append(f"firmware sha1:            {fh.sha1}")
    text_lines.append("")
    text_lines.append("Partition")
    text_lines.append("---------")
    text_lines.append(f"offset:                   {info.offset:#x}")
    text_lines.append(f"nominal output size:      {info.nominal_output_size:#x}")
    text_lines.append(f"packed span:              {info.packed_span:#x}")
    text_lines.append(f"packed end:               {info.packed_end:#x}")
    text_lines.append(f"literal table offset:     {info.lit_table_offset:#x}")
    text_lines.append(f"distance table offset:    {info.dist_table_offset:#x}")
    text_lines.append(f"stream offset:            {info.stream_offset:#x}")
    text_lines.append(f"stream size:              {info.stream_size:#x}")
    text_lines.append(f"literal Kraft sum:        {info.lit_kraft_sum:.12g}")
    text_lines.append(f"distance Kraft sum:       {info.dist_kraft_sum:.12g}")

    if decoded is not None:
        rh = file_hashes(decoded.raw)
        mh = file_hashes(decoded.ramstyle)
        text_lines.append("")
        text_lines.append("Decode")
        text_lines.append("------")
        text_lines.append(f"status:                   {decoded.status}")
        text_lines.append(f"raw size:                 {rh.length:#x}")
        text_lines.append(f"ramstyle size:            {mh.length:#x}")
        text_lines.append(f"nominal - raw size:       {info.nominal_output_size - rh.length:#x}")
        text_lines.append(f"bits consumed:            {decoded.bits_consumed}")
        text_lines.append(f"bytes consumed rounded:   {decoded.bytes_consumed_rounded:#x}")
        text_lines.append(f"unused stream bytes:      {decoded.unused_stream_bytes:#x}")
        text_lines.append(f"branch fixup sites:       {decoded.branch_fixup_sites}")
        text_lines.append(f"raw crc32:                {rh.crc32}")
        text_lines.append(f"raw md5:                  {rh.md5}")
        text_lines.append(f"raw sha1:                 {rh.sha1}")
        text_lines.append(f"ramstyle crc32:           {mh.crc32}")
        text_lines.append(f"ramstyle md5:             {mh.md5}")
        text_lines.append(f"ramstyle sha1:            {mh.sha1}")

    if input_path is not None and input_data is not None:
        ih = file_hashes(input_data)
        text_lines.append("")
        text_lines.append("Input decompressed image")
        text_lines.append("------------------------")
        text_lines.append(f"path:                     {input_path}")
        text_lines.append(f"length:                   {ih.length:#x}")
        text_lines.append(f"crc32:                    {ih.crc32}")
        text_lines.append(f"md5:                      {ih.md5}")
        text_lines.append(f"sha1:                     {ih.sha1}")

    if compressed is not None:
        sh = file_hashes(compressed.stream)
        bh = file_hashes(compressed.packed_block)
        text_lines.append("")
        text_lines.append("Compress")
        text_lines.append("--------")
        text_lines.append(f"raw size:                 {compressed.raw_size:#x}")
        text_lines.append(f"nominal output size:      {compressed.nominal_output_size:#x}")
        text_lines.append(f"packed span:              {compressed.packed_span:#x}")
        text_lines.append(f"stream size:              {compressed.compressed_stream_size:#x}")
        text_lines.append(f"bits written:             {compressed.bits_written}")
        text_lines.append(f"literals:                 {compressed.literals}")
        text_lines.append(f"matches:                  {compressed.matches}")
        text_lines.append(f"branch unfix sites:       {compressed.branch_unfix_sites}")
        text_lines.append(f"branch fixup sites:       {compressed.branch_fixup_sites}")
        text_lines.append(f"old packed span:          {info.packed_span:#x}")
        text_lines.append(f"fits original span:       {compressed.packed_span <= info.packed_span}")
        text_lines.append(f"span delta new-old:       {compressed.packed_span - info.packed_span:+#x}")
        text_lines.append(f"stream crc32:             {sh.crc32}")
        text_lines.append(f"stream md5:               {sh.md5}")
        text_lines.append(f"stream sha1:              {sh.sha1}")
        text_lines.append(f"packed block crc32:       {bh.crc32}")
        text_lines.append(f"packed block md5:         {bh.md5}")
        text_lines.append(f"packed block sha1:        {bh.sha1}")

    if output_firmware is not None:
        oh = file_hashes(output_firmware)
        text_lines.append("")
        text_lines.append("Output firmware")
        text_lines.append("---------------")
        text_lines.append(f"length:                   {oh.length:#x}")
        text_lines.append(f"crc32:                    {oh.crc32}")
        text_lines.append(f"md5:                      {oh.md5}")
        text_lines.append(f"sha1:                     {oh.sha1}")

    if notes:
        text_lines.append("")
        text_lines.append("Notes")
        text_lines.append("-----")
        for note in notes:
            text_lines.append(f"- {note}")

    return "\n".join(text_lines) + "\n", report


def write_reports(base: Optional[Path], text: str, data: dict) -> None:
    if base is None:
        return
    base.parent.mkdir(parents=True, exist_ok=True)
    base.with_suffix(base.suffix + ".txt" if base.suffix else ".txt").write_text(text, encoding="utf-8")
    base.with_suffix(base.suffix + ".json" if base.suffix else ".json").write_text(
        json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
    )


def cmd_extract(args: argparse.Namespace) -> int:
    firmware_path = Path(args.firmware)
    firmware = firmware_path.read_bytes()
    decoded = decode_packed_block(firmware, int(args.offset, 0), args.output_limit)

    if args.raw_out:
        Path(args.raw_out).write_bytes(decoded.raw)
    if args.ramstyle_out:
        Path(args.ramstyle_out).write_bytes(decoded.ramstyle)

    notes = []
    if decoded.unused_stream_bytes != 0:
        notes.append("compressed stream was not consumed exactly; investigate before patching")
    if decoded.info.nominal_output_size - len(decoded.raw) != NOMINAL_GAP_OBSERVED:
        notes.append("nominal-output-size gap differs from the 0x1eb pattern observed on BU40N 1.00/1.03MK")

    text, report = build_report(
        command="extract",
        firmware_path=firmware_path,
        firmware=firmware,
        info=decoded.info,
        decoded=decoded,
        notes=notes,
    )
    if args.report:
        write_reports(Path(args.report), text, report)
    print(text, end="")
    return 0


def cmd_repack(args: argparse.Namespace) -> int:
    firmware_path = Path(args.firmware)
    input_path = Path(args.input)
    firmware = firmware_path.read_bytes()
    input_data = input_path.read_bytes()
    offset = int(args.offset, 0)
    info = read_partition_info(firmware, offset)

    nominal = int(args.nominal_output_size, 0) if args.nominal_output_size else None
    compressed = make_packed_block(
        firmware,
        input_data,
        offset,
        input_kind=args.input_kind,
        nominal_output_size=nominal,
        max_chain=args.max_chain,
        lazy=not args.no_lazy,
    )

    notes = []
    if compressed.packed_span > info.packed_span:
        notes.append("new packed block is larger than the original span; not safe for normal in-place patching")

    output_firmware = None
    if args.firmware_out:
        output_firmware = patch_firmware_image(
            firmware,
            compressed.packed_block,
            offset,
            allow_grow=args.allow_grow,
        )
        Path(args.firmware_out).write_bytes(output_firmware)

        # Verify by decoding the output firmware image and comparing raw bytes.
        verify = decode_packed_block(output_firmware, offset)
        expected_raw = input_data if args.input_kind == "raw" else undo_drive_branch_fixups(input_data)[0]
        if verify.raw != expected_raw:
            notes.append("VERIFY FAILED: output firmware decodes to different raw bytes")
        else:
            notes.append("verify: output firmware decodes back to the requested raw bytes")

    if args.packed_block_out:
        Path(args.packed_block_out).write_bytes(compressed.packed_block)
    if args.stream_out:
        Path(args.stream_out).write_bytes(compressed.stream)

    text, report = build_report(
        command="repack",
        firmware_path=firmware_path,
        firmware=firmware,
        info=info,
        compressed=compressed,
        input_path=input_path,
        input_data=input_data,
        output_firmware=output_firmware,
        notes=notes,
    )
    if args.report:
        write_reports(Path(args.report), text, report)
    print(text, end="")
    return 0


def cmd_roundtrip(args: argparse.Namespace) -> int:
    firmware_path = Path(args.firmware)
    firmware = firmware_path.read_bytes()
    offset = int(args.offset, 0)
    decoded = decode_packed_block(firmware, offset)
    compressed = make_packed_block(
        firmware,
        decoded.raw,
        offset,
        input_kind="raw",
        nominal_output_size=decoded.info.nominal_output_size,
        max_chain=args.max_chain,
        lazy=not args.no_lazy,
    )
    patched = patch_firmware_image(firmware, compressed.packed_block, offset, allow_grow=args.allow_grow)
    verify = decode_packed_block(patched, offset)

    notes = []
    notes.append("roundtrip: recompressed stream is not expected to match original bytes")
    if verify.raw == decoded.raw:
        notes.append("verify: recompressed firmware decodes to original raw bytes")
    else:
        notes.append("VERIFY FAILED: recompressed firmware does not decode to original raw bytes")
    if verify.ramstyle == decoded.ramstyle:
        notes.append("verify: recompressed firmware produces original RAM-style bytes after fixup")
    else:
        notes.append("VERIFY FAILED: recompressed RAM-style bytes differ")

    if args.firmware_out:
        Path(args.firmware_out).write_bytes(patched)

    text, report = build_report(
        command="roundtrip",
        firmware_path=firmware_path,
        firmware=firmware,
        info=decoded.info,
        decoded=decoded,
        compressed=compressed,
        output_firmware=patched if args.firmware_out else None,
        notes=notes,
    )
    if args.report:
        write_reports(Path(args.report), text, report)
    print(text, end="")
    return 0 if verify.raw == decoded.raw else 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BU40N/MT1959 0x158000 packed block extractor/repacker",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="decompress the packed block and write reports")
    p.add_argument("firmware")
    p.add_argument("--offset", default=hex(DEFAULT_OFFSET))
    p.add_argument("--output-limit", type=lambda s: int(s, 0), default=None)
    p.add_argument("--raw-out", help="write pre-branch-fixup raw decoded bytes")
    p.add_argument("--ramstyle-out", help="write post-branch-fixup RAM-style bytes")
    p.add_argument("--report", help="report basename; writes .txt and .json")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("repack", help="compress a decompressed image and optionally patch it into firmware")
    p.add_argument("firmware", help="target firmware providing tables and insertion point")
    p.add_argument("input", help="decompressed raw or RAM-style image to compress")
    p.add_argument("--offset", default=hex(DEFAULT_OFFSET))
    p.add_argument("--input-kind", choices=["raw", "ramstyle"], default="raw")
    p.add_argument("--nominal-output-size", help="override header[0], e.g. 0x72464")
    p.add_argument("--firmware-out", help="write patched firmware image")
    p.add_argument("--packed-block-out", help="write replacement packed block only")
    p.add_argument("--stream-out", help="write compressed stream only")
    p.add_argument("--allow-grow", action="store_true", help="allow replacement block to exceed original packed span")
    p.add_argument("--max-chain", type=int, default=256, help="LZ search depth; higher is slower but may compress better")
    p.add_argument("--no-lazy", action="store_true", help="disable one-symbol lazy matching")
    p.add_argument("--report", help="report basename; writes .txt and .json")
    p.set_defaults(func=cmd_repack)

    p = sub.add_parser("roundtrip", help="extract, recompress, patch in memory, and verify exact decode")
    p.add_argument("firmware")
    p.add_argument("--offset", default=hex(DEFAULT_OFFSET))
    p.add_argument("--firmware-out", help="optionally write the round-tripped firmware image")
    p.add_argument("--allow-grow", action="store_true")
    p.add_argument("--max-chain", type=int, default=256)
    p.add_argument("--no-lazy", action="store_true")
    p.add_argument("--report", help="report basename; writes .txt and .json")
    p.set_defaults(func=cmd_roundtrip)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
