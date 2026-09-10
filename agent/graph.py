"""
agent/graph.py

Assembles the full LangGraph workflow for Clause Guard AI.

Flow:

    validate_input
        -> [input_valid?]

            False -> decide_and_route -> END

            True
                -> document_processing
                -> classify_contract
                -> extract_clauses
                -> guardrail_check

                    blocked -> decide_and_route -> END

                    continue
                        -> retrieve_playbook_rules
                        -> score_risk
                        -> reflect
                        -> decide_and_route
                        -> END
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
    """
    Decide whether the pipeline should continue after validation.
    """

    return (
        "continue"
        if state.get("input_valid")
        else "halt"
    )


def _route_after_guardrail(state: GraphState) -> str:
    """
    Decide whether the pipeline should continue after guardrails.
    """

    return (
        "halt"
        if state.get("blocked")
        else "continue"
    )


def build_graph():
    """
    Build and compile the Clause Guard AI LangGraph workflow.
    """

    graph = StateGraph(GraphState)

    # --------------------------------------------------------------
    # Nodes
    # --------------------------------------------------------------

    graph.add_node(
        "validate_input",
        validate_input_node,
    )

    graph.add_node(
        "document_processing",
        document_processing_node,
    )

    graph.add_node(
        "classify_contract",
        classify_contract_node,
    )

    graph.add_node(
        "extract_clauses",
        extract_clauses_node,
    )

    graph.add_node(
        "guardrail_check",
        guardrail_check_node,
    )

    graph.add_node(
        "retrieve_playbook_rules",
        retrieve_playbook_rules,
    )

    graph.add_node(
        "score_risk",
        score_risk,
    )

    graph.add_node(
        "reflect",
        reflect_node,
    )

    graph.add_node(
        "decide_and_route",
        decide_and_route_node,
    )

    # --------------------------------------------------------------
    # Entry point
    # --------------------------------------------------------------

    graph.set_entry_point(
        "validate_input"
    )

    # --------------------------------------------------------------
    # Validation routing
    # --------------------------------------------------------------

    graph.add_conditional_edges(
        "validate_input",
        _route_after_validate,
        {
            "continue": "document_processing",
            "halt": "decide_and_route",
        },
    )

    # --------------------------------------------------------------
    # Main processing pipeline
    # --------------------------------------------------------------

    graph.add_edge(
        "document_processing",
        "classify_contract",
    )

    graph.add_edge(
        "classify_contract",
        "extract_clauses",
    )

    graph.add_edge(
        "extract_clauses",
        "guardrail_check",
    )

    # --------------------------------------------------------------
    # Guardrail routing
    # --------------------------------------------------------------

    graph.add_conditional_edges(
        "guardrail_check",
        _route_after_guardrail,
        {
            "continue": "retrieve_playbook_rules",
            "halt": "decide_and_route",
        },
    )

    # --------------------------------------------------------------
    # Retrieval -> scoring -> reflection
    # --------------------------------------------------------------

    graph.add_edge(
        "retrieve_playbook_rules",
        "score_risk",
    )

    graph.add_edge(
        "score_risk",
        "reflect",
    )

    graph.add_edge(
        "reflect",
        "decide_and_route",
    )

    # --------------------------------------------------------------
    # End
    # --------------------------------------------------------------

    graph.add_edge(
        "decide_and_route",
        END,
    )

    return graph.compile()