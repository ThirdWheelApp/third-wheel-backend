import pytest

from app.agents.joint_agent import agent as joint_agent_module


class FakeRepo:
    def get_relationship_profile_text(self, group_id):
        return ""

    def get_group_context(self, group_id):
        return []

    def get_joint_guidance_context(self, group_id):
        return []


class CompiledLikeAsyncGraph:
    """
    Mimics LangGraph's compiled graph shape where ainvoke is available but is
    not necessarily detected by asyncio.iscoroutinefunction.
    """

    def __init__(self):
        self.ainvoke_called = False
        self.invoke_called = False

    def ainvoke(self, state):
        async def run():
            self.ainvoke_called = True
            return {
                **state,
                "response_text": "Let's slow this down and each name one concrete need.",
                "should_end_session": False,
                "turn_count": state["turn_count"] + 1,
            }

        return run()

    def invoke(self, state):
        self.invoke_called = True
        raise AssertionError("sync invoke should not be used when ainvoke exists")


@pytest.mark.asyncio
async def test_joint_agent_prefers_async_graph_path(monkeypatch):
    graph = CompiledLikeAsyncGraph()
    monkeypatch.setattr(joint_agent_module, "get_llm_client", lambda: object())
    monkeypatch.setattr(joint_agent_module, "create_joint_agent_graph", lambda **kwargs: graph)

    agent = joint_agent_module.JointAgent(group_id="group-1", repo=FakeRepo())

    response_text, should_end, context = await agent.process_message(
        user_input="hello",
        sender_name="Vas",
        messages_history=[],
    )

    assert graph.ainvoke_called is True
    assert graph.invoke_called is False
    assert response_text == "Let's slow this down and each name one concrete need."
    assert should_end is False
    assert context == {"private_a_context": [], "private_b_context": []}
