import csv
from pathlib import Path

QUANTITY_NEEDED = {
    "frame": 1,
    "motor": 4,
    "esc": 1,
    "flight_controller": 1,
    "propeller": 2,
    "battery": 1,
    "camera": 1,
    "vtx": 1,
    "receiver": 1,
}

PRIORITY_WEIGHTS = {
    "performance": (0.8, 0.2),
    "price": (0.2, 0.8),
    "balanced": (0.5, 0.5),
}


def to_int(value):
    value = value.strip()
    return int(value) if value else 0


def to_float(value):
    value = value.strip()
    return float(value) if value else 0.0


def load_catalog():
    path = Path(__file__).parent / "data" / "parts.csv"
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["price_inr"] = to_int(row["price_inr"])
            row["cells"] = to_int(row["cells"])
            row["rating"] = to_float(row["rating"])
            row["cell_min"] = to_int(row["cell_min"])
            row["cell_max"] = to_int(row["cell_max"])
            row["prop_in"] = to_float(row["prop_in"])
            row["frame_prop_max"] = to_float(row["frame_prop_max"])
            row["current_a"] = to_float(row["current_a"])
            rows.append(row)
    return rows


def line_total(part):
    return part["price_inr"] * QUANTITY_NEEDED[part["category"]]


def describe(part):
    qty = QUANTITY_NEEDED[part["category"]]
    return f"{part['brand']} {part['name']}  x{qty}  =  ₹{line_total(part)}"


def find(catalog, name):
    name = name.lower().strip()
    for part in catalog:
        if part["name"].lower() == name:
            return part
    for part in catalog:
        full = f"{part['brand']} {part['name']}".lower()
        if part["name"].lower() in name or name in full:
            return part
    return None


def in_stock(catalog, category):
    return [r for r in catalog if r["category"] == category and r["stock"] == "yes"]


def options_for(catalog, category):
    return sorted(in_stock(catalog, category), key=lambda r: r["price_inr"])


def score(part, group, priority):
    rating_weight, price_weight = PRIORITY_WEIGHTS.get(priority, PRIORITY_WEIGHTS["balanced"])
    top_price = max(p["price_inr"] for p in group)
    rating_part = part["rating"] / 5.0
    price_part = 1.0 - (part["price_inr"] / top_price if top_price else 0.0)
    return rating_weight * rating_part + price_weight * price_part


def options_scored(catalog, category, profile):
    rule = (profile or {}).get(category, {})
    min_rating = rule.get("min_rating", 0.0)
    priority = rule.get("priority", "balanced")
    group = [p for p in in_stock(catalog, category) if p["rating"] >= min_rating]
    if not group:
        group = in_stock(catalog, category)
    return sorted(group, key=lambda p: score(p, group, priority), reverse=True)


def cells_in_range(cells, part):
    return part["cell_min"] <= cells <= part["cell_max"]


def rule_battery_esc_cells(picks):
    battery, esc = picks.get("battery"), picks.get("esc")
    if battery and esc and not cells_in_range(battery["cells"], esc):
        return f"{battery['cells']}S battery is outside the ESC range {esc['cell_min']}-{esc['cell_max']}S"
    return None


def rule_battery_motor_cells(picks):
    battery, motor = picks.get("battery"), picks.get("motor")
    if battery and motor and not cells_in_range(battery["cells"], motor):
        return f"{battery['cells']}S battery is outside the motor range {motor['cell_min']}-{motor['cell_max']}S"
    return None


def rule_fc_esc_mount(picks):
    fc, esc = picks.get("flight_controller"), picks.get("esc")
    if fc and esc and fc["mount"] != esc["mount"]:
        return f"flight controller mount {fc['mount']} does not match ESC mount {esc['mount']}"
    return None


def rule_fc_frame_mount(picks):
    fc, frame = picks.get("flight_controller"), picks.get("frame")
    if fc and frame and fc["mount"] != frame["mount"]:
        return f"flight controller mount {fc['mount']} does not fit frame mount {frame['mount']}"
    return None


def rule_prop_frame_size(picks):
    prop, frame = picks.get("propeller"), picks.get("frame")
    if prop and frame and prop["prop_in"] > frame["frame_prop_max"]:
        return f"{prop['prop_in']}-inch prop exceeds frame limit of {frame['frame_prop_max']} inch"
    return None


def rule_esc_motor_current(picks):
    esc, motor = picks.get("esc"), picks.get("motor")
    if esc and motor and esc["current_a"] < motor["current_a"]:
        return f"ESC rated {esc['current_a']}A is below motor draw {motor['current_a']}A"
    return None


def rule_battery_esc_connector(picks):
    battery, esc = picks.get("battery"), picks.get("esc")
    if battery and esc and battery["connector"] and esc["connector"] and battery["connector"] != esc["connector"]:
        return f"battery connector {battery['connector']} does not match ESC connector {esc['connector']}"
    return None


COMPATIBILITY_RULES = [
    rule_battery_esc_cells,
    rule_battery_motor_cells,
    rule_fc_esc_mount,
    rule_fc_frame_mount,
    rule_prop_frame_size,
    rule_esc_motor_current,
    rule_battery_esc_connector,
]


def compatibility_issues(picks):
    issues = []
    for rule in COMPATIBILITY_RULES:
        issue = rule(picks)
        if issue:
            issues.append(issue)
    return issues


def total_cost(picks):
    return sum(line_total(part) for part in picks.values())


def reoptimize(picks, budget, catalog, profile):
    picks = dict(picks)
    changes = []
    while total_cost(picks) > budget:
        best = None
        for category, current in picks.items():
            priority = (profile or {}).get(category, {}).get("priority", "balanced")
            group = options_scored(catalog, category, profile)
            for candidate in group:
                if line_total(candidate) >= line_total(current):
                    continue
                trial = dict(picks)
                trial[category] = candidate
                if compatibility_issues(trial):
                    continue
                savings = line_total(current) - line_total(candidate)
                loss = score(current, group, priority) - score(candidate, group, priority)
                ratio = savings / (loss + 0.01)
                if best is None or ratio > best["ratio"]:
                    best = {"category": category, "current": current, "candidate": candidate,
                            "savings": savings, "ratio": ratio}
        if best is None:
            break
        picks[best["category"]] = best["candidate"]
        changes.append(
            f"swapped {best['current']['brand']} {best['current']['name']} for "
            f"{best['candidate']['brand']} {best['candidate']['name']} (saved ₹{best['savings']})"
        )
    return picks, changes
