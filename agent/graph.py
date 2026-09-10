"""
agent/graph.py

Person 4 — assembles the full LangGraph workflow from every node the team
has built. This file did not exist yet in the repo; the pieces it wires
together (validate_input, document_processing, classify_contract,
extract_clauses, guardrail_check, retrieve_playbook_rules, score_risk)
were already built by Persons 1-3. reflect and decide_and_route are
this file's own siblings (nodes/reflect.py, nodes/decide_and_route.py).

Flow (matches docs/guardrails.md §1 and §2-3 short-circuit behavior):

  validate_input
    -> [conditional: input_valid?]
         False -> decide_and_route -> END   (docs/guardrails.md §1)
         True  -> document_processing -> classify_contract
                    -> extract_clauses -> guardrail_check
                    -> [conditional: blocked?]
                         True  -> decide_and_route -> END   (docs/guardrails.md §2-3)
                         False -> retrieve_playbook_rules -> score_risk
                                    -> reflect -> decide_and_route -> END
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from agent.state import GraphState
from nodes.classify_contract import classify_contract_node
from nodes.decide_and_route import decide_and_route_node
from nodes.document_processing import document_processing_node
from nodes.extract_clauses import extract_clauses_node
from nodes.guardrail_check import guardrail_check_node
from nodes.reflect import reflect_node
from nodes.retrieve_playbook_rules import retrieve_playbook_rules
from nodes.score_risk import score_risk
from nodes.validate_input import validate_input_node


def _route_after_validate(state: GraphState) -> str:
    return "continue" if state.get("input_valid") else "halt"


def _route_after_guardrail(state: GraphState) -> str:
    return "halt" if state.get("blocked") else "continue"


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("validate_input", validate_input_node)
    graph.add_node("document_processing", document_processing_node)
    graph.add_node("classify_contract", classify_contract_node)
    graph.add_node("extract_clauses", extract_clauses_node)
    graph.add_node("guardrail_check", guardrail_check_node)
    graph.add_node("retrieve_playbook_rules", retrieve_playbook_rules)
    graph.add_node("score_risk", score_risk)
    graph.add_node("reflect", reflect_node)
    graph.add_node("decide_and_route", decide_and_route_node)

    graph.set_entry_point("validate_input")

    graph.add_conditional_edges(
        "validate_input",
        _route_after_validate,
        {"continue": "document_processing", "halt": "decide_and_route"},
    )

    graph.add_edge("document_processing", "classify_contract")
    graph.add_edge("classify_contract", "extract_clauses")
    graph.add_edge("extract_clauses", "guardrail_check")

    graph.add_conditional_edges(
        "guardrail_check",
        _route_after_guardrail,
        {"continue": "retrieve_playbook_rules", "halt": "decide_and_route"},
    )

    graph.add_edge("retrieve_playbook_rules", "score_risk")
    graph.add_edge("score_risk", "reflect")
    graph.add_edge("reflect", "decide_and_route")
    graph.add_edge("decide_and_route", END)

    return graph.compile()
