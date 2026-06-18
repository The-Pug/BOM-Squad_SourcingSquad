import os
import re
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from catalog import (
    compatibility_issues,
    describe,
    find,
    load_catalog,
    options_scored,
    reoptimize,
    total_cost,
)

try:
    CATALOG = load_catalog()
except Exception:
    CATALOG = []

CATEGORY_MAP = {
    "frame": "frame",
    "frames": "frame",
    "motor": "motor",
    "motors": "motor",
    "esc": "esc",
    "escs": "esc",
    "flight controller": "flight_controller",
    "flight_controller": "flight_controller",
    "fc": "flight_controller",
    "propeller": "propeller",
    "propellers": "propeller",
    "prop": "propeller",
    "props": "propeller",
    "battery": "battery",
    "batteries": "battery",
    "lipo": "battery",
    "camera": "camera",
    "cam": "camera",
    "fpv camera": "camera",
    "vtx": "vtx",
    "video transmitter": "vtx",
    "receiver": "receiver",
    "rx": "receiver",
}


def norm_category(category):
    key = category.strip().lower().replace("-", " ")
    return CATEGORY_MAP.get(key, key)


def resolve(part_names):
    picks = {}
    for raw in part_names.replace("\n", ",").split(","):
        name = raw.strip()
        if not name:
            continue
        part = find(CATALOG, name)
        if part:
            picks[part["category"]] = part
    return picks


def search_model():
    return ChatGoogleGenerativeAI(
        model=os.getenv("LLM_MODEL", "gemini-2.5-flash"),
        google_api_key=os.getenv("LLM_API_KEY"),
        temperature=0.2,
    ).bind_tools([{"google_search": {}}])


@tool
def search_parts(category: str, requirements: str) -> str:
    """Search the live web for real, currently purchasable parts in the given category that meet the requirements. Returns real candidate products with brand and model, approximate price in INR, the specs that matter for compatibility, and the source. Use this to fetch real parts for any kind of build instead of guessing."""
    model = search_model()
    prompt = (
        f"Find 3 to 5 real, currently purchasable {category} products that meet these needs: {requirements}. "
        "For each product give one line: brand and model, approximate price in INR, the two or three specs that matter for fitting it into a build, and the store or source. "
        "Only list products you can actually find. If a price is uncertain, write 'price unverified'. No preamble, just the list."
    )
    response = model.invoke(prompt)
    content = response.content
    return content if isinstance(content, str) else str(content)


@tool
def shortlist(category: str, min_rating: float = 0.0, priority: str = "balanced") -> str:
    """List the best in-stock options for a part category. Drops anything below min_rating and sorts by priority (performance, price, or balanced). Always use this to find real parts and prices instead of guessing."""
    category = norm_category(category)
    profile = {category: {"min_rating": min_rating, "priority": priority}}
    options = options_scored(CATALOG, category, profile)
    if not options:
        return f"No in-stock options found for {category}."
    return "\n".join(
        f"{p['brand']} {p['name']} | rating {p['rating']}/5 | {describe(p)} | mount {p['mount']} | {p['specs']}"
        for p in options[:5]
    )


@tool
def build_shortlist(categories: str, defining_category: str, min_rating: float = 0.0, priority: str = "balanced") -> str:
    """Build the whole shortlist in one call. Pass the comma-separated list of part categories the build needs and the build-defining category (the part everything else must fit). Returns the single best in-stock pick for every other category, plus the candidates for the build-defining component for the Specialist to choose from. Use this once instead of calling shortlist many times."""
    wanted = [norm_category(c) for c in categories.split(",") if c.strip()]
    defining = norm_category(defining_category)
    lines = []
    for category in wanted:
        if category == defining:
            continue
        options = options_scored(CATALOG, category, {category: {"min_rating": min_rating, "priority": priority}})
        if options:
            lines.append(f"{category}: {describe(options[0])}")
        else:
            lines.append(f"{category}: no in-stock option found")
    candidates = options_scored(CATALOG, defining, {defining: {"min_rating": min_rating, "priority": priority}})
    if candidates:
        lines.append(f"{defining} candidates: " + ", ".join(f"{p['brand']} {p['name']} (₹{p['price_inr']})" for p in candidates[:3]))
    else:
        lines.append(f"{defining} candidates: none found")
    return "\n".join(lines)


@tool
def finalize_budget(part_names: str, budget: int) -> str:
    """Finalise the order in one call: total the cart, check compatibility, and if it is over budget swap in cheaper compatible parts. Returns the final bill. Pass part names separated by commas and the budget in INR."""
    picks = resolve(part_names)
    if not picks:
        return "No matching parts found."
    total = total_cost(picks)
    changes = []
    if total > budget:
        picks, changes = reoptimize(picks, budget, CATALOG, {})
        total = total_cost(picks)
    issues = compatibility_issues(picks)
    margin = budget - total
    lines = "\n".join(describe(p) for p in picks.values())
    result = f"{lines}\n\nTotal: ₹{total} | Budget: ₹{budget} | Margin: ₹{margin}"
    result += "\nCompatibility: " + ("all rules pass" if not issues else "; ".join(issues))
    if changes:
        result += "\nRe-optimizer: " + "; ".join(changes)
    return result


@tool
def live_shortlist(categories: str, defining_category: str, requirements: str) -> str:
    """Build the whole live shortlist in ONE call. Pass the comma-separated categories the build needs as name:units (units default 1), the build-defining category, and a short requirements description. For each category it searches the live web for real purchasable parts and returns the best pick as 'category: brand model | ₹price | xunits | specs', plus 3 candidates for the build-defining component. Use this once instead of searching each category yourself."""
    defining = defining_category.strip()
    wants = []
    for raw in categories.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ":" in raw:
            name, quantity = raw.split(":", 1)
            name, quantity = name.strip(), quantity.strip()
            quantity = quantity if quantity.isdigit() else "1"
        else:
            name, quantity = raw, "1"
        wants.append((name, quantity))
    spec = "\n".join(
        (f"- {name}: 3 candidate products" if name.lower() == defining.lower() else f"- {name}: the 1 best product")
        + f", each as '{name}: brand and model | ₹unit price in INR | x{quantity} | key specs | source: store name or link'"
        for name, quantity in wants
    )
    prompt = (
        f"For this build: {requirements}\n"
        f"Find real, currently purchasable products for these parts:\n{spec}\n"
        "Use only real products with real prices found on the web. One product per line, in the exact format shown. "
        "If a price is uncertain write 'price unverified'. No preamble, no headings, just the lines."
    )
    try:
        response = search_model().invoke(prompt)
        text = response.content if isinstance(response.content, str) else str(response.content)
        text = text.strip()
    except Exception as error:
        return f"Live search temporarily unavailable ({type(error).__name__}); no parts retrieved."
    return text or "No parts found."


def parse_line(line):
    amounts = re.findall(r"₹\s*([\d,]+)", line)
    if not amounts:
        return None
    category = line.split(":")[0].strip()
    unit = int(amounts[-1].replace(",", ""))
    quantity_match = re.search(r"[x×X]\s*(\d+)", line)
    quantity = int(quantity_match.group(1)) if quantity_match else 1
    return [category, unit, quantity, line]


def cheaper_alternative(category, max_price, quantity, requirements):
    prompt = (
        f"For this build: {requirements}. Find ONE real, currently purchasable {category} "
        f"that costs clearly less than ₹{max_price} per unit but still fits the build. "
        f"Reply with exactly one line: {category}: brand and model | ₹unit price in INR | "
        f"x{quantity} | key specs. Only a real product with a real price you can find; if "
        "nothing cheaper exists, reply with the single word none."
    )
    try:
        response = search_model().invoke(prompt)
        text = response.content if isinstance(response.content, str) else str(response.content)
    except Exception:
        return None
    text = text.strip().split("\n")[0]
    parsed = parse_line(text)
    if not parsed or parsed[1] >= max_price:
        return None
    return parsed


@tool
def finalize_budget_live(cart: str, budget: int, requirements: str = "") -> str:
    """Total a cart of live-sourced parts in code, check it against the budget, and if it is over budget re-optimize: swap the most expensive part for a cheaper real alternative in the same category, repeating until within budget or no cheaper option remains. Pass one part per line (each with its unit price as ₹NNN and quantity as xN), the budget in INR, and a short build description as requirements (so swaps stay on-topic). Returns the final bill, any swaps made, and the budget status. Never total the cart yourself."""
    items = [p for p in (parse_line(line.strip()) for line in cart.split("\n")) if p]
    if not items:
        return "No priced parts found in the cart; cannot total."

    def total():
        return sum(unit * quantity for _, unit, quantity, _ in items)

    def show(rows, rows_total):
        body = "\n".join(f"{line}  =>  ₹{unit * quantity}" for _, unit, quantity, line in rows)
        margin = budget - rows_total
        state = f"under budget by ₹{margin}" if margin >= 0 else f"OVER budget by ₹{-margin}"
        return f"{body}\nTotal: ₹{rows_total} | Budget: ₹{budget} | {state}"

    original = [row[:] for row in items]
    original_total = total()

    swaps = []
    guard = 0
    while total() > budget and requirements and guard < 6:
        guard += 1
        index = max(range(len(items)), key=lambda i: items[i][1] * items[i][2])
        category, unit, quantity, line = items[index]
        alternative = cheaper_alternative(category, unit, quantity, requirements)
        if not alternative:
            break
        swaps.append(
            f"swapped {line.split('|')[0].strip()} (₹{unit}) for "
            f"{alternative[3].split('|')[0].strip()} (₹{alternative[1]}), saved ₹{unit - alternative[1]}"
        )
        items[index] = alternative

    if not swaps:
        return show(items, total())
    return (
        "PRIMARY — as sourced, best quality:\n" + show(original, original_total)
        + "\n\nSECONDARY — re-optimized to fit budget:\n" + show(items, total())
        + "\n\nRe-optimizer swaps: " + "; ".join(swaps)
        + "\n\nYour call: reply APPROVE PRIMARY (pay over budget for the premium parts), "
        "APPROVE SECONDARY (stay within budget), or REJECT."
    )


def write_order(order):
    order = order.strip()
    links = []
    for line in order.split("\n"):
        low = line.lower()
        if "source:" in low:
            name = line.split("|")[0].strip()
            source = line[low.find("source:") + len("source:"):].strip()
            if source:
                links.append(f"- {name} — {source}")
    path = Path(__file__).parent / "approved_order.md"
    header = f"# Approved Purchase Order\n\nSaved: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    body = order
    if links:
        body += "\n\n## Where to buy\n" + "\n".join(links)
    path.write_text(header + body + "\n", encoding="utf-8")
    return f"Order saved to {path.name} with {len(links)} source link(s)."


@tool
def save_order(order: str) -> str:
    """Save the final approved purchase order to a file so the buyer keeps it with every part, price, and source link. Call this only after the human approves. Pass the exact chosen cart text (the part lines, with their source links if present). Returns the saved file path."""
    return write_order(order)


@tool
def compatibility_check(part_names: str) -> str:
    """Check whether a set of chosen parts physically work together. Pass the part names separated by commas. Returns the list of problems found, or confirms all rules pass."""
    picks = resolve(part_names)
    if not picks:
        return "No matching parts found."
    issues = compatibility_issues(picks)
    if issues:
        return "Compatibility problems:\n" + "\n".join(f"- {i}" for i in issues)
    return "All compatibility rules pass."


@tool
def price_total(part_names: str) -> str:
    """Total the real catalog price of a set of parts, counting the quantity needed of each. Pass the part names separated by commas."""
    picks = resolve(part_names)
    if not picks:
        return "No matching parts found."
    lines = "\n".join(describe(p) for p in picks.values())
    return f"{lines}\n\nTotal: ₹{total_cost(picks)}"


@tool
def reoptimize_cart(part_names: str, budget: int) -> str:
    """When a cart is over budget, swap parts for cheaper compatible ones, losing the least value and never breaking compatibility. Pass part names separated by commas and the budget in INR."""
    picks = resolve(part_names)
    if not picks:
        return "No matching parts found."
    new_picks, changes = reoptimize(picks, budget, CATALOG, {})
    result = "\n".join(describe(p) for p in new_picks.values())
    result += f"\n\nNew total: ₹{total_cost(new_picks)}"
    if changes:
        return result + "\nChanges: " + "; ".join(changes)
    return result + "\nNo compatible swap found to get under budget."
