"""
Orchestrator for Level 3 Synthesis Engine - V2
Branch-and-Save Architecture with Meta-Learning

Main control flow that coordinates all components with:
- Fresh restart after N L5 failures
- Negative constraint injection for architectural diversity
- Complete architecture saving for research analysis
- Constraint violation signature tracking
"""

import logging
import yaml
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum

from output_parser import OutputParser, SynthesisResult, ValidatorResult
from llm_client import LLMClient, LLMResponse
from aggregator import VerdictAggregator, VerdictType, AggregatedVerdict
from context_manager import ContextManager, SynthesisContext, ArchitectureRecord
from prompt_assembler import PromptAssembler, AssembledPrompt


class RunStatus(Enum):
    """Possible run completion states"""
    SUCCESS = "SUCCESS"                      # At least one architecture completed L5
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"      # Multiple L0-L4 architectures saved
    ARCHITECTURE_LIMIT = "ARCHITECTURE_LIMIT"  # Exhausted max fresh restarts
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"      # Hit token budget limit
    ERROR = "ERROR"                          # System error during execution


@dataclass
class ConflictReport:
    """High-fidelity conflict report for backtracking"""
    failed_level: str
    target_level: str
    failed_constraints: List[str]
    constraint_details: Dict[str, str]
    detailed_reasoning: str
    guidance: str


@dataclass 
class TokenUsage:
    """Track token consumption throughout run"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    calls_made: int = 0
    
    def add_usage(self, input_tokens: int, output_tokens: int, cost_config: Dict[str, float]):
        """Add token usage from an API call"""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += (input_tokens + output_tokens)
        self.estimated_cost += (input_tokens * cost_config['input_token_cost'] + 
                               output_tokens * cost_config['output_token_cost'])
        self.calls_made += 1


@dataclass
class FailureAnalytics:
    """Track failure patterns for analysis"""
    constraint_violations: Dict[str, int] = field(default_factory=dict)
    backtrack_triggers: List[Dict[str, Any]] = field(default_factory=list)
    attempt_patterns: Dict[str, List[int]] = field(default_factory=dict)
    parse_errors: Dict[str, int] = field(default_factory=dict)
    
    def record_constraint_violation(self, constraint_id: str, level: str, details: str):
        """Record a constraint violation"""
        key = f"{constraint_id}_{level}"
        self.constraint_violations[key] = self.constraint_violations.get(key, 0) + 1
    
    def record_backtrack(self, failed_level: str, target_level: str, reason: str):
        """Record a backtracking event"""
        self.backtrack_triggers.append({
            'failed_level': failed_level,
            'target_level': target_level,
            'reason': reason,
            'timestamp': time.time()
        })
    
    def record_attempts(self, level: str, attempts: int):
        """Record attempt count for a level"""
        if level not in self.attempt_patterns:
            self.attempt_patterns[level] = []
        self.attempt_patterns[level].append(attempts)
    
    def get_constraint_signature(self) -> Dict[str, int]:
        """Get constraint violation signature for this architecture"""
        return self.constraint_violations.copy()
    
    def reset_for_new_architecture(self):
        """Reset analytics for new architecture while preserving patterns"""
        self.constraint_violations = {}
        self.backtrack_triggers = []


@dataclass
class RunResult:
    """Final result of synthesis run"""
    status: RunStatus
    completed_levels: List[str]              # Levels from successful architecture (if any)
    successful_architecture: Optional[Dict]  # Full L5 synthesis if successful
    all_architectures: List[Dict]            # All explored architectures
    conflict_report: Optional[ConflictReport]
    metrics: Dict[str, Any]
    token_usage: TokenUsage
    failure_analytics: FailureAnalytics
    run_time: float
    variation_config: Dict[str, Any]


class SynthesisOrchestrator:
    """
    Main orchestrator for Level 3 synthesis engine - V2.
    
    Branch-and-Save Architecture:
    - Explores multiple architectural paths
    - Fresh restart after N L5 failures
    - Injects negative constraints for diversity
    - Saves all architectures for analysis
    """

    def __init__(self, config_path: str):
        """Initialize orchestrator with configuration file."""
        self.logger = logging.getLogger(__name__)
        
        # Load configuration
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        # Initialize components
        self.parser = OutputParser()
        self.llm_client = LLMClient(self.config)
        self.aggregator = VerdictAggregator()
        self.context_manager = ContextManager(self.config)
        self.prompt_assembler = PromptAssembler(self.config)
        
        # Engine parameters
        self.max_attempts = self.config['engine_parameters']['max_attempts']
        self.max_backtrack_depth = self.config['engine_parameters']['max_backtrack_depth']
        self.max_total_backtracks = self.config['engine_parameters'].get('max_total_backtracks', 6)
        
        # V2: Branch-and-Save parameters
        self.fresh_restart_after_n = self.config['engine_parameters'].get('fresh_restart_after_n_l5_failures', 3)
        self.max_fresh_restarts = self.config['engine_parameters'].get('max_fresh_restarts', 10)
        self.inject_negative_constraints = self.config['engine_parameters'].get('inject_negative_constraints', True)
        self.save_all_architectures = self.config['engine_parameters'].get('save_all_architectures', True)
        
        # Token budget management
        self.budget_config = self.config.get('budget_management', {})
        self.budget_enabled = self.budget_config.get('enabled', False)
        self.per_run_limit = self.budget_config.get('per_run_limit', 5000000)
        self.warning_threshold = self.budget_config.get('warning_threshold', 4000000)
        self.emergency_stop = self.budget_config.get('emergency_stop', True)
        
        # Cost estimation
        self.cost_config = self.budget_config.get('cost_estimation', {
            'input_token_cost': 0.000003,
            'output_token_cost': 0.000015
        })
        
        # Initialize tracking
        self.token_usage = TokenUsage()
        self.failure_analytics = FailureAnalytics()
        self.total_backtracks = 0
        self.fresh_restart_count = 0
        
        # SEDF level progression
        self.levels = ["L0", "L1", "L2", "L3", "L4", "L5"]
        
        self.logger.info("SynthesisOrchestrator V2 initialized")
        self.logger.info(f"Branch-and-Save: N={self.fresh_restart_after_n}, max_restarts={self.max_fresh_restarts}")
        if self.budget_enabled:
            self.logger.info(f"Token budget: {self.per_run_limit:,}")

    def run_synthesis(self, variation_config: Optional[Dict[str, Any]] = None) -> RunResult:
        """
        Execute complete synthesis run with Branch-and-Save architecture.
        """
        start_time = time.time()
        
        # Reset tracking for new run
        self.token_usage = TokenUsage()
        self.failure_analytics = FailureAnalytics()
        self.total_backtracks = 0
        self.fresh_restart_count = 0
        
        # Apply variation config if provided
        if variation_config:
            self._apply_variation_config(variation_config)
        
        # Reset context manager for new run
        self.context_manager.reset_for_new_run()
        
        self.logger.info("=" * 60)
        self.logger.info("Starting Branch-and-Save Synthesis Run")
        self.logger.info("=" * 60)
        
        successful_architecture = None
        
        try:
            # Main architecture exploration loop
            while self.fresh_restart_count < self.max_fresh_restarts:
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"ARCHITECTURE #{self.context_manager.current_architecture_id}")
                self.logger.info(f"{'='*60}")
                
                # Check budget
                if not self._check_token_budget():
                    break
                
                # Execute level progression for this architecture
                status = self._execute_architecture()
                
                if status == "SUCCESS":
                    # L5 completed! Save and exit
                    self.logger.info("🎉 L5 COMPLETED SUCCESSFULLY!")
                    successful_architecture = self._collect_final_synthesis()
                    break
                    
                elif status == "FRESH_RESTART":
                    # Save current architecture and restart
                    self.fresh_restart_count += 1
                    
                    if self.fresh_restart_count >= self.max_fresh_restarts:
                        self.logger.info(f"Reached max fresh restarts ({self.max_fresh_restarts})")
                        break
                    
                    # Get failure reason from most recent constraint violations
                    failure_reason = self._extract_failure_reason()
                    
                    # Fresh restart with negative constraint injection
                    self.context_manager.fresh_restart(
                        failure_reason=failure_reason,
                        constraint_violations=self.failure_analytics.get_constraint_signature()
                    )
                    
                    # Reset analytics for new architecture
                    self.failure_analytics.reset_for_new_architecture()
                    self.total_backtracks = 0
                    
                    self.logger.info(f"Fresh restart #{self.fresh_restart_count} initiated")
                    continue
                    
                elif status == "BUDGET_EXCEEDED":
                    self.logger.info("Budget exceeded - stopping exploration")
                    break
                    
                else:
                    self.logger.error(f"Unexpected status: {status}")
                    break
            
            # Collect results
            all_architectures = self._collect_all_architectures()
            completed_levels = self.context_manager.get_committed_levels()
            
            # Determine final status
            if successful_architecture:
                final_status = RunStatus.SUCCESS
            elif all_architectures:
                final_status = RunStatus.PARTIAL_SUCCESS
            elif self.token_usage.total_tokens >= self.per_run_limit:
                final_status = RunStatus.BUDGET_EXCEEDED
            else:
                final_status = RunStatus.ARCHITECTURE_LIMIT
            
            metrics = self._collect_metrics()
            run_time = time.time() - start_time
            
            result = RunResult(
                status=final_status,
                completed_levels=completed_levels,
                successful_architecture=successful_architecture,
                all_architectures=all_architectures,
                conflict_report=self._generate_final_conflict_report(final_status),
                metrics=metrics,
                token_usage=self.token_usage,
                failure_analytics=self.failure_analytics,
                run_time=run_time,
                variation_config=variation_config or {}
            )
            
            self._log_final_summary(result)
            
            return result
            
        except Exception as e:
            import traceback
            self.logger.error(f"Synthesis run failed with error: {e}")
            traceback.print_exc()
            
            return RunResult(
                status=RunStatus.ERROR,
                completed_levels=[],
                successful_architecture=None,
                all_architectures=self._collect_all_architectures(),
                conflict_report=None,
                metrics={"error": str(e)},
                token_usage=self.token_usage,
                failure_analytics=self.failure_analytics,
                run_time=time.time() - start_time,
                variation_config=variation_config or {}
            )

    def _execute_architecture(self) -> str:
        """
        Execute level progression for a single architecture.
        
        Returns:
            "SUCCESS" - L5 completed
            "FRESH_RESTART" - Should restart with new architecture
            "BUDGET_EXCEEDED" - Token limit hit
        """
        current_level_index = 0
        l5_failure_cycles = 0
        
        while current_level_index < len(self.levels):
            level = self.levels[current_level_index]
            self.logger.info(f"Processing {level}")
            
            # Check budget
            if not self._check_token_budget():
                return "BUDGET_EXCEEDED"
            
            # Execute level
            level_result = self._execute_level(level)
            
            if level_result == "SUCCESS":
                current_level_index += 1
                continue
                
            elif level_result == "BACKTRACK":
                # Handle backtracking within this architecture
                backtrack_result = self._handle_backtracking(level)
                
                if backtrack_result == "BACKTRACK_LIMIT":
                    # Check if this was L5 failing
                    if level == "L5":
                        self.context_manager.record_l5_failure()
                        l5_failure_cycles += 1
                        
                        # Check if should fresh restart
                        if self.context_manager.should_fresh_restart(self.fresh_restart_after_n):
                            return "FRESH_RESTART"
                        
                        # Reset backtrack counter and try again from current position
                        self.total_backtracks = 0
                        self.context_manager.backtrack_depth = 0
                        current_level_index = self._get_current_level_index()
                        continue
                    else:
                        # Non-L5 level hit backtrack limit - this is unusual
                        return "FRESH_RESTART"
                        
                elif backtrack_result == "CONTINUE":
                    current_level_index = self._get_current_level_index()
                    continue
                else:
                    return "BUDGET_EXCEEDED" if backtrack_result == "BUDGET" else "FRESH_RESTART"
                    
            elif level_result == "BUDGET_EXCEEDED":
                return "BUDGET_EXCEEDED"
                
            else:
                self.logger.error(f"Unexpected level result: {level_result}")
                return "FRESH_RESTART"
        
        # All levels completed
        return "SUCCESS"

    def _execute_level(self, level: str) -> str:
        """
        Execute synthesis and validation for a single level.
        """
        attempts = 0
        
        while attempts < self.max_attempts:
            self.context_manager.increment_attempt(level)
            attempts += 1
            
            self.logger.info(f"Attempting {level} (attempt {attempts}/{self.max_attempts})")
            
            if not self._check_token_budget():
                self.failure_analytics.record_attempts(level, attempts)
                return "BUDGET_EXCEEDED"
            
            # Get context (includes negative constraints for L0)
            context = self.context_manager.get_context_for_level(level)
            
            # Generate synthesis
            synthesis_result = self._generate_synthesis(context)
            
            if synthesis_result.parse_error:
                self.logger.warning(f"Synthesis parse error: {synthesis_result.parse_error}")
                self.failure_analytics.parse_errors[level] = self.failure_analytics.parse_errors.get(level, 0) + 1
                continue
            
            # Validate
            validation_result = self._validate_synthesis(context, synthesis_result)
            
            if validation_result.verdict == VerdictType.PASS:
                self.context_manager.commit_level(level, synthesis_result)
                self.failure_analytics.record_attempts(level, attempts)
                self.logger.info(f"✓ {level} completed successfully")
                return "SUCCESS"
                
            elif validation_result.verdict == VerdictType.PASS_WITH_FLAGS:
                self.context_manager.commit_level(level, synthesis_result, validation_result.flagged_constraints)
                self.failure_analytics.record_attempts(level, attempts)
                self.logger.info(f"✓ {level} completed with {len(validation_result.flagged_constraints)} flags")
                return "SUCCESS"
                
            elif validation_result.verdict == VerdictType.FAIL:
                self._record_validation_failures(validation_result, level)
                feedback = self.aggregator.format_feedback_for_agent(validation_result)
                self.logger.warning(f"✗ {level} failed: {feedback[:100]}...")
        
        self.failure_analytics.record_attempts(level, attempts)
        self.logger.warning(f"{level} failed after {self.max_attempts} attempts")
        return "BACKTRACK"

    def _handle_backtracking(self, failed_level: str) -> str:
        """Handle backtracking when a level fails validation."""
        
        # Check backtrack depth limit
        if self.context_manager.is_backtrack_limit_exceeded():
            return "BACKTRACK_LIMIT"
        
        # Check total backtracks for this architecture
        self.total_backtracks += 1
        if self.total_backtracks > self.max_total_backtracks:
            self.logger.warning(f"Total backtrack limit: {self.total_backtracks}/{self.max_total_backtracks}")
            return "BACKTRACK_LIMIT"
        
        if failed_level == "L0":
            self.logger.error("L0 failed - cannot backtrack")
            return "BACKTRACK_LIMIT"
        
        # Backtrack one level
        level_index = self.levels.index(failed_level)
        target_level = self.levels[level_index - 1]
        
        # Generate conflict report
        conflict_report = self._generate_conflict_report(failed_level, target_level)
        
        # Record backtrack
        self.failure_analytics.record_backtrack(
            failed_level, target_level,
            f"Failed after {self.max_attempts} attempts"
        )
        
        # Update context manager
        self.context_manager.increment_backtrack_depth()
        self.context_manager.invalidate_from_level(target_level)
        self.context_manager.add_conflict_report(target_level, conflict_report.detailed_reasoning)
        
        self.logger.info(f"Backtracking: {failed_level} → {target_level}")
        
        return "CONTINUE"

    def _extract_failure_reason(self) -> str:
        """Extract human-readable failure reason from constraint violations."""
        if not self.failure_analytics.constraint_violations:
            return "Unknown failure reason"
        
        # Get top constraint violations
        sorted_violations = sorted(
            self.failure_analytics.constraint_violations.items(),
            key=lambda x: x[1],
            reverse=True
        )[:3]
        
        reasons = []
        for constraint_key, count in sorted_violations:
            parts = constraint_key.rsplit('_', 1)
            constraint_id = parts[0] if len(parts) > 1 else constraint_key
            
            # Map constraint IDs to human-readable descriptions
            constraint_descriptions = {
                'P1': 'Energy conservation',
                'P2': 'Temporal feasibility',
                'P3': 'Mechanism prerequisites',
                'P5': 'Power adequacy',
                'S2': 'Provenance accuracy',
                'S9': 'SYNTH justification'
            }
            
            desc = constraint_descriptions.get(constraint_id, constraint_id)
            reasons.append(f"{desc} ({count}x)")
        
        return "; ".join(reasons)

    def _check_token_budget(self) -> bool:
        """Check if within token budget."""
        if not self.budget_enabled:
            return True
        
        if self.token_usage.total_tokens >= self.per_run_limit:
            self.logger.error(f"Budget exceeded: {self.token_usage.total_tokens:,}/{self.per_run_limit:,}")
            return False
        
        if self.token_usage.total_tokens >= self.warning_threshold:
            self.logger.warning(f"Approaching budget: {self.token_usage.total_tokens:,}/{self.per_run_limit:,}")
        
        return True

    def _get_current_level_index(self) -> int:
        """Get index of highest uncommitted level."""
        for i, level in enumerate(self.levels):
            if not self.context_manager.levels[level].committed:
                return i
        return len(self.levels)

    def _generate_synthesis(self, context: SynthesisContext, feedback: Optional[str] = None) -> SynthesisResult:
        """Generate synthesis specification for current level."""
        assembled_prompt = self.prompt_assembler.assemble_synthesis_prompt(context, feedback)
        
        llm_response = self.llm_client.call_synthesis_agent(
            system_prompt=assembled_prompt.system_prompt,
            user_prompt=assembled_prompt.user_prompt
        )
        
        if llm_response.success and hasattr(llm_response, 'input_tokens'):
            self.token_usage.add_usage(
                llm_response.input_tokens,
                llm_response.output_tokens,
                self.cost_config
            )
        
        if not llm_response.success:
            return SynthesisResult(
                reasoning="",
                specification="",
                extracted_values={},
                parse_error=f"LLM call failed: {llm_response.error}",
                raw_response=""
            )
        
        return self.parser.parse_synthesis_response(llm_response.content)

    def _validate_synthesis(self, context: SynthesisContext, synthesis: SynthesisResult) -> AggregatedVerdict:
        """Validate synthesis with both validators."""
        physics_result = self._call_validator("physics_validator", context, synthesis)
        structural_result = self._call_validator("structural_validator", context, synthesis)
        
        return self.aggregator.aggregate_results(physics_result, structural_result)

    def _call_validator(self, validator_type: str, context: SynthesisContext, synthesis: SynthesisResult) -> ValidatorResult:
        """Call a specific validator."""
        assembled_prompt = self.prompt_assembler.assemble_validator_prompt(
            validator_type, context, synthesis.specification
        )
        
        llm_response = self.llm_client.call_validator(
            system_prompt=assembled_prompt.system_prompt,
            user_prompt=assembled_prompt.user_prompt
        )
        
        if llm_response.success and hasattr(llm_response, 'input_tokens'):
            self.token_usage.add_usage(
                llm_response.input_tokens,
                llm_response.output_tokens,
                self.cost_config
            )
        
        if not llm_response.success:
            return ValidatorResult(
                validator=validator_type.split('_')[0],
                level=context.current_level,
                overall_status="PARSE_ERROR",
                results=[],
                parse_error=f"LLM call failed: {llm_response.error}",
                raw_response=""
            )
        
        return self.parser.parse_validator_response(llm_response.content)

    def _record_validation_failures(self, validation_result: AggregatedVerdict, level: str):
        """Record validation failures for analytics."""
        for failure in validation_result.failed_constraints:
            self.failure_analytics.record_constraint_violation(
                failure.constraint_id, level, failure.reasoning
            )

    def _generate_conflict_report(self, failed_level: str, target_level: str) -> ConflictReport:
        """Generate conflict report for backtracking."""
        failed_constraints = []
        constraint_details = {}
        
        for key, count in self.failure_analytics.constraint_violations.items():
            if key.endswith(f"_{failed_level}"):
                constraint_id = key.replace(f"_{failed_level}", "")
                failed_constraints.append(constraint_id)
                constraint_details[constraint_id] = f"Failed {count} times"
        
        return ConflictReport(
            failed_level=failed_level,
            target_level=target_level,
            failed_constraints=failed_constraints,
            constraint_details=constraint_details,
            detailed_reasoning=f"Level {failed_level} failed after {self.max_attempts} attempts. "
                             f"Violations: {', '.join(failed_constraints)}.",
            guidance=f"Redesign {target_level} to address: {', '.join(failed_constraints)}"
        )

    def _collect_final_synthesis(self) -> Dict[str, Any]:
        """Collect final synthesis from all committed levels."""
        synthesis = {}
        for level, state in self.context_manager.levels.items():
            if state.committed and state.synthesis:
                synthesis[level] = {
                    "reasoning": state.synthesis.reasoning,
                    "specification": state.synthesis.specification,
                    "extracted_values": state.synthesis.extracted_values
                }
        return synthesis

    def _collect_all_architectures(self) -> List[Dict]:
        """Collect all explored architectures."""
        architectures = []
        
        # Get saved architecture records
        for record in self.context_manager.get_all_architecture_records():
            architectures.append({
                "architecture_id": record.architecture_id,
                "fingerprint": record.fingerprint,
                "summary": record.summary,
                "completed_levels": record.completed_levels,
                "l5_attempts": record.l5_attempts,
                "constraint_violations": record.constraint_violations,
                "failure_reason": record.failure_reason,
                "synthesis_content": record.synthesis_content
            })
        
        # Add current architecture if it has any committed levels
        current_committed = self.context_manager.get_committed_levels()
        if current_committed:
            current_synthesis = {}
            for level, state in self.context_manager.levels.items():
                if state.committed and state.synthesis:
                    current_synthesis[level] = {
                        "reasoning": state.synthesis.reasoning,
                        "specification": state.synthesis.specification,
                        "extracted_values": state.synthesis.extracted_values
                    }
            
            architectures.append({
                "architecture_id": self.context_manager.current_architecture_id,
                "fingerprint": self.context_manager._extract_fingerprint() if self.context_manager.levels["L2"].committed else "INCOMPLETE",
                "summary": "Current/final architecture",
                "completed_levels": current_committed,
                "l5_attempts": self.context_manager.l5_failure_count_this_architecture * 3,
                "constraint_violations": self.failure_analytics.get_constraint_signature(),
                "failure_reason": "Final state" if "L5" not in current_committed else "SUCCESS",
                "synthesis_content": current_synthesis
            })
        
        return architectures

    def _collect_metrics(self) -> Dict[str, Any]:
        """Collect run metrics."""
        history = self.context_manager.get_synthesis_history()
        
        return {
            "completed_levels": len(history['committed_levels']),
            "total_attempts": history['total_attempts'],
            "total_flags": history['total_flags'],
            "backtrack_depth": history['backtrack_depth'],
            "architectures_explored": len(self.context_manager.architecture_records) + 1,
            "fresh_restarts": self.fresh_restart_count,
            "architecture_fingerprints": self.context_manager.get_architecture_fingerprints(),
            "token_usage": {
                "total_tokens": self.token_usage.total_tokens,
                "input_tokens": self.token_usage.input_tokens,
                "output_tokens": self.token_usage.output_tokens,
                "estimated_cost": self.token_usage.estimated_cost,
                "api_calls": self.token_usage.calls_made
            }
        }

    def _generate_final_conflict_report(self, status: RunStatus) -> Optional[ConflictReport]:
        """Generate final conflict report."""
        if status == RunStatus.SUCCESS:
            return None
        
        return ConflictReport(
            failed_level="L5" if self.context_manager.get_highest_committed_level() == "L4" else "UNKNOWN",
            target_level="N/A",
            failed_constraints=list(self.failure_analytics.constraint_violations.keys())[:5],
            constraint_details={},
            detailed_reasoning=f"Exploration ended with status {status.value}",
            guidance="Review architecture fingerprints and constraint patterns"
        )

    def _log_final_summary(self, result: RunResult):
        """Log final run summary."""
        self.logger.info("\n" + "=" * 60)
        self.logger.info("BRANCH-AND-SAVE SYNTHESIS COMPLETE")
        self.logger.info("=" * 60)
        self.logger.info(f"Status: {result.status.value}")
        self.logger.info(f"Architectures explored: {len(result.all_architectures)}")
        self.logger.info(f"Fresh restarts: {self.fresh_restart_count}")
        self.logger.info(f"Total tokens: {result.token_usage.total_tokens:,}")
        self.logger.info(f"Estimated cost: ${result.token_usage.estimated_cost:.2f}")
        self.logger.info(f"Run time: {result.run_time:.1f}s")
        
        if result.successful_architecture:
            self.logger.info("🎉 SUCCESS: Complete L5 specification achieved!")
        else:
            self.logger.info(f"Saved {len(result.all_architectures)} partial architectures")
        
        # Log fingerprints
        self.logger.info("\nArchitecture Fingerprints:")
        for arch in result.all_architectures:
            self.logger.info(f"  #{arch['architecture_id']}: {arch['fingerprint']}")

    def _apply_variation_config(self, variation_config: Dict[str, Any]):
        """Apply variation configuration."""
        if 'synthesis_model' in variation_config:
            self.config['models']['synthesis_agent'] = variation_config['synthesis_model']
        if 'synthesis_temperature' in variation_config:
            self.config['api_settings']['synthesis_temperature'] = variation_config['synthesis_temperature']
        self.llm_client = LLMClient(self.config)

    def run_multiple_variations(self) -> List[RunResult]:
        """Execute all experimental variations."""
        variations = self.config['experiment_settings']['variations']
        results = []
        
        for variation in variations:
            variation_name = variation['name']
            variation_count = variation['count']
            
            self.logger.info(f"Running variation '{variation_name}'")
            
            for run_num in range(variation_count):
                variation_config = {
                    'name': variation_name,
                    'run_number': run_num + 1,
                    'synthesis_model': variation.get('synthesis_model'),
                    'synthesis_temperature': variation.get('synthesis_temperature'),
                }
                
                result = self.run_synthesis(variation_config)
                results.append(result)
        
        return results

    def save_results(self, results: List[RunResult], output_dir: str):
        """Save run results with full architecture details."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        
        # Save summary
        summary = {
            "timestamp": timestamp,
            "version": "2.0",
            "architecture": "branch_and_save",
            "total_runs": len(results),
            "successful_runs": len([r for r in results if r.status == RunStatus.SUCCESS]),
            "total_architectures_explored": sum(len(r.all_architectures) for r in results),
            "results": [
                {
                    "variation": r.variation_config.get('name', 'unknown'),
                    "run_number": r.variation_config.get('run_number', 0),
                    "status": r.status.value,
                    "architectures_explored": len(r.all_architectures),
                    "completed_levels": r.completed_levels,
                    "run_time": r.run_time,
                    "token_usage": {
                        "total_tokens": r.token_usage.total_tokens,
                        "estimated_cost": r.token_usage.estimated_cost,
                        "api_calls": r.token_usage.calls_made
                    }
                }
                for r in results
            ]
        }
        
        summary_path = output_path / f"synthesis_runs_{timestamp}.json"
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)
        
        # Save detailed results for each run
        for i, result in enumerate(results):
            detail_path = output_path / f"run_{i+1:02d}_{timestamp}.json"
            with open(detail_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "status": result.status.value,
                    "completed_levels": result.completed_levels,
                    "successful_architecture": result.successful_architecture,
                    "all_architectures": result.all_architectures,
                    "conflict_report": {
                        "failed_level": result.conflict_report.failed_level,
                        "failed_constraints": result.conflict_report.failed_constraints,
                        "detailed_reasoning": result.conflict_report.detailed_reasoning,
                        "guidance": result.conflict_report.guidance
                    } if result.conflict_report else None,
                    "metrics": result.metrics,
                    "token_usage": {
                        "total_tokens": result.token_usage.total_tokens,
                        "input_tokens": result.token_usage.input_tokens,
                        "output_tokens": result.token_usage.output_tokens,
                        "estimated_cost": result.token_usage.estimated_cost,
                        "api_calls": result.token_usage.calls_made
                    },
                    "failure_analytics": {
                        "constraint_violations": result.failure_analytics.constraint_violations,
                        "backtrack_triggers": result.failure_analytics.backtrack_triggers,
                        "attempt_patterns": result.failure_analytics.attempt_patterns,
                        "parse_errors": result.failure_analytics.parse_errors
                    },
                    "run_time": result.run_time,
                    "variation_config": result.variation_config
                }, f, indent=2)
        
        self.logger.info(f"Results saved to {output_path}")


def main():
    """Main entry point for Level 3 synthesis engine V2"""
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("Level 3 Synthesis Engine V2")
    print("Branch-and-Save Architecture with Meta-Learning")
    print("=" * 60)
    
    orchestrator = SynthesisOrchestrator("config.yaml")
    results = orchestrator.run_multiple_variations()
    orchestrator.save_results(results, "outputs/")
    
    # Print summary
    successful = len([r for r in results if r.status == RunStatus.SUCCESS])
    partial = len([r for r in results if r.status == RunStatus.PARTIAL_SUCCESS])
    total_archs = sum(len(r.all_architectures) for r in results)
    total_cost = sum(r.token_usage.estimated_cost for r in results)
    
    print("\n" + "=" * 60)
    print("CAMPAIGN COMPLETE")
    print("=" * 60)
    print(f"Total runs: {len(results)}")
    print(f"Complete successes (L5): {successful}")
    print(f"Partial successes (L0-L4): {partial}")
    print(f"Total architectures explored: {total_archs}")
    print(f"Total estimated cost: ${total_cost:.2f}")
    print(f"\nResults saved to: outputs/")


if __name__ == "__main__":
    main()
