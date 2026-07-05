# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import re
import json
import sys
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.adk.workflow import Workflow, START
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from google.genai import types

from app.config import config

# Configure basic logging
logging.basicConfig(level=logging.INFO)

# ==========================================
# MCP Toolset Configuration
# ==========================================

mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable or "python",
            args=["-m", "app.mcp_server"],
        ),
    )
)

# ==========================================
# Specialized Sub-Agents
# ==========================================

benefits_eligibility_agent = LlmAgent(
    name="benefits_eligibility_agent",
    model=config.model,
    instruction="""You are a Benefits Eligibility specialist. Your job is to check if a client qualifies for health and social programs (e.g., Medicaid, SNAP, LIHEAP).
Evaluate household size, monthly income, and program rules.
Use the tools in the MCP toolset to look up benefit rules and check income thresholds.
Provide a clear summary of your eligibility assessment and explain the reasoning.
""",
    description="Evaluates eligibility rules and income thresholds for benefits.",
    tools=[mcp_toolset],
)

application_guide_agent = LlmAgent(
    name="application_guide_agent",
    model=config.model,
    instruction="""You are an Application Guide specialist. Your job is to help users prepare their applications for benefits.
Explain the list of documents required (e.g. proof of income, identity, residency).
Help draft letters of inquiry or guide the user on where to submit their applications.
Use the tools in the MCP toolset to search for local offices or check program rules.
Provide a checklist of next steps.
""",
    description="Guides users on documentation requirements, drafts application letters, and provides submission steps.",
    tools=[mcp_toolset],
)

# ==========================================
# Orchestrator
# ==========================================

care_navigator_orchestrator = LlmAgent(
    name="care_navigator_orchestrator",
    model=config.model,
    instruction="""You are the CareNavigator coordinator. Your goal is to guide the user in finding health and social benefits they qualify for, and help them apply.
You have two specialized assistants:
- benefits_eligibility_agent: Use this tool to evaluate eligibility rules and income thresholds.
- application_guide_agent: Use this tool to get documentation checklists, inquiry letter drafts, and submission guidance.

Always delegate to these agents using their respective AgentTools.
Determine if the user needs to confirm having specific documents (like tax returns, proof of income, or ID cards) before they proceed with the application.
""",
    tools=[AgentTool(benefits_eligibility_agent), AgentTool(application_guide_agent)],
    output_key="orchestrator_output",
)

# ==========================================
# Workflow Nodes
# ==========================================

def security_checkpoint(ctx: Context, node_input: Any) -> Event:
    """Security Checkpoint node that filters inputs for PII and prompt injection."""
    user_text = ""
    if isinstance(node_input, str):
        user_text = node_input
    elif hasattr(node_input, "parts") and node_input.parts:
        user_text = "".join(part.text for part in node_input.parts if part.text)
    
    ctx.state["original_query"] = user_text
    
    # 1. PII Scrubbing (SSN, Phone)
    scrubbed = user_text
    if config.pii_redaction_enabled:
        # SSN regex
        scrubbed = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED SSN]', scrubbed)
        # Phone regex
        scrubbed = re.sub(r'\b\d{3}-\d{3}-\d{4}\b', '[REDACTED PHONE]', scrubbed)
        scrubbed = re.sub(r'\b\(\d{3}\)\s*\d{3}-\d{4}\b', '[REDACTED PHONE]', scrubbed)
        
    ctx.state["scrubbed_query"] = scrubbed
    
    # 2. Prompt Injection Check
    is_injection = False
    injection_keywords = ["ignore previous instructions", "system prompt", "bypass security", "developer mode"]
    if config.injection_detection_enabled:
        for kw in injection_keywords:
            if kw in user_text.lower():
                is_injection = True
                break
                
    # 3. Domain-Specific Rule: Consent check for third-party lookup
    requires_consent_violation = False
    third_party_keywords = ["mother", "father", "spouse", "wife", "husband", "son", "daughter", "friend", "someone else", "another person"]
    consent_keywords = ["consent", "authorize", "permission", "agreement", "poa"]
    
    is_third_party = any(kw in user_text.lower() for kw in third_party_keywords)
    has_consent = any(cw in user_text.lower() for cw in consent_keywords)
    
    if is_third_party and not has_consent:
        requires_consent_violation = True
        
    # Audit log
    audit_data = {
        "pii_scrubbed": scrubbed != user_text,
        "injection_detected": is_injection,
        "consent_violation": requires_consent_violation,
        "severity": "CRITICAL" if is_injection else ("WARNING" if (scrubbed != user_text or requires_consent_violation) else "INFO")
    }
    logging.info(f"AUDIT LOG: {json.dumps(audit_data)}")
    
    if is_injection or requires_consent_violation:
        reason = "Unsafe prompt content detected." if is_injection else "Inquiries about another person require explicit consent (e.g. 'I have consent from my mother...')."
        return Event(
            route="SECURITY_EVENT",
            state={"has_security_violation": True, "security_reason": reason}
        )
        
    return Event(
        route="PROCEED",
        state={"scrubbed_query": scrubbed}
    )

def security_event_handler(ctx: Context, node_input: Any) -> Event:
    """Handles security violations and returns a safe response."""
    reason = ctx.state.get("security_reason", "Your request was flagged as potentially unsafe.")
    msg = f"Security Alert: {reason}"
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=msg)]))
    yield Event(output={"error": msg, "status": "blocked"})

async def hitl_checkpoint(ctx: Context, node_input: Any) -> AsyncGenerator[Event, None]:
    """Pauses execution to ask the user to confirm documents if required."""
    # Extract text from node_input
    response_text = ""
    if isinstance(node_input, str):
        response_text = node_input
    elif hasattr(node_input, "parts") and node_input.parts:
        response_text = "".join(part.text for part in node_input.parts if part.text)
    
    # Simple heuristics to see if we should request document confirmation
    requires_hitl = False
    docs = []
    
    # If the user is asking to apply or wants documents, and we haven't confirmed yet
    lower_response = response_text.lower()
    if "apply" in lower_response or "document" in lower_response or "checklist" in lower_response or "office" in lower_response:
        if "medicaid" in lower_response:
            docs = ["Proof of Income", "ID Card", "Proof of Residency"]
            requires_hitl = True
        elif "snap" in lower_response:
            docs = ["Proof of Income", "Social Security Number"]
            requires_hitl = True
        elif "liheap" in lower_response:
            docs = ["Utility Bill", "Proof of Income"]
            requires_hitl = True

    # If already confirmed or not required, just yield the response and exit
    if not requires_hitl or ctx.state.get("hitl_status") == "provided":
        if isinstance(node_input, str):
            yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=node_input)]))
        else:
            yield Event(content=node_input)
        yield Event(output={"response": response_text, "status": "completed"})
        return
        
    interrupt_id = "confirm_docs"
    if not ctx.resume_inputs or interrupt_id not in ctx.resume_inputs:
        # Prompt the user for input
        docs_list = ", ".join(docs)
        msg = f"✋ **HITL Action Required:** Please confirm if you have the following documents ready to apply: **{docs_list}**. (Reply with 'yes' or list what you have)"
        
        # Mark status as requested
        ctx.state["hitl_status"] = "requested"
        
        # Yield the main agent's text first
        if isinstance(node_input, str):
            yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=node_input)]))
        else:
            yield Event(content=node_input)
        # Yield the interrupt question
        yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=f"\n\n{msg}")]))
        yield RequestInput(
            interrupt_id=interrupt_id,
            message=msg
        )
        return
        
    # We have the user's confirmation!
    user_reply = ctx.resume_inputs[interrupt_id]
    ctx.state["user_confirmed_documents"] = "yes" in user_reply.lower() or "have" in user_reply.lower()
    ctx.state["hitl_status"] = "provided"
    
    result_msg = f"\n\nThank you for confirming. You replied: \"{user_reply}\". Proceeding with your application guidance..."
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=result_msg)]))
    
    final_data = {
        "response": response_text,
        "hitl_response": user_reply,
        "user_confirmed_documents": ctx.state["user_confirmed_documents"]
    }
    yield Event(output=final_data)

# ==========================================
# Workflow definition
# ==========================================

care_navigator_workflow = Workflow(
    name="care_navigator_workflow",
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {
            "SECURITY_EVENT": security_event_handler,
            "PROCEED": care_navigator_orchestrator
        }),
        (care_navigator_orchestrator, hitl_checkpoint),
    ],
    description="Secure workflow guiding users through health and social benefits navigation.",
)

root_agent = care_navigator_workflow

# App wrapping the workflow
app = App(
    root_agent=care_navigator_workflow,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True)
)
