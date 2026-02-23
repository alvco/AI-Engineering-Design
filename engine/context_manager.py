"""
Context Manager for Level 3 Synthesis Engine - V2
Branch-and-Save Architecture with Meta-Learning

Manages synthesis state, handles backtracking, provides rolling context,
and supports fresh restarts with negative constraint injection.
"""

import logging
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from copy import deepcopy

from output_parser import SynthesisResult
from aggregator import ConstraintFailure


@dataclass
class LevelState:
    """State information for a single SEDF level"""
    synthesis: Optional[SynthesisResult] = None
    flags: List[ConstraintFailure] = field(default_factory=list)
    attempt_count: int = 0
    committed: bool = False
    conflict_report: Optional[str] = None


@dataclass
class SynthesisContext:
    """Context package provided to agents"""
    current_level: str
    parent_synthesis: Dict[str, SynthesisResult]  # All validated levels
    conflict_report: Optional[str] = None
    attempt_number: int = 1
    backtrack_depth: int = 0
    # V2: Negative constraints from previous architectures
    negative_constraints: Optional[str] = None
    architecture_id: int = 1


@dataclass
class ArchitectureRecord:
    """Record of a complete architecture exploration"""
    architecture_id: int
    fingerprint: str                          # L2 domain primitives
    summary: str                              # One-paragraph description
    completed_levels: List[str]
    l5_attempts: int
    constraint_violations: Dict[str, int]     # constraint_id -> count
    failure_reason: str
    negative_constraint_generated: str        # For next architecture
    synthesis_content: Dict[str, Dict]        # level -> {reasoning, specification, extracted_values}


class ContextManager:
    """
    Manages synthesis state progression, backtracking, and fresh restarts.
    
    V2 Enhancements:
    - Track multiple architecture explorations
    - Generate and inject negative constraints
    - Extract architecture fingerprints from L2 primitives
    - Support complete reset with meta-learning
    """

    def __init__(self, config: Optional[Dict] = None):
        self.logger = logging.getLogger(__name__)
        
        # Initialize state for all levels
        self.levels = {
            "L0": LevelState(),
            "L1": LevelState(), 
            "L2": LevelState(),
            "L3": LevelState(),
            "L4": LevelState(),
            "L5": LevelState()
        }
        
        # Backtracking state
        self.backtrack_depth = 0
        self.max_backtrack_depth = config.get('engine_parameters', {}).get('max_backtrack_depth', 2) if config else 2
        
        # V2: Architecture tracking
        self.current_architecture_id = 1
        self.architecture_records: List[ArchitectureRecord] = []
        self.cumulative_negative_constraints: List[str] = []
        self.l5_failure_count_this_architecture = 0
        
        self.logger.info("ContextManager V2 initialized with architecture tracking")

    def commit_level(self, 
                    level: str, 
                    synthesis: SynthesisResult, 
                    flags: List[ConstraintFailure] = None) -> bool:
        """
        Commit validated synthesis for a level.
        """
        if level not in self.levels:
            self.logger.error(f"Invalid level: {level}")
            return False
        
        level_state = self.levels[level]
        level_state.synthesis = synthesis
        level_state.flags = flags or []
        level_state.committed = True
        level_state.conflict_report = None
        
        self.logger.info(f"Committed {level}: {len(synthesis.specification)} chars, "
                        f"{len(level_state.flags)} flags")
        
        return True

    def get_context_for_level(self, level: str) -> SynthesisContext:
        """
        Get synthesis context for the specified level.
        V2: Includes negative constraints from previous architectures.
        """
        if level not in self.levels:
            self.logger.error(f"Invalid level: {level}")
            return SynthesisContext(
                current_level=level,
                parent_synthesis={},
                conflict_report="ERROR: Invalid level requested"
            )
        
        # Collect all committed parent synthesis
        parent_synthesis = {}
        level_order = ["L0", "L1", "L2", "L3", "L4", "L5"]
        current_index = level_order.index(level)
        
        for parent_level in level_order[:current_index]:
            parent_state = self.levels[parent_level]
            if parent_state.committed and parent_state.synthesis:
                parent_synthesis[parent_level] = parent_state.synthesis
        
        conflict_report = self.levels[level].conflict_report
        attempt_number = self.levels[level].attempt_count + 1
        
        # V2: Format cumulative negative constraints
        negative_constraints = None
        if self.cumulative_negative_constraints and level == "L0":
            negative_constraints = self._format_negative_constraints()
        
        context = SynthesisContext(
            current_level=level,
            parent_synthesis=parent_synthesis,
            conflict_report=conflict_report,
            attempt_number=attempt_number,
            backtrack_depth=self.backtrack_depth,
            negative_constraints=negative_constraints,
            architecture_id=self.current_architecture_id
        )
        
        self.logger.info(f"Generated context for {level}: "
                        f"{len(parent_synthesis)} parents, "
                        f"attempt {attempt_number}, "
                        f"architecture #{self.current_architecture_id}")
        
        return context

    def _format_negative_constraints(self) -> str:
        """Format cumulative negative constraints for prompt injection."""
        if not self.cumulative_negative_constraints:
            return None
        
        lines = ["## CONSTRAINTS FROM PREVIOUS ARCHITECTURES", 
                 "The following approaches have been tried and failed. You MUST explore a different design:",
                 ""]
        
        for i, constraint in enumerate(self.cumulative_negative_constraints, 1):
            lines.append(f"Architecture {i}: {constraint}")
            lines.append("")
        
        lines.append("Design a fundamentally different approach that avoids these patterns.")
        
        return "\n".join(lines)

    def increment_attempt(self, level: str) -> int:
        """Increment attempt count for a level and return new count."""
        if level in self.levels:
            self.levels[level].attempt_count += 1
            return self.levels[level].attempt_count
        return 0

    def get_attempt_count(self, level: str) -> int:
        """Get current attempt count for a level."""
        return self.levels.get(level, LevelState()).attempt_count

    def record_l5_failure(self):
        """Record an L5 failure for this architecture."""
        self.l5_failure_count_this_architecture += 1
        self.logger.info(f"L5 failure #{self.l5_failure_count_this_architecture} for architecture #{self.current_architecture_id}")

    def should_fresh_restart(self, n_failures_threshold: int) -> bool:
        """
        Check if we should do a fresh restart based on L5 failures.
        
        Args:
            n_failures_threshold: Number of L5 failure cycles before restart
            
        Returns:
            True if should restart fresh
        """
        # Count cycles where L5 failed (each cycle = max_attempts at L5)
        should_restart = self.l5_failure_count_this_architecture >= n_failures_threshold
        
        if should_restart:
            self.logger.info(f"Fresh restart triggered: {self.l5_failure_count_this_architecture} >= {n_failures_threshold}")
        
        return should_restart

    def save_current_architecture(self, failure_reason: str, constraint_violations: Dict[str, int]) -> ArchitectureRecord:
        """
        Save the current architecture before fresh restart.
        
        Args:
            failure_reason: Why this architecture failed at L5
            constraint_violations: Accumulated constraint violation counts
            
        Returns:
            ArchitectureRecord for this architecture
        """
        # Extract fingerprint from L2 if available
        fingerprint = self._extract_fingerprint()
        
        # Generate summary
        summary = self._generate_architecture_summary(failure_reason)
        
        # Generate negative constraint for next architecture
        negative_constraint = self._generate_negative_constraint(fingerprint, failure_reason)
        
        # Collect synthesis content
        synthesis_content = {}
        for level, state in self.levels.items():
            if state.committed and state.synthesis:
                synthesis_content[level] = {
                    "reasoning": state.synthesis.reasoning,
                    "specification": state.synthesis.specification,
                    "extracted_values": state.synthesis.extracted_values
                }
        
        record = ArchitectureRecord(
            architecture_id=self.current_architecture_id,
            fingerprint=fingerprint,
            summary=summary,
            completed_levels=self.get_committed_levels(),
            l5_attempts=self.l5_failure_count_this_architecture * 3,  # attempts per cycle
            constraint_violations=constraint_violations.copy(),
            failure_reason=failure_reason,
            negative_constraint_generated=negative_constraint,
            synthesis_content=synthesis_content
        )
        
        self.architecture_records.append(record)
        self.logger.info(f"Saved architecture #{self.current_architecture_id}: {fingerprint}")
        
        return record

    def _extract_fingerprint(self) -> str:
        """
        Extract architecture fingerprint from L2 domain primitives.
        
        Returns:
            Fingerprint string like "A2+A3 / B2 / C1+C4 / D1+D2 / E2 / F2"
        """
        if not self.levels["L2"].committed or not self.levels["L2"].synthesis:
            return "UNKNOWN"
        
        l2_spec = self.levels["L2"].synthesis.specification
        l2_values = self.levels["L2"].synthesis.extracted_values
        
        # Try to extract from extracted_values first
        if l2_values and 'domain_primitives' in l2_values:
            dp = l2_values['domain_primitives']
            parts = []
            for domain in ['A', 'B', 'C', 'D', 'E', 'F']:
                if domain in dp:
                    parts.append(str(dp[domain]))
            if parts:
                return " / ".join(parts)
        
        # Fallback: regex extraction from specification text
        domains = {}
        for domain in ['A', 'B', 'C', 'D', 'E', 'F']:
            pattern = rf'{domain}[1-4]'
            matches = re.findall(pattern, l2_spec)
            if matches:
                domains[domain] = "+".join(sorted(set(matches)))
        
        if domains:
            parts = [domains.get(d, '?') for d in ['A', 'B', 'C', 'D', 'E', 'F']]
            return " / ".join(parts)
        
        return "UNKNOWN"

    def _generate_architecture_summary(self, failure_reason: str) -> str:
        """Generate one-paragraph summary of this architecture."""
        l0_spec = ""
        if self.levels["L0"].committed and self.levels["L0"].synthesis:
            l0_spec = self.levels["L0"].synthesis.specification[:200]
        
        fingerprint = self._extract_fingerprint()
        completed = self.get_committed_levels()
        
        summary = (
            f"Architecture #{self.current_architecture_id} ({fingerprint}): "
            f"Completed levels {', '.join(completed)}. "
            f"Governing concept: {l0_spec[:100]}... "
            f"Failed at L5 due to: {failure_reason}"
        )
        
        return summary

    def _generate_negative_constraint(self, fingerprint: str, failure_reason: str) -> str:
        """
        Generate negative constraint for next architecture based on this failure.
        Uses SEDF domain codes for unambiguous constraints.
        """
        # Extract the key domain choices that led to failure
        constraint_parts = []
        
        # Parse fingerprint for domain codes
        if fingerprint != "UNKNOWN":
            parts = fingerprint.split(" / ")
            domain_letters = ['A', 'B', 'C', 'D', 'E', 'F']
            
            for i, part in enumerate(parts):
                if i < len(domain_letters):
                    domain = domain_letters[i]
                    codes = part.replace("+", ", ")
                    constraint_parts.append(f"Domain {domain}: used {codes}")
        
        # Analyze failure reason for key issues
        failure_keywords = []
        if "energy" in failure_reason.lower():
            failure_keywords.append("energy conservation issues")
        if "velocity" in failure_reason.lower() or "cavitation" in failure_reason.lower():
            failure_keywords.append("velocity/cavitation requirements")
        if "electronic" in failure_reason.lower() or "plc" in failure_reason.lower():
            failure_keywords.append("electronic control complexity")
        if "timing" in failure_reason.lower() or "cycle" in failure_reason.lower():
            failure_keywords.append("timing/cycle constraints")
        
        negative_constraint = (
            f"AVOID Architecture #{self.current_architecture_id} pattern: "
            f"{fingerprint}. "
            f"This approach failed due to {', '.join(failure_keywords) if failure_keywords else failure_reason}. "
            f"Explore fundamentally different domain primitive combinations."
        )
        
        return negative_constraint

    def fresh_restart(self, failure_reason: str, constraint_violations: Dict[str, int]):
        """
        Perform a fresh restart for new architecture exploration.
        
        Saves current architecture, increments ID, resets state,
        and adds negative constraint for next attempt.
        """
        # Save current architecture
        record = self.save_current_architecture(failure_reason, constraint_violations)
        
        # Add negative constraint for next architecture
        self.cumulative_negative_constraints.append(record.negative_constraint_generated)
        
        # Increment architecture ID
        self.current_architecture_id += 1
        
        # Reset all level states
        for level_state in self.levels.values():
            level_state.synthesis = None
            level_state.flags = []
            level_state.attempt_count = 0
            level_state.committed = False
            level_state.conflict_report = None
        
        # Reset backtrack depth and L5 failure count
        self.backtrack_depth = 0
        self.l5_failure_count_this_architecture = 0
        
        self.logger.info(f"Fresh restart complete. Starting architecture #{self.current_architecture_id}")
        self.logger.info(f"Cumulative negative constraints: {len(self.cumulative_negative_constraints)}")

    def invalidate_from_level(self, level: str) -> bool:
        """Invalidate the specified level and all levels below it."""
        if level not in self.levels:
            self.logger.error(f"Invalid level: {level}")
            return False
        
        level_order = ["L0", "L1", "L2", "L3", "L4", "L5"]
        start_index = level_order.index(level)
        
        invalidated_levels = []
        for target_level in level_order[start_index:]:
            level_state = self.levels[target_level]
            level_state.synthesis = None
            level_state.committed = False
            level_state.flags = []
            invalidated_levels.append(target_level)
        
        self.logger.info(f"Invalidated levels: {invalidated_levels}")
        return True

    def add_conflict_report(self, level: str, report: str) -> bool:
        """Add conflict report for a level."""
        if level not in self.levels:
            self.logger.error(f"Invalid level: {level}")
            return False
        
        self.levels[level].conflict_report = report
        self.logger.info(f"Added conflict report for {level}: {len(report)} chars")
        return True

    def increment_backtrack_depth(self) -> int:
        """Increment backtrack depth and return new value."""
        self.backtrack_depth += 1
        self.logger.info(f"Incremented backtrack depth to {self.backtrack_depth}")
        return self.backtrack_depth

    def is_backtrack_limit_exceeded(self) -> bool:
        """Check if backtrack depth limit has been exceeded."""
        exceeded = self.backtrack_depth >= self.max_backtrack_depth
        if exceeded:
            self.logger.warning(f"Backtrack limit exceeded: {self.backtrack_depth} >= {self.max_backtrack_depth}")
        return exceeded

    def get_synthesis_history(self) -> Dict[str, Any]:
        """Get complete synthesis history for analysis and metrics."""
        history = {
            'committed_levels': [],
            'total_attempts': 0,
            'total_flags': 0,
            'backtrack_depth': self.backtrack_depth,
            'level_details': {},
            # V2 additions
            'current_architecture_id': self.current_architecture_id,
            'total_architectures_explored': len(self.architecture_records) + 1,
            'l5_failures_this_architecture': self.l5_failure_count_this_architecture
        }
        
        for level, state in self.levels.items():
            history['total_attempts'] += state.attempt_count
            history['total_flags'] += len(state.flags)
            
            if state.committed:
                history['committed_levels'].append(level)
            
            history['level_details'][level] = {
                'committed': state.committed,
                'attempt_count': state.attempt_count,
                'flag_count': len(state.flags),
                'has_conflict_report': state.conflict_report is not None,
                'synthesis_length': len(state.synthesis.specification) if state.synthesis else 0
            }
        
        return history

    def get_committed_levels(self) -> List[str]:
        """Get list of levels that have committed synthesis."""
        return [level for level, state in self.levels.items() if state.committed]

    def get_highest_committed_level(self) -> Optional[str]:
        """Get the highest level that has committed synthesis."""
        committed = self.get_committed_levels()
        if not committed:
            return None
        
        level_order = ["L0", "L1", "L2", "L3", "L4", "L5"]
        for level in reversed(level_order):
            if level in committed:
                return level
        return None

    def is_synthesis_complete(self) -> bool:
        """Check if synthesis is complete (L5 committed)."""
        return self.levels["L5"].committed

    def get_all_architecture_records(self) -> List[ArchitectureRecord]:
        """Get all saved architecture records."""
        return self.architecture_records

    def get_architecture_fingerprints(self) -> List[str]:
        """Get fingerprints of all explored architectures."""
        fingerprints = [r.fingerprint for r in self.architecture_records]
        # Add current architecture if has L2
        if self.levels["L2"].committed:
            fingerprints.append(self._extract_fingerprint())
        return fingerprints

    def format_context_for_prompt(self, context: SynthesisContext) -> str:
        """Format synthesis context as text for agent prompts."""
        lines = []
        
        lines.append(f"CURRENT LEVEL: {context.current_level}")
        lines.append(f"ATTEMPT: #{context.attempt_number}")
        lines.append(f"ARCHITECTURE: #{context.architecture_id}")
        
        if context.backtrack_depth > 0:
            lines.append(f"BACKTRACK DEPTH: {context.backtrack_depth}")
        
        # V2: Add negative constraints at L0
        if context.negative_constraints:
            lines.append("")
            lines.append(context.negative_constraints)
        
        if context.parent_synthesis:
            lines.append("\nVALIDATED PARENT LEVELS:")
            for level in ["L0", "L1", "L2", "L3", "L4", "L5"]:
                if level in context.parent_synthesis:
                    synthesis = context.parent_synthesis[level]
                    lines.append(f"\n{level}:")
                    lines.append(f"{synthesis.specification}")
        else:
            lines.append("\nNO PARENT LEVELS (Starting fresh)")
        
        if context.conflict_report:
            lines.append("\nCONFLICT REPORT:")
            lines.append(context.conflict_report)
        
        return "\n".join(lines)

    def reset_for_new_run(self):
        """Reset context manager for a completely new synthesis run."""
        for level_state in self.levels.values():
            level_state.synthesis = None
            level_state.flags = []
            level_state.attempt_count = 0
            level_state.committed = False
            level_state.conflict_report = None
        
        self.backtrack_depth = 0
        self.current_architecture_id = 1
        self.architecture_records = []
        self.cumulative_negative_constraints = []
        self.l5_failure_count_this_architecture = 0
        
        self.logger.info("ContextManager V2 reset for new run")


# Test functions for development
def test_context_manager_v2():
    """Test V2 context manager with architecture tracking"""
    
    from output_parser import SynthesisResult
    
    cm = ContextManager()
    
    # Simulate Architecture 1
    print("=== Architecture 1 ===")
    
    l0_synthesis = SynthesisResult(
        reasoning="Test reasoning",
        specification="L0: Hydraulic-cavitation hybrid for rock breaking",
        extracted_values={"level": "L0"}
    )
    cm.commit_level("L0", l0_synthesis)
    
    l2_synthesis = SynthesisResult(
        reasoning="Domain selection",
        specification="L2: A2+A3 hydraulic+elastic, B2 latch, C1 linear, D1+D2 percussion+cavitation, E2 sealed, F2 electronic",
        extracted_values={
            "level": "L2",
            "domain_primitives": {"A": "A2+A3", "B": "B2", "C": "C1", "D": "D1+D2", "E": "E2", "F": "F2"}
        }
    )
    cm.commit_level("L2", l2_synthesis)
    
    print(f"Fingerprint: {cm._extract_fingerprint()}")
    
    # Simulate L5 failures
    cm.record_l5_failure()
    cm.record_l5_failure()
    cm.record_l5_failure()
    
    print(f"Should fresh restart (N=3): {cm.should_fresh_restart(3)}")
    
    # Perform fresh restart
    cm.fresh_restart(
        failure_reason="Energy conservation: needed 750J, only had 500J",
        constraint_violations={"P1_L5": 3, "S2_L5": 2}
    )
    
    print(f"\n=== Architecture 2 ===")
    print(f"Current architecture ID: {cm.current_architecture_id}")
    print(f"Negative constraints: {len(cm.cumulative_negative_constraints)}")
    
    # Get context with negative constraints
    l0_context = cm.get_context_for_level("L0")
    print(f"L0 context has negative constraints: {l0_context.negative_constraints is not None}")
    
    # Check architecture records
    print(f"\nSaved architectures: {len(cm.architecture_records)}")
    if cm.architecture_records:
        print(f"Architecture 1 fingerprint: {cm.architecture_records[0].fingerprint}")


if __name__ == "__main__":
    test_context_manager_v2()
