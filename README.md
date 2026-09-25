# uhh-parse

A Python parser for `.uhh` files, the history files that Honeywell-style paperless
chart recorders spit out. As far as I can tell, the only official way to read them
is the vendor's software, which I did not feel like dealing with. So I
reverse-engineered the format instead, which took way longer than installing the
software would have. No regrets. (Some regrets.)

## What it does

Point it at a `.uhh` file and it'll tell you:

- Site name, instrument name, firmware, timezone, all that metadata stuff
- Pen (channel) config: names, ranges, units
- The actual temperature readings from the block snapshots (roughly one every 20 minutes)
- How many sample records there are and how often they were logged (every 10 s in the samples here)

## What it doesn't do (yet)

Decode the per-sample data. Every 10 seconds the recorder writes 5-7 bytes per
channel, and those bytes look like random noise no matter how hard I stare at
them. The parser knows exactly where every one of those bytes is, it just has no
idea what they mean. If you figure it out, please tell me. Seriously.

All the gory details (and some theories) are in [FINDINGS.md](FINDINGS.md).

## Usage

Python 3.7+, standard library only. No `pip install` required, you're welcome.

```
python uhh_parse.py samples/Gas-Furnace~20240123_810D9B30000004C0.uhh
```

Options:

| Flag | What it does |
|---|---|
| `--csv out.csv` | Dump the snapshot readings to CSV |
| `--json out.json` | Dump everything it decoded to JSON |
| `--dump` | Print the first 10 raw sample records, for staring at |

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
- `smol.uhh`: a tiny one with just 3 samples. Good for debugging, bad for
  everything else.

## Things that would help

If you have one of these recorders and want to contribute, the most useful
things would be:

1. A `.uhh` file plus the CSV export of the same file from the vendor software
   (TrendManager / TrendView). Having the real values next to the encoded bytes
   would crack this wide open.
2. A recording of the instrument doing nothing (thermocouples disconnected, or
   sitting at a stable setpoint). Boring data is really useful here.

## Disclaimer

Not affiliated with Honeywell in any way. This is a hobby project built on
guesswork, hex dumps, and stubbornness. Don't use it for anything where a wrong
number gets someone hurt.
