"""
Output Parser for Level 3 Synthesis Engine

Extracts structured data from LLM responses with robust error handling.
Handles both Synthesis Agent and Validator responses.
"""

import json
import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass


@dataclass
class SynthesisResult:
    """Structured result from parsing Synthesis Agent response"""
    reasoning: str
    specification: str
    extracted_values: Dict[str, Any]
    parse_error: Optional[str] = None
    raw_response: str = ""


@dataclass
class ValidatorResult:
    """Structured result from parsing Validator response"""
    validator: str
    level: str
    overall_status: str
    results: List[Dict[str, Any]]
    parse_error: Optional[str] = None
    raw_response: str = ""


class OutputParser:
    """
    Parser for LLM responses with flexible section extraction and robust error handling.

    Handles two response types:
    1. Synthesis Agent - sections with reasoning, specification, and JSON values
    2. Validator - JSON response with constraint results
    """

    def __init__(self):
        # Flexible section header patterns (case-insensitive, allow various formats)
        # Matches: === REASONING ===, ## REASONING, ## Section 1: REASONING, ### REASONING, etc.
        self.section_patterns = {
            'reasoning': re.compile(
                r'^[\s]*(?:[=#\-]+\s*)?(?:Section\s*\d*:?\s*)?REASONING[\s:]*(?:[=#\-]+)?[\s]*$', 
                re.IGNORECASE | re.MULTILINE
            ),
            'specification': re.compile(
                r'^[\s]*(?:[=#\-]+\s*)?(?:Section\s*\d*:?\s*)?SPECIFICATION[\s:]*(?:[=#\-]+)?[\s]*$', 
                re.IGNORECASE | re.MULTILINE
            ),    
            'extracted_values': re.compile(
                r'^[\s]*(?:[=#\-]+\s*)?(?:Section\s*\d*:?\s*)?EXTRACTED[_\s]*VALUES?[\s:]*(?:[=#\-]+)?[\s]*$', 
                re.IGNORECASE | re.MULTILINE
            )
        }

        # JSON extraction patterns
        self.json_fence_pattern = re.compile(r'```\s*json\s*\n(.*?)\n```', re.DOTALL | re.IGNORECASE)
        self.json_brace_pattern = re.compile(r'(\{.*\})', re.DOTALL)

    def parse_synthesis_response(self, raw_text: str) -> SynthesisResult:
        """
        Parse Synthesis Agent response into structured components.

        Expected format (flexible):
        === REASONING === (or ## Section 1: REASONING or ## REASONING)
        [prose content]

        === SPECIFICATION === (or ## Section 2: SPECIFICATION or ## SPECIFICATION)
        [SEDF content]

        === EXTRACTED_VALUES === (or ## Section 3: EXTRACTED_VALUES or ## EXTRACTED_VALUES)
        ```json
        {...}
        ```

        Returns partial results with error flags if parsing fails.
        """
        try:
            # Extract sections
            sections = self._extract_sections(raw_text)

            # Extract JSON from extracted_values section
            extracted_values = {}
            json_error = None

            if 'extracted_values' in sections:
                extracted_values, json_error = self._extract_json_block(sections['extracted_values'])
            else:
                # Try to find JSON anywhere in the response as fallback
                extracted_values, json_error = self._extract_json_block(raw_text)

            # Build result
            result = SynthesisResult(
                reasoning=sections.get('reasoning', '').strip(),
                specification=sections.get('specification', '').strip(),
                extracted_values=extracted_values,
                raw_response=raw_text
            )

            # Check for errors
            errors = []
            if not result.reasoning:
                errors.append("Missing REASONING section")
            if not result.specification:
                errors.append("Missing SPECIFICATION section")
            if json_error and not extracted_values:
                errors.append(f"JSON parsing error: {json_error}")

            if errors:
                result.parse_error = "; ".join(errors)

            return result

        except Exception as e:
            return SynthesisResult(
                reasoning="",
                specification="",
                extracted_values={},
                parse_error=f"Critical parsing error: {str(e)}",
                raw_response=raw_text
            )

    def parse_validator_response(self, raw_text: str) -> ValidatorResult:
        """
        Parse Validator response into structured components.

        Expected format: JSON response with validator, level, overall_status, results fields

        Returns partial results with error flags if parsing fails.
        """
        try:
            # Try to extract JSON from the response
            json_data, json_error = self._extract_json_block(raw_text)

            if json_error:
                return ValidatorResult(
                    validator="unknown",
                    level="unknown",
                    overall_status="PARSE_ERROR",
                    results=[],
                    parse_error=f"JSON parsing error: {json_error}",
                    raw_response=raw_text
                )

            # Validate required fields
            required_fields = ['validator', 'level', 'overall_status', 'results']
            missing_fields = [field for field in required_fields if field not in json_data]

            if missing_fields:
                return ValidatorResult(
                    validator=json_data.get('validator', 'unknown'),
                    level=json_data.get('level', 'unknown'),
                    overall_status=json_data.get('overall_status', 'PARSE_ERROR'),
                    results=json_data.get('results', []),
                    parse_error=f"Missing required fields: {missing_fields}",
                    raw_response=raw_text
                )

            return ValidatorResult(
                validator=json_data['validator'],
                level=json_data['level'],
                overall_status=json_data['overall_status'],
                results=json_data['results'],
                raw_response=raw_text
            )

        except Exception as e:
            return ValidatorResult(
                validator="unknown",
                level="unknown",
                overall_status="PARSE_ERROR",
                results=[],
                parse_error=f"Critical parsing error: {str(e)}",
                raw_response=raw_text
            )

    def _extract_sections(self, text: str) -> Dict[str, str]:
        """
        Extract sections from text using flexible header patterns.

        Looks for section headers and extracts content between them.
        """
        sections = {}

        # Find all section headers and their positions
        header_matches = []
        for section_name, pattern in self.section_patterns.items():
            for match in pattern.finditer(text):
                header_matches.append((match.start(), match.end(), section_name))

        # Sort by position
        header_matches.sort(key=lambda x: x[0])

        # Extract content between headers
        for i, (start, end, section_name) in enumerate(header_matches):
            # Find content start (after header)
            content_start = end

            # Find content end (before next header or end of text)
            if i + 1 < len(header_matches):
                content_end = header_matches[i + 1][0]
            else:
                content_end = len(text)

            # Extract and clean content
            content = text[content_start:content_end].strip()
            sections[section_name] = content

        return sections

    def _extract_json_block(self, text: str) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Extract JSON from text with automatic code fence stripping.

        Strategy:
        1. Try to find JSON within markdown code fences
        2. If that fails, look for JSON between first { and last }
        3. Return parsed JSON and any error message

        Returns: (json_dict, error_message)
        """
        # Strategy 1: Look for JSON code fences
        fence_match = self.json_fence_pattern.search(text)
        if fence_match:
            json_text = fence_match.group(1).strip()
            try:
                return json.loads(json_text), None
            except json.JSONDecodeError as e:
                return {}, f"Invalid JSON in code fence: {str(e)}"

        # Strategy 2: Look for JSON between braces
        brace_match = self.json_brace_pattern.search(text)
        if brace_match:
            json_text = brace_match.group(1).strip()
            try:
                return json.loads(json_text), None
            except json.JSONDecodeError as e:
                return {}, f"Invalid JSON in braces: {str(e)}"

        # Strategy 3: Try parsing the entire text as JSON (fallback)
        try:
            return json.loads(text.strip()), None
        except json.JSONDecodeError:
            return {}, "No valid JSON found in text"

    def validate_synthesis_json(self, extracted_values: Dict[str, Any]) -> List[str]:
        """
        Validate that extracted JSON has expected structure for synthesis responses.

        Returns list of validation errors (empty if valid).
        """
        errors = []

        # Check for expected top-level fields
        expected_fields = ['level', 'parameters', 'mechanisms_claimed', 'domain_primitives']

        for field in expected_fields:
            if field not in extracted_values:
                errors.append(f"Missing expected field: {field}")

        # Validate level format
        if 'level' in extracted_values:
            level = extracted_values['level']
            if not isinstance(level, str) or not level.startswith('L'):
                errors.append(f"Invalid level format: {level}")

        # Validate parameters structure
        if 'parameters' in extracted_values:
            params = extracted_values['parameters']
            if not isinstance(params, dict):
                errors.append("Parameters must be a dictionary")
            else:
                # Check parameter structure
                for param_name, param_data in params.items():
                    if not isinstance(param_data, dict):
                        errors.append(f"Parameter {param_name} must be a dictionary")
                    else:
                        required_param_fields = ['value', 'unit', 'provenance']
                        for field in required_param_fields:
                            if field not in param_data:
                                errors.append(f"Parameter {param_name} missing field: {field}")

        # Validate domain_primitives structure
        if 'domain_primitives' in extracted_values:
            domains = extracted_values['domain_primitives']
            if not isinstance(domains, dict):
                errors.append("Domain primitives must be a dictionary")
            else:
                expected_domains = ['A', 'B', 'C', 'D', 'E', 'F']
                for domain in expected_domains:
                    if domain not in domains:
                        errors.append(f"Missing domain primitive: {domain}")

        return errors


# Test functions for development
def test_synthesis_parsing():
    """Test synthesis response parsing with various formats"""

    parser = OutputParser()
    
    # Test case 1: Claude's actual format (## Section N: NAME)
    test_response_1 = """## Section 1: REASONING

This is my design reasoning with multiple lines
and detailed explanations.

## Section 2: SPECIFICATION

L0 - Governing Concept
- Description here [FROM-A]
- More details [FROM-B]

## Section 3: EXTRACTED_VALUES

```json
{
  "level": "L0",
  "parameters": {},
  "mechanisms_claimed": ["cavitation"],
  "domain_primitives": {}
}
```
"""

    result1 = parser.parse_synthesis_response(test_response_1)
    print("Test 1 (Claude format) Results:")
    print(f"  Parse Error: {result1.parse_error}")
    print(f"  Reasoning Length: {len(result1.reasoning)}")
    print(f"  Specification Length: {len(result1.specification)}")
    print(f"  Extracted Values: {result1.extracted_values}")
    print()

    # Test case 2: Original format (=== NAME ===)
    test_response_2 = """
=== REASONING ===
This is my design reasoning.

=== SPECIFICATION ===
L5: Parameters
- Velocity: 25 m/s [FROM-A]

=== EXTRACTED_VALUES ===
```json
{
  "level": "L5",
  "parameters": {"velocity": {"value": 25, "unit": "m/s", "provenance": "[FROM-A]"}},
  "mechanisms_claimed": ["cavitation"],
  "domain_primitives": {"A": "A2", "B": "B1", "C": "C1", "D": "D2", "E": "E2", "F": "F1"}
}
```
"""

    result2 = parser.parse_synthesis_response(test_response_2)
    print("Test 2 (Original format) Results:")
    print(f"  Parse Error: {result2.parse_error}")
    print(f"  Reasoning Length: {len(result2.reasoning)}")
    print(f"  Specification Length: {len(result2.specification)}")
    print()

    # Test case 3: Simple ## headers
    test_response_3 = """## REASONING

Simple reasoning here.

## SPECIFICATION

Simple spec here.

## EXTRACTED_VALUES

```json
{"level": "L0", "parameters": {}, "mechanisms_claimed": [], "domain_primitives": {}}
```
"""

    result3 = parser.parse_synthesis_response(test_response_3)
    print("Test 3 (Simple ## format) Results:")
    print(f"  Parse Error: {result3.parse_error}")
    print(f"  Reasoning Length: {len(result3.reasoning)}")
    print(f"  Specification Length: {len(result3.specification)}")


def test_validator_parsing():
    """Test validator response parsing"""

    # Test case 1: Valid validator response
    test_response = """
{
  "validator": "physics",
  "level": "L5",
  "overall_status": "FAIL",
  "results": [
    {
      "constraint_id": "P1",
      "constraint_name": "Energy Conservation",
      "status": "FAIL",
      "reasoning": "Insufficient energy",
      "feedback": "Increase stored energy"
    }
  ]
}
"""

    parser = OutputParser()
    result = parser.parse_validator_response(test_response)

    print("Validator Test Results:")
    print(f"  Parse Error: {result.parse_error}")
    print(f"  Validator: {result.validator}")
    print(f"  Status: {result.overall_status}")
    print(f"  Results Count: {len(result.results)}")


if __name__ == "__main__":
    test_synthesis_parsing()
    print()
    test_validator_parsing()