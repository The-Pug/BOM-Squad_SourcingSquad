import asyncio
import json
import queue
import re
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from tools import search_model, write_order

load_dotenv(override=True)
app = FastAPI()
STATIC = Path(__file__).parent / "static"


class Session:
    def __init__(self):
        self.events = queue.Queue()
        self.result = None


current = Session()


class Part(BaseModel):
    name: str
    specs: str = ""
    quality: str = "balanced"


class RunBody(BaseModel):
    budget: int
    parts: list[Part]


class ApproveBody(BaseModel):
    choice: str


def status(session, agent, message):
    session.events.put({"type": "status", "agent": agent, "message": message})


def amount(text):
    match = re.search(r"([\d,]+(?:\.\d+)?)", text)
    return int(float(match.group(1).replace(",", ""))) if match else 0


def field(text, label):
    match = re.search(label + r"\s*:\s*([^|]+)", text, re.I)
    return match.group(1).strip() if match else ""


def search_part(name, specs, quality, max_landed=None):
    ceiling = f" It must cost clearly less than ₹{max_landed} landed." if max_landed else ""
    prompt = (
        f"Find ONE real, currently purchasable {name} meeting: {specs or 'general use'}; prioritise {quality}.{ceiling} "
        "Prefer sellers that ship within India; if none suitable, pick the best worldwide option and state its country. "
        "Reply with ONE line using these labels exactly, separated by | :\n"
        "PRODUCT: brand and model | UNIT: ₹<price in INR> | ORIGIN: <country> | SHIPPING: ₹<est to India, 0 if local> | "
        "DUTY: ₹<est import duty, 0 if domestic> | DELIVERY: <est days> | BULK: <bulk discount note or none> | "
        "SOURCE: <store or link>. Use a real product and realistic estimates."
    )
    try:
        response = search_model().invoke(prompt)
        text = response.content if isinstance(response.content, str) else str(response.content)
        text = text.strip().split("\n")[0]
    except Exception:
        return None
    unit = amount(field(text, "UNIT"))
    product = field(text, "PRODUCT")
    if unit <= 0 or not product:
        return None
    origin = field(text, "ORIGIN") or "—"
    bulk = field(text, "BULK")
    if bulk.lower() in ("none", "n/a", ""):
        bulk = ""
    return {
        "category": name,
        "product": product,
        "specs": specs,
        "qty": 1,
        "unit": unit,
        "origin": origin,
        "shipping": amount(field(text, "SHIPPING")),
        "duty": amount(field(text, "DUTY")),
        "delivery": field(text, "DELIVERY") or "—",
        "bulk": bulk,
        "source": field(text, "SOURCE") or "—",
        "imported": origin.strip().lower() not in ("india", "in", "domestic", "—", ""),
    }


def landed_line(row):
    return row["unit"] * row["qty"] + row["shipping"] + row["duty"]


def line_text(row):
    extra = f"origin {row['origin']} | shipping est ₹{row['shipping']} | duty est ₹{row['duty']} | delivery {row['delivery']}"
    return f"{row['category']}: {row['product']} | ₹{row['unit']} | x{row['qty']} | {extra} | source: {row['source']}"


def cart_view(rows, budget):
    total = sum(landed_line(r) for r in rows)
    margin = budget - total
    return {
        "rows": rows,
        "total": total,
        "margin": margin,
        "over": margin < 0,
        "status": f"under budget by ₹{margin}" if margin >= 0 else f"over budget by ₹{-margin}",
    }


def pipeline(budget, parts, session):
    try:
        status(session, "Intake", f"Structured {len(parts)} parts, budget ₹{budget}")
        status(session, "Sourcing", "Searching — local first, widening worldwide if needed…")
        rows = []
        for part in parts:
            row = search_part(part.name, part.specs, part.quality)
            if row:
                rows.append(row)
                where = row["origin"] if row["imported"] else "in India"
                status(session, "Sourcing", f"Found {part.name} ({where})")

        status(session, "Specialist", "Validating fit from specs…")
        status(session, "Trust", "Checking sellers…")
        status(session, "Budget", "Totalling landed cost (price + shipping + duty) in code…")

        primary = cart_view([dict(r) for r in rows], budget)

        secondary = None
        if primary["over"]:
            status(session, "Budget", "Over budget — re-optimizing on landed cost…")
            work = [dict(r) for r in rows]
            swaps = []
            guard = 0
            while sum(landed_line(r) for r in work) > budget and guard < 6:
                guard += 1
                index = max(range(len(work)), key=lambda i: landed_line(work[i]))
                old = work[index]
                alternative = search_part(old["category"], old["specs"], "price", max_landed=landed_line(old))
                if not alternative or landed_line(alternative) >= landed_line(old):
                    break
                swaps.append(
                    f"{old['product']} (landed ₹{landed_line(old)}) → {alternative['product']} "
                    f"(landed ₹{landed_line(alternative)}), saved ₹{landed_line(old) - landed_line(alternative)}"
                )
                work[index] = alternative
            secondary = cart_view(work, budget)
            secondary["swaps"] = swaps

        bulk = [{"product": r["product"], "note": r["bulk"]} for r in rows if r["bulk"]]
        result = {"budget": budget, "primary": primary, "secondary": secondary, "bulk": bulk}
        session.result = result
        session.events.put({"type": "done", "result": result})
    except Exception as error:
        session.events.put({"type": "error", "message": str(error)})
        session.events.put({"type": "done", "result": None})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/run")
def run(body: RunBody):
    global current
    current = Session()
    threading.Thread(target=pipeline, args=(body.budget, body.parts, current), daemon=True).start()
    return {"ok": True}


@app.post("/approve")
def approve(body: ApproveBody):
    if not current.result:
        return {"ok": False, "error": "nothing to approve"}
    chosen = current.result["secondary"] if body.choice == "secondary" and current.result["secondary"] else current.result["primary"]
    lines = "\n".join(line_text(r) for r in chosen["rows"])
    summary = f"{lines}\n\nLanded total: ₹{chosen['total']} | Budget: ₹{current.result['budget']} | {chosen['status']}"
    bulk = current.result.get("bulk") or []
    if bulk:
        summary += "\n\nBulk savings available:\n" + "\n".join(f"- {b['product']}: {b['note']}" for b in bulk)
    saved = write_order(summary)
    links = [{"name": r["category"], "product": r["product"], "source": r["source"]}
             for r in chosen["rows"] if r.get("source") and r["source"] != "—"]
    return {"ok": True, "saved": saved, "links": links}


@app.get("/stream")
async def stream():
    session = current

    async def gen():
        while True:
            try:
                event = session.events.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.1)
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
