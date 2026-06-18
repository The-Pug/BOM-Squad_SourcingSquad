import json

from catalog import (
    QUANTITY_NEEDED,
    compatibility_issues,
    describe,
    find,
    line_total,
    options_scored,
    reoptimize,
    total_cost,
)
from room import Message

BUILD_DEFINING = "flight_controller"


class BaseAgent:
    def __init__(self, name, room, llm):
        self.name = name
        self.room = room
        self.llm = llm
        room.join(name)

    def say(self, to, kind, body):
        self.room.post(Message(self.name, to, kind, body))


class IntakeAgent(BaseAgent):
    def translate(self, raw_input):
        system = (
            "You convert a hardware build request or parts list into structured JSON. "
            "Return JSON with keys: project (string), parts (list of {category, name, qty}). "
            "Use an empty name when the user gave only a category without a specific product."
        )
        return self.llm.ask_json("translator", system, raw_input)

    def build_profile(self, raw_input):
        system = (
            "You turn a hardware build request into procurement requirements. "
            "Return JSON with keys: project (string), budget_inr (integer), "
            "categories (list from frame, motor, esc, flight_controller, propeller, battery, "
            "camera, vtx, receiver), profile (map of category to {min_rating, priority} where "
            "priority is performance, price, or balanced), notes (string)."
        )
        return self.llm.ask_json("intake", system, raw_input)

    def run(self, raw_input):
        translated = self.translate(raw_input)
        requirements = self.build_profile(raw_input)
        parts = translated["parts"]
        summary = (
            f"Project: {requirements['project']}\n"
            f"Budget: ₹{requirements['budget_inr']}\n"
            f"Parts requested: {len(parts)}\n"
            f"Notes: {requirements['notes']}"
        )
        self.say("Sourcing", "requirements", summary)
        return requirements, parts


class SourcingAgent(BaseAgent):
    def source(self, parts, profile, catalog):
        picks = {}
        specialist_candidates = None
        missing = []
        for item in parts:
            category = item["category"]
            name = (item.get("name") or "").strip()
            if name:
                part = find(catalog, name)
                if part:
                    picks[category] = part
                else:
                    missing.append(item)
                continue
            if category == BUILD_DEFINING:
                specialist_candidates = options_scored(catalog, category, profile)
                continue
            choices = options_scored(catalog, category, profile)
            if choices:
                picks[category] = choices[0]
            else:
                missing.append(item)
        lines = [describe(part) for part in picks.values()]
        rationale = self.llm.ask(
            "sourcing",
            "You explain a sourcing shortlist in two sentences.",
            "Shortlist: " + ", ".join(lines),
        )
        self.say("Trust", "shortlist", "\n".join(lines) + f"\n\n{rationale}")
        return picks, specialist_candidates, missing

    def recruit_specialist(self, candidates):
        names = ", ".join(f"{p['brand']} {p['name']} (₹{p['price_inr']})" for p in candidates)
        self.say(
            "Specialist",
            "recruitment_request",
            f"The flight controller shapes the whole build and I have {len(candidates)} candidates: "
            f"{names}. Requesting a specialist to validate fit before we commit.",
        )

    def recruit_replacement(self, item):
        self.say(
            "Replacement Scout",
            "recruitment_request",
            f"Could not source a {item['category']} named '{item.get('name', '')}'. "
            f"Requesting a substitute suggestion.",
        )


class SpecialistAgent(BaseAgent):
    def validate(self, candidates, picks, notes):
        menu = "\n".join(
            f"- {p['brand']} {p['name']} | ₹{p['price_inr']} | mount {p['mount']} | {p['specs']}"
            for p in candidates
        )
        system = (
            "You are a flight controller specialist for FPV drones. The build already has other "
            "parts chosen. Pick the one option that physically fits and serves the build. "
            "Return JSON with keys: choice (the option name), reason (two sentences)."
        )
        user = f"Options:\n{menu}\nBuild notes: {notes}"
        verdict = self.llm.ask_json("specialist", system, user)
        proposed = None
        for part in candidates:
            if part["name"].lower() in verdict["choice"].lower():
                proposed = part
        compatible = [c for c in candidates if not compatibility_issues({**picks, BUILD_DEFINING: c})]
        if proposed and proposed in compatible:
            chosen = proposed
        elif compatible:
            chosen = compatible[0]
        else:
            chosen = candidates[0]
        self.say("Sourcing", "specialist_verdict", f"Pick: {describe(chosen)}\n{verdict['reason']}")
        return chosen


class TrustAgent(BaseAgent):
    def review(self, picks):
        listing = ", ".join(f"{p['brand']} {p['name']}" for p in picks.values())
        system = (
            "You assess seller and product trust from reviews for a procurement build. "
            "Return JSON with keys: score (0 to 5 float), verdict (two sentences)."
        )
        report = self.llm.ask_json("trust", system, "Listings under review: " + listing)
        flag = "" if report["score"] >= 3.5 else "  ⚠ trust below threshold"
        self.say("Budget", "trust_review", f"Trust score: {report['score']}/5{flag}\n{report['verdict']}")
        return report["score"]


class BudgetAgent(BaseAgent):
    def review(self, picks, budget, catalog, profile):
        changes = []
        total = total_cost(picks)
        if total > budget:
            picks, changes = reoptimize(picks, budget, catalog, profile)
            total = total_cost(picks)
        issues = compatibility_issues(picks)
        margin = budget - total
        compat_line = "Compatibility: all rules pass" if not issues else "Compatibility FAIL: " + "; ".join(issues)
        deviation = "within budget" if margin >= 0 else f"₹{-margin} over budget after re-optimizing"
        commentary = self.llm.ask(
            "budget",
            "You are a budget reviewer. Summarize the review in two sentences.",
            f"Total {total} of {budget} INR, {deviation}. {compat_line}. "
            f"Changes made: {'; '.join(changes) if changes else 'none'}.",
        )
        change_block = ("\nRe-optimizer: " + "; ".join(changes)) if changes else ""
        report = (
            f"Total: ₹{total}  |  Budget: ₹{budget}  |  Margin: ₹{margin}\n"
            f"{compat_line}{change_block}\n{commentary}"
        )
        self.say("Human Desk", "budget_review", report)
        return picks, total, margin, issues, changes


class ReplacementAgent(BaseAgent):
    def suggest(self, item, picks, catalog):
        system = (
            "A part could not be sourced. Suggest one commonly used substitute part name. "
            "Return JSON with keys: suggestion (a product name), reason (two sentences)."
        )
        advice = self.llm.ask_json("replacement", system, f"Missing {item['category']}: {item.get('name', '')}")
        candidate = find(catalog, advice["suggestion"])
        ok = candidate and not compatibility_issues({**picks, item["category"]: candidate})
        verdict = (
            f"Suggested {advice['suggestion']} — passes compatibility checks."
            if ok
            else f"Suggested {advice['suggestion']} — but code rejected it; no compatible substitute in catalog."
        )
        self.say("Human Desk", "replacement_suggestion", f"{verdict}\n{advice['reason']}")
        return candidate if ok else None


class HumanDesk(BaseAgent):
    def __init__(self, name, room, llm, decider=None):
        super().__init__(name, room, llm)
        self.decider = decider

    def ask(self, prompt):
        if self.decider:
            return self.decider(prompt)
        return input(prompt).strip().lower().startswith("y")

    def approve_substitute(self, item, candidate):
        prompt = f"Use {candidate['brand']} {candidate['name']} as the {item['category']}? [y/n] "
        self.say("Room", "substitute_request", prompt.strip())
        return self.ask(prompt)

    def approve(self, total, budget, issues):
        if issues:
            self.say("Room", "compatibility_warning", "Build has unresolved compatibility issues: " + "; ".join(issues))
        self.say("Room", "approval_request", f"Crew recommends purchase at ₹{total} of ₹{budget}. Approve?")
        approved = self.ask("Approve this purchase order? [y/n] ")
        verdict = "APPROVED — drafting purchase order." if approved else "REJECTED — crew adjourned."
        self.say("Room", "human_decision", verdict)
        return approved


def write_purchase_order(path, request_text, picks, total, margin, issues, trust_score, changes):
    lines = [
        "# Purchase Order — BOM Squad",
        "",
        f"**Request:** {request_text}",
        "",
        "| Part | Brand | Qty | Line Total |",
        "|---|---|---|---|",
    ]
    for part in picks.values():
        qty = QUANTITY_NEEDED[part["category"]]
        lines.append(f"| {part['name']} | {part['brand']} | {qty} | ₹{line_total(part)} |")
    lines += [
        "",
        f"**Total: ₹{total}**  ·  Margin left: ₹{margin}",
        f"**Trust score:** {trust_score}/5",
        f"**Compatibility:** {'all rules pass' if not issues else '; '.join(issues)}",
    ]
    if changes:
        lines.append("**Re-optimizer:** " + "; ".join(changes))
    lines += [
        "",
        "Prepared by Intake, Sourcing, Trust, Specialist and Budget agents. Approved by Human Desk.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return json.dumps({"total": total, "items": len(picks)})
