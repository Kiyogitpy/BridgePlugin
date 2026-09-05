import gauntlet_ids as g
import gauntlet_kit as k

CFG = {"fish_target": 20, "egniol_target": 2, "shard_reserve": 0}


def inv(**items):
    return [{"slot": i, "name": n, "qty": q, "screenX": 1, "screenY": 1} for i, (n, q) in enumerate(items.items())]


def named(pairs):
    return [{"slot": i, "name": n, "qty": q, "screenX": 1, "screenY": 1} for i, (n, q) in enumerate(pairs)]


EQ_NONE = {"weapon": {"name": g.ITEM_SCEPTRE}, "helmet": None, "body": None, "legs": None}
EQ_T1 = {"weapon": {"name": "Corrupted bow (attuned)"}, "helmet": {"name": "Corrupted helm (basic)"},
         "body": {"name": "Corrupted body (basic)"}, "legs": {"name": "Corrupted legs (basic)"}}


def test_owned_tier_reads_inventory_and_worn():
    assert k.owned_tier("helm", [], EQ_NONE) == 0
    assert k.owned_tier("helm", [], EQ_T1) == 1
    assert k.owned_tier("bow", [], EQ_T1) == 2
    assert k.owned_tier("staff", inv(**{"Corrupted staff (perfected)": 1}), EQ_T1) == 3


def test_deficit_at_entry_is_the_whole_kit():
    d = k.kit_deficit(inv(**{g.ITEM_SCEPTRE: 1, g.ITEM_PESTLE: 1, g.ITEM_AXE: 1, g.ITEM_PICKAXE: 1, g.ITEM_HARPOON: 1, g.ITEM_TELEPORT: 1}), EQ_NONE, CFG)
    assert (d.ore, d.bark, d.linum) == (3, 3, 3)
    assert d.frames == 2
    assert d.fish == 20 and d.grym == 2
    # 150 (T1) + 100 (two attuned) + 2 * (10 vial + 10 dust) = 290
    assert d.shards == 290


def test_deficit_after_the_recordings_gather_phase():
    # REC at ~160 s: 7 ore/bark/linum, 8 raw fish, 2 grym, 2 frames, ~590 shards
    d = k.kit_deficit(named([(g.ITEM_ORE, 7), (g.ITEM_BARK, 7), (g.ITEM_LINUM, 7), (g.ITEM_RAW_FISH, 8),
                             (g.ITEM_GRYM, 2), (g.ITEM_FRAME, 2), (g.ITEM_SHARDS, 590)]), EQ_NONE, CFG)
    assert (d.ore, d.bark, d.linum, d.frames, d.grym, d.shards) == (0, 0, 0, 0, 0, 0)
    assert d.fish == 12                       # raw fish count toward the target
    assert not k.kit_minimum_met(named([(g.ITEM_ORE, 7)]), EQ_NONE, CFG)
    assert k.kit_minimum_met(named([(g.ITEM_ORE, 3), (g.ITEM_BARK, 3), (g.ITEM_LINUM, 3), (g.ITEM_FRAME, 2),
                                    (g.ITEM_SHARDS, 290), (g.ITEM_RAW_FISH, 20), (g.ITEM_GRYM, 2)]), EQ_NONE, CFG)


def test_deficit_counts_owned_pieces_and_potions():
    d = k.kit_deficit(named([("Egniol potion (3)", 1), (g.ITEM_FISH, 5), (g.ITEM_CFISH, 3)]), EQ_T1, CFG)
    assert (d.ore, d.bark, d.linum) == (0, 0, 0)         # T1 worn
    assert d.frames == 1 and d.shards == 50 + 10 + 10   # one attuned staff + one potion
    assert d.fish == 12 and d.grym == 1


def test_craft_plan_order_and_shard_budget():
    inv_ = named([(g.ITEM_ORE, 7), (g.ITEM_BARK, 7), (g.ITEM_LINUM, 7), (g.ITEM_RAW_FISH, 20),
                  (g.ITEM_GRYM, 2), (g.ITEM_FRAME, 2), (g.ITEM_SHARDS, 590), (g.ITEM_PESTLE, 1)])
    plan = k.craft_plan(inv_, EQ_NONE, CFG)
    ops = [s for s in plan]
    # vials first (potions need two), then the T1 set, then both weapons basic->attuned, then T2 cheapest
    assert ops[:2] == [("bowl", "vial"), ("bowl", "vial")]
    assert ops[2:5] == [("bowl", "helm"), ("bowl", "body"), ("bowl", "legs")]
    assert ops[5:9] == [("bowl", "bow"), ("bowl", "bow"), ("bowl", "staff"), ("bowl", "staff")]
    assert ("pump", None) in ops and ("pestle", None) in ops and ("potion", None) in ops and ("cook", None) in ops
    t2 = [a for o, a in ops[9:] if o == "bowl" and a in ("helm", "legs", "body")]
    assert t2[:2] == ["helm", "legs"]             # cheapest T2 first; body (100) after
    spent = 20 + 150 + 100 + 50 + 50 + 100 + 20   # vials, T1, attuned x2, T2 helm/legs/body, dust
    assert spent <= 590
    assert ("wear", "Corrupted helm (attuned)") in ops or ("wear", "Corrupted helm (basic)") in ops


def test_craft_plan_never_overspends():
    inv_ = named([(g.ITEM_ORE, 3), (g.ITEM_BARK, 3), (g.ITEM_LINUM, 3), (g.ITEM_FRAME, 2), (g.ITEM_SHARDS, 120)])
    plan = k.craft_plan(inv_, EQ_NONE, CFG)
    bowl = [a for o, a in plan if o == "bowl"]
    assert bowl == ["helm", "body", "bow", "staff"]     # 120 shards: 2x T1 armour + basic weapons only (basic = frame, no shards)
    assert ("bowl", "vial") not in plan           # no grym -> no potions
    # Basic weapons cost frames only; insufficient shards (20 left) prevents attune (need 50 each)
    assert plan.count(("bowl", "bow")) == 1 and plan.count(("bowl", "staff")) == 1


def test_gather_value_scores_deficit_only():
    d = k.Deficit(ore=3, bark=0, linum=1, shards=0, frames=1, fish=0, grym=0, orb=0, bowstring=0)
    deposit = next(n for n in g.RESOURCE_NODES.values() if "Deposit" in n)
    phren = next(n for n in g.RESOURCE_NODES.values() if "Phren" in n)
    objs = [{"name": deposit, "id": 1}, {"name": deposit, "id": 1}, {"name": phren, "id": 1}]
    assert k.gather_value(objs, [{"id": 9040}], d) == 3 * 3 + 1
