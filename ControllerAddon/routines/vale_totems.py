# routines/vale_totems.py
#
# Vale Totems (Forestry, Auburn Valley) -- see docs/superpowers/specs/2026-09-08-vale-totems-
# design.md for the full derivation and the recording it came from
# (recordings/2026-09-08_135457_vale-totem.*). Facts repeated here are MEASURED, not guessed.
#
# WHAT THE ACTIVITY IS: eight totem sites ring the Auburn Valley. At each: Claim the offerings
# an ent left, Build a totem (1 log), Carve three animals into it, Decorate it with four
# fletched bows. Ents walk the ring every 80 ticks (48s) leaving offerings at completed totems,
# and each visit costs the totem one durability until it collapses and needs rebuilding. The
# three carvings must match the three spirits currently standing at that site -- of five spirit
# variants, only three are active at once, and per the wiki (and seen once in the recording)
# they can change WHILE you are carving, so which spirits are missing must be re-derived from a
# fresh read before every carve rather than decided once. Per complete totem: 5 logs (1 build +
# 4 fletched into bows for the decoration); 40 logs for a full lap of all eight sites.
#
# THE 270-TITLE TRAP: the carve prompt and the fletch prompt are BOTH widget group 270
# (skillmulti's Skillmulti.if3) -- the site base loc's own action cycles Build -> Carve ->
# Decorate on one object id, and the last two share both an object id AND a widget group, so
# neither can tell the two prompts apart. The only discriminator is the TITLE at widget 270.5:
# "What animal would you like to carve?" is the carve prompt (options A..E are the five
# animals in a fixed order, so option n carves the spirit whose nearbyValue is n); "What would
# you like to make?" is the fletch prompt. skillmulti.py (shared with routines/fletching.py)
# carries TITLE_CARVE/TITLE_MAKE for exactly this gate. Answering blind (Space repeats the last
# item crafted, not necessarily a bow) fails far from its cause -- see that module's docstring.
#
# NO ROUTER: worldmap.py has NO usable collision data anywhere in Varlamore. MEASURED
# 2026-09-08 -- reachable_from(radius=40) answers a single tile at every totem site, at the
# bank, at Civitas illa Fortis, at Aldarin and at the Hunter guild, and find_path between any
# two sites answers None. vale_totem_route.py's waypoints are the human's own recorded path,
# not a computed route, and they only ever connect sites ON the ring to each other and to the
# bank -- there is no way to walk from an arbitrary tile back into that ring. So a player found
# outside the valley cannot be walked home by this routine at all; the only safe response is a
# safety stop asking the user to start it standing in the valley.
import time

import controller
import debug_log
import skillmulti
import vale_totems as vt
import vale_totem_route as route

LOG = "vale_totems"

# Per wood: the inventory log name, the inventory name of the unstrung bow used to decorate,
# and the Fletching level the wiki requires to build that totem. The skillmulti MENU label is
# derived from the bow name by skillmulti.menu_label -- the menu names the finished item
# ("Yew longbow"), the inventory receives the unstrung one ("Yew longbow (u)").
WOODS = {
    "OAK":     {"log": "Oak logs",     "bow": "Oak longbow (u)",     "level": 20},
    "WILLOW":  {"log": "Willow logs",  "bow": "Willow longbow (u)",  "level": 35},
    "MAPLE":   {"log": "Maple logs",   "bow": "Maple longbow (u)",   "level": 50},
    "YEW":     {"log": "Yew logs",     "bow": "Yew longbow (u)",     "level": 65},
    "MAGIC":   {"log": "Magic logs",   "bow": "Magic longbow (u)",   "level": 80},
}

DEFAULT_CONFIG = {"wood": "YEW"}

_now = time.monotonic
_sleep = time.sleep


def get_config(bridge_config):
    """Resolve which wood to totem with -- the bridge's `valeTotemsWood` wins over the
    default, and an unrecognised value falls back rather than crashing later at the bow menu."""
    wood = (bridge_config or {}).get("valeTotemsWood") or DEFAULT_CONFIG["wood"]
    wood = str(wood).upper()
    if wood not in WOODS:
        debug_log.log(LOG, "unknown wood %r -- falling back to %s"
                      % (wood, DEFAULT_CONFIG["wood"]))
        wood = DEFAULT_CONFIG["wood"]
    cfg = dict(WOODS[wood])
    cfg["wood"] = wood
    return cfg


def read_state():
    """The whole minigame in one request. None means "cannot verify", never "nothing to do"."""
    return vt.parse_state(controller.get_api("minigames", "vale-totems"))


def setup(ctx):
    debug_log.log(LOG, "setup: %s totems, circuit %s"
                  % (ctx.config["wood"], "->".join(str(n) for n in route.RING)))


def tick(ctx):
    state = read_state()
    if state is None:
        debug_log.log(LOG, "no vale-totems reader -- is the client up?")
        return
    if not state.at_vale_totems:
        ctx.state.trigger_safety_stop(
            "not in the Auburn Valley. This routine has no router -- worldmap has no "
            "collision data for Varlamore -- so it cannot walk back. Start it in the valley.")
        return
    # Later tasks add the ladder here.
