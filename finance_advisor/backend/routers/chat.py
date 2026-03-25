# backend/routers/chat.py

from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import json

from finance_advisor.backend.groq_client import client
from ..config import settings
from finance_advisor.backend.memory.store import memory_store
from finance_advisor.backend.models.chat import ChatRequest, ChatResponse

# Our custom MCP-like tool server
from finance_advisor.backend.mcp.server import get_mcp_schema, call_mcp_tool
from finance_advisor.backend.guardrails.input_guard import check_user_input, is_finance_related
from finance_advisor.backend.guardrails.output_guard import sanitize_output, append_disclaimer

from finance_advisor.backend.db.conversation_store import save_message
from finance_advisor.backend.db.user_store import ensure_user





router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat_endpoint(payload: ChatRequest):
    """
    PURE MCP mode:
    - No manual intent detection
    - No agents
    - LLM sees all tools and decides which one to call
    - Tools executed by our MCP tool registry
    - Supports tool chaining (multiple tool calls in one conversation)
    """

    try:
        session_id = payload.session_id

        ensure_user(session_id)

        # -----------------------------
        # 0. Input guardrails
        # -----------------------------
        allowed, guard_msg = check_user_input(payload.message)
        if not allowed:
            return ChatResponse(reply=guard_msg)
        
        # Check if message is finance-related
        if not is_finance_related(payload.message):
            finance_only_msg = (
                "I'm a specialized financial advisor and can only help with finance-related queries. "
                "Please ask me about investments, portfolio planning, mutual funds, insurance, retirement planning, or any other financial topics. "
                "For example: 'Guide me in investing 250000' or 'What is SIP?'"
            )
            save_message(payload.session_id, "user", payload.message)
            save_message(payload.session_id, "assistant", finance_only_msg)
            return ChatResponse(reply=finance_only_msg)
        
        # Save the user message into SQLite
        save_message(session_id, "user", payload.message)

        # -----------------------------
        # Load memory
        # -----------------------------
        entity = memory_store.get_entity(session_id)
        summary = memory_store.get_summary(session_id)

        system_prompt = (
            "You are a qualified Indian financial advisor. "
            "You are a SEBI-aware Indian financial advisor assistant. "
            "You MUST follow these rules strictly:\n"
            "1. Do NOT provide guaranteed, risk-free, or sure-shot returns.\n"
            "2. Do NOT suggest illegal, unethical, or non-compliant practices "
            "(including insider trading, market manipulation, tax evasion, or misuse of financial products).\n"
            "3. For product-definitions or financial terms, provide accurate definitions based on your knowledge.\n"
            "4. For regulatory, SEBI, or product-definition questions, provide accurate information.\n"
            "5. Make risk disclosures explicit and remind the user that all market-linked products carry risk.\n"
            "6. If a user asks for something unsafe, illegal, or outside allowed scope, politely refuse and explain why.\n"
            "7. Always ensure safety, SEBI compliance, and clarity.\n"
            "8. Prioritize detailed responses for all finance queries: include reasoning, step-by-step calculations, assumptions, and relevant context.\n"
            "9. When a user asks to convert currency, use the currency_tool to fetch real-time conversion rates.\n"
            "10. For currency conversions: ALWAYS display the calculation clearly in this format:\n"
            "    Amount Source = Amount Target (Exchange Rate: Rate)\n"
            "    Example: 100 USD = 9,405 INR (Exchange Rate: 1 USD = 94.05 INR).\n"
            "11. Include the TOTAL CONVERTED AMOUNT prominently in your response.\n"
            "12. Also provide additional context, such as historical trend notes, typical fee expectations, and how rates may change.\n"
            "13. Format results clearly with proper formatting and present them in an easy-to-understand manner.\n"
            "14. IMPORTANT: For NAV, fund_type, or risk_level queries about mutual funds, ALWAYS use the nav_tool:\n"
            "    - When user asks: 'What is the NAV of HDFC Equity Fund?'\n"
            "    - When user asks: 'What type of fund is this?'\n"
            "    - When user asks: 'What is the risk level?'\n"
            "    Call nav_tool with the fund name or scheme code to get real-time data including NAV, fund_type, and risk_level.\n"
            "15. After using nav_tool, provide a comprehensive explanation with context:\n"
            "    - NAV definition and what it means\n"
            "    - Current NAV value and date\n"
            "    - Fund type and its characteristics\n"
            "    - Risk level and what it implies\n"
            "    - Suitability for different investor profiles\n"
            "16. For investment queries (like 'I have 25k, suggest investment plan'), ALWAYS provide detailed, helpful suggestions. Do NOT refuse or say you cannot provide advice.\n"
            "17. For investment queries, provide detailed, helpful suggestions including:\n"
            "    - Risk assessment based on amount and goals\n"
            "    - Diversified portfolio recommendations\n"
            "    - Specific product suggestions (mutual funds, SIPs, FDs, etc.)\n"
            "    - Step-by-step implementation plan\n"
            "    - Expected returns (historical averages, not guarantees)\n"
            "    - Tax implications and benefits\n"
            "    - Emergency fund considerations\n"
            "    - Regular review and rebalancing suggestions\n"
            "18. Always suggest consulting a SEBI-registered advisor for personalized advice, but provide comprehensive general guidance.\n"
            "19. Structure investment responses with clear sections: Risk Profile, Recommended Allocation, Implementation Steps, Expected Returns, and Important Notes.\n"
            "20. NEVER respond with 'I cannot provide financial advice' or similar refusals for legitimate investment questions."
        )

        memory_context = (
            f"User Profile Memory: {entity}\n"
            f"Summary Memory: {summary or 'None'}"
        )

        # Get MCP tools schema
        mcp_tools = get_mcp_schema()

        # -----------------------------
        # Build LLM messages
        # -----------------------------
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": memory_context},
            {"role": "user", "content": payload.message},
        ]

        # ================================
        # Multi-turn tool calling loop
        # ================================
        max_iterations = 5
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            
            # Ask LLM
            response = client.chat.completions.create(
                model=settings.groq_model,
                messages=messages,
                tools=mcp_tools,
            )

            # Check if we got a tool call or a regular message
            stop_reason = response.choices[0].finish_reason
            message_obj = response.choices[0].message

            # Add assistant's response to messages
            messages.append({"role": "assistant", "content": message_obj.content or "", "tool_calls": getattr(message_obj, 'tool_calls', None)})

            # If no tool calls, break the loop
            if stop_reason == "end_turn" or not hasattr(message_obj, 'tool_calls') or not message_obj.tool_calls:
                raw_reply = message_obj.content or ""
                break

            # Execute all tool calls
            tool_results = []
            for tool_call in message_obj.tool_calls:
                print(f"[Tool Call] Executing: {tool_call.function.name} with args: {tool_call.function.arguments}")
                tool_result = call_mcp_tool(tool_call)
                print(f"[Tool Result] {tool_result}")
                
                # Format tool result for better LLM understanding
                tool_name = tool_call.function.name
                
                # Special formatting for currency tool
                if tool_name == "currency_tool" and isinstance(tool_result, dict) and tool_result.get("success"):
                    content = (
                        f"Currency Conversion Result:\n"
                        f"{tool_result.get('formatted_message', 'Conversion completed')}\n"
                        f"Details: {json.dumps(tool_result, indent=2)}"
                    )
                else:
                    content = json.dumps(tool_result, indent=2)
                
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id if hasattr(tool_call, 'id') else tool_call.function.name,
                    "content": content,
                })

            # Add tool results to messages
            for tool_result in tool_results:
                messages.append({
                    "role": "user",
                    "content": tool_result["content"],
                })

        # Get final reply (exit loop with last message content)
        raw_reply = messages[-1].get("content", "") if messages else ""

        # If raw_reply is still empty, get it from the last non-tool response
        if not raw_reply:
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    raw_reply = msg["content"]
                    break

        # --------------------------------------
        # Apply output guardrails
        # --------------------------------------
        cleaned_text, _ = sanitize_output(raw_reply)
        final_reply = append_disclaimer(cleaned_text)

        # Save assistant reply
        save_message(session_id, "assistant", final_reply)
        memory_store.save_summary(session_id, final_reply)

        return ChatResponse(reply=final_reply)

    except Exception as ex:
        print("----------- BACKEND /chat ERROR -----------")
        import traceback
        traceback.print_exc()
        print("--------------------------------------------")
        raise HTTPException(status_code=500, detail=str(ex))
