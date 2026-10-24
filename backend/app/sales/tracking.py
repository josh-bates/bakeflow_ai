"""UC-02 read-only Sales Agent explanations grounded in recorded order stages."""
import json

from app.llm.gateway import ILLMClient, LLMGateway, MockLLMClient, InvalidLLMOutput, parse_structured_reply
from app.workflow.order_flow import WorkflowService, OrderStage, RecordRef, AIOperationFailed

# These describe stages, not promises about a particular order's price or timing.
# Restricting the agent to approved explanations prevents invented delivery,
# payment, approval and allergen claims from reaching the customer.
EXPLANATIONS = {
    "Draft": ("Your requirements are saved as a draft. This is not a confirmed sale. The bakery needs to review the request before quotation.",
              "The bakery has your draft request. Next comes review and quotation; your order is not confirmed yet."),
    "Quoted": ("Your order is recorded as quoted. Review the available quotation before deciding whether to accept it.",
               "The order has reached the quotation stage. Your next step is to review the quotation."),
    "Confirmed": ("Your order is confirmed. Production scheduling is the next stage; a collection-ready time is not established by this status alone.",
                  "The recorded order is confirmed and has not yet reached the scheduled stage. The bakery will arrange production next."),
    "Scheduled": ("Your order is scheduled for production. The next stage is production; this status does not mean it is ready for collection.",
                  "A production schedule is recorded for your order. Watch for the status to change when production starts."),
    "InProduction": ("Your order is in production. Completion is the next stage; please wait for collection instructions from the bakery.",
                     "The bakery is producing your order. It has not yet been recorded as completed."),
    "Completed": ("Your order is recorded as completed. Check collection arrangements with the bakery; this status alone does not confirm collection or payment.",
                  "Production is recorded as complete. Contact the bakery if you need to confirm your collection arrangements."),
    "Cancelled": ("Your order is cancelled. Contact the bakery if you want to discuss a new request; this status does not establish any refund outcome.",
                  "The recorded order has been cancelled. A new request would need a separate review by the bakery."),
}
UNAVAILABLE = "Explanation unavailable. Your recorded order status is shown above. Please try again later."


class StatusSalesAgent:
    def __init__(self, client: ILLMClient):
        self.client = client

    def explain(self, status: str) -> str:
        choices = EXPLANATIONS[status]
        prompt = (
            "Explain a customer's recorded bakery order stage. This is read-only: never change its status. "
            "Choose the clearest applicable explanation from the approved choices and copy it exactly. "
            "Do not add promises, dates, payment facts or other details. Return only JSON with status and explanation. "
            f"Recorded status: {json.dumps(status)}. Approved explanations: {json.dumps(choices)}"
        )
        result = parse_structured_reply(self.client.complete(prompt), ("status", "explanation"))
        if set(result) != {"status", "explanation"} or result["status"] != status or result["explanation"] not in choices:
            raise InvalidLLMOutput("Explanation does not match the recorded stage and approved facts.")
        return result["explanation"]


def create_client(status: str, mode: str) -> ILLMClient:
    if mode == "mock":
        return MockLLMClient(default_reply=json.dumps({"status": status, "explanation": EXPLANATIONS[status][0]}))
    return LLMGateway.from_environment()


def explain_record(status: str, version: int, order_id: str, actor_id: str, mode: str) -> dict:
    try:
        explanation = WorkflowService().run_ai_task(
            RecordRef("Order", order_id, version), OrderStage(status),
            lambda: StatusSalesAgent(create_client(status, mode)).explain(status),
            lambda value: isinstance(value, str) and bool(value),
            actor=actor_id, event_type="explainOrderStatus",
        )
        return {"explanation": explanation, "explanationAvailable": True}
    except (AIOperationFailed, ValueError, KeyError):
        return {"explanation": UNAVAILABLE, "explanationAvailable": False}
