# ruff: noqa
import os
import json
import sys
import re
from typing import AsyncGenerator, Any
from pydantic import BaseModel, Field

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models import Gemini
from google.adk.workflow import Workflow
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.tools import AgentTool
from google.genai import types
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from app.config import config

# ── MCP Toolset ──────────────────────────────────────────────────────────────
python_executable = sys.executable or "python3"
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=python_executable,
            args=["-m", "app.mcp_server"],
        )
    )
)

# ── PII / Security patterns ───────────────────────────────────────────────────
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
CARD_REGEX  = re.compile(r"\b(?:\d[ -]*?){13,16}\b")

INJECTION_KEYWORDS = [
    "ignore previous instructions",
    "system prompt",
    "override rules",
    "developer mode",
    "jailbreak",
]
MEDICAL_KEYWORDS = [
    "diagnose", "cure", "prescribe", "medication",
    "disease", "insulin", "diabetes treatment",
]

# ── Gemini model ──────────────────────────────────────────────────────────────
model_instance = Gemini(model=config.model)

# ── Specialist sub-agents ─────────────────────────────────────────────────────
meal_planner = LlmAgent(
    name="meal_planner",
    model=model_instance,
    description="Generates custom meal plans, recipes, and shopping lists.",
    instruction=(
        "You are a professional chef and nutritionist. "
        "Generate custom meal plans, recipes, and shopping lists based on the user's "
        "dietary preferences, restrictions, calorie needs, and goals. "
        "Be specific with ingredients, portion sizes, and calorie/macro breakdowns. "
        "You have access to nutrition lookup and meal-logging tools via MCP."
    ),
    tools=[mcp_toolset],
)

macro_tracker = LlmAgent(
    name="macro_tracker",
    model=model_instance,
    description="Calculates calories and macros for foods, and suggests healthy substitutions.",
    instruction=(
        "You are a macro-tracking and food science specialist. "
        "Analyse meals or food items, calculate calorie, protein, carb, and fat content, "
        "and suggest goal-aligned substitutions. "
        "Use the MCP tools to look up nutrition data, calculate BMI if requested, "
        "and log meals to the daily intake file."
    ),
    tools=[mcp_toolset],
)

# ── Orchestrator ──────────────────────────────────────────────────────────────
orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    model=model_instance,
    description="Main coordinator for Nutri-Coach.",
    instruction=(
        "You are the main coordinator of the Nutri-Coach system.\n"
        "- For meal plans, recipes, or shopping lists → call the meal_planner tool.\n"
        "- For macro calculations, food analysis, or substitutions → call the macro_tracker tool.\n\n"
        "Revision feedback from the user (if any): {revision_feedback}\n\n"
        "After delegating, summarise the result clearly for the user. "
        "If the task is a meal plan, end your response with the exact marker: [MEAL_PLAN_READY]"
    ),
    tools=[AgentTool(meal_planner), AgentTool(macro_tracker)],
    output_key="coach_output",
)

# ── Workflow nodes ─────────────────────────────────────────────────────────────

def security_checkpoint(ctx: Context, node_input: types.Content) -> Event:
    """Gate-1: PII scrubbing, injection detection, domain restriction, audit log."""
    # Initialise state
    ctx.state.setdefault("revision_feedback", "None")
    ctx.state.setdefault("audit_log", [])

    # Extract text
    user_text = ""
    if node_input and node_input.parts:
        user_text = "".join(p.text for p in node_input.parts if p.text)

    has_injection = any(kw in user_text.lower() for kw in INJECTION_KEYWORDS)
    has_medical   = any(kw in user_text.lower() for kw in MEDICAL_KEYWORDS)

    if has_injection or has_medical:
        severity = "CRITICAL" if has_injection else "WARNING"
        reason   = "prompt_injection" if has_injection else "medical_restriction"
        entry    = {"severity": severity, "action": "BLOCKED", "reason": reason}
        ctx.state["audit_log"].append(entry)
        print(f"[{severity}] Security block: {json.dumps(entry)}")
        return Event(output=reason, route="SECURITY_EVENT")

    # PII scrubbing
    scrubbed = EMAIL_REGEX.sub("[EMAIL_REDACTED]", user_text)
    scrubbed = PHONE_REGEX.sub("[PHONE_REDACTED]", scrubbed)
    scrubbed = CARD_REGEX.sub("[CARD_REDACTED]", scrubbed)

    entry = {"severity": "INFO", "action": "PASSED", "pii_scrubbed": scrubbed != user_text}
    ctx.state["audit_log"].append(entry)
    print(f"[INFO] Security passed: {json.dumps(entry)}")

    clean_content = types.Content(role="user", parts=[types.Part.from_text(text=scrubbed)])
    return Event(output=clean_content)


def security_event_handler(node_input: str):
    """Inform the user their request was blocked and why."""
    if node_input == "prompt_injection":
        msg = "⚠️ Security Rejection: Prompt injection attempt detected. This request has been logged."
    elif node_input == "medical_restriction":
        msg = (
            "⚠️ Restriction: I'm a nutrition coach, not a medical professional. "
            "I cannot diagnose conditions or provide medical/prescription advice. "
            "Please consult a qualified doctor."
        )
    else:
        msg = "⚠️ Security Rejection: Request blocked by safety policy."

    yield Event(content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]))
    yield Event(output=msg)


async def hitl_gate(ctx: Context, node_input: str):
    """Gate-2: pause for human approval when a meal plan was generated."""
    # Detect whether a meal plan was produced
    is_meal_plan = "[MEAL_PLAN_READY]" in (node_input or "")

    if not is_meal_plan:
        # Non-meal-plan response: display it directly and finish
        display = (node_input or "").replace("[MEAL_PLAN_READY]", "").strip()
        yield Event(content=types.Content(role="model", parts=[types.Part.from_text(text=display)]))
        yield Event(output=display, route="approved")
        return

    # Strip the marker for display
    draft = node_input.replace("[MEAL_PLAN_READY]", "").strip()

    if not ctx.resume_inputs or "approve_plan" not in ctx.resume_inputs:
        # Show draft and request approval
        yield Event(content=types.Content(role="model", parts=[
            types.Part.from_text(text=f"### Draft Meal Plan:\n\n{draft}")
        ]))
        yield RequestInput(
            interrupt_id="approve_plan",
            message="Do you approve this meal plan? Reply 'yes' to confirm or describe changes you'd like."
        )
        return

    # Process the human's reply
    reply = ctx.resume_inputs["approve_plan"].strip().lower()
    if reply in ("yes", "y", "approve", "approved", "ok", "looks good"):
        yield Event(
            content=types.Content(role="model", parts=[types.Part.from_text(text="✅ Plan approved! Here is your finalised meal plan:")]),
            output=draft,
            route="approved",
            state={"plan_approved": True},
        )
    else:
        feedback = f"User requested revisions: '{reply}'. Please revise the meal plan accordingly."
        yield Event(
            output=feedback,
            route="revision_needed",
            state={"plan_approved": False, "revision_feedback": feedback},
        )


def final_output(ctx: Context, node_input: str):
    """Display the finalised result."""
    msg = node_input or ""
    if "###" not in msg:
        msg = f"### ✅ Final Recommendation:\n\n{msg}"
    yield Event(content=types.Content(role="model", parts=[types.Part.from_text(text=msg)]))
    yield Event(output=msg)


# ── Workflow Graph ────────────────────────────────────────────────────────────
workflow_agent = Workflow(
    name="nutri_coach_workflow",
    edges=[
        ("START", security_checkpoint),
        (
            security_checkpoint,
            {
                "SECURITY_EVENT": security_event_handler,
                "__DEFAULT__": orchestrator_agent,
            },
        ),
        (orchestrator_agent, hitl_gate),
        (
            hitl_gate,
            {
                "revision_needed": orchestrator_agent,
                "approved": final_output,
            },
        ),
    ],
    description="Secure, multi-agent nutrition coach with human-in-the-loop meal plan approval.",
)

# ── App ───────────────────────────────────────────────────────────────────────
app = App(
    name="app",
    root_agent=workflow_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
