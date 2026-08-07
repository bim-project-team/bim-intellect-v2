import json
import logging
from typing import Dict, Any

from bim_graph.graph_retriever import GraphRetriever
from rag.openrouter_client import chat_completion
from rag.prompts import ROUTER_PROMPT, COMBINE_PROMPT
from rag.retriever import get_vector_context

logger = logging.getLogger("bim_intellect.orchestrator")


class RAGOrchestrator:
    """
    Coordinates the Hybrid RAG flow:
    1. Route: Decides if the question needs Vector DB, Graph DB, or both.
    2. Retrieve: Fetches data from the selected sources.
    3. Generate: Combines context and generates a final cited answer.
    """

    def __init__(self):
        self.graph_retriever = GraphRetriever()

    def route_question(self, question: str) -> Dict[str, Any]:
        """Ask the LLM to classify the required data sources."""
        messages = [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": question}
        ]

        try:
            content = chat_completion(messages, temperature=0.0)
            
            # Defensive clean up in case LLM added markdown block
            content = content.replace("```json", "").replace("```", "").strip()
            decision = json.loads(content)
            
            logger.info(f"Router decision: vector={decision.get('needs_vector')} "
                        f"graph={decision.get('needs_graph')} | {decision.get('reasoning')}")
            return decision

        except json.JSONDecodeError as e:
            logger.warning(f"Router failed to return valid JSON. Fallback to both sources. Error: {e}")
        except Exception as e:
            logger.warning(f"Router failed ({e}) — falling back to both sources.")

        # Fail safe: if routing breaks, pull from everywhere
        return {
            "needs_vector": True,
            "needs_graph": True,
            "reasoning": "Fallback due to error"
        }

    def ask(self, question: str) -> Dict[str, Any]:
        """Execute the full Hybrid RAG pipeline."""
        decision = self.route_question(question)
        
        context_parts = []
        sources = []
        
        # 1. Vector Retrieval (Regulations)
        if decision.get("needs_vector", True):
            try:
                v_context, v_sources = get_vector_context(question, k=5)
                if v_context:
                    context_parts.append(f"--- REGULATION CONTEXT ---\n{v_context}")
                    sources.extend(v_sources)
            except Exception as e:
                logger.error(f"Vector retrieval failed: {e}")

        # 2. Graph Retrieval (BIM Elements)
        if decision.get("needs_graph", True):
            try:
                g_result = self.graph_retriever.ask(question)
                if g_result and g_result.get("context"):
                    context_parts.append(f"--- BUILDING GRAPH CONTEXT ---\n{g_result['context']}")
                    sources.extend(g_result.get("sources", []))
            except Exception as e:
                logger.error(f"Graph retrieval failed: {e}")

        # 3. Combine and Generate
        if not context_parts:
            # If no context was retrieved at all, short-circuit
            return {
                "answer": "I couldn't find any relevant regulations or building elements to answer your question.",
                "sources": [],
                "used_vector": decision.get("needs_vector", True),
                "used_graph": decision.get("needs_graph", True)
            }

        combined_context = "\n\n".join(context_parts)
        prompt = COMBINE_PROMPT.format(context=combined_context, question=question)

        messages = [
            {"role": "user", "content": prompt}
        ]

        try:
            answer = chat_completion(messages, temperature=0.0)
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            answer = "Sorry, I encountered an error while generating the final answer."

        return {
            "answer": answer,
            "sources": sources,
            "used_vector": decision.get("needs_vector", True),
            "used_graph": decision.get("needs_graph", True)
        }