"""Which Motherlode Mine tiles are on the UPPER level.

VENDORED from the Plugin Hub plugin "Motherlode Mine Improved"
(tictac7x-motherlode, commit 2909d5622024a4f011d10eea9c513aa6435e393c,
src/main/java/tictac7x/motherlode/sectors/Sectors.java) -- the union of its six UPSTAIRS_*
sector arrays, transcribed mechanically rather than by hand. That repo has no LICENSE file, the
same footing as the m8aq readers and the Shortest Path transport tables already vendored here.

WHY THIS EXISTS, and when it is used. The mine's upper level sits DIRECTLY ABOVE the lower one
at the same world x/y, so no coordinate is unique to a floor and /objects lists both. The real
answer is the tile's RENDER HEIGHT (RuneLite's own MotherlodePlugin: getTileHeight < -490), and
routines/motherlode_mine.py uses that whenever the bridge reports `tileHeight`.

This table is the FALLBACK for a client that predates that field. It is a hand-authored list
made for drawing overlays, not a survey, so treat it as advisory:

  * A genuinely upstairs tile missing from it reads as downstairs, and the routine simply skips
    that vein -- fewer veins, never a wrong click.
  * A downstairs tile wrongly present would let one bad click through, which
    motherlode_mine.note_vein_failure sets aside after two dead attempts.

MEASURED against the first live run 2026-09-07: it calls (3749,5685) -- the downstairs vein that
cost 90 seconds of dead clicks -- correctly downstairs, and every vein that mined fine that
morning correctly upstairs.
"""

# {x: (y, ...)} rather than a set of pairs: smaller to read in a diff, and the routine builds
# the set once on import.
_UPSTAIRS_BY_X = {
    3734: (5685,),
    3735: (5684, 5685, 5686,),
    3736: (5685,),
    3737: (5682, 5683, 5684, 5685,),
    3738: (5682, 5683, 5684, 5685,),
    3739: (5683, 5684, 5685,),
    3740: (5684, 5685, 5686,),
    3741: (5683, 5684, 5685, 5686,),
    3742: (5683, 5684, 5685, 5686,),
    3743: (5684, 5686, 5687,),
    3744: (5686, 5687,),
    3745: (5684, 5685, 5686,),
    3746: (5684, 5685, 5686,),
    3747: (5683, 5685, 5686,),
    3748: (5682, 5683, 5684, 5685,),
    3749: (5682, 5683, 5684,),
    3750: (5682, 5683, 5684, 5685,),
    3751: (5678, 5680, 5681, 5683, 5684,),
    3752: (5676, 5677, 5678, 5679, 5680, 5681, 5682, 5683, 5684,),
    3753: (5676, 5677, 5678, 5680, 5682, 5683, 5684,),
    3754: (5675, 5676, 5677, 5678, 5682, 5683,),
    3755: (5675, 5676, 5677, 5681, 5682, 5683,),
    3756: (5674, 5675, 5676, 5677, 5678, 5679, 5681, 5682, 5683, 5684,),
    3757: (5673, 5674, 5675, 5676, 5677, 5678, 5679, 5680, 5681, 5682, 5683, 5684,),
    3758: (5673, 5675, 5677, 5678, 5680, 5681, 5683, 5685,),
    3759: (5672, 5673, 5674, 5676, 5677, 5678, 5682, 5683, 5684, 5685,),
    3760: (5671, 5672, 5673, 5674, 5677, 5678, 5679, 5680, 5683, 5684, 5685,),
    3761: (5660, 5661, 5662, 5663, 5664, 5668, 5669, 5670, 5671, 5672, 5673, 5674, 5675, 5676, 5677, 5678, 5679, 5680, 5681, 5682, 5683, 5684, 5685,),
    3762: (5657, 5658, 5660, 5661, 5662, 5663, 5664, 5665, 5667, 5668, 5670, 5671, 5672, 5673, 5675, 5676, 5677, 5678, 5679, 5680, 5682, 5683,),
    3763: (5656, 5657, 5658, 5659, 5660, 5662, 5663, 5665, 5666, 5667, 5668, 5670, 5678, 5679, 5681, 5682, 5683,),
    3764: (5656, 5657, 5658, 5659, 5665, 5667, 5668, 5669, 5670, 5677, 5678, 5679, 5680, 5681, 5682, 5683,),
    3765: (5656, 5657, 5658, 5677, 5678, 5679,),
    3766: (5657, 5678,),
}

UPSTAIRS_TILES = frozenset((x, y) for x, ys in _UPSTAIRS_BY_X.items() for y in ys)


def is_upstairs_tile(x, y) -> bool:
    """True when (x, y) is on the upper level per the vendored table. See the module docstring
    for why this is a fallback and not the answer."""
    return (x, y) in UPSTAIRS_TILES
