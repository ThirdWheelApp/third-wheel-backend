"""
LangGraph State Machine for Joint Agent

Defines the state machine that orchestrates the joint therapy session.
Uses LangGraph for structured multi-step agent workflow.
"""

from typing import TypedDict, Annotated, Sequence, Literal, Union
from langgraph.graph import StateGraph, END
from app.config.settings import settings
from app.demo import get_llm_client
from app.config.prompts import format_context_for_llm, format_messages_for_llm, SESSION_END_DETECTION_PROMPT
from app.agents.private_agent.agent import PrivateAgent
from app.utils.logger import get_logger
import time
import operator

logger = get_logger(__name__)


class JointAgentState(TypedDict):
    """
    State for the Joint Agent workflow.

    This state is passed between nodes in the LangGraph state machine.
    """
    # Inputs
    user_input: str
    sender_name: str
    messages_history: list
    relationship_profile: str
    group_context: str
    joint_guidance_context: str

    # Accumulated context from private agents
    private_a_context: Annotated[Sequence[str], operator.add]
    private_b_context: Annotated[Sequence[str], operator.add]

    # Query tracking
    query_count_a: int
    query_count_b: int
    turn_count: int

    # Decision flags
    needs_more_context: bool
    should_end_session: bool

    # Output
    response_text: str
    error: str | None

    # Metrics
    input_tokens: int
    output_tokens: int
    latency_ms: int


def create_joint_agent_graph(
    client,  # Anthropic or MockAnthropicClient
    group_id: str,
    private_agent_a: PrivateAgent | None = None,
    private_agent_b: PrivateAgent | None = None,
    relationship_profile: str = "",
    group_context: str = "",
    joint_guidance_context: str = ""
):
    """
    Create a LangGraph state machine for the Joint Agent.

    The graph has the following nodes:
    1. decide_context_need - Decides if private context is needed
    2. query_private_agents - Queries private agents for context
    3. generate_response - Generates therapeutic response
    4. check_end_session - Checks if session should end

    Args:
        client: Anthropic client
        group_id: UUID of the relationship
        private_agent_a: Private agent for partner A
        private_agent_b: Private agent for partner B
        group_context: Shared group context

    Returns:
        Compiled LangGraph state machine
    """

    # Node 1: Decide if we need more context
    def decide_context_need(state: JointAgentState) -> JointAgentState:
        """
        Use LLM to decide if we need to query private agents.

        This replaces the simple keyword-based approach with LLM reasoning.
        """
        logger.info(f"[JointAgent] Deciding context need for message: {state['user_input'][:50]}...")

        # Check if we've hit query limits
        if (state['query_count_a'] >= settings.MAX_PRIVATE_AGENT_QUERIES and
            state['query_count_b'] >= settings.MAX_PRIVATE_AGENT_QUERIES):
            logger.info("[JointAgent] Query limits reached, skipping context check")
            state['needs_more_context'] = False
            return state

        # Check if we've hit turn limits
        if state['turn_count'] >= settings.MAX_AGENT_TURNS:
            logger.info("[JointAgent] Turn limit reached, skipping context check")
            state['needs_more_context'] = False
            return state

        # Build prompt for decision
        recent_history = format_messages_for_llm(state['messages_history'][-5:])
        decision_prompt = f"""You are a couples therapist assistant analyzing a conversation.

Recent conversation:
{recent_history}

Current message from {state['sender_name']}: {state['user_input']}

Question: Do you need additional private context about the individuals to provide a thoughtful response?

Consider these factors:
- Is this about recurring patterns or behaviors?
- Are there emotional triggers or past events referenced?
- Would understanding individual histories help?
- Is this a surface-level question or deeper issue?

Answer with ONLY "YES" or "NO" and a brief reason (one sentence).

Format: YES|reason or NO|reason"""

        try:
            start_time = time.time()
            response = client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=100,
                temperature=0.3,  # Lower temperature for more consistent decisions
                messages=[{"role": "user", "content": decision_prompt}]
            )
            latency = int((time.time() - start_time) * 1000)

            decision_text = response.content[0].text.strip()

            # Parse decision before logging. The model-provided reason can name
            # sensitive inferred topics, so logs should only include the boolean.
            needs_context = decision_text.upper().startswith("YES")
            logger.info(
                "[JointAgent] Context decision: %s (%sms)",
                "YES" if needs_context else "NO",
                latency,
            )
            state['needs_more_context'] = needs_context
            state['input_tokens'] = state.get('input_tokens', 0) + response.usage.input_tokens
            state['output_tokens'] = state.get('output_tokens', 0) + response.usage.output_tokens
            state['latency_ms'] = state.get('latency_ms', 0) + latency

            return state

        except Exception as e:
            logger.error(f"[JointAgent] Error in context decision: {e}")
            # Default to not needing context on error
            state['needs_more_context'] = False
            return state

    # Node 2: Query private agents
    async def query_private_agents(state: JointAgentState) -> JointAgentState:
        """Query private agents for relevant context."""
        logger.info("[JointAgent] Querying private agents for context...")

        # Build query for private agents
        query = f"""Based on this couples therapy conversation message, what relevant context do you have?

Message from {state['sender_name']}: {state['user_input']}

Provide a concise summary (2-3 sentences) of relevant patterns, history, or context that would help the couples therapist respond effectively. If nothing is relevant, say "No specific context available."

IMPORTANT: Only share information with secret_level <= {settings.COUPLES_MAX_SECRET_LEVEL}."""

        # Query Private Agent A
        if (private_agent_a and
            state['query_count_a'] < settings.MAX_PRIVATE_AGENT_QUERIES):
            try:
                logger.info(f"[JointAgent] Querying Private Agent A ({state['query_count_a'] + 1}/{settings.MAX_PRIVATE_AGENT_QUERIES})")
                context_a = await private_agent_a.get_group_message(query)

                if context_a and "No specific context" not in context_a:
                    state['private_a_context'] = state.get('private_a_context', []) + [context_a]
                    state['query_count_a'] = state.get('query_count_a', 0) + 1
                    logger.info(
                        "[JointAgent] Received context from Agent A (%s chars)",
                        len(context_a),
                    )
            except Exception as e:
                logger.error(f"[JointAgent] Error querying Private Agent A: {e}")

        # Query Private Agent B
        if (private_agent_b and
            state['query_count_b'] < settings.MAX_PRIVATE_AGENT_QUERIES):
            try:
                logger.info(f"[JointAgent] Querying Private Agent B ({state['query_count_b'] + 1}/{settings.MAX_PRIVATE_AGENT_QUERIES})")
                context_b = await private_agent_b.get_group_message(query)

                if context_b and "No specific context" not in context_b:
                    state['private_b_context'] = state.get('private_b_context', []) + [context_b]
                    state['query_count_b'] = state.get('query_count_b', 0) + 1
                    logger.info(
                        "[JointAgent] Received context from Agent B (%s chars)",
                        len(context_b),
                    )
            except Exception as e:
                logger.error(f"[JointAgent] Error querying Private Agent B: {e}")

        state['turn_count'] = state.get('turn_count', 0) + 1
        return state

    # Node 3: Generate therapeutic response
    def generate_response(state: JointAgentState) -> JointAgentState:
        """Generate the final therapeutic response."""
        logger.info("[JointAgent] Generating therapeutic response...")

        # Build context sections
        context_sections = []

        if state.get('relationship_profile'):
            context_sections.append(f"Relationship profile:\n{state['relationship_profile']}")

        if state.get('group_context'):
            context_sections.append(f"Relationship context:\n{state['group_context']}")

        if state.get('joint_guidance_context'):
            context_sections.append(
                "Redacted private-informed therapist guidance (internal only; "
                "do not mention the source or imply hidden facts). Apply this "
                "only when the live conversation itself raises trust, emotional "
                "distance, honesty, accountability, repair, or emotional safety; "
                "otherwise ignore it:\n"
                f"{state['joint_guidance_context']}"
            )

        if state.get('private_a_context'):
            context_a = "\n".join(state['private_a_context'])
            context_sections.append(f"Context about Partner A:\n{context_a}")

        if state.get('private_b_context'):
            context_b = "\n".join(state['private_b_context'])
            context_sections.append(f"Context about Partner B:\n{context_b}")

        full_context = "\n\n".join(context_sections) if context_sections else "No additional context available."

        # Format conversation history
        history_text = format_messages_for_llm(state['messages_history'][-15:])

        # Build comprehensive prompt
        from app.config.prompts import JOINT_AGENT_SYSTEM_PROMPT

        prompt = f"""{full_context}

Recent conversation:
{history_text}

{state['sender_name']}: {state['user_input']}

As the couples therapist, reply in a concise spoken turn. Acknowledge the live concern, name one pattern or emotion if useful, offer one concrete next move, and ask at most one simple question. Keep it 60-120 words. Do not use markdown formatting or long bullet lists unless the partners explicitly ask for a plan. Do not write generic privacy-boundary filler; if you are constrained by privacy, still make a specific therapeutic move grounded only in the live transcript.

Your response:"""

        try:
            start_time = time.time()
            response = client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=min(settings.MAX_TOKENS, 420),
                temperature=settings.LLM_TEMPERATURE,
                system=JOINT_AGENT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            latency = int((time.time() - start_time) * 1000)

            state['response_text'] = response.content[0].text
            state['input_tokens'] = state.get('input_tokens', 0) + response.usage.input_tokens
            state['output_tokens'] = state.get('output_tokens', 0) + response.usage.output_tokens
            state['latency_ms'] = state.get('latency_ms', 0) + latency

            logger.info(f"[JointAgent] Generated response ({len(state['response_text'])} chars, {latency}ms)")
            return state

        except Exception as e:
            logger.error(f"[JointAgent] Error generating response: {e}", exc_info=True)
            state['error'] = str(e)
            state['response_text'] = (
                "I'm having trouble reaching the therapist model right now. "
                "Your message was received, but I can't give a thoughtful "
                "response until the connection is back. Please try again in a moment."
            )
            return state

    # Node 4: Check if session should end (LLM-based detection)
    def check_end_session(state: JointAgentState) -> JointAgentState:
        """
        Use LLM to determine if session is naturally concluding.

        Replaces heuristic keyword matching with intelligent analysis
        of conversation flow, emotional state, and resolution indicators.
        """
        logger.info("[JointAgent] Checking if session should end (LLM-based)...")

        # Build recent conversation including current exchange
        recent_conv = format_messages_for_llm(state['messages_history'][-8:])
        recent_conv += f"\n{state['sender_name']}: {state['user_input']}"
        recent_conv += f"\nTherapist: {state['response_text']}"

        # Build prompt for session end detection
        prompt = SESSION_END_DETECTION_PROMPT.format(conversation=recent_conv)

        try:
            start_time = time.time()
            response = client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=100,
                temperature=0.3,  # Lower temperature for consistent decisions
                messages=[{"role": "user", "content": prompt}]
            )
            latency = int((time.time() - start_time) * 1000)

            decision_text = response.content[0].text.strip()
            logger.info(f"[JointAgent] Session end detection: {decision_text} ({latency}ms)")

            # Parse decision
            should_end = decision_text.upper().startswith("YES")
            state['should_end_session'] = should_end

            # Track token usage
            state['input_tokens'] = state.get('input_tokens', 0) + response.usage.input_tokens
            state['output_tokens'] = state.get('output_tokens', 0) + response.usage.output_tokens
            state['latency_ms'] = state.get('latency_ms', 0) + latency

            if should_end:
                logger.info("[JointAgent] LLM suggests session should end")

        except Exception as e:
            logger.error(f"[JointAgent] Error in session end detection: {e}")
            # Fall back to not suggesting end on error
            state['should_end_session'] = False

        return state

    # Routing function
    def route_after_decision(state: JointAgentState) -> Literal["query_agents", "generate"]:
        """Route based on whether we need more context."""
        if state.get('needs_more_context', False):
            return "query_agents"
        return "generate"

    # Build the graph
    workflow = StateGraph(JointAgentState)

    # Add nodes
    workflow.add_node("decide_context", decide_context_need)
    workflow.add_node("query_agents", query_private_agents)
    workflow.add_node("generate", generate_response)
    workflow.add_node("check_end", check_end_session)

    # Define edges
    workflow.set_entry_point("decide_context")
    workflow.add_conditional_edges(
        "decide_context",
        route_after_decision,
        {
            "query_agents": "query_agents",
            "generate": "generate"
        }
    )
    workflow.add_edge("query_agents", "generate")
    workflow.add_edge("generate", "check_end")
    workflow.add_edge("check_end", END)

    # Compile and return
    return workflow.compile()
