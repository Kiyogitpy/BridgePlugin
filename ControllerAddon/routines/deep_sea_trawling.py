# routines/deep_sea_trawling.py
#
# Deep sea trawling: park the boat at a shoal and OPERATE the trawling net once per game tick.
#
# Why one click per tick: the game rolls for a catch every 3 ticks while a net is operating in
# range, but clicking the net every tick makes it roll every 2 ticks -- a flat +50% catch rate
# (OSRS Wiki, Deep sea trawling rev 15297127, "1-tick clicking"). That burst is the whole point
# of this routine, and it is why the net is clicked IN PLACE (controller.click_human, ~92ms,
# measured in giants_foundry) rather than re-flown per click: a fresh click_entity flight costs
# ~1s and cannot hold a 0.6s cadence at all.
#
# Written 2026-09-06 from the Wiki, the decoded cache (dump 5bc2ff1e) and runelite-api 1.12.38.
# There is NO human recording and NOTHING here has been run live -- see the design spec's
# "Live-verification checklist", which is the first session's worklist:
#   docs/superpowers/specs/2026-09-06-deep-sea-trawling-design.md
#
# Game facts this rests on:
#   loc 59755 "Rope trawling net"  op1=Operate op2=Raise op3=Lower op4=Collect-from op5=Modify
#       (59756/59757/59758 linen/hemp/cotton; 59759..59766 the 3x8 port/starboard variants)
#   loc 59734 "Giant krill shoal"  a 6x6 object out at sea; 59740 the shimmering shoal that
#       replaces it 10% of the time with identical movement and stop timers
#   inv 968   the shared 250-fish net container, per player  -> GET /trawling
#   Every roll grants a small SAILING xp drop; a SUCCESSFUL roll also grants FISHING xp. The
#       Sailing drop is therefore a pushed, sub-tick HEARTBEAT proving "the net is operating in
#       range" -- it is the range test, not distance to a moving 6x6 object.
#   Fish in the nets are DISCARDED on disembark, teleport, world-hop or a forced logout. This
#       routine therefore never hops worlds, never teleports, and collects before every break.
#
# Preconditions (checked by _startup_checks, each with its own one-line stop reason):
#   - standing on your boat, which has a trawling net built
#   - a CREWMATE on the helm. Helmsmanship lets a crewmate hold the helm so the player can stand
#     at another facility while the player still clicks the heading (Wiki, Crew Management).
#     Without one the player must stand at the helm to steer and cannot trawl at the same time.
#   - Fishing level >= the configured shoal's requirement
#   - the boat is on the configured route
#
# Run with:
#     python controller.py routines/deep_sea_trawling.py

import time
from typing import NamedTuple, Optional

import bridge_events
import controller
import debug_log
import outcome_waits
import sailing
import trawling_routes

# --------------------------------------------------------------------------- config

DEFAULT_CONFIG = {
    "shoal": "giant_krill",
    "route": "great_sound",
    # UNVERIFIED (spec checklist #3): the largest boat->shoal-centre distance that still keeps
    # the Sailing XP heartbeat alive. Park inside this; the heartbeat, not this number, decides
    # whether we are actually catching.
    "trawlRange": 8,
    "netFullAt": 240,            # collect here, not at the 250 cap
    "collectTo": "cargo",        # "cargo" | "inventory"
    "stopWhenCargoFull": True,
    "safety": {},
}

# The engine's safety check runs BEFORE tick(), so an inventory-full stop would win the race
# against our own collect. Same reason mining.py forces it off.
_FORCED_SAFETY = {"stopOnInventoryFull": False}

# Net depth (19206/19208) and "carrying cargo" (19134). The engine posts these on load and after
# every login; on_event and the burst both key on them.
VARBIT_SUBSCRIPTIONS = [19206, 19208, 19134]

NET_NAME_FRAGMENT = "trawling net"
NET_OBJECT_IDS = tuple(range(59755, 59767))     # rope/linen/hemp/cotton x 2x5 + 3x8 port/starboard

# --------------------------------------------------------------------------- clock indirection

def _now():
    return time.time()


def _sleep(seconds):
    time.sleep(seconds)


# --------------------------------------------------------------------------- state


class TrawlSnapshot(NamedTuple):
    px: Optional[int]
    py: Optional[int]
    plane: Optional[int]
    boat_speed: float
    navigating: bool
    net: Optional[dict]
    net_depth: Optional[int]
    shoal: Optional[dict]
    shoal_distance: Optional[float]
    net_fish: Optional[int]
    cargo_used: Optional[int]
    cargo_capacity: Optional[int]
    ticks_since_sailing_xp: float
    in_route_area: bool


def get_config(bridge_config):
    """Plain DEFAULT_CONFIG -- this routine reads nothing from GET /config, so wiring it into the
    plugin needs only the RoutineChoice enum entry and controller.ROUTINE_FILES, no
    ControllerConfig section (CLAUDE.md, "The routine contract")."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["safety"] = dict(cfg.get("safety") or {})
    cfg["safety"].update(_FORCED_SAFETY)
    return cfg


def shoal_ids(cfg):
    return trawling_routes.all_shoal_ids(cfg["shoal"])


BOAT_MOVING_SPEED = 0.05     # tiles/tick below which the boat counts as stopped


def derive_phase(snap: TrawlSnapshot, cfg: dict) -> str:
    """Pure. Every phase comes from LIVE state, never from a stored step -- ctx.state.data is
    wiped on restart while the boat's position, its nets and its cargo are not."""
    if snap.net is None or snap.net_fish is None:
        return "lost"

    if snap.cargo_used is not None and snap.cargo_capacity is not None \
            and snap.cargo_used >= snap.cargo_capacity and cfg["stopWhenCargoFull"]:
        return "collect" if snap.net_fish > 0 else "done"

    if snap.net_fish >= cfg["netFullAt"]:
        return "collect"

    if snap.shoal is None:
        return "travel" if snap.in_route_area else "lost"

    if snap.boat_speed > BOAT_MOVING_SPEED:
        return "approach"
    if snap.shoal_distance is None or snap.shoal_distance > cfg["trawlRange"]:
        return "approach"

    if not snap.net_depth:
        return "lower"

    return "trawl"


# --------------------------------------------------------------------------- reading the world


def find_net(objects):
    """The trawling net facility on our own deck. Matched by ID first: /objects reports
    ObjectComposition.getName() raw, so a name can carry a <col=...> tag (the shoal's does), and
    the 12 net variants (4 tiers x 2x5 / 3x8-port / 3x8-starboard) are all tabulated. The name
    fragment is only a fallback for a variant the cache dump did not have."""
    for obj in objects or []:
        if obj.get("id") in NET_OBJECT_IDS:
            return obj
    for obj in objects or []:
        if NET_NAME_FRAGMENT in (obj.get("name") or "").lower():
            return obj
    return None


def find_shoal(objects, cfg, px, py):
    """The nearest shoal of the configured type, plus its distance from the player. Returns
    (None, None) when none is in the scene -- the shoal is a 6x6 loc out on the ocean and
    /objects walks the top-level scene as well as the boat's nested WorldView, so both the net
    and the shoal come out of one call in comparable top-level world coordinates."""
    wanted = shoal_ids(cfg)
    best = None
    best_dist = None
    for obj in objects or []:
        if obj.get("id") not in wanted:
            continue
        if px is None or obj.get("x") is None:
            continue
        dist = ((obj["x"] - px) ** 2 + (obj["y"] - py) ** 2) ** 0.5
        if best_dist is None or dist < best_dist:
            best, best_dist = obj, dist
    return best, best_dist


def _built_net_depth(trawling):
    """The depth of the net slot that is actually built. `nets` reports both facility slots and
    an unbuilt one reads hotspotId 0 -- reading slot 0 blindly would call a built starboard net
    'raised' forever."""
    for entry in (trawling.get("nets") or []):
        if entry.get("hotspotId"):
            return entry.get("depth")
    return None


def read_snapshot(ctx, cfg, ticks_since_sailing_xp: float = 0.0) -> TrawlSnapshot:
    player = controller.get_player() or {}
    px, py, plane = player.get("x"), player.get("y"), player.get("plane")

    sail = controller.api_get("/sailing") or {}
    objects = controller.get_objects() or []
    trawling = controller.get_trawling() or {}

    net = find_net(objects)
    shoal, shoal_dist = find_shoal(objects, cfg, px, py)
    cargo = trawling.get("cargo") or {}

    return TrawlSnapshot(
        px=px, py=py, plane=plane,
        boat_speed=float(sail.get("boatSpeed") or 0.0),
        navigating=bool(sail.get("navigating")),
        net=net,
        net_depth=_built_net_depth(trawling),
        shoal=shoal, shoal_distance=shoal_dist,
        net_fish=trawling.get("netFish"),
        cargo_used=cargo.get("used"), cargo_capacity=cargo.get("capacity"),
        ticks_since_sailing_xp=ticks_since_sailing_xp,
        in_route_area=(px is not None and trawling_routes.in_route_area(
            cfg["shoal"], cfg["route"], px, py)),
    )


def net_refresh_target(net):
    """A refresh_target for the net. The boat is stopped while trawling, so the net's top-level
    world tile is stable and /object/at can re-resolve its projection as the camera moves."""
    return controller.object_refresh_target(net["x"], net["y"], net.get("plane", 0), net["id"])
