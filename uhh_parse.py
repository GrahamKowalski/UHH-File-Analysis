#!/usr/bin/env python3
"""
Parser for .uhh history files from Eurotherm paperless chart recorders.

Format notes are in FINDINGS.md.

Decoded: record framing, byte stuffing, header/site/instrument info, pen
config, block snapshots (every pen's absolute value), and the sample record
layout and timing.

Not decoded: the 5-7 byte per-pen payload inside each sample record.

Usage:
    python uhh_parse.py FILE.uhh [--json out.json] [--csv out.csv] [--dump]
"""

import argparse
import csv
import datetime as dt
import json
import re
import struct
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Primitives

ESC = 0xFE          # record-marker prefix, also the escape prefix
SAMPLE = 0xFF       # sample-record marker
ESC_FE, ESC_FF = 0xFC, 0xFD     # FE FC -> 0xFE, FE FD -> 0xFF

TICK = 0.125        # one timestamp tick = 1/8 second

RECORD_NAMES = {
    0x00: "file_header",
    0x01: "file_footer",
    0x02: "format_version",
    0x03: "instrument_info",
    0x04: "pen_config",
    0x12: "block_snapshot",
    0x18: "audit_event",
    0x1D: "block_checkpoint",
}


def unescape(buf):
    """Undo the FE FC / FE FD byte stuffing."""
    out = bytearray()
    i = 0
    n = len(buf)
    while i < n:
        b = buf[i]
        if b == ESC and i + 1 < n and buf[i + 1] in (ESC_FE, ESC_FF):
            out.append(ESC if buf[i + 1] == ESC_FE else SAMPLE)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def varint(buf, p):
    """LEB128, little-endian groups, MSB = continuation."""
    val = shift = 0
    while True:
        b = buf[p]
        p += 1
        val |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return val, p


_STR_RE = re.compile(rb"(?:[\x20-\x7e\xb0]\x00){1,}")


def utf16z(buf, p):
    """NUL-terminated UTF-16LE string starting at p (low byte first)."""
    end = p
    while end + 1 < len(buf) and buf[end] != 0:
        end += 2
    return buf[p:end].decode("utf-16le", "replace"), end + 2


def strings16(buf, start=0):
    """All printable UTF-16LE runs in buf as (offset, text)."""
    return [(m.start(), m.group().decode("utf-16le"))
            for m in _STR_RE.finditer(buf, start)]


def ts_to_dt(ticks):
    return dt.datetime.fromtimestamp(ticks * TICK, dt.timezone.utc)


# Tokenizer

def tokenize(data):
    """Split the file into (offset, kind, type, raw_payload) records.

    kind is 'rec' for FE-framed records and 'sample' for FF-framed ones.
    raw_payload still contains the byte stuffing; callers apply unescape().
    """
    recs = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b not in (ESC, SAMPLE):
            # stray bytes before the first marker (should not happen)
            i += 1
            continue
        if b == ESC:
            kind, rtype, j = "rec", data[i + 1], i + 2
        else:
            kind, rtype, j = "sample", None, i + 1
        start_payload = j
        while j < n:
            if data[j] == ESC and j + 1 < n and data[j + 1] in (ESC_FE, ESC_FF):
                j += 2
            elif data[j] in (ESC, SAMPLE):
                break
            else:
                j += 1
        recs.append((i, kind, rtype, data[start_payload:j]))
        i = j
    return recs


# Record decoders

def decode_file_header(p):
    """FE 00: magic, start timestamp, file sequence number."""
    assert p[:3] == b"UHH", "bad magic"
    ts, q = varint(p, 5)
    return {
        "magic": "UHH",
        "byte3": p[3], "byte4": p[4],
        "start_ticks": ts,
        "start_utc": ts_to_dt(ts).isoformat(),
        "trailer": p[q:].hex(" "),
        # matches the last 4 hex digits of the filename ID (e.g. ...04C0)
        "file_seq": int.from_bytes(p[q + 6:q + 8], "big") if len(p) >= q + 8 else None,
    }


def decode_format_version(p):
    ver, q = utf16z(p, 1)
    return {"format_version": ver, "instrument_id_blob": p[q:].hex(" ")}


def decode_instrument(p):
    """FE 03: firmware, site, instrument name, locale, chart configuration."""
    out = {}
    ss = [s for _, s in strings16(p)]
    out["firmware"] = ss[0] if ss else None
    out["site"] = ss[1] if len(ss) > 1 else None
    out["instrument"] = ss[2] if len(ss) > 2 else None
    rest = ss[3:]
    two = [s for s in rest if len(s) in (2, 3) and s.isalpha() and s != "PP"]
    out["language"] = two[0] if two else None
    out["country"] = two[1] if len(two) > 1 else None
    out["timezone"] = two[2] if len(two) > 2 else None
    # "<pen count>,-1,1,<chart span in ms>", one per configured chart/group
    out["chart_configs"] = [s for s in rest
                            if s and all(c.isdigit() or c in ",-" for c in s)]
    return out


def decode_pen(p):
    """FE 04: one logging pen / input channel.

    Two identical (low, high, ?, span%) blocks precede the text tail; the second
    is presumably the chart scale vs. the input range.
    """
    d = lambda o: struct.unpack(">d", p[o:o + 8])[0]
    f = lambda o: struct.unpack(">f", p[o:o + 4])[0]
    pen = {
        "index": p[1],
        "input": p[3],          # input/channel number; 0x19 seen for a derived pen
        "range_low": d(4),
        "range_high": d(12),
        "span_pct": f(25),
        "range_low_2": d(32),
        "range_high_2": d(40),
        "span_pct_2": f(53),
    }
    ss = [s for _, s in strings16(p, 60)]
    pen["name"] = ss[0] if ss else None
    pen["units"] = ss[1] if len(ss) > 1 else None
    pen["format_masks"] = ss[2:]
    return pen


def decode_snapshot(p, npens):
    """FE 12: absolute value of every pen at a block boundary.

    Timestamps are relative to the file header; parse() converts them to UTC.
    """
    q = 1
    if p[q] in (0x07, 0x27):        # sub-type byte
        q += 1
    ts, q = varint(p, q)
    q += 2                          # constant 0x50 0x00 (0x50 <flag> on the first block)
    vals = []
    for _ in range(npens):
        if q + 8 > len(p):
            break
        vals.append(struct.unpack(">d", p[q:q + 8])[0])
        q += 8
    return {
        "ticks": ts,
        "values": vals,
        "status_nibbles": p[q:q + (npens + 1) // 2].hex(),
        "trailer": p[q:].hex(" "),
    }


def decode_checkpoint(p):
    ts, q = varint(p, 1)
    return {"ticks": ts, "trailer": p[q:].hex(" ")}


def decode_audit(p):
    """FE 18: configuration-change / event entry (seen in smol.uhh)."""
    ss = [s for _, s in strings16(p)]
    ss.sort(key=len, reverse=True)
    return {"message": ss[0] if ss else None, "raw": p.hex(" ")}


def decode_sample(p, npens):
    """FF: one logging interval.

    Layout:  [ceil(npens/2) nibble bytes][per-pen field]...[1 trailer byte]
    Nibbles are packed LOW nibble first.  Field length = nibble + 1 bytes,
    except nibble 0xF ("no data" / channel invalid) which takes 10 bytes.
    The field contents are not decoded yet, see FINDINGS.md.
    """
    nhdr = (npens + 1) // 2
    nib = []
    for k in range(nhdr):
        nib += [p[k] & 0x0F, p[k] >> 4]
    nib = nib[:npens]
    fields, q = [], nhdr
    for n in nib:
        ln = 10 if n == 0x0F else n + 1
        fields.append(p[q:q + ln])
        q += ln
    return {"nibbles": nib, "fields": fields, "trailer": p[q:], "consumed_ok": q == len(p) - 1}


# Top level

def parse(path):
    with open(path, "rb") as fh:
        data = fh.read()
    recs = tokenize(data)

    out = {
        "file": path,
        "size": len(data),
        "header": None, "format": None, "instrument": None,
        "pens": [], "snapshots": [], "checkpoints": [], "audit": [],
        "samples": {"count": 0, "bad_length": 0, "nibble_hist": {}},
        "records": [],
    }
    samples = []

    for off, kind, rtype, raw in recs:
        p = unescape(raw)
        if kind == "sample":
            samples.append((off, p))
            continue
        out["records"].append({
            "offset": off, "type": rtype,
            "name": RECORD_NAMES.get(rtype, "unknown_%02X" % rtype),
            "payload_len": len(p),
        })
        try:
            if rtype == 0x00:
                out["header"] = decode_file_header(p)
            elif rtype == 0x02:
                out["format"] = decode_format_version(p)
            elif rtype == 0x03:
                out["instrument"] = decode_instrument(p)
            elif rtype == 0x04:
                out["pens"].append(decode_pen(p))
            elif rtype == 0x18:
                out["audit"].append(decode_audit(p))
        except Exception as e:
            # keep going, the error ends up in the JSON output
            out["records"][-1]["error"] = repr(e)

    npens = max(1, len(out["pens"]))
    base = out["header"]["start_ticks"] if out["header"] else 0
    # second pass, since snapshots need the pen count
    rec_info = iter(out["records"])
    for off, kind, rtype, raw in recs:
        if kind != "rec":
            continue
        info = next(rec_info)
        p = unescape(raw)
        try:
            if rtype == 0x12:
                s = decode_snapshot(p, npens)
                s["offset"] = off
                out["snapshots"].append(s)
            elif rtype == 0x1D:
                c = decode_checkpoint(p)
                c["offset"] = off
                out["checkpoints"].append(c)
        except Exception as e:
            info["error"] = repr(e)
    # block timestamps are relative to the file-header timestamp
    for s in out["snapshots"] + out["checkpoints"]:
        s["utc"] = ts_to_dt(base + s["ticks"]).isoformat()

    hist = {}
    bad = 0
    for off, p in samples:
        s = decode_sample(p, npens)
        if not s["consumed_ok"]:
            bad += 1
        for n in s["nibbles"]:
            hist[n] = hist.get(n, 0) + 1
    out["samples"] = {"count": len(samples), "bad_length": bad,
                      "nibble_hist": dict(sorted(hist.items()))}

    # cadence = ticks between block markers / number of samples between them
    marks = sorted([(s["offset"], s["ticks"]) for s in out["snapshots"]] +
                   [(c["offset"], c["ticks"]) for c in out["checkpoints"]])
    cad = []
    soff = [o for o, _ in samples]
    for (o1, t1), (o2, t2) in zip(marks, marks[1:]):
        n = sum(1 for o in soff if o1 < o < o2)
        if n:
            cad.append((t2 - t1) / n)
    out["cadence_ticks_per_sample"] = cad
    if cad:
        mode = max(set(round(c, 3) for c in cad), key=lambda v: cad.count(v))
        out["sample_interval_s"] = mode * TICK
    return out, samples, npens


def main():
    ap = argparse.ArgumentParser(description="Decode a .uhh chart recorder history file.")
    ap.add_argument("path")
    ap.add_argument("--json", help="write everything decoded as JSON")
    ap.add_argument("--csv", help="write the block snapshots as CSV")
    ap.add_argument("--dump", action="store_true", help="print sample-record breakdown")
    a = ap.parse_args()

    res, samples, npens = parse(a.path)
    h, inst = res["header"], res["instrument"] or {}

    print("== %s  (%d bytes)" % (res["file"], res["size"]))
    print("   format %s   firmware %s" % ((res["format"] or {}).get("format_version"),
                                          inst.get("firmware")))
    print("   site '%s'   instrument '%s'   tz %s (%s/%s)" % (
        inst.get("site"), inst.get("instrument"), inst.get("timezone"),
        inst.get("language"), inst.get("country")))
    if h:
        print("   start %s   file_seq 0x%04X" % (h["start_utc"], h["file_seq"] or 0))
    print("   charts %s" % inst.get("chart_configs"))
    print("\n   pens (%d):" % len(res["pens"]))
    for p in res["pens"]:
        print("     %d  %-16s %8.1f .. %-8.1f %s" %
              (p["index"], p["name"], p["range_low"], p["range_high"], p["units"]))

    print("\n   records: " + ", ".join(
        "%s x%d" % (n, sum(1 for r in res["records"] if r["name"] == n))
        for n in dict.fromkeys(r["name"] for r in res["records"])))
    print("   sample records: %d (%d with unexplained length)" %
          (res["samples"]["count"], res["samples"]["bad_length"]))
    print("   field-length nibble histogram: %s" % res["samples"]["nibble_hist"])
    if res.get("sample_interval_s"):
        print("   cadence: %s ticks/sample -> %.3f s per sample" %
              (sorted(set(round(c, 2) for c in res["cadence_ticks_per_sample"])),
               res["sample_interval_s"]))
        span = res["samples"]["count"] * res["sample_interval_s"]
        print("   covered span: %d samples ~= %.0f s (%.2f h)" %
              (res["samples"]["count"], span, span / 3600))

    if res["snapshots"]:
        print("\n   block snapshots (absolute pen values, %s):" %
              (res["pens"][0]["units"] if res["pens"] else "?"))
        names = [p["name"] for p in res["pens"]]
        print("     %-26s %8s  %s" % ("utc", "ticks", "  ".join("%9s" % n[:9] for n in names)))
        for s in res["snapshots"]:
            print("     %-26s %8d  %s" % (
                s["utc"], s["ticks"],
                "  ".join("%9.2f" % v for v in s["values"])))

    if res["audit"]:
        print("\n   audit events:")
        for e in res["audit"]:
            print("     %s" % e["message"])

    if a.dump:
        print("\n   first 10 sample records:")
        for off, p in samples[:10]:
            s = decode_sample(p, npens)
            print("     @%06X %s | %s | tail %s" % (
                off, s["nibbles"], "  ".join(f.hex() for f in s["fields"]),
                s["trailer"].hex()))

    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["utc", "ticks"] + [p["name"] for p in res["pens"]])
            for s in res["snapshots"]:
                w.writerow([s["utc"], s["ticks"]] + ["%.4f" % v for v in s["values"]])
        print("\n   wrote %s" % a.csv)

    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=2, default=str)
        print("   wrote %s" % a.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
