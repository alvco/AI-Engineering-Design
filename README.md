# Impossible Biomimetic Synthesis: AI Reasoning in Engineering Design

A hierarchical, constrained multi-agent synthesis framework for studying language model reasoning when tasked with engineering design under adversarial conditions.

## Overview

This system instructs a language model to synthesize a hydraulic rock breaker with a pistol shrimp to produce a novel hybrid mechanical construction. The deep incompatibility between these two systems - disparate energy mechanisms, operating mediums, and energy scales - makes coherent synthesis extremely unlikely. This deliberate adversarial framing creates conditions for observing where model reasoning succeeds and where it degrades.

The framework decomposes mechanical systems across six levels of abstraction, enforces provenance tracking against source specifications, and validates outputs against 22 physics and structural constraints. Three LLM-based agents (synthesis, physics validation, structural validation) are coordinated by Python orchestration code.

The system produced seven discrete designs across its full execution. The results and analysis are documented in the accompanying paper.

## Repository Structure

```
├── constraints/                    # Validation constraint definitions
│   ├── physics.yaml                # Universal physics constraints (P1-P12)
│   ├── structural.yaml             # Structural/methodology constraints (S1-S9, T1)
│   ├── mechanism_prerequisites.yaml # Mechanism-specific thresholds
│   └── edf_codes.yaml              # Valid domain primitive codes
│
├── engine/                         # Core synthesis engine
│   ├── orchestrator.py             # Main orchestration and control flow
│   ├── llm_client.py               # Anthropic API interface
│   ├── prompt_assembler.py         # Prompt construction for all agents
│   ├── output_parser.py            # LLM response parsing
│   ├── aggregator.py               # Verdict aggregation logic
│   ├── context_manager.py          # Synthesis state and context tracking
│   ├── config.yaml                 # Engine configuration and parameters
│   ├── run_synthesis.py            # Entry point
│   └── __init__.py
│
├── prompts/                        # System prompts for LLM agents
│   ├── synthesis_agent.md          # Synthesis agent instructions
│   ├── physics_validator.md        # Physics validator instructions
│   └── structural_validator.md     # Structural validator instructions
│
├── Source Specs and EDF/           # Source specifications and framework
│   ├── spec_a.yaml                 # Pistol shrimp technical specification
│   ├── spec_b.yaml                 # Hydraulic rock breaker technical specification
│   └── EDF.docx                    # Engineering Decomposition Framework document
│
├── outputs/                        # Raw synthesis outputs
│   └── raw outputs.json            # Complete JSON outputs for all seven designs
│
└── Impossible Biomimetic Synthesis AI Reasoning in Engineering Design.docx
```

## Architecture

The system employs three LLM-based agents coordinated by Python orchestration code:

- **Synthesis Agent** (temperature 0.4) — generates design specifications level by level and draws from source specifications with provenance tracking
- **Physics Validator** (temperature 0.1) — independently verifies quantitative claims through extraction and recalculation
- **Structural Validator** (temperature 0.1) — verifies provenance claims against source specifications via mini-RAG search and enforces SYNTH justification requirements

The orchestrator routes synthesis outputs to both validators in parallel. Any constraint failure triggers retry with structured feedback. After three failed attempts at a given level, the system restarts from L0 with negative constraint injection.

## Configuration

The engine requires an Anthropic API key set as an environment variable:

```
set ANTHROPIC_API_KEY=your-key-here
```

Model and parameter settings are defined in `engine/config.yaml`.

## Citation

If referencing this work, please cite the accompanying paper:

> Impossible Biomimetic Synthesis: AI Reasoning in Engineering Design (2026)

## License

MIT
