"""
Level 3 Synthesis Engine

A hierarchical constrained synthesis system for AI-mediated functional design.
Implements systematic biomimetic synthesis with rigorous physics and structural validation.
"""

__version__ = "1.0.0"
__author__ = "Level 3 Synthesis Research Team"

# Core engine components
from .orchestrator import SynthesisOrchestrator, RunStatus, RunResult
from .output_parser import OutputParser, SynthesisResult, ValidatorResult  
from .llm_client import LLMClient, LLMResponse
from .aggregator import VerdictAggregator, VerdictType, AggregatedVerdict
from .context_manager import ContextManager, SynthesisContext
from .prompt_assembler import PromptAssembler, AssembledPrompt

__all__ = [
    'SynthesisOrchestrator',
    'OutputParser', 
    'LLMClient',
    'VerdictAggregator',
    'ContextManager',
    'PromptAssembler',
    'RunStatus',
    'RunResult',
    'SynthesisResult',
    'ValidatorResult',
    'LLMResponse',
    'VerdictType',
    'AggregatedVerdict', 
    'SynthesisContext',
    'AssembledPrompt'
]
