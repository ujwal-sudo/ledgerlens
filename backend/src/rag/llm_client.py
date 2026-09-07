import os
from typing import Any, Dict, Optional

import google.generativeai as genai


class GeminiClient:
    """Gemini Flash LLM client for the investigation agent.

    Wraps the Google Generative AI API with structured output support.
    All financial arithmetic is OFF-LIMITS for this model — the LLM only
    receives pre-calculated numbers and reasons about WHY, not WHAT THE NUMBER IS.
    """

    def __init__(self, api_key: str | None = None, model_name: str = "gemini-1.5-flash"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY must be set in environment or passed to client")
        genai.configure(api_key=self.api_key)
        self.model_name = model_name
        self.model = genai.GenerativeModel(model_name)

    def generate_structured(self, prompt: str, schema: dict | None = None, max_tokens: int = 1000) -> dict:
        """Generate LLM response, optionally constrained by a JSON schema.

        Args:
            prompt: The prompt to send to the LLM
            schema: Optional JSON schema to constrain the output
            max_tokens: Maximum tokens to generate

        Returns:
            Parsed dictionary from LLM response
        """
        full_prompt = prompt
        if schema:
            # Add schema instruction to prompt
            full_prompt = prompt + "\n\nReturn your response as valid JSON matching this schema: " + str(schema)

        try:
            response = self.model.generate_content(
                full_prompt,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.2,  # Low temperature for deterministic-ish behavior
                )
            )

            text = response.text.strip()

            # Try to parse as JSON
            import json
            try:
                result = json.loads(text)
                if schema and not self._validate_schema(result, schema):
                    # Schema validation failed — return raw parsed output
                    return {"_raw": text, "_schema_warn": "schema validation failed"}
                return result
            except json.JSONDecodeError:
                # Return raw text if not JSON
                return {"_raw": text, "_schema_warn": "not json"}

        except Exception as e:
            # Return structured error — never propagate exception to caller
            return {
                "_error": str(e),
                "_type": type(e).__name__,
            }

    def _validate_schema(self, data: dict, schema: dict) -> bool:
        """Basic schema validation — check required fields exist."""
        if not isinstance(data, dict):
            return False
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for field in required:
            if field not in data:
                return False
        # Check types of known properties
        for field, rules in properties.items():
            if field in data and "type" in rules:
                expected_type = rules["type"]
                if expected_type == "string" and not isinstance(data[field], str):
                    return False
                if expected_type == "number" and not isinstance(data[field], (int, float)):
                    return False
                if expected_type == "boolean" and not isinstance(data[field], bool):
                    return False
        return True

    def call_tool_style(self, system_prompt: str, user_prompt: str) -> dict:
        """Agent tool call format: system prompt + user prompt.

        This is the format used by the InvestigationAgent ReAct loop.
        The system prompt enforces the AI/Deterministic boundary.
        """
        full_system = system_prompt
        full_user = user_prompt

        # Inject financial boundary reminder into system prompt
        if "FINANCIAL_BOUNDARY" not in full_system:
            full_system += "\n\nIMPORTANT: You are a financial investigation agent. " \
                           "NEVER perform arithmetic (addition, subtraction, multiplication, division) " \
                           "within your responses. All calculations are handled by the deterministic " \
                           "FinancialCalculator. You may only reason about pre-calculated numbers " \
                           "and evaluate evidence. Do not generate or modify numeric values."

        try:
            response = self.model.generate_content(
                [{"role": "system", "content": full_system},
                 {"role": "user", "content": full_user}],
                generation_config=genai.GenerationConfig(
                    max_output_tokens=1000,
                    temperature=0.2,
                )
            )

            text = response.text.strip()

            # Try to extract JSON from the response
            import json
            # Find JSON block if present
            start = text.find('{')
            end = text.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = text[start:end]
                try:
                    result = json.loads(json_str)
                    return {"json": result, "raw": text}
                except json.JSONDecodeError:
                    pass

            # Try parsing entire text as JSON
            try:
                result = json.loads(text)
                return {"json": result, "raw": text}
            except json.JSONDecodeError:
                pass

            # Return raw text
            return {"raw": text, "json": None}

        except Exception as e:
            return {
                "_error": str(e),
                "_type": type(e).__name__,
            }