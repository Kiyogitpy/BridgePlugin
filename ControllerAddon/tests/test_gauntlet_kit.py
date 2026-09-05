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


def _inv_from_dict(d):
    return [{"slot": i, "name": n, "qty": q, "screenX": 1, "screenY": 1}
            for i, (n, q) in enumerate(d.items()) if q]


def _reverse_armour(name):
    for kind in g.ARMOUR_KINDS:
        for tier in (1, 2, 3):
            if name == g.armour_name(kind, tier):
                return kind, tier
    return None


def _reverse_weapon(name):
    for kind in ("bow", "staff"):
        for tier in (1, 2, 3):
            if name == g.weapon_name(kind, tier):
                return kind, tier
    return None


def _apply_step(inv, eq, step):
    """Mirrors the real game effect of one craft_plan step on a plain {name: qty} inventory
    dict, per the brief's spec (C1, final-fix-brief.md). Used to drive craft_plan repeatedly and
    assert it terminates -- a stand-in for craft_phase only ever executing plan[0] and re-planning
    from the live inventory every call."""
    op, arg = step

    def take(name, n=1):
        inv[name] = inv.get(name, 0) - n

    def give(name, n=1):
        inv[name] = inv.get(name, 0) + n

    if op == "bowl" and arg == "vial":
        take(g.ITEM_SHARDS, k.VIAL_SHARDS); give(g.ITEM_VIAL)
    elif op == "pump":
        n = inv.get(g.ITEM_VIAL, 0)
        inv[g.ITEM_VIAL] = 0
        give(g.ITEM_WATER_VIAL, n)
    elif op == "pestle":
        take(g.ITEM_SHARDS, k.DUST_SHARDS); give(g.ITEM_DUST, 10)
    elif op == "potion":
        while inv.get(g.ITEM_GRYM, 0) > 0 and inv.get(g.ITEM_WATER_VIAL, 0) > 0:
            take(g.ITEM_GRYM); take(g.ITEM_WATER_VIAL); give(g.ITEM_GRYM_UNF)
        while inv.get(g.ITEM_GRYM_UNF, 0) > 0 and inv.get(g.ITEM_DUST, 0) >= 10:
            take(g.ITEM_GRYM_UNF); take(g.ITEM_DUST, 10); give("Egniol potion (3)")
    elif op == "cook":
        n = inv.get(g.ITEM_RAW_FISH, 0)
        inv[g.ITEM_RAW_FISH] = 0
        give(g.ITEM_FISH, n)
    elif op == "bowl" and arg == "cfish":
        take(g.ITEM_SHARDS, k.CFISH_SHARDS); take(g.ITEM_FISH); give(g.ITEM_CFISH)
    elif op == "bowl" and arg in g.ARMOUR_KINDS:
        inv_list = _inv_from_dict(inv)
        tier = k.owned_tier(arg, inv_list, eq) + 1
        o, b, l, s = k.ARMOUR_COST[(arg, tier)]
        take(g.ITEM_ORE, o); take(g.ITEM_BARK, b); take(g.ITEM_LINUM, l); take(g.ITEM_SHARDS, s)
        if tier > 1:
            take(g.armour_name(arg, tier - 1))
        give(g.armour_name(arg, tier))
    elif op == "bowl" and arg in ("bow", "staff"):
        inv_list = _inv_from_dict(inv)
        tier = k.owned_tier(arg, inv_list, eq) + 1
        if tier == 1:
            take(g.ITEM_FRAME)
        elif tier == 2:
            take(g.ITEM_SHARDS, k.ATTUNE_SHARDS); take(g.weapon_name(arg, 1))
        elif tier == 3:
            take(g.ITEM_ORB if arg == "staff" else g.ITEM_BOWSTRING)
            take(g.weapon_name(arg, 2))
        give(g.weapon_name(arg, tier))
    elif op == "wear":
        pair = _reverse_armour(arg) or _reverse_weapon(arg)
        assert pair is not None, f"apply_step: unrecognised wear target {arg}"
        kind, tier = pair
        assert inv.get(arg, 0) > 0, f"apply_step: wearing {arg} we don't hold"
        take(arg)
        eq[k.EQUIP_SLOT[kind]] = {"name": arg}
    else:
        raise AssertionError(f"apply_step: unhandled op {step}")


def test_craft_plan_is_idempotent_and_terminates(monkeypatch):
    """C1: craft_phase only ever acts on plan[0] and re-plans from the live inventory every call
    -- so craft_plan must never keep re-issuing a step whose effect is already sitting in the
    inventory (the vial/cfish bug). Drives craft_plan/apply_step exactly the way craft_phase
    does, starting from the recording's ~160s inventory."""
    inv = {g.ITEM_ORE: 7, g.ITEM_BARK: 7, g.ITEM_LINUM: 7, g.ITEM_RAW_FISH: 20,
           g.ITEM_GRYM: 2, g.ITEM_FRAME: 2, g.ITEM_SHARDS: 590, g.ITEM_PESTLE: 1}
    eq = dict(EQ_NONE)
    steps_taken = []
    for _ in range(40):
        plan = k.craft_plan(_inv_from_dict(inv), eq, CFG)
        if not plan:
            break
        step = plan[0]
        steps_taken.append(step)
        _apply_step(inv, eq, step)
    else:
        raise AssertionError(f"craft_plan did not terminate within 40 steps: {steps_taken}")

    final_inv = _inv_from_dict(inv)
    assert sum(inv.get(dose, 0) for dose in g.EGNIOL_DOSES) == 2
    for kind in g.ARMOUR_KINDS:
        assert k.owned_tier(kind, final_inv, eq) >= 1, f"{kind} never reached T1"
    for kind in ("bow", "staff"):
        assert k.owned_tier(kind, final_inv, eq) >= 2, f"{kind} never reached attuned"
    assert inv.get(g.ITEM_CFISH, 0) <= 4
    assert inv.get(g.ITEM_VIAL, 0) == 0
