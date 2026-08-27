#include <Mouse.h>
#include <Keyboard.h>

// ---------------------------------------------------------------------------------------------
// Arduino Pro Micro / Leonardo (ATmega32U4) HID relay for controller.py.
//
// PROTOCOL (v2, 2026-08-24). Every v1 command is unchanged and still means exactly what it did;
// v2 only ADDS commands, so this firmware can be flashed without touching the Python side.
//
//   M <dx> <dy>                 Move, whole pixels, as fast as the link allows. LEGACY.
//   S <dxq> <dyq> <dur_ms>      Segment: move dxq/dyq HUNDREDTHS of a pixel, interpolated
//                               smoothly across dur_ms at the USB frame rate. THE NEW PATH.
//   MLD MLU MLC / MRD MRU MRC / MMD MMU MMC     Mouse buttons (down / up / click).
//   KT <key> | KD <key> | KU <key>              Keyboard (type / down / up).
//
// WHY v2 EXISTS -- both reasons were measured, not assumed (2026-08-24):
//
//  1. SUB-PIXEL MOTION. The device sustains ~1000 HID reports/s (measured 0.99 ms/report, so
//     bInterval is already 1 -- there is nothing to gain in the descriptor). But v1 took only
//     whole-pixel deltas, so slow motion could not be expressed: at 50 px/s a 1000 Hz report
//     stream needs 0.05 px per report, and integer deltas can only send 0 or 1. Since every
//     movement decelerates to zero at its end, the TAIL OF EVERY MOVE was stair-stepping. `S`
//     takes hundredths of a pixel and carries the sub-pixel remainder across segments (see
//     carry_x_q), so a stream of segments tracks the intended path exactly.
//
//  2. LINK SATURATION. In v1 one serial line produced one HID report, so driving 1000 Hz meant
//     1000 lines/s -- which is exactly the measured ceiling, leaving no headroom. One `S`
//     command produces however many reports its duration needs, so Python emits ~20-50 short
//     segments per movement instead of ~300 raw deltas, and can re-aim between them.
//
// ORDERING. Motion, buttons and keys share ONE queue, so a click sent after a segment happens
// AFTER that segment finishes rather than in the middle of it. With v1 traffic the queue is
// almost always empty and everything executes immediately, exactly as before.
//
// BACKPRESSURE. Serial is not read unless the queue has room, so an over-eager sender blocks on
// its own write instead of having motion silently dropped -- a dropped segment is a permanent
// position error, since segments are relative.
//
// This firmware never writes to the serial port. On a 32U4 the CDC Serial.write() stalls when
// the host is not draining, and controller.py never reads -- so any unsolicited output would
// risk wedging the device.
//
// PROTOCOL v3 (2026-08-27). Again purely ADDITIVE -- v1/v2 commands are untouched, and a v2
// firmware silently ignores every v3 command, so either side can be upgraded first. The Python
// side MUST feature-detect via `V` before relying on `X` (an ignored X means queued motion
// plays anyway, which silently corrupts the sender's position bookkeeping).
//
//   X    Flush: discard every queued command NOW. The active segment always finishes (it is
//        executed in place at queue[q_head]; see flushQueue). Immediate, not queued -- a queued
//        flush could not retract anything ahead of itself. NOTE: X drops queued button/key
//        commands too, so the sender must only flush while nothing but segments are queued
//        (controller.py only sends it from inside a mouse flight). Worst-case latency: while
//        the queue lacks QUEUE_RESERVE free slots, serial is not read, so an X can wait up to
//        one segment (~18 ms) before being seen.
//   V    Version query. Responds "HB 3\n" -- the ONE solicited serial write in this firmware.
//        A single short line cannot fill the 64-byte CDC TX buffer, so it cannot stall even if
//        the host never reads it; the never-write rule below still holds for UNSOLICITED output.
//   R    Reset: flush everything INCLUDING the active segment, zero the sub-pixel carry and
//        chain phase, release every held mouse button and key. For startup cleanup after a
//        crashed sender and for safety tooling.
//   W <n>  Scroll wheel, n detents (positive = away from user), clamped to +/-127. Queued, so
//          it keeps program order with motion and clicks like every other queued command.
//
// WATCHDOG (v3): if any button/key is held and no serial line has arrived for 60 s, everything
// is released. The panic path on the host is a hard process kill that cannot send key-ups; this
// is the only thing standing between that and a button held forever. It never fires when
// nothing is held, so multi-minute idle periods (scheduled breaks) are unaffected.
// ---------------------------------------------------------------------------------------------

static const uint32_t BAUD = 115200;   // nominal only: this is USB CDC, the rate is not real
static const uint16_t LINE_BUF = 96;

// A segment may not exceed these, because the interpolator computes (total_q * elapsed_ms) in
// int32: 200000 * 1000 = 2e8, comfortably inside 2.147e9. Raising either risks silent overflow.
static const int32_t MAX_SEG_Q = 200000L;   // +/- 2000.00 px
static const uint16_t MAX_SEG_MS = 1000;

static const uint16_t REPORT_INTERVAL_US = 1000;  // matches the measured 1 ms USB frame
static const uint8_t QUEUE_LEN = 16;
static const uint8_t QUEUE_RESERVE = 3;  // MLC expands to 3 entries; never pump below this

// If the queue starves, don't let the next segment try to "catch up" a huge backlog -- past this
// gap the timeline is restarted from now instead of chained onto the previous segment.
static const uint32_t CHAIN_MAX_LAG_US = 50000UL;

static const uint16_t CLICK_HOLD_MS = 60;  // only for the legacy one-shot MLC/MRC/MMC path

enum : uint8_t { CMD_SEG = 0, CMD_BTN = 1, CMD_KEY = 2, CMD_WHEEL = 3 };

struct Cmd {
  uint8_t type;
  int32_t dx_q;
  int32_t dy_q;
  uint16_t dur_ms;
  uint8_t p0;
  uint8_t p1;
};

static Cmd queue[QUEUE_LEN];
static uint8_t q_head = 0;
static uint8_t q_count = 0;

// Live segment state.
static bool seg_active = false;
static uint32_t seg_start_us = 0;
static int32_t seg_total_x_q = 0, seg_total_y_q = 0;  // includes the inherited sub-pixel carry
static int32_t seg_emitted_x = 0, seg_emitted_y = 0;  // whole px already reported this segment
static uint32_t last_report_us = 0;

// Sub-pixel remainder carried between segments -- this is what makes fractional motion real
// rather than repeatedly truncated. Always strictly within +/-100.
static int32_t carry_x_q = 0, carry_y_q = 0;

// v3: what WE are currently holding down, for R and the stuck-input watchdog. Mouse buttons
// are the MOUSE_* bitmask values (1/2/4), so a mask works; keys are just a depth counter --
// enough to know "something is held", which is all the watchdog needs.
static uint8_t held_buttons = 0;
static uint8_t held_keys = 0;
static uint32_t last_line_us = 0;
static const uint32_t STUCK_INPUT_TIMEOUT_US = 60000000UL;  // 60 s; longest legit hold is ~5 s

// Start time the NEXT segment should use, so a stream of segments stays phase-locked instead of
// each one starting slightly late and accumulating drift.
static uint32_t next_start_us = 0;
static bool chain_valid = false;

static char buf[LINE_BUF];
static uint16_t len = 0;

static inline bool isSpaceChar(char c) { return c == ' ' || c == '\t'; }

static void skipSpaces(const char *&p) {
  while (*p && isSpaceChar(*p)) p++;
}

static bool readToken(const char *&p, char *out, size_t outSize) {
  skipSpaces(p);
  if (!*p) return false;
  size_t i = 0;
  while (*p && !isSpaceChar(*p)) {
    if (i + 1 < outSize) out[i++] = *p;
    p++;
  }
  out[i] = '\0';
  return i > 0;
}

// Returns int32_t, NOT int. On AVR `int` is 16 bits, so v1's parseInt silently wrapped above
// 32767 -- harmless for whole-pixel deltas, but fixed-point values run to 200000.
static int32_t parseLong(const char *&p, bool &ok) {
  skipSpaces(p);
  ok = false;
  if (!*p) return 0;

  bool neg = false;
  if (*p == '-') { neg = true; p++; }
  else if (*p == '+') { p++; }

  int32_t v = 0;
  bool any = false;
  while (*p >= '0' && *p <= '9') {
    any = true;
    if (v < 100000000L) v = v * 10 + (*p - '0');  // saturate rather than wrap on junk input
    p++;
  }
  if (!any) return 0;

  ok = true;
  return neg ? -v : v;
}

static int32_t clampLong(int32_t v, int32_t lo, int32_t hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

// One HID report can carry at most a signed byte per axis. v1 CLAMPED to this and silently threw
// the remainder away; here the caller keeps reporting until the whole delta has been delivered,
// so a large move is split across frames instead of being lost.
static int8_t clampReport(int32_t v) {
  if (v < -127) return -127;
  if (v > 127) return 127;
  return (int8_t)v;
}

// ---------------------------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------------------------

static inline uint8_t q_free() { return QUEUE_LEN - q_count; }

static bool q_push(uint8_t type, int32_t dx_q, int32_t dy_q, uint16_t dur_ms, uint8_t p0, uint8_t p1) {
  if (q_count >= QUEUE_LEN) return false;
  uint8_t slot = q_head + q_count;
  if (slot >= QUEUE_LEN) slot -= QUEUE_LEN;
  Cmd &c = queue[slot];
  c.type = type;
  c.dx_q = dx_q;
  c.dy_q = dy_q;
  c.dur_ms = dur_ms;
  c.p0 = p0;
  c.p1 = p1;
  q_count++;
  return true;
}

static void q_pop() {
  if (q_count == 0) return;
  q_head++;
  if (q_head >= QUEUE_LEN) q_head = 0;
  q_count--;
}

// Drop everything queued but NEVER the active segment: serviceMotion executes the active
// command in place at queue[q_head] and endSegment pops it, so evicting the head mid-play
// would leave the interpolator reading a slot q_push is free to overwrite.
static void flushQueue() {
  q_count = seg_active ? 1 : 0;
}

static void releaseAllInputs() {
  Mouse.release(MOUSE_LEFT);
  Mouse.release(MOUSE_RIGHT);
  Mouse.release(MOUSE_MIDDLE);
  Keyboard.releaseAll();
  held_buttons = 0;
  held_keys = 0;
}

// ---------------------------------------------------------------------------------------------
// Command decoding
// ---------------------------------------------------------------------------------------------

static uint8_t buttonFromChar(char c) {
  switch (c) {
    case 'L': return MOUSE_LEFT;
    case 'R': return MOUSE_RIGHT;
    case 'M': return MOUSE_MIDDLE;
    default: return 0;
  }
}

static uint8_t keyFromToken(const char *tok) {
  // Single printable ASCII character.
  if (tok[0] && !tok[1]) {
    char c = tok[0];
    if (c >= 'a' && c <= 'z') c = (char)(c - 'a' + 'A');
    if ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) return (uint8_t)c;
  }

  if (!strcmp(tok, "SPACE")) return (uint8_t)' ';
  if (!strcmp(tok, "ESC")) return KEY_ESC;
  if (!strcmp(tok, "ENTER")) return KEY_RETURN;
  if (!strcmp(tok, "TAB")) return KEY_TAB;
  if (!strcmp(tok, "BACKSPACE")) return KEY_BACKSPACE;

  if (!strcmp(tok, "UP")) return KEY_UP_ARROW;
  if (!strcmp(tok, "DOWN")) return KEY_DOWN_ARROW;
  if (!strcmp(tok, "LEFT")) return KEY_LEFT_ARROW;
  if (!strcmp(tok, "RIGHT")) return KEY_RIGHT_ARROW;

  if (!strcmp(tok, "CTRL")) return KEY_LEFT_CTRL;
  if (!strcmp(tok, "SHIFT")) return KEY_LEFT_SHIFT;
  if (!strcmp(tok, "ALT")) return KEY_LEFT_ALT;

  if (tok[0] == 'F' && tok[1]) {
    int n = atoi(tok + 1);
    if (n >= 1 && n <= 12) {
      static const uint8_t fkeys[] = {
        KEY_F1, KEY_F2, KEY_F3, KEY_F4, KEY_F5, KEY_F6,
        KEY_F7, KEY_F8, KEY_F9, KEY_F10, KEY_F11, KEY_F12
      };
      return fkeys[n - 1];
    }
  }

  return 0;
}

// Button op codes carried in Cmd.p1.
enum : uint8_t { BTN_PRESS = 0, BTN_RELEASE = 1 };
// Key op codes carried in Cmd.p1.
enum : uint8_t { KEY_WRITE = 0, KEY_PRESS = 1, KEY_RELEASE = 2 };

static void handleLine(const char *line) {
  if (!line[0]) return;

  // v3 immediate commands -- these act NOW, not in queue order. X exists to retract queued
  // motion, so queueing it would defeat its purpose; V is a pure query.
  if (line[0] == 'X' && !line[1]) {
    flushQueue();
    return;
  }
  if (line[0] == 'V' && !line[1]) {
    Serial.print("HB 3\n");   // solicited, single short line: cannot stall (see header)
    return;
  }
  if (line[0] == 'R' && !line[1]) {
    // Hard reset: unlike X, this abandons the ACTIVE segment too, and forgets the sub-pixel
    // carry and chain phase -- the whole point is a clean slate after a crashed sender, and
    // chaining onto a dead session's timeline would be wrong. q_head is left as-is: with
    // q_count = 0 and seg_active false, no path reads the abandoned slot.
    q_count = 0;
    seg_active = false;
    carry_x_q = 0;
    carry_y_q = 0;
    chain_valid = false;
    releaseAllInputs();
    return;
  }

  // "M <dx> <dy>" -- legacy immediate move. Enqueued as a zero-duration segment so it keeps its
  // place in program order relative to clicks; with an empty queue that is indistinguishable
  // from v1's behavior. Whole pixels in, hundredths out.
  if (line[0] == 'M' && (line[1] == ' ' || line[1] == '\t')) {
    const char *p = line + 1;
    bool ok1, ok2;
    int32_t dx = parseLong(p, ok1);
    int32_t dy = parseLong(p, ok2);
    if (ok1 && ok2) {
      q_push(CMD_SEG,
             clampLong(dx * 100, -MAX_SEG_Q, MAX_SEG_Q),
             clampLong(dy * 100, -MAX_SEG_Q, MAX_SEG_Q),
             0, 0, 0);
    }
    return;
  }

  // "S <dxq> <dyq> <dur_ms>" -- interpolated segment, hundredths of a pixel.
  if (line[0] == 'S' && (line[1] == ' ' || line[1] == '\t')) {
    const char *p = line + 1;
    bool ok1, ok2, ok3;
    int32_t dxq = parseLong(p, ok1);
    int32_t dyq = parseLong(p, ok2);
    int32_t dur = parseLong(p, ok3);
    if (ok1 && ok2 && ok3) {
      q_push(CMD_SEG,
             clampLong(dxq, -MAX_SEG_Q, MAX_SEG_Q),
             clampLong(dyq, -MAX_SEG_Q, MAX_SEG_Q),
             (uint16_t)clampLong(dur, 0, MAX_SEG_MS), 0, 0);
    }
    return;
  }

  // "W <n>" -- scroll wheel, n detents. Queued so it keeps program order with motion/clicks.
  if (line[0] == 'W' && (line[1] == ' ' || line[1] == '\t')) {
    const char *p = line + 1;
    bool ok;
    int32_t n = parseLong(p, ok);
    if (ok && n != 0) {
      q_push(CMD_WHEEL, 0, 0, 0, (uint8_t)(int8_t)clampLong(n, -127, 127), 0);
    }
    return;
  }

  // Mouse buttons: exactly 3 chars, "M" + L/R/M + D/U/C.
  if (line[0] == 'M' && line[1] && line[2] && !line[3]) {
    uint8_t button = buttonFromChar(line[1]);
    if (!button) return;
    switch (line[2]) {
      case 'D':
        q_push(CMD_BTN, 0, 0, 0, button, BTN_PRESS);
        break;
      case 'U':
        q_push(CMD_BTN, 0, 0, 0, button, BTN_RELEASE);
        break;
      case 'C':
        // v1 used Mouse.click(), a press and release in the same instant -- a 0 ms hold, which no
        // physical hand can produce. Expanded here into press / hold / release using the segment
        // machinery (a zero-motion segment is just a delay). Nothing in controller.py uses this;
        // click_human sends MLD, waits a randomized 45-110 ms, then MLU, which is better still
        // because the hold varies. Kept working, and no longer a footgun, but prefer that path.
        q_push(CMD_BTN, 0, 0, 0, button, BTN_PRESS);
        q_push(CMD_SEG, 0, 0, CLICK_HOLD_MS, 0, 0);
        q_push(CMD_BTN, 0, 0, 0, button, BTN_RELEASE);
        break;
    }
    return;
  }

  // Keyboard: "KT <key>" / "KD <key>" / "KU <key>".
  if (line[0] == 'K' && (line[1] == 'T' || line[1] == 'D' || line[1] == 'U') && isSpaceChar(line[2])) {
    const char *p = line + 2;
    char tok[20];
    if (!readToken(p, tok, sizeof(tok))) return;

    for (char *q = tok; *q; ++q) {
      if (*q >= 'a' && *q <= 'z') *q = (char)(*q - 'a' + 'A');
    }

    uint8_t key = keyFromToken(tok);
    if (!key) return;

    uint8_t op = (line[1] == 'T') ? KEY_WRITE : (line[1] == 'D') ? KEY_PRESS : KEY_RELEASE;
    q_push(CMD_KEY, 0, 0, 0, key, op);
    return;
  }
}

// ---------------------------------------------------------------------------------------------
// Execution
// ---------------------------------------------------------------------------------------------

static void beginSegment(const Cmd &c) {
  // Inherit the sub-pixel remainder the previous segment could not express in whole pixels --
  // but ONLY for a segment that actually moves. A deliberate zero-motion segment is a pause (the
  // MLC hold is exactly this), and flushing a pending carry into it would emit a 1 px move while
  // a button is held down, turning a click into a drag. The carry stays pending instead.
  bool moves = (c.dx_q != 0 || c.dy_q != 0);
  seg_total_x_q = moves ? c.dx_q + carry_x_q : 0;
  seg_total_y_q = moves ? c.dy_q + carry_y_q : 0;
  seg_emitted_x = 0;
  seg_emitted_y = 0;

  uint32_t now = micros();
  if (chain_valid && (uint32_t)(now - next_start_us) < CHAIN_MAX_LAG_US) {
    seg_start_us = next_start_us;   // phase-locked onto the previous segment
  } else {
    seg_start_us = now;             // starved or first segment: restart the timeline
  }
  last_report_us = seg_start_us - REPORT_INTERVAL_US;  // allow an immediate first report
  seg_active = true;
}

static void endSegment(const Cmd &c) {
  // Whatever fraction of a pixel we could not express becomes the next segment's head start.
  // Skipped for a non-moving segment: beginSegment deliberately did not inherit the carry there,
  // so recomputing it from this segment's own totals (0 - 0) would silently discard it.
  if (c.dx_q != 0 || c.dy_q != 0) {
    carry_x_q = clampLong(seg_total_x_q - seg_emitted_x * 100, -99, 99);
    carry_y_q = clampLong(seg_total_y_q - seg_emitted_y * 100, -99, 99);
  }

  next_start_us = seg_start_us + (uint32_t)c.dur_ms * 1000UL;
  chain_valid = true;
  seg_active = false;
  q_pop();
}

// Drains any non-motion commands sitting at the head of the queue and starts the next segment.
static void serviceQueue() {
  while (!seg_active && q_count > 0) {
    Cmd &c = queue[q_head];

    if (c.type == CMD_SEG) {
      beginSegment(c);
      return;
    }

    if (c.type == CMD_BTN) {
      if (c.p1 == BTN_PRESS) {
        Mouse.press(c.p0);
        held_buttons |= c.p0;
      } else {
        Mouse.release(c.p0);
        held_buttons &= (uint8_t)~c.p0;
      }
    } else if (c.type == CMD_KEY) {
      if (c.p1 == KEY_WRITE) {
        Keyboard.write(c.p0);
      } else if (c.p1 == KEY_PRESS) {
        Keyboard.press(c.p0);
        if (held_keys < 255) held_keys++;
      } else {
        Keyboard.release(c.p0);
        if (held_keys) held_keys--;
      }
    } else if (c.type == CMD_WHEEL) {
      Mouse.move(0, 0, (int8_t)c.p0);
    }
    q_pop();
  }
}

static void serviceMotion() {
  if (!seg_active) return;

  uint32_t now = micros();
  if ((uint32_t)(now - last_report_us) < REPORT_INTERVAL_US) return;

  Cmd &c = queue[q_head];

  // Interpolate in MILLISECONDS, not microseconds: (200000 * 1000) fits int32, but
  // (200000 * 1000000) does not. At one report per millisecond this loses no resolution.
  uint32_t elapsed_ms = (uint32_t)(now - seg_start_us) / 1000UL;
  bool expired = (c.dur_ms == 0) || (elapsed_ms >= c.dur_ms);

  int32_t target_x_q, target_y_q;
  if (expired) {
    target_x_q = seg_total_x_q;
    target_y_q = seg_total_y_q;
  } else {
    target_x_q = seg_total_x_q * (int32_t)elapsed_ms / (int32_t)c.dur_ms;
    target_y_q = seg_total_y_q * (int32_t)elapsed_ms / (int32_t)c.dur_ms;
  }

  int8_t dx = clampReport(target_x_q / 100 - seg_emitted_x);
  int8_t dy = clampReport(target_y_q / 100 - seg_emitted_y);

  // A real mouse reports nothing when it has not moved; skip empty frames rather than emitting
  // a stream of zero-delta reports during the slow parts of a curve.
  if (dx != 0 || dy != 0) {
    Mouse.move(dx, dy, 0);
    seg_emitted_x += dx;
    seg_emitted_y += dy;
  }
  last_report_us = now;

  // Finish only once the whole delta has actually been delivered -- a move larger than 127 px
  // needs several frames, and ending early would silently drop the remainder (the v1 bug).
  if (expired && seg_emitted_x == target_x_q / 100 && seg_emitted_y == target_y_q / 100) {
    endSegment(c);
  }
}

static void pumpSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\r') continue;

    if (c == '\n') {
      while (len > 0 && isSpaceChar(buf[len - 1])) len--;
      buf[len] = '\0';
      last_line_us = micros();
      handleLine(buf);
      len = 0;
      return;   // one command per pass, so motion is serviced promptly between commands
    }

    if (len + 1 < LINE_BUF) {
      buf[len++] = c;
    } else {
      len = 0;  // overlong junk line: drop it rather than truncating into a bogus command
    }
  }
}

void setup() {
  Serial.begin(BAUD);
  while (!Serial) { ; }

  Mouse.begin();
  Keyboard.begin();

  delay(200);
}

void loop() {
  // Backpressure: stop reading while the queue is nearly full, so the sender blocks on its own
  // write instead of us dropping motion. QUEUE_RESERVE covers the widest command (MLC -> 3).
  if (q_free() >= QUEUE_RESERVE) {
    pumpSerial();
  }
  serviceQueue();
  serviceMotion();

  // Stuck-input watchdog (v3). The host's panic key is a hard process kill that cannot send
  // key-ups, so anything held at that instant would otherwise stay down FOREVER. Fires only
  // when something is actually held: breaks (minutes of silence, nothing held) never trip it,
  // and any legitimate hold happens inside a flight that streams segments constantly.
  // micros() wraps every ~71 min; the unsigned subtraction handles the wrap.
  if ((held_buttons || held_keys) &&
      (uint32_t)(micros() - last_line_us) > STUCK_INPUT_TIMEOUT_US) {
    releaseAllInputs();
  }
}
