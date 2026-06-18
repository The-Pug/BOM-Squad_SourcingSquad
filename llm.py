import hashlib
import json
import os
from pathlib import Path

DEMO_SCRIPT = {
    "translator": '{"project": "5-inch FPV racing quad", "parts": [{"category": "frame", "name": "Mark4 V2 5inch", "qty": 1}, {"category": "motor", "name": "ECO II 2306 1700KV", "qty": 4}, {"category": "esc", "name": "BLS 50A 4in1", "qty": 1}, {"category": "flight_controller", "name": "", "qty": 1}, {"category": "propeller", "name": "5x4.3x3 set of 4", "qty": 2}, {"category": "battery", "name": "1500mAh 4S 100C", "qty": 1}, {"category": "camera", "name": "Ratel 2", "qty": 1}, {"category": "vtx", "name": "FX2 Ultimate 800mW", "qty": 1}, {"category": "receiver", "name": "EP1 ELRS 2.4G", "qty": 1}]}',
    "intake": '{"project": "5-inch FPV racing quad", "budget_inr": 18000, "categories": ["frame", "motor", "esc", "flight_controller", "propeller", "battery", "camera", "vtx", "receiver"], "profile": {"motor": {"min_rating": 4.0, "priority": "performance"}, "battery": {"min_rating": 4.0, "priority": "performance"}, "camera": {"min_rating": 3.5, "priority": "price"}}, "notes": "racing capable, 4S power preferred"}',
    "sourcing": "Picked the best-scoring in-stock option for every category, but the flight controller decides the whole build, so I am recruiting a specialist instead of guessing.",
    "specialist": '{"choice": "F405 V4", "reason": "The build runs a 6S-rated ESC and the user wants race performance, so the F405 V4 in a 30.5x30.5 stack matches the frame mounting and leaves room for the OSD and blackbox the GHF411 lacks. The Matek F722-SE is overkill for a first racing build at this budget."}',
    "trust": '{"score": 4.4, "verdict": "Seller has consistent positive feedback over 1800 orders with no counterfeit complaints; a few reports of slow shipping but no DOA pattern."}',
    "budget": "Totals verified in code. The margin is thin, so shipping and spare props are flagged as watch items before the human signs off.",
    "replacement": '{"suggestion": "T-Motor F60 Pro V 2207 1750KV", "reason": "When a 2306 race motor is unavailable, builders most often substitute a 2207 in the same KV class; the code will still re-check it against the frame and ESC before it counts."}',
}

ROLE_PROFILE = {
    "intake": "aiml",
    "trust": "aiml",
    "specialist": "featherless",
    "translator": "groq",
    "sourcing": "groq",
    "budget": "groq",
    "replacement": "groq",
}

PROFILE_DEFAULTS = {
    "aiml": ("https://api.aimlapi.com/v1", "gpt-4o-mini"),
    "featherless": ("https://api.featherless.ai/v1", "meta-llama/Meta-Llama-3.1-8B-Instruct"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
}


def load_profile(name):
    prefix = name.upper()
    base_default, model_default = PROFILE_DEFAULTS[name]
    return {
        "key": os.getenv(prefix + "_API_KEY"),
        "base": os.getenv(prefix + "_BASE_URL", base_default),
        "model": os.getenv(prefix + "_MODEL", model_default),
    }


class LLM:
    def __init__(self):
        self.mode = os.getenv("LLM_MODE", "demo").lower()
        self.max_calls = int(os.getenv("MAX_LIVE_CALLS", "40"))
        self.calls = 0
        self.profiles = {name: load_profile(name) for name in PROFILE_DEFAULTS}
        self.clients = {}
        self.cache_dir = Path(__file__).parent / ".cache"

    def client_for(self, profile_name):
        if profile_name not in self.clients:
            from openai import OpenAI

            profile = self.profiles[profile_name]
            self.clients[profile_name] = OpenAI(api_key=profile["key"], base_url=profile["base"])
        return self.clients[profile_name]

    def cache_path(self, key):
        return self.cache_dir / (key + ".txt")

    def read_cache(self, key):
        path = self.cache_path(key)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def write_cache(self, key, value):
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_path(key).write_text(value, encoding="utf-8")

    def ask(self, role, system, user):
        if self.mode == "demo":
            return DEMO_SCRIPT[role]

        profile_name = ROLE_PROFILE.get(role, "groq")
        profile = self.profiles[profile_name]
        if not profile["key"]:
            return DEMO_SCRIPT[role]

        key = hashlib.sha256(f"{profile['model']}|{system}|{user}".encode("utf-8")).hexdigest()[:32]
        cached = self.read_cache(key)
        if cached is not None:
            return cached

        if self.calls >= self.max_calls:
            raise RuntimeError(f"live call cap of {self.max_calls} reached; raise MAX_LIVE_CALLS to continue")

        reply = self.client_for(profile_name).chat.completions.create(
            model=profile["model"],
            temperature=0.2,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        self.calls += 1
        text = reply.choices[0].message.content.strip()
        self.write_cache(key, text)
        return text

    def ask_json(self, role, system, user):
        raw = self.ask(role, system, user + "\nReply with valid JSON only. No markdown, no extra text.")
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            raise RuntimeError("model did not return JSON, got: " + raw[:200])
        return json.loads(raw[start : end + 1])
