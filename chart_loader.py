"""
Chart Loader for 2-Button Rhythm Game
Supports:
  - BMS/BME/BML  (4-button or 7-button → 2-button via lane_map)
  - JSON         (native 2-button format)

BMS 4-button → 2-button default mapping
  ch11, ch12  →  lane 0  (F / left)
  ch13, ch14  →  lane 1  (J / right)

To use a different source game, pass a custom lane_map dict:
  {"11": 0, "12": 0, "13": 1, "14": 1, "15": 1}  # 5-key example
"""

import re
import json
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional


BEATS_PER_MEASURE = 4.0

# Default: 4-button BMS channels → 2 lanes
DEFAULT_LANE_MAP: Dict[str, int] = {
    "11": 0,  # P1 key 1  → left
    "12": 0,  # P1 key 2  → left
    "13": 1,  # P1 key 3  → right
    "14": 1,  # P1 key 4  → right
}


@dataclass
class ChartData:
    title: str
    artist: str
    bpm: float
    notes: list      # List[Note] — Note is imported from rhythm_game at runtime
    source_lanes: int = 4


# ─────────────────────────────────────────────
#  BMS Parser
# ─────────────────────────────────────────────
def _parse_bms_raw(filepath: str) -> Tuple[dict, List[Tuple[float, int]]]:
    """
    Returns (meta, raw_notes) where raw_notes = [(beat, channel_str), ...]
    Does not depend on Note class — caller maps channels to lanes.
    """
    meta = {"title": "", "artist": "", "bpm": 130.0}
    measure_lengths: Dict[int, float] = {}
    channel_events: List[Tuple[float, str]] = []  # (beat, channel)

    encodings = ["utf-8", "shift_jis", "cp932", "latin-1"]
    lines = []
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as f:
                lines = f.readlines()
            break
        except (UnicodeDecodeError, FileNotFoundError):
            continue

    for line in lines:
        line = line.strip()
        if not line.startswith("#"):
            continue

        # ── Headers ──────────────────────────
        m = re.match(r"#TITLE\s+(.+)", line, re.IGNORECASE)
        if m:
            meta["title"] = m.group(1).strip()
            continue

        m = re.match(r"#ARTIST\s+(.+)", line, re.IGNORECASE)
        if m:
            meta["artist"] = m.group(1).strip()
            continue

        m = re.match(r"#BPM\s+(\d+(?:\.\d+)?)\s*$", line, re.IGNORECASE)
        if m:
            meta["bpm"] = float(m.group(1))
            continue

        # ── Data lines: #MMCCC:data ───────────
        m = re.match(r"#(\d{3})([0-9A-Za-z]{2}):(.+)", line)
        if not m:
            continue

        measure = int(m.group(1))
        channel = m.group(2).upper()
        data    = m.group(3).strip()

        # Measure length multiplier (channel 02)
        if channel == "02":
            try:
                measure_lengths[measure] = float(data)
            except ValueError:
                pass
            continue

        # Skip non-note channels
        if len(data) % 2 != 0:
            continue

        slots = len(data) // 2
        ml    = measure_lengths.get(measure, 1.0)

        for i in range(slots):
            val = data[i * 2 : (i + 1) * 2]
            if val == "00":
                continue
            beat = (measure * BEATS_PER_MEASURE
                    + (i / slots) * BEATS_PER_MEASURE * ml)
            channel_events.append((beat, channel))

    channel_events.sort(key=lambda x: x[0])
    return meta, channel_events


def load_bms(filepath: str,
             lane_map: Optional[Dict[str, int]] = None,
             note_class=None) -> ChartData:
    """
    Load a BMS/BME/BML file and convert to 2-button chart.

    lane_map  : dict mapping BMS channel string → game lane index
                Default: DEFAULT_LANE_MAP (4-button → 2-lane)
    note_class: the Note dataclass from rhythm_game (passed to avoid circular import)
    """
    if lane_map is None:
        lane_map = DEFAULT_LANE_MAP
    if note_class is None:
        from rhythm_game import Note as _Note
        note_class = _Note

    meta, channel_events = _parse_bms_raw(filepath)

    notes = []
    for beat, channel in channel_events:
        if channel in lane_map:
            notes.append(note_class(lane=lane_map[channel], beat=beat))

    return ChartData(
        title=meta["title"] or filepath,
        artist=meta["artist"],
        bpm=meta["bpm"],
        notes=notes,
        source_lanes=len(set(lane_map.keys())),
    )


# ─────────────────────────────────────────────
#  JSON loader
# ─────────────────────────────────────────────
def load_json(filepath: str, note_class=None) -> ChartData:
    """
    Load a native 2-button JSON chart.

    JSON schema:
    {
      "title":  "Song Name",
      "artist": "Artist",
      "bpm":    130,
      "lanes":  2,
      "notes":  [{"lane": 0, "beat": 1.0}, ...]
    }
    """
    if note_class is None:
        from rhythm_game import Note as _Note
        note_class = _Note

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    notes = [note_class(lane=n["lane"], beat=n["beat"])
             for n in data.get("notes", [])]
    notes.sort(key=lambda n: n.beat)

    return ChartData(
        title=data.get("title", filepath),
        artist=data.get("artist", ""),
        bpm=float(data.get("bpm", 130)),
        notes=notes,
        source_lanes=int(data.get("lanes", 2)),
    )


# ─────────────────────────────────────────────
#  Auto-detect and load
# ─────────────────────────────────────────────
def load_chart(filepath: str,
               lane_map: Optional[Dict[str, int]] = None,
               note_class=None) -> ChartData:
    """Detect format by extension and load."""
    ext = filepath.rsplit(".", 1)[-1].lower()
    if ext in ("bms", "bme", "bml", "pms"):
        return load_bms(filepath, lane_map=lane_map, note_class=note_class)
    elif ext == "json":
        return load_json(filepath, note_class=note_class)
    else:
        raise ValueError(f"Unknown chart format: .{ext}  (supported: bms, bme, bml, json)")


# ─────────────────────────────────────────────
#  CLI: quick info dump
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python chart_loader.py <chart.bms|chart.json>")
        sys.exit(1)

    chart = load_chart(sys.argv[1])
    print(f"Title  : {chart.title}")
    print(f"Artist : {chart.artist}")
    print(f"BPM    : {chart.bpm}")
    print(f"Notes  : {len(chart.notes)}")
    if chart.notes:
        print(f"First  : beat={chart.notes[0].beat:.2f}  lane={chart.notes[0].lane}")
        print(f"Last   : beat={chart.notes[-1].beat:.2f} lane={chart.notes[-1].lane}")
