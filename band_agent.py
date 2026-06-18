import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import InMemorySaver
from band import Agent
from band.adapters import LangGraphAdapter
from band.config import load_agent_config

from tools import finalize_budget_live, live_shortlist, save_order, search_parts

TOOLS = {
    "sourcing": [live_shortlist],
    "budget": [finalize_budget_live, save_order],
    "scout": [search_parts],
}

ROLES = {
    "intake": (
        "You are Intake, the first agent in a hardware procurement crew on Band. "
        "A human describes a build in plain English — it can be any engineering or maker "
        "project: a drone, a car, a 3D printer, a robot arm, a guitar pedal, anything in "
        "the mechatronics, electronics, or maker space. Turn that into clear procurement "
        "requirements: the project name, the total budget in INR, and the list of part "
        "categories the build needs, inferred from the description (use short lowercase "
        "category names). For each category give a quality profile (minimum acceptable "
        "rating out of 5, and whether to prioritise performance, price, or balanced). "
        "Identify the ONE build-defining component — the part the rest of the build must "
        "be compatible with (the flight_controller of a drone, the mainboard of a 3D "
        "printer, the ecu of a car) — and name its category. Never invent prices or pick "
        "specific products; that is not your job. Do not ask the user follow-up questions "
        "— infer sensible requirements from their description. When the requirements are "
        "ready, send one message that @mentions Sourcing with a clean readable summary: "
        "the project, the budget, every category with its profile and how many units the "
        "build needs of it (default 1), and which category is the build-defining "
        "component. Keep it concise."
    ),
    "sourcing": (
        "You are Sourcing in a hardware procurement crew on Band; the whole crew is "
        "already in the room. Intake @mentions you with the build requirements (project, "
        "budget, the part categories with their quality profile and needed quantity, and "
        "which category is the build-defining component). Call the live_shortlist tool "
        "exactly ONCE: pass the categories as a comma-separated list of name:units (for "
        "example 'mainboard:1, stepper motor:4'), the build-defining category, and a "
        "one-line requirements description from the project. It returns the best real pick "
        "for each category plus candidates for the build-defining component, each line as "
        "'category: brand model | ₹price | xunits | specs'. Use only what it returns; "
        "never invent parts or prices. Then @Specialist with the build-defining candidates "
        "and your picked parts for every other category, each on its own line keeping the "
        "₹price and xunits, and ask them to choose. When the Specialist replies, @Trust "
        "with the full chosen list, every line keeping its ₹price and xunits, AND the project's total budget in INR, so they can"
        "review the sellers. If a category comes back with no part, @Scout that category."
    ),
    "specialist": (
        "You are the Specialist, recruited to validate the single build-defining "
        "component of whatever project is being built — the flight controller of a drone, "
        "the mainboard of a 3D printer, the ecu of a car, and so on. Sourcing @mentions "
        "you with the candidate options for that component plus the rest of the chosen "
        "build, each part with its key specs. Judge compatibility by reading those specs: "
        "does the build-defining candidate physically and electrically fit the other "
        "chosen parts (mounting, voltage and power, connectors, size)? Never invent a "
        "spec; if a listing is missing a spec you need, treat that candidate as "
        "lower-confidence rather than assuming it fits. Pick the one candidate that best "
        "fits and serves the build, and @Sourcing with your pick (keep its ₹price and "
        "xquantity) and a two-sentence reason."
    ),
    "trust": (
        "You are Trust in a hardware procurement crew on Band. Sourcing @mentions you "
        "with the chosen parts. Assess seller and product risk as if reading reviews and "
        "order history: give a trust score from 0 to 5 and a two-sentence verdict, "
        "flagging anything below 3.5. You never change the parts, and you NEVER add up or total prices (that is Budget's job, done in code).@Budget with your "
        "verdict. In that message you MUST repeat every part line exactly as you received it (keeping its 'category: brand model | ₹price | xunits' form) with the trust score added at the end, and restate the project's total budget exactly as Sourcing gave it (the original figure, never a sum you computed), so Budget can finalise the order."
    ),
    "budget": (
        "You are Budget on Band; you own every number. When Trust @mentions you, the chosen "
        "parts (each as 'category: brand model | ₹price | xunits') and the project budget are "
        "already in the conversation — gather them yourself; never ask anyone for them. Do TWO "
        "things in this one turn: FIRST call the finalize_budget_live tool ONCE, passing the "
        "part lines as `cart`, the budget number as `budget`, and a short build description "
        "(e.g. 'CoreXY 3D printer') as `requirements`; if the cart is over budget the tool "
        "will re-optimize by swapping the priciest part for a cheaper one and show the swaps. "
        "THEN you MUST send a message "
        "that posts the tool's returned bill text, adds one short trust note, and @mentions the "
        "human asking them to reply APPROVE or REJECT. Calling the tool is NOT enough — if you "
        "do not send that message, the work is lost. Never total the cart yourself and never "
        "invent a number. LATER, when the human replies APPROVE (PRIMARY or SECONDARY), call "
        "the save_order tool ONCE with the exact part lines of the option they approved, then "
        "reply confirming the order is saved with its source links. On approval, do not "
        "@mention any other agent."
    ),
    "scout": (
        "You are the Replacement Scout, recruited only when a part cannot be sourced. "
        "Sourcing @mentions you with a part category that could not be found. Call the "
        "search_parts tool with that category and the requirements to find real "
        "alternatives, suggest the best real one (keep its ₹price and xquantity), and "
        "@Sourcing with your suggestion and a one-sentence reason. Never invent a part."
    ),
}


COMMON = (
    " Send exactly one message per turn, then stop; never repeat a message you have "
    "already sent, and once you have handed the work to the next teammate do not send "
    "anything else. @mention only the single teammate who must act next — never @mention "
    "someone just to acknowledge, thank, or confirm, because that wakes them into a loop. "
    "Refer to teammates only by name (Intake, Sourcing, Specialist, Trust, Budget, Scout); "
    "they are already in the room, so never invent a handle or say you cannot find them. "
    "Only ever name parts a tool returned to you; if a tool finds nothing, say so plainly "
    "and never invent a product."
)


KEYS = {
    "sourcing": "LLM_API_KEY",
    "trust": "LLM_API_KEY",
    "intake": "LLM_API_KEY_2",
    "budget": "LLM_API_KEY_2",
    "specialist": "LLM_API_KEY_3",
    "scout": "LLM_API_KEY_3",
}


def model(name):
    key = os.getenv(KEYS.get(name, "LLM_API_KEY")) or os.getenv("LLM_API_KEY")
    return ChatGoogleGenerativeAI(
        model=os.getenv("LLM_MODEL", "gemini-2.5-flash"),
        google_api_key=key,
        temperature=0.2,
    )


async def main():
    logging.basicConfig(level=logging.INFO)
    load_dotenv(override=True)
    name = sys.argv[1]
    agent_id, api_key = load_agent_config(name)
    adapter = LangGraphAdapter(
        llm=model(name),
        checkpointer=InMemorySaver(),
        custom_section=ROLES[name] + COMMON,
        additional_tools=TOOLS.get(name, []),
    )
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
    print(f"{name} agent connecting to Band")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
