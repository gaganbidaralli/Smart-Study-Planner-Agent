import os
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.workflow import node, START, Edge, Workflow, DEFAULT_ROUTE
from google.adk.events import RequestInput
from google.adk.tools import AgentTool, ToolContext, McpToolset
from mcp import StdioServerParameters
from google.genai import types

from app.config import config

# =====================================================================
# State Schema
# =====================================================================

class StudyPlanState(BaseModel):
    proposed_plan: Optional[str] = None
    approved_plan: Optional[str] = None
    pending_approval: bool = False
    audit_log: List[Dict[str, Any]] = Field(default_factory=list)
    user_query: Optional[str] = None

# =====================================================================
# MCP Server Toolset Connection
# =====================================================================

mcp_toolset = McpToolset(
    connection_params=StdioServerParameters(
        command="uv",
        args=["run", "python", "app/mcp_server.py"],
    )
)

# =====================================================================
# Specialized Sub-Agents
# =====================================================================

planner_agent = Agent(
    name="study_planner_agent",
    description="Generates detailed study plans, schedules, and revision reminders based on dates, subjects, difficulty, and available hours.",
    instruction=(
        "You are the specialized Study Planner Agent. Your job is to create highly structured "
        "and detailed daily study schedules. Use the exam date, subjects, difficulty levels, "
        "and available daily study hours to design the plan. Break down the subjects into daily tasks, "
        "suggest active recall/spaced repetition revision schedules, and provide clear step-by-step guidance. "
        "Respond in a clear, formatted, and encouraging markdown structure. "
        "You have access to MCP tools to get exam countdown, study tips, and log completed hours."
    ),
    tools=[mcp_toolset],
    model=Gemini(
        model=config.model,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
)

motivator_agent = Agent(
    name="student_motivator_agent",
    description="Encourages students, tracks progress, and provides personalized motivational advice when they feel overwhelmed or miss schedules.",
    instruction=(
        "You are the specialized Student Motivator Agent. Your job is to inspire and motivate students. "
        "When students express anxiety, lack of motivation, or report missing their study goals, provide "
        "warm, empathetic, and actionable advice. Offer micro-steps to reduce cognitive overload, time management "
        "hacks (like the Pomodoro technique), and positive reinforcement. Keep your tone encouraging and supportive. "
        "You have access to MCP tools to check exam countdown, study tips, and log completed hours."
    ),
    tools=[mcp_toolset],
    model=Gemini(
        model=config.model,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
)

# =====================================================================
# Custom Agent Tools for Orchestrator
# =====================================================================

class PlannerAgentTool(AgentTool):
    """Custom AgentTool that wraps the Study Planner sub-agent and updates workflow state."""
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        res = await super().run_async(args=args, tool_context=tool_context)
        # Save proposed plan in the workflow state and mark pending approval
        tool_context.state.proposed_plan = str(res)
        tool_context.state.pending_approval = True
        return res

class MotivatorAgentTool(AgentTool):
    """Custom AgentTool wrapping the Student Motivator agent."""
    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        res = await super().run_async(args=args, tool_context=tool_context)
        return res

planner_tool = PlannerAgentTool(agent=planner_agent)
motivator_tool = MotivatorAgentTool(agent=motivator_agent)

# =====================================================================
# Orchestrator Coordinator Agent
# =====================================================================

orchestrator = Agent(
    name="orchestrator",
    description="The main student-facing coordinator that delegates planner and motivator requests.",
    instruction=(
        "You are the Smart Study Planner Orchestrator. You help students plan their exam preparation and stay motivated. "
        "For any request involving creating a study plan, calendar, schedule, or revision timeline, delegate to study_planner_agent. "
        "For any request involving encouragement, feeling overwhelmed, procrastinating, or reporting missed study hours, delegate to student_motivator_agent. "
        "Analyze the user query carefully and use the correct tool. Always remain professional, empathetic, and structured."
    ),
    tools=[planner_tool, motivator_tool],
    model=Gemini(
        model=config.model,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
)

# =====================================================================
# Workflow Function Nodes
# =====================================================================

import re
import json
import sys
from datetime import datetime

@node
async def security_checkpoint(ctx, node_input):
    query = str(node_input)
    violations = []
    severity = "INFO"
    scrubbed_query = query

    # 1. PII Scrubbing
    # Email regex
    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    if re.search(email_pattern, scrubbed_query):
        scrubbed_query = re.sub(email_pattern, "[EMAIL]", scrubbed_query)
        violations.append("PII_EMAIL_DETECTED")
        severity = "WARNING"
        
    # Phone regex
    phone_pattern = r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
    if re.search(phone_pattern, scrubbed_query):
        scrubbed_query = re.sub(phone_pattern, "[PHONE]", scrubbed_query)
        violations.append("PII_PHONE_DETECTED")
        severity = "WARNING"

    # 2. Prompt Injection Detection
    injection_keywords = [
        "ignore previous instructions",
        "system prompt",
        "override settings",
        "bypass safety",
        "you are now",
        "new instruction"
    ]
    query_lower = query.lower()
    for kw in injection_keywords:
        if kw in query_lower:
            violations.append(f"PROMPT_INJECTION_DETECTED: '{kw}'")
            severity = "CRITICAL"

    # 3. Domain Specific Check: Study hour limits (burnout prevention)
    # Check if user requests > 16 study hours per day
    hour_match = re.search(r'\b(\d+)\s*(?:hour|hr)s?\b', query_lower)
    if hour_match:
        requested_hours = int(hour_match.group(1))
        if requested_hours > 16:
            violations.append("DOMAIN_VIOLATION_STUDY_HOURS_EXCEEDED")
            severity = "WARNING"
        elif requested_hours <= 0:
            violations.append("DOMAIN_VIOLATION_INVALID_STUDY_HOURS")
            severity = "WARNING"

    # 4. Action / Routing Decision
    if severity == "CRITICAL":
        ctx.route = "SECURITY_EVENT"
        decision = "BLOCKED"
        output_res = "⚠️ **Security System Alert**: Request blocked due to potential prompt injection attempt."
    elif "DOMAIN_VIOLATION_STUDY_HOURS_EXCEEDED" in violations:
        ctx.route = "SECURITY_EVENT"
        decision = "BLOCKED"
        output_res = "⚠️ **Study Health Alert**: Daily study hours cannot exceed 16 hours to prevent burnout. Please plan a realistic study schedule."
    elif "DOMAIN_VIOLATION_INVALID_STUDY_HOURS" in violations:
        ctx.route = "SECURITY_EVENT"
        decision = "BLOCKED"
        output_res = "⚠️ **Study Alert**: Daily study hours must be greater than 0."
    else:
        ctx.route = DEFAULT_ROUTE
        decision = "PASSED"
        output_res = scrubbed_query

    # 5. Audit Log (Structured JSON)
    audit_entry = {
        "timestamp": datetime.now().isoformat(),
        "event": "security_checkpoint_evaluation",
        "decision": decision,
        "violations": violations,
        "severity": severity,
        "query_scrubbed": scrubbed_query != query
    }
    
    # Write to sys.stderr (avoids corrupting stdio JSON-RPC transport)
    print(json.dumps(audit_entry), file=sys.stderr)
    
    # Append to workflow state audit logs
    ctx.state.audit_log.append(audit_entry)
    ctx.state.user_query = scrubbed_query
    
    return output_res

@node(rerun_on_resume=True)
async def human_approval_node(ctx, node_input):
    # If the sub-agent triggered a proposed study plan that requires student review
    if ctx.state.pending_approval:
        interrupt_id = "plan_approval"
        
        # Check if the user has responded to our request for input
        if interrupt_id in ctx.resume_inputs:
            user_response = ctx.resume_inputs[interrupt_id]
            val = False
            if isinstance(user_response, dict):
                val_str = str(user_response.get("result", "")).lower()
                val = "yes" in val_str or "true" in val_str or val_str == "y"
            elif isinstance(user_response, bool):
                val = user_response
            elif isinstance(user_response, str):
                val = "yes" in user_response.lower() or "true" in user_response.lower()

            if val:
                # User approved: save plan as approved
                ctx.state.approved_plan = ctx.state.proposed_plan
                ctx.state.pending_approval = False
                ctx.route = DEFAULT_ROUTE
                yield f"✅ **Study Plan Approved & Activated!**\n\nHere is your active schedule:\n\n{ctx.state.approved_plan}"
                return
            else:
                # User rejected: reset pending flag and route back to orchestrator for revisions
                ctx.state.pending_approval = False
                ctx.route = "REJECTED"
                yield "❌ **Study Plan Rejected.** Please let the orchestrator know what changes or adjustments you need."
                return
        else:
            # Yield RequestInput to pause execution and ask the user
            yield RequestInput(
                interrupt_id=interrupt_id,
                message=(
                    f"### Please review your proposed study plan:\n\n{ctx.state.proposed_plan}\n\n"
                    "Do you approve this schedule? (Reply 'Yes' or 'No')"
                ),
                response_schema=str
            )
            return

    # If no approval is pending (e.g. it was just a motivation or status query), pass through
    ctx.route = DEFAULT_ROUTE
    yield node_input
    return

@node
async def final_output(ctx, node_input):
    # Terminal node that presents the final result
    return node_input

# =====================================================================
# Workflow Compile & App Definition
# =====================================================================

workflow = Workflow(
    name="study_planner_workflow",
    state_schema=StudyPlanState,
    edges=[
        (START, security_checkpoint),
        # Security routing: if security check fails, it can go direct to final_output, otherwise to orchestrator
        (security_checkpoint, {"SECURITY_EVENT": final_output, DEFAULT_ROUTE: orchestrator}),
        # Orchestrator to human_approval
        (orchestrator, human_approval_node),
        # Approval routing: if rejected, go back to orchestrator; if approved, go to final_output
        (human_approval_node, {"REJECTED": orchestrator, DEFAULT_ROUTE: final_output}),
    ],
)

# Export the required variables for fast_api_app.py
root_agent = orchestrator
app = App(
    root_agent=workflow,
    name="app",
)
