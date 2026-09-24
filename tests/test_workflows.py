from app.workflows import WorkflowEngine


def test_workflow_state_and_context_update():
    engine = WorkflowEngine()

    workflow = engine.create_workflow(
        conversation_id="conversation-1",
        tenant_id="tenant-1",
        business_id="business-1",
    )
    assert workflow["state"] == "new"
    assert workflow["context"] == {}

    workflow = engine.update_state(
        "conversation-1",
        "intent_classified",
        {"intent": "appointment_inquiry"},
    )
    assert workflow["state"] == "intent_classified"
    assert workflow["context"] == {"intent": "appointment_inquiry"}

    workflow = engine.update_state("conversation-1", "blocked", {"reason": "policy"})
    assert workflow["state"] == "blocked"
    assert workflow["context"] == {
        "intent": "appointment_inquiry",
        "reason": "policy",
    }
    assert engine.get("conversation-1") is workflow
