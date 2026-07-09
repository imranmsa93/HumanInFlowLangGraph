from __future__ import annotations

from typing import TypedDict, Annotated, Sequence, Dict, Any, Optional
import operator
import textwrap
import os

from langgraph.graph import StateGraph, END
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import InMemorySaver

def read_log_file(path: str = "log.txt") -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Log file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        raise ValueError(f"Log file is empty: {path}")

    return content 

# State 
class IncidentState(TypedDict):
    messages: Annotated[Sequence[str], operator.add]
    history: Annotated[Sequence[str], operator.add]
    counter: int

    raw_incident_text: str
    processed_data: str
    draft_summary: str
    approved_summary: str

    approval: Dict[str, Any]
    context: Dict[str, Any]

# Nodes
def input_node(state: IncidentState) -> IncidentState:
    print("\n=== INPUT NODE ===")
    print("Received raw incident text.")
    return {
        "messages": ["User: (incident logs received)"],
        "history": ["input_node"],
        "counter": state["counter"] + 1,
    }

def process_node(state: IncidentState) -> IncidentState:
    print("\n=== PROCESS NODE ===")
    cleaned = " ".join(state["raw_incident_text"].strip().split())
    print(f"Processed length: {len(cleaned)} chars")

    return {
        "processed_data": cleaned,
        "context": {**state.get("context", {}), "processed_length": len(cleaned)},
        "history": ["process_node"],
        "counter": state["counter"] + 1,
    }

def enrich_node(state: IncidentState) -> IncidentState:
    print("\n=== ENRICH NODE ===")
    text = state["processed_data"].lower()
    sev = "SEV-1" if ("outage" in text or "down" in text) else "SEV-2"
    service = "payments-api" if "payment" in text else "unknown-service"

    enriched = {
        "severity": sev,
        "service": service,
        "word_count": len(state["processed_data"].split()),
    }
    print(f"Enriched: {enriched}")

    return {
        "context": {**state.get("context", {}), "enriched": enriched},
        "history": ["enrich_node"],
        "counter": state["counter"] + 1,
    }

def draft_summary_node(state: IncidentState) -> IncidentState:
    print("\n=== DRAFT SUMMARY NODE ===")
    enriched = state["context"].get("enriched", {})
    sev = enriched.get("severity", "SEV-?")
    service = enriched.get("service", "unknown")
    wc = enriched.get("word_count", 0)

    draft = (
        f"[{sev}] Incident detected affecting `{service}`. "
        f"Signals observed in logs/alerts. "
        f"Initial triage suggests impact is ongoing. "
        f"(source size: {wc} words)"
    )

    print("Draft summary created.")

    return {
        "draft_summary": draft,
        "context": {**state.get("context", {}), "draft_ready": True},
        "history": ["draft_summary_node"],
        "counter": state["counter"] + 1,
    }

def human_approval_node(state: IncidentState) -> IncidentState:
    print("\n=== HUMAN APPROVAL NODE (INTERRUPT) ===")

    payload = {
        "message": "Review incident summary before publishing.",
        "draft_summary": state["draft_summary"],
        "suggested_checks": [
            "Accuracy (no wrong claims)",
            "No sensitive data (tokens, secrets, customer PII)",
            "Clear and actionable",
        ],
    }

    decision = interrupt(payload)

    status = decision.get("status", "rejected")
    edited_summary = decision.get("edited_summary", "").strip()

    if status == "edited" and edited_summary:
        final = edited_summary
    elif status == "approved":
        final = state["draft_summary"]
    else:
        final = ""

    return {
        "approval": decision,
        "approved_summary": final,
        "messages": [f"Reviewer decision: {status}"],
        "history": ["human_approval_node"],
        "counter": state["counter"] + 1,
    }

def publish_node(state: IncidentState) -> IncidentState:
    print("\n=== PUBLISH NODE ===")
    approval_status = state.get("approval", {}).get("status", "rejected")

    if approval_status in ("approved", "edited") and state.get("approved_summary"):
        publish_text = textwrap.fill(state["approved_summary"], width=90)
        print("\nPUBLISHED INCIDENT SUMMARY:\n")
        print(publish_text)
        msg = "Assistant: Summary published."
    else:
        print("\nNOT PUBLISHED (rejected or missing final summary).")
        msg = "Assistant: Summary not published."

    return {
        "messages": [msg],
        "history": ["publish_node"],
        "counter": state["counter"] + 1,
    }

# Graph builder
def create_graph():
    workflow = StateGraph(IncidentState)

    workflow.add_node("input", input_node)
    workflow.add_node("process", process_node)
    workflow.add_node("enrich", enrich_node)
    workflow.add_node("draft", draft_summary_node)
    workflow.add_node("human_approval", human_approval_node)
    workflow.add_node("publish", publish_node)

    workflow.add_edge("input", "process")
    workflow.add_edge("process", "enrich")
    workflow.add_edge("enrich", "draft")
    workflow.add_edge("draft", "human_approval")
    workflow.add_edge("human_approval", "publish")
    workflow.add_edge("publish", END)

    workflow.set_entry_point("input")

    memory = InMemorySaver()
    return workflow.compile(checkpointer=memory)

def run_demo():
    app = create_graph()

    try:
        raw = read_log_file("log.txt")
    except Exception as e:
        print(f"Error reading log file: {e}")
        return

    thread_id = "incident-thread-1"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: IncidentState = {
        "messages": [],
        "history": [],
        "counter": 0,
        "raw_incident_text": raw,
        "processed_data": "",
        "draft_summary": "",
        "approved_summary": "",
        "context": {},
        "approval": {},
    }

    print("\n--- Running until interrupt ---")
    result = app.invoke(initial_state, config=config)

    interrupts = result.get("__interrupt__")
    if not interrupts:
        print("\n(No interrupt occurred — unexpected for this demo.)")
        return

    interrupt_obj = interrupts[0]
    payload = interrupt_obj.value

    print("\nHUMAN REVIEW REQUIRED")
    print(payload["message"])
    print("\nDRAFT SUMMARY:\n" + textwrap.fill(payload["draft_summary"], width=90))

    print("\nChoose:")
    print("1) Approve as-is")
    print("2) Edit summary")
    print("3) Reject")

    choice = input("Enter 1/2/3: ").strip()
    reviewer = input("Reviewer (name/email): ").strip() or "unknown-reviewer"
    notes = input("Notes (optional): ").strip()

    if choice == "1":
        decision = {"status": "approved", "reviewer": reviewer, "notes": notes}
    elif choice == "2":
        edited = input("\nPaste edited summary:\n> ").strip()
        decision = {
            "status": "edited",
            "reviewer": reviewer,
            "notes": notes,
            "edited_summary": edited,
        }
    else:
        decision = {"status": "rejected", "reviewer": reviewer, "notes": notes}

    print("\n--- Resuming graph ---")
    final = app.invoke(Command(resume=decision), config=config)

    print("\n--- Final messages ---")
    for m in final.get("messages", []):
        print(m)

    print("\n--- Execution history ---")
    print(" --> ".join(final.get("history", [])))




# Main file
if __name__ == "__main__":
    run_demo()
