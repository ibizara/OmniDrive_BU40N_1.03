#!/usr/bin/env python3
import argparse
import struct
from pathlib import Path

LBASE = [
    3, 4, 5, 6, 7, 8, 9, 10,
    11, 13, 15, 17, 19, 23, 27, 31,
    35, 43, 51, 59, 67, 83, 99, 115,
    131, 163, 195, 227, 258, 258, 258,
]

LEXT = [
    0, 0, 0, 0, 0, 0, 0, 0,
    1, 1, 1, 1, 2, 2, 2, 2,
    3, 3, 3, 3, 4, 4, 4, 4,
    5, 5, 5, 5, 0, 0, 0,
]


class BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.bitpos = 0

    def read(self, n: int) -> int:
        value = 0

        for i in range(n):
            if self.bitpos >= len(self.data) * 8:
                raise EOFError("ran out of compressed input")

            byte = self.data[self.bitpos >> 3]
            bit = (byte >> (7 - (self.bitpos & 7))) & 1
            value |= bit << i
            self.bitpos += 1

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

        # Required for this stream.
        stored_code = reverse_bits(canonical, length)
        table[(stored_code, length)] = symbol

    return table


def decode_symbol(br: BitReader, table: dict[tuple[int, int], int]) -> int:
    code = 0

    for length in range(1, 32):
        code |= br.read(1) << (length - 1)

        symbol = table.get((code, length))
        if symbol is not None:
            return symbol

    raise ValueError(f"bad Huffman code at bit {br.bitpos}")


def decompress_partition(firmware: bytes, offset: int = 0x158000) -> tuple[bytes, int, int, int]:
    compressed_size, output_size = struct.unpack_from("<II", firmware, offset)

    lit_table_off = offset + 8
    dist_table_off = lit_table_off + 288
    stream_off = dist_table_off + 32

    lit_lengths = firmware[lit_table_off:lit_table_off + 288]
    dist_lengths = firmware[dist_table_off:dist_table_off + 32]
    stream = firmware[stream_off:stream_off + compressed_size]

    lit_tree = build_canonical_table(lit_lengths)
    dist_tree = build_canonical_table(dist_lengths)

    br = BitReader(stream)
    out = bytearray()

    while len(out) < output_size:
        symbol = decode_symbol(br, lit_tree)

        if symbol < 256:
            out.append(symbol)
            continue

        # In this format, symbol 256 behaves as literal zero.
        if symbol == 256:
            out.append(0)
            continue

        length_index = symbol - 257

        if length_index < 0 or length_index >= len(LBASE):
            raise ValueError(
                f"bad length symbol {symbol} at output={len(out):#x}, bit={br.bitpos}"
            )

        length = LBASE[length_index]
        extra_bits = LEXT[length_index]

        if extra_bits:
            length += br.read(extra_bits)

        distance_symbol = decode_symbol(br, dist_tree)

        # Unlike DEFLATE, this currently appears to use raw distance symbols.
        distance = distance_symbol + 1

        if distance <= 0 or distance > len(out):
            raise ValueError(
                f"invalid distance {distance} at output={len(out):#x}, bit={br.bitpos}"
            )

        for _ in range(length):
            out.append(out[-distance])

            if len(out) >= output_size:
                break

    return bytes(out), compressed_size, output_size, br.bitpos


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Experimental BU40N 1.00 0x158000 partition decoder"
    )
    parser.add_argument("firmware", help="input BU40N firmware .bin")
    parser.add_argument("-o", "--output", default="decoded_158000.bin")
    parser.add_argument("--offset", default="0x158000")

    args = parser.parse_args()

    firmware = Path(args.firmware).read_bytes()
    offset = int(args.offset, 0)

    decoded, compressed_size, output_size, bits_used = decompress_partition(
        firmware, offset
    )

    Path(args.output).write_bytes(decoded)

    print(f"partition offset:   {offset:#x}")
    print(f"compressed size:    {compressed_size:#x}")
    print(f"decompressed size:  {len(decoded):#x}/{output_size:#x}")
    print(f"bits consumed:      {bits_used}")
    print(f"wrote:              {args.output}")


if __name__ == "__main__":
    main()
