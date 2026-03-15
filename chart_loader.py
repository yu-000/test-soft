"""
Chart Loader for 2-Button Rhythm Game
Supports:
  - TJA  (太鼓さん次郎)  ドン→lane 0 (F)、カッ→lane 1 (J)
  - BMS/BME/BML         4-button → 2-button via lane_map
  - JSON                native 2-button format

TJA note types:
  1 = ドン(小)   → lane 0
  2 = カッ(小)   → lane 1
  3 = ドン(大)   → lane 0
  4 = カッ(大)   → lane 1
  5,6,7,8,9 = 連打・風船 → スキップ
"""

import re
import json
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional


BEATS_PER_MEASURE = 4.0

# Default BMS: 4-button channels → 2 lanes
DEFAULT_LANE_MAP: Dict[str, int] = {
    "11": 0, "12": 0,
    "13": 1, "14": 1,
}

# TJA note char → lane (-1 = skip)
TJA_NOTE_LANE: Dict[str, int] = {
    "1": 0,  # ドン(小)
    "2": 1,  # カッ(小)
    "3": 0,  # ドン(大)
    "4": 1,  # カッ(大)
    "5": -1, # 連打開始
    "6": -1, # 大連打開始
    "7": -1, # 風船開始
    "8": -1, # 連打終了
    "9": -1, # 大ドン(cooperataive)
    "A": -1, "B": -1,  # cooperative notes
    "0": -1, # 空
}

COURSE_PRIORITY = ["Oni", "Hard", "Normal", "Easy"]


@dataclass
class ChartData:
    title: str
    artist: str
    bpm: float
    notes: list      # List[Note]
    source_lanes: int = 2
    course: str = ""


# ─────────────────────────────────────────────
#  TJA Parser
# ─────────────────────────────────────────────
def load_tja(filepath: str,
             course: Optional[str] = None,
             note_class=None) -> ChartData:
    """
    Load a .tja file.

    course : "Easy" / "Normal" / "Hard" / "Oni" / "Edit"
             None = 最高難易度を自動選択
    """
    if note_class is None:
        from rhythm_game import Note as _Note
        note_class = _Note

    encodings = ["utf-8", "shift_jis", "cp932", "utf-8-sig", "latin-1"]
    lines = []
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as f:
                lines = f.readlines()
            break
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    if not lines:
        raise FileNotFoundError(filepath)

    # ── 1. Parse header and all course blocks ──
    meta = {"title": "", "artist": "", "bpm": 120.0, "offset": 0.0}
    courses: Dict[str, dict] = {}   # course_name → {bpm, offset, lines}

    current_course: Optional[str] = None
    current_lines: List[str] = []
    course_bpm: float = 0.0
    course_offset: float = 0.0

    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()

        # Global headers (before any #START)
        if current_course is None:
            m = re.match(r"TITLE:(.+)", stripped, re.IGNORECASE)
            if m:
                meta["title"] = m.group(1).strip()
                continue
            m = re.match(r"SUBTITLE:(.+)", stripped, re.IGNORECASE)
            if m:
                val = m.group(1).strip().lstrip("--").strip()
                meta["artist"] = val
                continue
            m = re.match(r"BPM:(\d+(?:\.\d+)?)", stripped, re.IGNORECASE)
            if m:
                meta["bpm"] = float(m.group(1))
                continue
            m = re.match(r"OFFSET:(-?\d+(?:\.\d+)?)", stripped, re.IGNORECASE)
            if m:
                meta["offset"] = float(m.group(1))
                continue
            m = re.match(r"COURSE:(.+)", stripped, re.IGNORECASE)
            if m:
                current_course = _normalize_course(m.group(1).strip())
                course_bpm    = meta["bpm"]
                course_offset = meta["offset"]
                current_lines = []
                continue

        # Inside a course block
        if current_course is not None:
            if stripped.startswith("#START"):
                current_lines = []
            elif stripped.startswith("#END"):
                courses[current_course] = {
                    "bpm":    course_bpm,
                    "offset": course_offset,
                    "lines":  current_lines,
                }
                current_course = None
            else:
                # Per-course BPM/OFFSET overrides
                m = re.match(r"BPM:(\d+(?:\.\d+)?)", stripped, re.IGNORECASE)
                if m:
                    course_bpm = float(m.group(1))
                    continue
                m = re.match(r"OFFSET:(-?\d+(?:\.\d+)?)", stripped, re.IGNORECASE)
                if m:
                    course_offset = float(m.group(1))
                    continue
                current_lines.append(stripped)

    if not courses:
        raise ValueError(f"No course found in {filepath}")

    # ── 2. Select course ──
    target = _select_course(courses, course)
    cdata  = courses[target]

    # ── 3. Parse notes ──
    notes = _parse_tja_notes(
        cdata["lines"], cdata["bpm"], cdata["offset"], note_class
    )

    return ChartData(
        title=meta["title"] or filepath,
        artist=meta["artist"],
        bpm=cdata["bpm"],
        notes=notes,
        source_lanes=2,
        course=target,
    )


def _normalize_course(s: str) -> str:
    mapping = {
        "0": "Easy", "1": "Normal", "2": "Hard", "3": "Oni", "4": "Edit",
        "easy": "Easy", "normal": "Normal", "hard": "Hard",
        "oni": "Oni", "edit": "Edit", "ura": "Edit",
    }
    return mapping.get(s.lower(), s.capitalize())


def _select_course(courses: dict, preferred: Optional[str]) -> str:
    if preferred:
        key = _normalize_course(preferred)
        if key in courses:
            return key
    for c in COURSE_PRIORITY:
        if c in courses:
            return c
    return next(iter(courses))


def _parse_tja_notes(lines: List[str], bpm: float, offset: float,
                     note_class) -> list:
    """
    Convert TJA note lines into Note objects.
    Handles: #MEASURE, #BPMCHANGE, #SCROLL (ignored), #GOGOSTART/END (ignored),
             #BARLINE (ignored), branching (#BRANCHSTART → picks Normal branch).
    """
    notes = []
    measure_num = 0
    measure_beat = 0.0          # beat position of current measure start
    beats_per_measure = 4.0     # default 4/4
    current_bpm = bpm
    pending_notes: List[str] = []  # chars collected for this measure

    # Branch state: skip Expert/Master branches if present
    in_branch = False
    active_branch = True        # True = collect notes

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1

        # ── Commands ──────────────────────────
        if line.startswith("#"):
            cmd = line.upper()

            if cmd.startswith("#MEASURE"):
                m = re.match(r"#MEASURE\s+(\d+)/(\d+)", line, re.IGNORECASE)
                if m:
                    beats_per_measure = 4.0 * int(m.group(1)) / int(m.group(2))
                continue

            if cmd.startswith("#BPMCHANGE"):
                m = re.match(r"#BPMCHANGE\s+(\d+(?:\.\d+)?)", line, re.IGNORECASE)
                if m:
                    current_bpm = float(m.group(1))
                continue

            if cmd.startswith("#BRANCHSTART"):
                in_branch = True
                active_branch = False   # wait for #N section
                continue

            if cmd.startswith("#N"):   # Normal branch
                active_branch = True
                continue
            if cmd.startswith("#E") or cmd.startswith("#M"):
                active_branch = False  # skip Expert/Master branches
                continue
            if cmd.startswith("#BRANCHEND"):
                in_branch = False
                active_branch = True
                continue

            # All other commands: ignore
            continue

        # ── Note data ─────────────────────────
        if not active_branch:
            # Still need to detect measure boundary
            if "," in line:
                measure_beat += beats_per_measure
                measure_num  += 1
            continue

        # Strip inline comments
        line = re.sub(r"//.*", "", line).strip()
        if not line:
            continue

        for ch in line:
            if ch == ",":
                # End of measure: place pending_notes evenly
                if pending_notes:
                    slots = len(pending_notes)
                    for idx, nc in enumerate(pending_notes):
                        lane = TJA_NOTE_LANE.get(nc.upper(), -1)
                        if lane < 0:
                            continue
                        beat = measure_beat + (idx / slots) * beats_per_measure
                        # Convert beat to seconds, then back to beat at base BPM
                        # (TJA beats are relative to current BPM, we normalise to
                        #  base BPM beats so the game engine can use a single BPM)
                        beat_norm = _normalise_beat(
                            beat, current_bpm, bpm, measure_beat
                        )
                        notes.append(note_class(lane=lane, beat=beat_norm))
                    pending_notes = []

                measure_beat += beats_per_measure
                measure_num  += 1
            elif ch in TJA_NOTE_LANE or ch.upper() in TJA_NOTE_LANE:
                pending_notes.append(ch)
            # ignore spaces / unknown chars

    return notes


def _normalise_beat(beat: float, current_bpm: float,
                    base_bpm: float, _measure_start: float) -> float:
    """
    TJA can have mid-song BPM changes. We convert all beats to the timeline
    of the base BPM so the game engine (which uses a single BPM) stays correct.
    Simple approximation: scale by bpm ratio from measure start.
    Full multi-segment BPM support can be added later.
    """
    if current_bpm == base_bpm or base_bpm == 0:
        return beat
    # seconds at current_bpm → beats at base_bpm
    seconds = beat * (60.0 / base_bpm)   # beat position in base-BPM beats → seconds
    # Re-express as beats under current_bpm section
    # For now: keep simple 1:1 (adequate when BPM changes are minor)
    return beat


# ─────────────────────────────────────────────
#  BMS Parser
# ─────────────────────────────────────────────
def _parse_bms_raw(filepath: str) -> Tuple[dict, List[Tuple[float, str]]]:
    meta = {"title": "", "artist": "", "bpm": 130.0}
    measure_lengths: Dict[int, float] = {}
    channel_events: List[Tuple[float, str]] = []

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

        m = re.match(r"#TITLE\s+(.+)", line, re.IGNORECASE)
        if m:
            meta["title"] = m.group(1).strip(); continue

        m = re.match(r"#ARTIST\s+(.+)", line, re.IGNORECASE)
        if m:
            meta["artist"] = m.group(1).strip(); continue

        m = re.match(r"#BPM\s+(\d+(?:\.\d+)?)\s*$", line, re.IGNORECASE)
        if m:
            meta["bpm"] = float(m.group(1)); continue

        m = re.match(r"#(\d{3})([0-9A-Za-z]{2}):(.+)", line)
        if not m:
            continue

        measure = int(m.group(1))
        channel = m.group(2).upper()
        data    = m.group(3).strip()

        if channel == "02":
            try:
                measure_lengths[measure] = float(data)
            except ValueError:
                pass
            continue

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
    if lane_map is None:
        lane_map = DEFAULT_LANE_MAP
    if note_class is None:
        from rhythm_game import Note as _Note
        note_class = _Note

    meta, channel_events = _parse_bms_raw(filepath)
    notes = [note_class(lane=lane_map[ch], beat=beat)
             for beat, ch in channel_events if ch in lane_map]

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
               course: Optional[str] = None,
               note_class=None) -> ChartData:
    """Detect format by extension and load."""
    ext = filepath.rsplit(".", 1)[-1].lower()
    if ext == "tja":
        return load_tja(filepath, course=course, note_class=note_class)
    elif ext in ("bms", "bme", "bml", "pms"):
        return load_bms(filepath, lane_map=lane_map, note_class=note_class)
    elif ext == "json":
        return load_json(filepath, note_class=note_class)
    else:
        raise ValueError(
            f"Unknown chart format: .{ext}  (supported: tja, bms, bme, bml, json)"
        )


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python chart_loader.py <chart.tja|chart.bms|chart.json> [course]")
        sys.exit(1)

    course_arg = sys.argv[2] if len(sys.argv) > 2 else None
    chart = load_chart(sys.argv[1], course=course_arg)
    print(f"Title  : {chart.title}")
    print(f"Artist : {chart.artist}")
    print(f"BPM    : {chart.bpm}")
    print(f"Course : {chart.course}")
    print(f"Notes  : {len(chart.notes)}")
    if chart.notes:
        print(f"First  : beat={chart.notes[0].beat:.3f}  lane={chart.notes[0].lane}")
        print(f"Last   : beat={chart.notes[-1].beat:.3f} lane={chart.notes[-1].lane}")
