"""Which runes can pay for an elemental rune cost.

FOUND LIVE 2026-09-21, one command before the first live run of routines/slayer.py: the account
carries 11,477 DUST runes and zero plain air runes, and `npc_contact.missing_requirements`
answered "NPC Contact needs 2 air rune(s) per cast (have 0)". A dust rune IS an air rune and an
earth rune, so the cast was affordable and the routine refused to make it -- the run would have
stopped on its very first tick.

Both rune checks in this codebase had the same gap (`npc_contact.RUNES_PER_CAST` and
`slayer_routes.missing_runes`), so the knowledge lives here once rather than in each of them.

Staves are NOT handled here. A staff supplies unlimited runes of its element but only while WORN,
which is a live equipment question rather than an inventory one -- npc_contact already answers it
separately, and conflating the two would let a banked staff pay for a cast.
"""

#: The six combination runes and the two elements each one supplies. OSRS combination runes are
#: exactly these; nothing combines into astral, cosmic, law, nature or any other rune, which is
#: why a lookup for one of those must never fall back to a combination.
COMBINATIONS = {
    "mist rune": ("air", "water"),
    "dust rune": ("air", "earth"),
    "mud rune": ("water", "earth"),
    "smoke rune": ("air", "fire"),
    "steam rune": ("water", "fire"),
    "lava rune": ("earth", "fire"),
}

ELEMENTS = ("air", "water", "earth", "fire")


def available(carried, rune_name) -> int:
    """How many of `rune_name` the account can actually spend, combinations included.

    `carried` is a {lowercased name: quantity} map of the kind slayer_loadout and npc_contact
    already build. An unknown name, an empty map or None all answer 0 -- this can only ever report
    MORE than a naive count, never less, so it cannot make an affordable cast look unaffordable.
    """
    counts = {str(k).lower(): v for k, v in (carried or {}).items()}
    wanted = str(rune_name or "").lower()
    total = int(counts.get(wanted) or 0)

    element = wanted[:-len(" rune")] if wanted.endswith(" rune") else wanted
    if element not in ELEMENTS:
        return total
    for combo, elements in COMBINATIONS.items():
        if element in elements:
            total += int(counts.get(combo) or 0)
    return total
