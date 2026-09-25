# UHH file format notes

Sample files: `samples/Gas-Furnace~20240123_810D9B30000004C0.uhh` (39,018 bytes,
called "the furnace file" below) and `samples/smol.uhh` (1,301 bytes, an earlier
recording from the same instrument). The parser is `uhh_parse.py`.

Status: container, metadata, timing and per-block absolute values are decoded.
The per-sample payload is not.

## 1. What the file is

A history file from a Eurotherm paperless chart recorder (5100/6100 series).
The furnace file contains:

| Field | Value |
|---|---|
| Site | HXXXXXXX Company |
| Instrument | Gas Furnace |
| Firmware / file format | 5.2 / 2.0 |
| Locale / timezone | en-US / EST |
| Recording start | 2024-01-23 12:45:03 UTC (07:45:03 EST) |
| Duration | 910 logging intervals x 10 s, about 2 h 32 min |
| Pens (channels) | 6 |
| Engineering units | °F, input range 45 to 1550 °F (TC 6: 45 to 1450 °F) |
| Chart/group configs | `2,-1,1,3600000` and `9,-1,1,7200000` (chart spans of 1 h and 2 h, in ms) |

Pens in the furnace file: `Control TC`, `TC 2`, `TC 3`, `TC 4`, `TC 5`, `TC 6`.
Pens in `smol.uhh`: `Control TC`, `High-Limit TC`, `TC 3`, `TC 4`, `Load TC 2`.

`-9999.0` is the no-data / open thermocouple value. TC 6 reads `-9999` at the
start of the furnace file and is valid from the second block onward. TC 4 is
`-9999` in `smol.uhh`.

The filename ID `810D9B30000004C0` splits into an instrument/serial part
(`81 0D 9B` shows up verbatim at offset 0x24 and is the same in both files) and a
file sequence number: `0x04C0` in the furnace file, `0x04B6` in `smol.uhh`,
stored big-endian at offset 0x12.

## 2. Container format

### 2.1 Framing and byte stuffing

The file is a stream of records, each starting with one of two marker bytes:

* `0xFE <type>`: structural record
* `0xFF`: one logging interval (sample record)

A record's payload runs until the next marker. Since `0xFE` and `0xFF` are
reserved, they get byte-stuffed inside payloads:

```
FE FC  ->  literal 0xFE
FE FD  ->  literal 0xFF
```

The furnace file has 123 `FE FC` and 109 `FE FD` escapes, which is about what you
would expect if both bytes show up with probability 1/256 in the payload. You
have to undo the stuffing before anything else decodes correctly.

### 2.2 Record types

| Type | Name | Count (furnace / smol) | Contents |
|---|---|---|---|
| `FE 00` | file header | 1 / 1 | magic `UHH`, absolute start timestamp, file sequence |
| `FE 01` | file footer | 0 / 1 | end-of-file marker + timestamp |
| `FE 02` | format version | 1 / 1 | `"2.0"` + instrument ID blob |
| `FE 03` | instrument info | 1 / 1 | firmware, site, instrument, locale, tz, chart configs |
| `FE 04` | pen config | 6 / 5 | index, input no., range low/high, span %, name, units, format masks |
| `FE 12` | block snapshot | 8 / 1 | timestamp + absolute value of every pen as IEEE-754 doubles |
| `FE 18` | audit event | 0 / 2 | UTF-16 change log text |
| `FE 1D` | block checkpoint | 7 / 0 | timestamp only |
| `FF` | sample record | 910 / 3 | one logging interval, all pens (payload not decoded) |

Strings are NUL-terminated UTF-16LE. Floats are big-endian IEEE-754 (`>d`
doubles for values and ranges, `>f` floats for the span percent). Timestamps
are LEB128 varints (little-endian 7-bit groups, high bit = continuation).

### 2.3 Time base

One tick = 1/8 second (0.125 s).

The `FE 00` header has an absolute varint timestamp in ticks since the Unix
epoch: `0x32EBF6CCF8` = 13,648,111,224 ticks / 8 = 1,706,013,903 s =
2024-01-23 12:45:03 UTC, which matches the `20240123` in the filename.
`smol.uhh` gives 2024-01-22 13:46:08.125 UTC.

`FE 12` and `FE 1D` timestamps are relative to the header timestamp. Between
each pair of consecutive block markers, the tick delta divided by the number of
`FF` records in between comes out to exactly 80.000 in 13 of the 14 spans in the
furnace file. The first block gives 79.74 (not sure why yet).

So 80 ticks = 10 s per sample record, and the 910 sample records cover 9,100 s
(about 2.53 h). That lines up with the 1 h / 2 h chart spans in the metadata.

### 2.4 Block layout

The data area goes:

```
FE 12  <snapshot: ts + N doubles>   ~95 FF records   FE 1D <ts>   ~24 FF records   FE 12 ...
```

`FE 12` repeats every 121 sample records (1,210 s, about 20 min) once things
settle, and `FE 1D` sits 22 to 24 records before each `FE 12`. On disk each
`FE 12` to `FE 1D` span is about 4.0 kB and each `FE 1D` to `FE 12` span about
1.0 kB, so the writer is probably flushing on 4096/1024-byte boundaries.

`FE 12` payload:

```
<subtype byte> <0x07|0x27> <varint ticks> 50 00
<N x big-endian double>            # absolute value of each pen, °F
44 44 44                           # per-pen status nibbles (0x4 = good)
<2 bytes x N, normally zero>
<1 trailer byte>
```

The eight snapshots in the furnace file decode to a believable furnace run: the
load thermocouples climb steadily from 167 to 962 °F while `Control TC` cycles
between about 945 and 1190 °F, i.e. a control loop holding setpoint while the
load soaks. That is the best evidence that the double decoding and channel
order are right.

### 2.5 Sample record (`FF`) layout

```
FF
<ceil(N/2) nibble bytes>       # one 4-bit field per pen, LOW NIBBLE FIRST
<per-pen field>  x N           # length = nibble + 1 bytes; nibble 0xF => 10 bytes
<1 trailer byte>
```

This accounts for every byte of all 913 sample records in both files with
nothing left over (`uhh_parse.py` reports `0 with unexplained length`).

Nibble histogram over the furnace file: `{4: 387, 5: 4836, 6: 236, 15: 1}`, so a
pen normally takes 6 bytes, sometimes 5 or 7. Nibble `0xF` means the pen has no
valid data. It shows up exactly once in each file, in the first sample record
after the header, and in both files it lands on the pen that reads `-9999.0` in
the preceding `FE 12` snapshot (TC 6 in the furnace file, TC 4 in `smol.uhh`).
That is what pins down the low-nibble-first ordering.

Field length also tracks how busy the signal is: the smoothly rising load pens
TC 3/TC 4/TC 5 use the short 5-byte form 12-14% of the time, while the noisy
`Control TC` and `TC 6` use it about 1.5% of the time.

## 3. Open problem: the per-pen field contents

The 5-7 byte per-pen field is the last piece, and it is not cooperating:

* **Statistics.** Across the 34 kB of sample payload, entropy is 7.906 bits/byte.
  Every byte position except the first byte of each field looks like uniform
  random (216-235 distinct values out of 256 over 509 samples, uniform
  expectation is 221).
* **The first byte of each field has structure.** Values 1 to ~50 dominate, only
  ~100 of 256 values ever show up, mean ~39 with std dev ~55 (heavy tail). But its
  lag-1 autocorrelation is only 0.02-0.09, so it is not a smoothly varying
  physical value, and its per-block sum doesn't scale with the temperature change
  between snapshots (ratios ranged 0.004-0.038, no relationship).
* **It is not compression of a single number.** 6 bytes/pen/interval is 48 bits,
  which is more than a raw float32 and way more than a delta needs. Either it
  holds several numbers or it is padded/whitened.
* **A constant channel does not give constant bytes.** In the first block of the
  furnace file, TC 6 sits at `-9999` but its field is different in every record.
  With any plain delta or fixed-point encoding it should repeat.

Two theories fit:

1. **Min/avg/max triplet.** 48 bits = 3 x 16-bit scaled integers. Recorders like
   this often log average, min and max per interval when the logging interval
   (10 s) is longer than the ADC sample interval. The 5/6/7 byte lengths would be
   variable-length coding of the triplet. Explains the size and the
   activity-dependent length, but not the random-looking bytes.
2. **Entropy-coded or whitened payload.** Range-coder output or some kind of
   scrambler would explain the uniform bytes, with the first byte sitting outside
   the coded part as a scale/exponent selector.

### Things to try next

* **Record a `.uhh` with the instrument idle**: all TCs disconnected (reading
  `-9999`), or a stable oven at setpoint, for several minutes. If the sample
  records repeat byte for byte, the payload is plain and theory 1 wins. If they
  keep changing, it is whitened and theory 2 wins. This is probably the most
  useful next step.
* **Export the same file to CSV from the vendor software (Eurotherm Data
  Reviewer).** Real values at 10 s resolution would turn this from guessing into
  curve fitting. With 910 known values per pen, any plain encoding should fall
  right out.
* **Record two files a few minutes apart from an unchanged process.** Comparing
  them would show whether there's per-file keying (e.g. a whitener seeded from
  the header timestamp or file sequence number).
* Figure out whether the 1-byte record trailer is a checksum. It is not a plain
  XOR or 8-bit sum of the record (tested both), but CRC-8 or a seeded running
  checksum haven't been ruled out. Finding it would confirm the field boundaries
  independently.

A brute-force search for known on-screen temperatures (try every float/double
encoding at every offset) is a reasonable way to attack this, as long as it runs
over the unescaped sample payload. An earlier version of that approach found
nothing because it skipped the unescaping step, and an `FE FC` escape sits right
in the middle of the first double in `smol.uhh`.
