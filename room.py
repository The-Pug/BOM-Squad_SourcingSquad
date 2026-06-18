from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel

console = Console()

AGENT_COLORS = {
    "Intake": "cyan",
    "Sourcing": "yellow",
    "Trust": "blue",
    "Specialist": "magenta",
    "Replacement Scout": "bright_magenta",
    "Budget": "green",
    "Human Desk": "bright_white",
}


@dataclass
class Message:
    sender: str
    to: str
    kind: str
    body: str


class Room:
    def __init__(self, name):
        self.name = name
        self.members = []
        self.log = []
        self.listeners = []

    def subscribe(self, listener):
        self.listeners.append(listener)

    def emit(self, event):
        self.log.append(event)
        for listener in self.listeners:
            listener(event)

    def join(self, member_name):
        self.members.append(member_name)
        console.print(f"[bright_black]· {member_name} joined {self.name}[/bright_black]")
        self.emit({"type": "join", "member": member_name})

    def post(self, message):
        color = AGENT_COLORS.get(message.sender, "white")
        header = f"{message.sender} → {message.to}  ·  {message.kind}"
        console.print(Panel(message.body, title=header, title_align="left", border_style=color))
        self.emit({"type": "message", "sender": message.sender, "to": message.to,
                   "kind": message.kind, "body": message.body})

    def announce(self, text):
        console.print(Panel(text, title="🎺 ROOM EVENT", title_align="left", border_style="red"))
        self.emit({"type": "announce", "body": text})
