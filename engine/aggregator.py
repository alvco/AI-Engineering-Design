"""
Verdict Aggregator for Level 3 Synthesis Engine

Combines Physics Validator and Structural Validator results into single verdict
with structured failure tracking for high-fidelity conflict reports.
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from output_parser import ValidatorResult


class VerdictType(Enum):
    """Possible verdict outcomes"""
    PASS = "PASS"
    PASS_WITH_FLAGS = "PASS_WITH_FLAGS"  
    FAIL = "FAIL"


@dataclass
class ConstraintFailure:
    """Structured information about a failed constraint"""
    constraint_id: str
    constraint_name: str
    validator: str
    status: str  # "FAIL" or "FLAG"
    reasoning: str
    feedback: str
    calculation: Optional[str] = None  # For physics constraints with calculations


@dataclass
class AggregatedVerdict:
    """Final verdict combining both validators"""
    verdict: VerdictType
    failed_constraints: List[ConstraintFailure]
    flagged_constraints: List[ConstraintFailure] 
    feedback_summary: List[str]
    physics_result: ValidatorResult
    structural_result: ValidatorResult
    parse_errors: List[str]


class VerdictAggregator:
    """
    Aggregates validator results into final verdict using the logic:
    
    1. Parse errors or any validator FAIL → FAIL
    2. Any flags from either validator → PASS_WITH_FLAGS  
    3. No failures or flags → PASS
    
    Tracks structured constraint failures for conflict report generation.
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def aggregate_results(self, 
                         physics_result: ValidatorResult, 
                         structural_result: ValidatorResult) -> AggregatedVerdict:
        """
        Aggregate physics and structural validator results into final verdict.
        
        Args:
            physics_result: Parsed result from Physics Validator
            structural_result: Parsed result from Structural Validator
            
        Returns:
            AggregatedVerdict with combined decision and structured failure info
        """
        self.logger.info("Aggregating validator results")
        
        # Collect parse errors
        parse_errors = []
        if physics_result.parse_error:
            parse_errors.append(f"Physics Validator: {physics_result.parse_error}")
        if structural_result.parse_error:
            parse_errors.append(f"Structural Validator: {structural_result.parse_error}")
        
        # Extract constraint results
        failed_constraints = []
        flagged_constraints = []
        
        # Process physics results
        if physics_result.results:
            failed, flagged = self._extract_constraint_failures(physics_result, "physics")
            failed_constraints.extend(failed)
            flagged_constraints.extend(flagged)
        
        # Process structural results  
        if structural_result.results:
            failed, flagged = self._extract_constraint_failures(structural_result, "structural")
            failed_constraints.extend(failed)
            flagged_constraints.extend(flagged)
        
        # Apply aggregation logic
        verdict = self._determine_verdict(parse_errors, failed_constraints, flagged_constraints)
        
        # Generate feedback summary
        feedback_summary = self._generate_feedback_summary(
            parse_errors, failed_constraints, flagged_constraints
        )
        
        result = AggregatedVerdict(
            verdict=verdict,
            failed_constraints=failed_constraints,
            flagged_constraints=flagged_constraints,
            feedback_summary=feedback_summary,
            physics_result=physics_result,
            structural_result=structural_result,
            parse_errors=parse_errors
        )
        
        self.logger.info(f"Aggregation complete: {verdict.value}, "
                        f"{len(failed_constraints)} failures, "
                        f"{len(flagged_constraints)} flags")
        
        return result

    def _extract_constraint_failures(self, 
                                   validator_result: ValidatorResult, 
                                   validator_name: str) -> tuple[List[ConstraintFailure], List[ConstraintFailure]]:
        """
        Extract failed and flagged constraints from validator result.
        
        Args:
            validator_result: Parsed validator response
            validator_name: "physics" or "structural" for identification
            
        Returns:
            Tuple of (failed_constraints, flagged_constraints)
        """
        failed = []
        flagged = []
        
        for constraint_result in validator_result.results:
            status = constraint_result.get('status', 'UNKNOWN')
            
            # Create structured constraint failure
            constraint_failure = ConstraintFailure(
                constraint_id=constraint_result.get('constraint_id', 'UNKNOWN'),
                constraint_name=constraint_result.get('constraint_name', 'Unknown Constraint'),
                validator=validator_name,
                status=status,
                reasoning=constraint_result.get('reasoning', 'No reasoning provided'),
                feedback=constraint_result.get('feedback', 'No feedback provided'),
                calculation=constraint_result.get('validator_calculation') or 
                           constraint_result.get('agent_calculation')  # Include any calculations
            )
            
            if status == "FAIL":
                failed.append(constraint_failure)
            elif status == "FLAG":
                flagged.append(constraint_failure)
            # PASS constraints are not included
        
        return failed, flagged

    def _determine_verdict(self, 
                          parse_errors: List[str],
                          failed_constraints: List[ConstraintFailure], 
                          flagged_constraints: List[ConstraintFailure]) -> VerdictType:
        """
        Apply aggregation logic to determine final verdict.
        
        Args:
            parse_errors: List of parse error messages
            failed_constraints: List of failed constraints
            flagged_constraints: List of flagged constraints
            
        Returns:
            Final verdict type
        """
        # Rule 1: Parse errors or any failures → FAIL
        if parse_errors or failed_constraints:
            return VerdictType.FAIL
        
        # Rule 2: Any flags → PASS_WITH_FLAGS
        elif flagged_constraints:
            return VerdictType.PASS_WITH_FLAGS
        
        # Rule 3: No issues → PASS
        else:
            return VerdictType.PASS

    def _generate_feedback_summary(self, 
                                  parse_errors: List[str],
                                  failed_constraints: List[ConstraintFailure],
                                  flagged_constraints: List[ConstraintFailure]) -> List[str]:
        """
        Generate human-readable feedback summary for the synthesis agent.
        
        Args:
            parse_errors: List of parse error messages
            failed_constraints: List of failed constraints
            flagged_constraints: List of flagged constraints
            
        Returns:
            List of feedback messages
        """
        feedback = []
        
        # Add parse error feedback
        for error in parse_errors:
            feedback.append(f"PARSE ERROR: {error}")
        
        # Add constraint failure feedback
        for failure in failed_constraints:
            feedback.append(
                f"CONSTRAINT FAILURE ({failure.constraint_id}): {failure.feedback}"
            )
        
        # Add flag feedback (informational)
        for flag in flagged_constraints:
            feedback.append(
                f"FLAG ({flag.constraint_id}): {flag.feedback}"
            )
        
        return feedback

    def get_failed_constraint_ids(self, verdict: AggregatedVerdict) -> List[str]:
        """
        Extract just the constraint IDs that failed for conflict report generation.
        
        Args:
            verdict: Aggregated verdict result
            
        Returns:
            List of failed constraint IDs (e.g., ["P1", "S2"])
        """
        return [failure.constraint_id for failure in verdict.failed_constraints]

    def get_failure_by_constraint(self, 
                                 verdict: AggregatedVerdict, 
                                 constraint_id: str) -> Optional[ConstraintFailure]:
        """
        Get specific constraint failure details for conflict report generation.
        
        Args:
            verdict: Aggregated verdict result
            constraint_id: Constraint ID to look up (e.g., "P1")
            
        Returns:
            ConstraintFailure object if found, None otherwise
        """
        for failure in verdict.failed_constraints:
            if failure.constraint_id == constraint_id:
                return failure
        return None

    def format_feedback_for_agent(self, verdict: AggregatedVerdict) -> str:
        """
        Format feedback summary as single string for synthesis agent prompt.
        
        Args:
            verdict: Aggregated verdict result
            
        Returns:
            Formatted feedback string
        """
        if not verdict.feedback_summary:
            return "No feedback - all constraints passed."
        
        # Group feedback by type
        errors = [f for f in verdict.feedback_summary if f.startswith("PARSE ERROR")]
        failures = [f for f in verdict.feedback_summary if f.startswith("CONSTRAINT FAILURE")]
        flags = [f for f in verdict.feedback_summary if f.startswith("FLAG")]
        
        formatted = []
        
        if errors:
            formatted.append("PARSE ERRORS:")
            formatted.extend(f"  - {error[13:]}" for error in errors)  # Remove "PARSE ERROR: "
            formatted.append("")
        
        if failures:
            formatted.append("CONSTRAINT FAILURES:")
            formatted.extend(f"  - {failure[21:]}" for failure in failures)  # Remove "CONSTRAINT FAILURE (XX): "
            formatted.append("")
        
        if flags:
            formatted.append("FLAGS (Informational):")
            formatted.extend(f"  - {flag[9:]}" for flag in flags)  # Remove "FLAG (XX): "
        
        return "\n".join(formatted)

    def get_constraint_summary(self, verdict: AggregatedVerdict) -> Dict[str, int]:
        """
        Get summary statistics for logging and metrics.
        
        Args:
            verdict: Aggregated verdict result
            
        Returns:
            Dict with constraint counts by type
        """
        # Count results from both validators
        total_constraints = 0
        passed_constraints = 0
        
        for result in [verdict.physics_result, verdict.structural_result]:
            if result.results:
                total_constraints += len(result.results)
                passed_constraints += len([r for r in result.results if r.get('status') == 'PASS'])
        
        return {
            'total_constraints': total_constraints,
            'passed_constraints': passed_constraints,
            'failed_constraints': len(verdict.failed_constraints),
            'flagged_constraints': len(verdict.flagged_constraints),
            'parse_errors': len(verdict.parse_errors)
        }


# Test functions for development
def test_aggregator():
    """Test aggregator with mock validator results"""
    
    # Mock validator results
    from output_parser import ValidatorResult
    
    # Test case 1: All pass
    physics_pass = ValidatorResult(
        validator="physics",
        level="L5",
        overall_status="PASS",
        results=[
            {"constraint_id": "P1", "constraint_name": "Energy Conservation", "status": "PASS", "reasoning": "Energy sufficient", "feedback": "Good"}
        ]
    )
    
    structural_pass = ValidatorResult(
        validator="structural", 
        level="L5",
        overall_status="PASS",
        results=[
            {"constraint_id": "S1", "constraint_name": "Closed-List Compliance", "status": "PASS", "reasoning": "Valid codes", "feedback": "Good"}
        ]
    )
    
    aggregator = VerdictAggregator()
    verdict1 = aggregator.aggregate_results(physics_pass, structural_pass)
    
    print("Test 1 - All Pass:")
    print(f"Verdict: {verdict1.verdict.value}")
    print(f"Failed: {len(verdict1.failed_constraints)}")
    print(f"Flags: {len(verdict1.flagged_constraints)}")
    print()
    
    # Test case 2: One failure
    physics_fail = ValidatorResult(
        validator="physics",
        level="L5", 
        overall_status="FAIL",
        results=[
            {"constraint_id": "P1", "constraint_name": "Energy Conservation", "status": "FAIL", 
             "reasoning": "Insufficient energy", "feedback": "Increase stored energy",
             "agent_calculation": "750J", "validator_calculation": "6250J"}
        ]
    )
    
    verdict2 = aggregator.aggregate_results(physics_fail, structural_pass)
    
    print("Test 2 - One Failure:")
    print(f"Verdict: {verdict2.verdict.value}")
    print(f"Failed: {len(verdict2.failed_constraints)}")
    print(f"Failed IDs: {aggregator.get_failed_constraint_ids(verdict2)}")
    print(f"Feedback:\n{aggregator.format_feedback_for_agent(verdict2)}")
    print()
    
    # Test case 3: Parse error
    physics_parse_error = ValidatorResult(
        validator="physics",
        level="L5",
        overall_status="PARSE_ERROR",
        results=[],
        parse_error="Invalid JSON in response"
    )
    
    verdict3 = aggregator.aggregate_results(physics_parse_error, structural_pass)
    
    print("Test 3 - Parse Error:")
    print(f"Verdict: {verdict3.verdict.value}")
    print(f"Parse Errors: {len(verdict3.parse_errors)}")
    print(f"Summary: {aggregator.get_constraint_summary(verdict3)}")


if __name__ == "__main__":
    test_aggregator()