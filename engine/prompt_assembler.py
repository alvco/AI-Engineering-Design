"""
Prompt Assembler for Level 3 Synthesis Engine

Loads static prompt templates and injects dynamic context including:
- Current level and parent synthesis
- Filtered constraints by level  
- Source specifications
- Feedback and conflict reports
"""

import logging
import yaml
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass

from context_manager import SynthesisContext, ContextManager


@dataclass 
class AssembledPrompt:
    """Assembled prompt with system and user components"""
    system_prompt: str
    user_prompt: str
    template_name: str
    level: str


class PromptAssembler:
    """
    Assembles prompts by loading templates and injecting dynamic context.
    
    Handles:
    - Template loading from .md files
    - System/user prompt separation  
    - Dynamic placeholder replacement
    - Constraint filtering by level
    - Source specification injection
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize prompt assembler with configuration.
        
        Args:
            config: Configuration dict from config.yaml
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Template and constraint cache
        self._template_cache = {}
        self._constraint_cache = {}
        self._source_cache = {}
        
        # Load static content
        self._load_constraints()
        self._load_source_specs()
        
        self.logger.info("PromptAssembler initialized")

    def _load_constraints(self):
        """Load constraint definitions from YAML files"""
        try:
            # Load physics constraints
            physics_path = Path(self.config['constraint_files']['physics'])
            with open(physics_path, 'r', encoding='utf-8') as f:
                physics_yaml = yaml.safe_load(f)
                self._constraint_cache['physics'] = physics_yaml['physics_constraints']
            
            # Load structural constraints  
            structural_path = Path(self.config['constraint_files']['structural'])
            with open(structural_path, 'r', encoding='utf-8') as f:
                structural_yaml = yaml.safe_load(f)
                self._constraint_cache['structural'] = structural_yaml['structural_constraints']
                self._constraint_cache['thematic'] = structural_yaml['thematic_constraints']
            
            # Load SEDF codes
            sedf_path = Path(self.config['constraint_files']['sedf_codes'])
            with open(sedf_path, 'r', encoding='utf-8') as f:
                sedf_yaml = yaml.safe_load(f)
                self._constraint_cache['sedf_codes'] = sedf_yaml['sedf_valid_codes']
            
            # Load mechanism prerequisites
            mech_path = Path(self.config['constraint_files']['mechanism_prerequisites'])
            with open(mech_path, 'r', encoding='utf-8') as f:
                mech_yaml = yaml.safe_load(f)
                self._constraint_cache['mechanism_prerequisites'] = mech_yaml['mechanism_prerequisites']
            
            self.logger.info("Constraint definitions loaded successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to load constraints: {e}")
            raise

    def _load_source_specs(self):
        """Load source specifications from YAML files"""
        try:
            # Load Spec A (Pistol Shrimp)
            spec_a_path = Path(self.config['source_specification_files']['spec_a'])
            with open(spec_a_path, 'r', encoding='utf-8') as f:
                self._source_cache['spec_a'] = yaml.safe_load(f)
            
            # Load Spec B (Rock Breaker)  
            spec_b_path = Path(self.config['source_specification_files']['spec_b'])
            with open(spec_b_path, 'r', encoding='utf-8') as f:
                self._source_cache['spec_b'] = yaml.safe_load(f)
            
            self.logger.info("Source specifications loaded successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to load source specs: {e}")
            raise

    def _load_template(self, template_name: str) -> str:
        """
        Load prompt template from .md file with caching.
        
        Args:
            template_name: Name of template (synthesis_agent, physics_validator, etc.)
            
        Returns:
            Template content as string
        """
        if template_name in self._template_cache:
            return self._template_cache[template_name]
        
        try:
            template_path = Path(self.config['prompt_files'][template_name])
            with open(template_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self._template_cache[template_name] = content
            self.logger.info(f"Loaded template: {template_name}")
            return content
            
        except Exception as e:
            self.logger.error(f"Failed to load template {template_name}: {e}")
            raise

    def _split_template(self, template_content: str) -> Tuple[str, str]:
        """
        Split template into system and user prompts.
        
        Looks for === SYSTEM_PROMPT === and === USER_PROMPT === separators.
        If not found, treats entire template as system prompt.
        
        Args:
            template_content: Raw template content
            
        Returns:
            Tuple of (system_prompt, user_prompt)
        """
        # Look for system/user separators
        system_pattern = re.compile(r'^[\s]*===\s*SYSTEM[_\s]*PROMPT\s*===\s*$', re.MULTILINE | re.IGNORECASE)
        user_pattern = re.compile(r'^[\s]*===\s*USER[_\s]*PROMPT\s*===\s*$', re.MULTILINE | re.IGNORECASE)
        
        system_match = system_pattern.search(template_content)
        user_match = user_pattern.search(template_content)
        
        if system_match and user_match:
            # Both separators found - split the template
            system_start = system_match.end()
            user_start = user_match.start()
            user_content_start = user_match.end()
            
            system_prompt = template_content[system_start:user_start].strip()
            user_prompt = template_content[user_content_start:].strip()
            
        elif system_match:
            # Only system separator found - everything after it is system
            system_prompt = template_content[system_match.end():].strip()
            user_prompt = ""
            
        elif user_match:
            # Only user separator found - everything before is system, after is user
            system_prompt = template_content[:user_match.start()].strip()
            user_prompt = template_content[user_match.end():].strip()
            
        else:
            # No separators - treat entire template as system prompt
            system_prompt = template_content.strip()
            user_prompt = ""
        
        return system_prompt, user_prompt

    def _filter_constraints_for_level(self, level: str) -> Dict[str, Any]:
        """
        Filter constraints to only those applicable to the specified level.
        
        Args:
            level: SEDF level (L0-L5)
            
        Returns:
            Dict of filtered constraints
        """
        filtered = {
            'physics': {},
            'structural': {},
            'thematic': {}
        }
        
        # Filter physics constraints
        for constraint_id, constraint in self._constraint_cache['physics'].items():
            if level in constraint.get('applies_to', []):
                filtered['physics'][constraint_id] = constraint
        
        # Filter structural constraints
        for constraint_id, constraint in self._constraint_cache['structural'].items():
            if level in constraint.get('applies_to', []):
                filtered['structural'][constraint_id] = constraint
        
        # Filter thematic constraints
        for constraint_id, constraint in self._constraint_cache['thematic'].items():
            if level in constraint.get('applies_to', []):
                filtered['thematic'][constraint_id] = constraint
        
        return filtered

    def _format_constraints_for_prompt(self, constraints: Dict[str, Any]) -> str:
        """
        Format filtered constraints as text for prompt injection.
        
        Args:
            constraints: Filtered constraints by category
            
        Returns:
            Formatted constraint text
        """
        lines = []
        
        # Physics constraints
        if constraints['physics']:
            lines.append("PHYSICS CONSTRAINTS:")
            for constraint_id, constraint in constraints['physics'].items():
                lines.append(f"- {constraint['id']}: {constraint['name']}")
                lines.append(f"  {constraint['description']}")
                if constraint.get('formula'):
                    lines.append(f"  Formula: {constraint['formula']}")
                lines.append("")
        
        # Structural constraints
        if constraints['structural']:
            lines.append("STRUCTURAL CONSTRAINTS:")
            for constraint_id, constraint in constraints['structural'].items():
                lines.append(f"- {constraint['id']}: {constraint['name']}")
                lines.append(f"  {constraint['description']}")
                lines.append("")
        
        # Thematic constraints
        if constraints['thematic']:
            lines.append("THEMATIC CONSTRAINTS:")
            for constraint_id, constraint in constraints['thematic'].items():
                lines.append(f"- {constraint['id']}: {constraint['name']}")
                lines.append(f"  {constraint['description']}")
                lines.append("")
        
        return "\n".join(lines)

    def _format_source_specs_for_prompt(self) -> str:
        """
        Format source specifications as text for prompt injection.
        
        Returns:
            Formatted source specification text
        """
        lines = []
        
        # Format Spec A (Pistol Shrimp)
        spec_a = self._source_cache['spec_a']
        lines.append("SOURCE SPECIFICATION A: PISTOL SHRIMP")
        lines.append(f"System: {spec_a['metadata']['system_name']}")
        lines.append(f"Function: {spec_a['metadata']['function']}")
        lines.append("")
        
        # Add key sections (abbreviated for prompt length)
        if 'L0_governing_concept' in spec_a:
            lines.append("L0 - Governing Concept:")
            lines.append(f"  {spec_a['L0_governing_concept']['description']}")
            lines.append("")
        
        if 'L2_domain_primitives' in spec_a:
            lines.append("L2 - Domain Primitives:")
            for domain, config in spec_a['L2_domain_primitives'].items():
                lines.append(f"  {domain}: {config['code']} - {config['description']}")
            lines.append("")
        
        if 'L5_parameters' in spec_a:
            lines.append("L5 - Reference Parameters:")
            # Simple dump of key parameters to save token space while keeping data
            for category, params in spec_a['L5_parameters'].items():
                lines.append(f"  {category}:")
                for name, details in params.items():
                    if isinstance(details, dict):
                        val = details.get('value', '?')
                        unit = details.get('unit', '')
                        lines.append(f"    {name}: {val} {unit}")
                    else:
                        lines.append(f"    {name}: {details}")
            lines.append("")
        
        # Format Spec B (Rock Breaker)
        spec_b = self._source_cache['spec_b']
        lines.append("SOURCE SPECIFICATION B: HYDRAULIC ROCK BREAKER")
        lines.append(f"System: {spec_b['metadata']['system_name']}")
        lines.append(f"Function: {spec_b['metadata']['function']}")
        lines.append("")
        
        if 'L0_governing_concept' in spec_b:
            lines.append("L0 - Governing Concept:")
            lines.append(f"  {spec_b['L0_governing_concept']['description']}")
            lines.append("")
        
        if 'L2_domain_primitives' in spec_b:
            lines.append("L2 - Domain Primitives:")
            for domain, config in spec_b['L2_domain_primitives'].items():
                lines.append(f"  {domain}: {config['code']} - {config['description']}")
            lines.append("")
        
        if 'L5_parameters' in spec_b:
            lines.append("L5 - Reference Parameters:")
            # Simple dump of key parameters to save token space while keeping data
            for category, params in spec_b['L5_parameters'].items():
                lines.append(f"  {category}:")
                for name, details in params.items():
                    if isinstance(details, dict):
                        val = details.get('value', '?')
                        unit = details.get('unit', '')
                        lines.append(f"    {name}: {val} {unit}")
                    else:
                        lines.append(f"    {name}: {details}")
            lines.append("")
        
        return "\n".join(lines)

    def assemble_synthesis_prompt(self, 
                                 context: SynthesisContext,
                                 feedback: Optional[str] = None) -> AssembledPrompt:
        """
        Assemble synthesis agent prompt with dynamic context injection.
        
        Args:
            context: Synthesis context from ContextManager
            feedback: Validation feedback (if revision attempt)
            
        Returns:
            AssembledPrompt with system and user components
        """
        # Load template
        template_content = self._load_template('synthesis_agent')
        system_prompt, user_prompt_template = self._split_template(template_content)
        
        # Get filtered constraints for this level
        filtered_constraints = self._filter_constraints_for_level(context.current_level)
        constraints_text = self._format_constraints_for_prompt(filtered_constraints)
        
        # Format context
        context_text = self._format_synthesis_context(context)
        
        # Format source specs
        source_specs_text = self._format_source_specs_for_prompt()
        
        # Format feedback
        feedback_text = feedback or "No feedback - initial attempt"
        
        # Replace placeholders in user prompt
        user_prompt = user_prompt_template.format(
            CURRENT_LEVEL=context.current_level,
            PARENT_CONTEXT=context_text,
            FEEDBACK=feedback_text,
            CONFLICT_REPORT=context.conflict_report or "No conflict report",
            CONSTRAINTS=constraints_text,
            SOURCE_SPECS=source_specs_text,
            ATTEMPT_NUMBER=context.attempt_number
        )
        
        return AssembledPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            template_name='synthesis_agent',
            level=context.current_level
        )

    def assemble_validator_prompt(self, 
                                 validator_type: str,
                                 context: SynthesisContext,
                                 synthesis_draft: str) -> AssembledPrompt:
        """
        Assemble validator prompt with constraint filtering and synthesis draft.
        
        Args:
            validator_type: "physics_validator" or "structural_validator"
            context: Synthesis context
            synthesis_draft: Draft specification to validate
            
        Returns:
            AssembledPrompt with system and user components
        """
        # Load template
        template_content = self._load_template(validator_type)
        system_prompt, user_prompt_template = self._split_template(template_content)
        
        # Get filtered constraints for this level
        filtered_constraints = self._filter_constraints_for_level(context.current_level)
        constraints_text = self._format_constraints_for_prompt(filtered_constraints)
        
        # Format additional validator-specific content
        if validator_type == 'physics_validator':
            # Include mechanism prerequisites for physics validator
            mech_prereqs = yaml.dump(self._constraint_cache['mechanism_prerequisites'], 
                                   default_flow_style=False)
            additional_content = f"MECHANISM PREREQUISITES:\n{mech_prereqs}"
        else:
            # Include SEDF codes for structural validator  
            sedf_codes = yaml.dump(self._constraint_cache['sedf_codes'],
                                 default_flow_style=False)
            additional_content = f"SEDF VALID CODES:\n{sedf_codes}"
        
        # Replace placeholders in user prompt
        user_prompt = user_prompt_template.format(
            CURRENT_LEVEL=context.current_level,
            SYNTHESIS_DRAFT=synthesis_draft,
            CONSTRAINTS=constraints_text,
            ADDITIONAL_CONTENT=additional_content
        )
        
        return AssembledPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            template_name=validator_type,
            level=context.current_level
        )

    def _format_synthesis_context(self, context: SynthesisContext) -> str:
        """
        Format synthesis context for prompt injection.
        
        Args:
            context: Synthesis context from ContextManager
            
        Returns:
            Formatted context text
        """
        lines = []
        
        lines.append(f"CURRENT LEVEL: {context.current_level}")
        lines.append(f"ATTEMPT: #{context.attempt_number}")
        
        if context.backtrack_depth > 0:
            lines.append(f"BACKTRACK DEPTH: {context.backtrack_depth}")
        
        if context.parent_synthesis:
            lines.append("\nVALIDATED PARENT LEVELS:")
            # Ensure chronological order
            for level in ["L0", "L1", "L2", "L3", "L4", "L5"]:
                if level in context.parent_synthesis:
                    synthesis = context.parent_synthesis[level]
                    lines.append(f"\n{level}:")
                    lines.append(synthesis.specification)
        else:
            lines.append("\nNO PARENT LEVELS (Starting fresh at L0)")
        
        if context.conflict_report:
            lines.append("\nCONFLICT REPORT FROM DOWNSTREAM FAILURE:")
            lines.append(context.conflict_report)
        
        return "\n".join(lines)

    def get_template_placeholders(self, template_name: str) -> List[str]:
        """
        Extract placeholder names from a template for validation.
        
        Args:
            template_name: Name of template to analyze
            
        Returns:
            List of placeholder names (without {})
        """
        template_content = self._load_template(template_name)
        
        # Find all placeholders in format {PLACEHOLDER_NAME}
        placeholder_pattern = re.compile(r'\{([A-Z_]+)\}')
        matches = placeholder_pattern.findall(template_content)
        
        return list(set(matches))  # Remove duplicates

    def validate_template_placeholders(self) -> Dict[str, List[str]]:
        """
        Validate that all templates have expected placeholders.
        
        Returns:
            Dict mapping template names to missing placeholders
        """
        expected_placeholders = {
            'synthesis_agent': [
                'CURRENT_LEVEL', 'PARENT_CONTEXT', 'FEEDBACK', 
                'CONFLICT_REPORT', 'CONSTRAINTS', 'SOURCE_SPECS', 'ATTEMPT_NUMBER'
            ],
            'physics_validator': [
                'CURRENT_LEVEL', 'SYNTHESIS_DRAFT', 'CONSTRAINTS', 'ADDITIONAL_CONTENT'
            ],
            'structural_validator': [
                'CURRENT_LEVEL', 'SYNTHESIS_DRAFT', 'CONSTRAINTS', 'ADDITIONAL_CONTENT'
            ]
        }
        
        missing_placeholders = {}
        
        for template_name, expected in expected_placeholders.items():
            try:
                actual = self.get_template_placeholders(template_name)
                missing = [p for p in expected if p not in actual]
                if missing:
                    missing_placeholders[template_name] = missing
            except Exception as e:
                missing_placeholders[template_name] = [f"Template load error: {e}"]
        
        return missing_placeholders


# Test functions for development
def test_prompt_assembler():
    """Test prompt assembler with mock configuration"""
    
    # Mock minimal config for testing
    config = {
        'constraint_files': {
            'physics': 'constraints/physics.yaml',
            'structural': 'constraints/structural.yaml', 
            'sedf_codes': 'constraints/sedf_codes.yaml',
            'mechanism_prerequisites': 'constraints/mechanism_prerequisites.yaml'
        },
        'prompt_files': {
            'synthesis_agent': 'prompts/synthesis_agent.md',
            'physics_validator': 'prompts/physics_validator.md',
            'structural_validator': 'prompts/structural_validator.md'
        },
        'source_specification_files': {
            'spec_a': 'source_specs/spec_a.yaml',
            'spec_b': 'source_specs/spec_b.yaml'  
        }
    }
    
    try:
        assembler = PromptAssembler(config)
        
        # Test constraint filtering
        l5_constraints = assembler._filter_constraints_for_level("L5")
        print(f"L5 physics constraints: {len(l5_constraints['physics'])}")
        
        # Test placeholder validation
        missing = assembler.validate_template_placeholders()
        print(f"Missing placeholders: {missing}")
        
    except Exception as e:
        print(f"Test failed (expected without actual files): {e}")


if __name__ == "__main__":
    test_prompt_assembler()