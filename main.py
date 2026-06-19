import argparse
from pathlib import Path
from web import app
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from agents import (
    BudgetAgent,
    HumanDesk,
    IntakeAgent,
    ReplacementAgent,
    SourcingAgent,
    SpecialistAgent,
    TrustAgent,
    write_purchase_order,
)
from catalog import load_catalog
from llm import LLM
from room import Room

DEFAULT_REQUEST = "Build me a racing-capable 5-inch FPV quad. Budget is 18000 INR."
ORDER_PATH = Path(__file__).parent / "purchase_order.md"


def run_crew(request_text, room, llm, catalog, decider=None):
    intake = IntakeAgent("Intake", room, llm)
    sourcing = SourcingAgent("Sourcing", room, llm)
    trust = TrustAgent("Trust", room, llm)
    budget = BudgetAgent("Budget", room, llm)
    human = HumanDesk("Human Desk", room, llm, decider=decider)

    requirements, parts = intake.run(request_text)
    picks, specialist_candidates, missing = sourcing.source(parts, requirements["profile"], catalog)

    for item in missing:
        sourcing.recruit_replacement(item)
        room.announce("Sourcing recruited Replacement Scout. A new agent is joining the room...")
        scout = ReplacementAgent("Replacement Scout", room, llm)
        candidate = scout.suggest(item, picks, catalog)
        if candidate and human.approve_substitute(item, candidate):
            picks[item["category"]] = candidate

    if specialist_candidates:
        sourcing.recruit_specialist(specialist_candidates)
        room.announce("Sourcing recruited a Specialist. A new agent is joining the room...")
        specialist = SpecialistAgent("Specialist", room, llm)
        picks["flight_controller"] = specialist.validate(specialist_candidates, picks, requirements["notes"])

    trust_score = trust.review(picks)
    picks, total, margin, issues, changes = budget.review(
        picks, requirements["budget_inr"], catalog, requirements["profile"]
    )

    approved = human.approve(total, requirements["budget_inr"], issues)
    if approved:
        write_purchase_order(ORDER_PATH, request_text, picks, total, margin, issues, trust_score, changes)
    return {"approved": approved, "total": total, "margin": margin, "order_path": str(ORDER_PATH)}


def main():
    parser = argparse.ArgumentParser(description="BOM Squad — a multi-agent procurement crew")
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    args = parser.parse_args()

    load_dotenv()
    console = Console()
    console.print(Panel(args.request, title="🛠️  New build request", border_style="bright_blue"))

    llm = LLM()
    catalog = load_catalog()
    room = Room("procurement-room")

    result = run_crew(args.request, room, llm, catalog)
    if result["approved"]:
        console.print(Panel(f"Purchase order saved to {result['order_path']}", title="✅ Done", border_style="green"))


if __name__ == "__main__":
    main()
