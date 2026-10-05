# uhh-parse

A Python parser for `.uhh` files, the history files that Eurotherm paperless
chart recorders (5100/6100 at least) spit out. As far as I can tell, the only official way to read them
is the vendor's software. So I
looked into the format instead, which took way longer than installing the
software would have 

## What it does

Point it at a `.uhh` file and it'll tell you:

- Site name, instrument name, firmware, timezone, all that metadata stuff
- Pen (channel) config: names, ranges, units
- The actual temperature readings from the block snapshots (roughly one every 20 minutes)
- How many sample records there are and how often they were logged (every 10s in the samples here)

## What it doesn't do (yet)

Decode the per-sample data. Every 10 seconds the recorder writes 5-7 bytes per
channel, and those bytes look like random noise. The parser knows exactly where every one of those bytes is, it just has no idea what they mean.

All the gory details (and some theories) are in [FINDINGS.md](FINDINGS.md).

## Usage

Python 3.7+, standard lib

```
python uhh_parse.py [your data].uhh
```

Options:

| Flag | What it does |
|---|---|
| `--csv out.csv` | Dump the snapshot readings to CSV |
| `--json out.json` | Dump everything it decoded to JSON |
| `--dump` | Print the first 10 raw sample records, for headers |

Example output (trimmed):

```
   pens (6):
     0  Control TC           45.0 .. 1550.0   °F
     1  TC 2                 45.0 .. 1550.0   °F
     ...

   block snapshots (absolute pen values, °F):
     utc                           ticks  Control T       TC 2       TC 3  ...
     2024-01-23T12:45:03+00:00         0     950.86     167.04     222.05  ...
     2024-01-23T13:04:20+00:00      9256    1189.31     468.77     532.94  ...
```

## Sample files

`samples/` has two real recordings from a gas furnace:

- `Gas-Furnace~20240123_810D9B30000004C0.uhh`: about 2.5 hours of a furnace
  heating a load up to ~960 °F
- `smol.uhh`: a tiny one with just 3 samples. Good for debugging

## Things that would help

If you have any other eurotherm recorders and the script isn't working, the most useful
things would be:

1. A `.uhh` file plus the CSV export of the same file from datareviewer. 
2. A recording of the instrument doing nothing (thermocouples disconnected, or
   sitting at a stable setpoint). idle data is really useful here.

## Disclaimer

Not affiliated with Eurotherm in any way. This is a hobby project built on
testing, hex dumps, and time. I'm not responsible if you fail an audit.
