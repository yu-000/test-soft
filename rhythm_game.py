"""
2-Button Rhythm Game
Controls: F (left lane) / J (right lane)

Usage:
  python rhythm_game.py                        # auto-generated chart
  python rhythm_game.py chart.json             # native 2-button JSON
  python rhythm_game.py song.bms               # 4-button BMS → 2-button
  python rhythm_game.py song.bms --map 11:0,12:0,13:1,14:1  # custom lane map
"""

import pygame
import sys
import argparse
from dataclasses import dataclass, field
from typing import List, Optional

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
LANES = 2
KEYS  = [pygame.K_f, pygame.K_j]

SCREEN_W, SCREEN_H = 480, 720
FPS = 60
BPM = 128
NOTE_SPEED = 400              # pixels per second
HIT_Y = SCREEN_H - 120        # judgment line Y
HIT_WINDOW_PERFECT = 45       # ms
HIT_WINDOW_GOOD    = 90       # ms
HIT_WINDOW_BAD     = 140      # ms

# Colors
BG_COLOR        = (10, 10, 20)
LANE_COLOR      = (25, 25, 45)
LANE_LINE_COLOR = (50, 50, 80)
HIT_LINE_COLOR  = (200, 200, 255)
NOTE_COLORS     = [(80, 160, 255), (255, 100, 160)]
KEY_LABELS      = ["F", "J"]
PERFECT_COLOR   = (255, 240, 80)
GOOD_COLOR      = (100, 255, 160)
BAD_COLOR       = (255, 140, 60)
MISS_COLOR      = (200, 60, 60)

# ─────────────────────────────────────────────
#  DATA
# ─────────────────────────────────────────────
@dataclass
class Note:
    lane: int
    beat: float           # spawn time in beats
    y: float = 0.0
    hit: bool = False
    missed: bool = False

@dataclass
class Judgment:
    text: str
    color: tuple
    alpha: int = 255
    y_offset: float = 0.0

@dataclass
class LaneEffect:
    lane: int
    alpha: int = 0

# ─────────────────────────────────────────────
#  CHART GENERATOR  (fallback when no file given)
# ─────────────────────────────────────────────
def generate_chart(lanes: int, total_beats: int = 48) -> List[Note]:
    notes = []
    patterns = [
        [0], [1], [0, 1], [0], [1], [0], [1, 0],
        [0], [0], [1], [1], [0, 1],
    ]
    beat = 2.0
    p = 0
    while beat < total_beats - 2:
        for lane in patterns[p % len(patterns)]:
            if lane < lanes:
                notes.append(Note(lane=lane, beat=beat))
        beat += 0.5 if p % 4 == 3 else 1.0
        p += 1
    return notes


# ─────────────────────────────────────────────
#  CHART FILE LOADER
# ─────────────────────────────────────────────
def load_chart_file(path: str, lane_map_str: Optional[str] = None):
    """
    Load a BMS/JSON chart and return (notes, bpm, title).
    lane_map_str example: "11:0,12:0,13:1,14:1"
    """
    from chart_loader import load_chart, DEFAULT_LANE_MAP

    lane_map = None
    if lane_map_str:
        lane_map = {}
        for pair in lane_map_str.split(","):
            ch, ln = pair.strip().split(":")
            lane_map[ch.upper()] = int(ln)

    chart = load_chart(path, lane_map=lane_map, note_class=Note)
    return chart.notes, chart.bpm, chart.title

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def lane_x(lane: int, lanes: int, w: int) -> int:
    """Center X of a lane."""
    lane_w = w // lanes
    return lane_w * lane + lane_w // 2

def draw_rounded_rect(surf, color, rect, radius=12):
    pygame.draw.rect(surf, color, rect, border_radius=radius)

# ─────────────────────────────────────────────
#  GAME
# ─────────────────────────────────────────────
class RhythmGame:
    def __init__(self, chart_path: Optional[str] = None,
                 lane_map_str: Optional[str] = None):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        self.clock  = pygame.time.Clock()
        self.font_big   = pygame.font.SysFont("monospace", 48, bold=True)
        self.font_med   = pygame.font.SysFont("monospace", 28, bold=True)
        self.font_small = pygame.font.SysFont("monospace", 20)

        # Load chart once; reset() reuses it
        if chart_path:
            self._notes_src, self._bpm, self._title = load_chart_file(
                chart_path, lane_map_str)
        else:
            self._notes_src = generate_chart(LANES)
            self._bpm  = BPM
            self._title = "2-Button Rhythm Game"

        pygame.display.set_caption(self._title)
        self.reset()

    def reset(self):
        # Deep-copy notes so hit/missed flags reset each retry
        self.notes = [Note(lane=n.lane, beat=n.beat) for n in self._notes_src]
        self.beat_time  = 60.0 / self._bpm  # seconds per beat
        self.elapsed    = 0.0               # seconds since start
        self.score      = 0
        self.combo      = 0
        self.max_combo  = 0
        self.judgments: List[Judgment] = []
        self.lane_effects = [LaneEffect(i) for i in range(LANES)]
        self.key_held   = [False] * LANES
        self.state      = "playing"          # playing / result
        self.total_notes = len(self.notes)
        self.hit_counts  = {"PERFECT": 0, "GOOD": 0, "BAD": 0, "MISS": 0}

    # ── Update ──────────────────────────────
    def update(self, dt: float):
        if self.state != "playing":
            return

        self.elapsed += dt
        spb = self.beat_time  # seconds per beat

        # Update note positions
        for note in self.notes:
            if note.hit or note.missed:
                continue
            note_time = note.beat * spb
            delta_sec = self.elapsed - note_time
            note.y = HIT_Y + delta_sec * NOTE_SPEED

            # Auto-miss
            if note.y > SCREEN_H + 20 and not note.hit:
                note.missed = True
                self.combo = 0
                self.hit_counts["MISS"] += 1
                self.add_judgment("MISS", MISS_COLOR, note.lane)

        # Fade judgments
        for j in self.judgments[:]:
            j.alpha -= 4
            j.y_offset -= 0.8
            if j.alpha <= 0:
                self.judgments.remove(j)

        # Fade lane effects
        for le in self.lane_effects:
            if le.alpha > 0:
                le.alpha = max(0, le.alpha - 8)

        # Check end
        if all(n.hit or n.missed for n in self.notes):
            self.state = "result"
            self.max_combo = max(self.max_combo, self.combo)

    def add_judgment(self, text: str, color: tuple, lane: int):
        x = lane_x(lane, LANES, SCREEN_W)
        self.judgments.append(Judgment(text=text, color=color, y_offset=float(HIT_Y - 60)))

    # ── Input ───────────────────────────────
    def on_key(self, key):
        if self.state == "result":
            if key == pygame.K_r:
                self.reset()
            return

        if key not in KEYS:
            return
        lane = KEYS.index(key)
        self.key_held[lane] = True
        self.lane_effects[lane].alpha = 180
        self.judge_lane(lane)

    def on_key_up(self, key):
        if key in KEYS:
            self.key_held[KEYS.index(key)] = False

    def judge_lane(self, lane: int):
        spb = self.beat_time
        best_note = None
        best_delta = float("inf")

        for note in self.notes:
            if note.lane != lane or note.hit or note.missed:
                continue
            note_time = note.beat * spb
            delta_ms = abs(self.elapsed - note_time) * 1000
            if delta_ms < best_delta:
                best_delta = delta_ms
                best_note = note

        if best_note is None:
            return

        if best_delta <= HIT_WINDOW_PERFECT:
            best_note.hit = True
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
            pts = int(1000 * (1 + 0.1 * min(self.combo, 50)))
            self.score += pts
            self.hit_counts["PERFECT"] += 1
            self.add_judgment("PERFECT", PERFECT_COLOR, lane)

        elif best_delta <= HIT_WINDOW_GOOD:
            best_note.hit = True
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
            pts = int(500 * (1 + 0.05 * min(self.combo, 50)))
            self.score += pts
            self.hit_counts["GOOD"] += 1
            self.add_judgment("GOOD", GOOD_COLOR, lane)

        elif best_delta <= HIT_WINDOW_BAD:
            best_note.hit = True
            self.combo = 0
            self.score += 100
            self.hit_counts["BAD"] += 1
            self.add_judgment("BAD", BAD_COLOR, lane)

    # ── Draw ────────────────────────────────
    def draw(self):
        self.screen.fill(BG_COLOR)

        if self.state == "playing":
            self.draw_lanes()
            self.draw_notes()
            self.draw_hit_line()
            self.draw_key_buttons()
            self.draw_judgments()
            self.draw_hud()
        elif self.state == "result":
            self.draw_result()

        pygame.display.flip()

    def draw_lanes(self):
        lane_w = SCREEN_W // LANES
        for i in range(LANES):
            x = lane_w * i
            pygame.draw.rect(self.screen, LANE_COLOR, (x + 2, 0, lane_w - 4, SCREEN_H))
            # Lane glow on key press
            le = self.lane_effects[i]
            if le.alpha > 0:
                glow = pygame.Surface((lane_w - 4, SCREEN_H), pygame.SRCALPHA)
                c = NOTE_COLORS[i % len(NOTE_COLORS)]
                glow.fill((*c, le.alpha // 4))
                self.screen.blit(glow, (x + 2, 0))
            # Dividers
            if i > 0:
                pygame.draw.line(self.screen, LANE_LINE_COLOR, (x, 0), (x, SCREEN_H), 2)

    def draw_hit_line(self):
        pygame.draw.line(self.screen, HIT_LINE_COLOR, (0, HIT_Y), (SCREEN_W, HIT_Y), 3)

    def draw_notes(self):
        lane_w = SCREEN_W // LANES
        note_w = lane_w - 16
        note_h = 24

        for note in self.notes:
            if note.hit or note.missed:
                continue
            if note.y < -note_h or note.y > SCREEN_H + note_h:
                continue
            x = lane_w * note.lane + 8
            y = int(note.y) - note_h // 2
            color = NOTE_COLORS[note.lane % len(NOTE_COLORS)]

            # Note body
            draw_rounded_rect(self.screen, color, (x, y, note_w, note_h), 8)
            # Shine
            shine = pygame.Surface((note_w, note_h // 2), pygame.SRCALPHA)
            shine.fill((255, 255, 255, 40))
            self.screen.blit(shine, (x, y))

    def draw_key_buttons(self):
        lane_w = SCREEN_W // LANES
        btn_w, btn_h = lane_w - 20, 50
        btn_y = HIT_Y + 20

        for i in range(LANES):
            x = lane_w * i + 10
            color = NOTE_COLORS[i % len(NOTE_COLORS)]
            base_color = tuple(min(255, int(c * 0.5)) for c in color)
            active_color = color

            c = active_color if self.key_held[i] else base_color
            draw_rounded_rect(self.screen, c, (x, btn_y, btn_w, btn_h), 10)

            # Label
            label = self.font_med.render(KEY_LABELS[i], True, (240, 240, 255))
            lx = x + btn_w // 2 - label.get_width() // 2
            ly = btn_y + btn_h // 2 - label.get_height() // 2
            self.screen.blit(label, (lx, ly))

    def draw_judgments(self):
        # Show latest judgment centered
        for j in self.judgments[-1:]:
            surf = self.font_big.render(j.text, True, (*j.color, j.alpha))
            surf.set_alpha(j.alpha)
            x = SCREEN_W // 2 - surf.get_width() // 2
            y = int(j.y_offset)
            self.screen.blit(surf, (x, y))

    def draw_hud(self):
        # Score
        score_surf = self.font_med.render(f"{self.score:,}", True, (220, 220, 255))
        self.screen.blit(score_surf, (SCREEN_W - score_surf.get_width() - 12, 12))

        # Combo
        if self.combo > 1:
            combo_surf = self.font_big.render(f"{self.combo}x", True, (255, 220, 100))
            cx = SCREEN_W // 2 - combo_surf.get_width() // 2
            self.screen.blit(combo_surf, (cx, 16))

        # Progress bar
        done = sum(1 for n in self.notes if n.hit or n.missed)
        progress = done / max(self.total_notes, 1)
        bar_w = SCREEN_W - 24
        pygame.draw.rect(self.screen, (40, 40, 60), (12, SCREEN_H - 10, bar_w, 6), border_radius=3)
        pygame.draw.rect(self.screen, (100, 180, 255),
                         (12, SCREEN_H - 10, int(bar_w * progress), 6), border_radius=3)

    def draw_result(self):
        # Background overlay
        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        # Title
        t = self.font_big.render("RESULT", True, (255, 240, 100))
        self.screen.blit(t, (SCREEN_W // 2 - t.get_width() // 2, 80))

        # Score
        s = self.font_big.render(f"{self.score:,}", True, (220, 220, 255))
        self.screen.blit(s, (SCREEN_W // 2 - s.get_width() // 2, 160))

        # Grade
        total = self.total_notes
        p = self.hit_counts["PERFECT"]
        rate = p / total if total else 0
        if rate >= 1.0:   grade, gc = "S",  (255, 215, 0)
        elif rate >= 0.9: grade, gc = "A",  (100, 255, 160)
        elif rate >= 0.75:grade, gc = "B",  (100, 180, 255)
        elif rate >= 0.5: grade, gc = "C",  (255, 180, 80)
        else:             grade, gc = "D",  (200, 100, 100)

        g = self.font_big.render(grade, True, gc)
        self.screen.blit(g, (SCREEN_W // 2 - g.get_width() // 2, 240))

        # Stats
        stats = [
            ("PERFECT", self.hit_counts["PERFECT"], PERFECT_COLOR),
            ("GOOD",    self.hit_counts["GOOD"],    GOOD_COLOR),
            ("BAD",     self.hit_counts["BAD"],     BAD_COLOR),
            ("MISS",    self.hit_counts["MISS"],    MISS_COLOR),
            ("MAX COMBO", self.max_combo,           (200, 200, 255)),
        ]
        y = 330
        for label, val, color in stats:
            row = self.font_small.render(f"{label:<12} {val}", True, color)
            self.screen.blit(row, (SCREEN_W // 2 - 100, y))
            y += 32

        hint = self.font_small.render("Press R to retry", True, (150, 150, 200))
        self.screen.blit(hint, (SCREEN_W // 2 - hint.get_width() // 2, y + 20))

    # ── Main loop ───────────────────────────
    def run(self):
        while True:
            dt = self.clock.tick(FPS) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.quit()
                        sys.exit()
                    self.on_key(event.key)
                elif event.type == pygame.KEYUP:
                    self.on_key_up(event.key)

            self.update(dt)
            self.draw()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="2-Button Rhythm Game")
    parser.add_argument("chart", nargs="?", help="Chart file (.bms/.bme/.json)")
    parser.add_argument("--map", dest="lane_map",
                        help='BMS channel→lane map, e.g. "11:0,12:0,13:1,14:1"')
    args = parser.parse_args()
    RhythmGame(chart_path=args.chart, lane_map_str=args.lane_map).run()
