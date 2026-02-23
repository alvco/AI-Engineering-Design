#!/usr/bin/env python3
"""
Run script for Level 3 Synthesis Engine V2
Branch-and-Save Architecture with Meta-Learning

Usage:
    cd v2
    python run_synthesis.py
"""

import sys
import os
import logging

# Add engine directory to Python path so imports work correctly
engine_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'engine')
sys.path.insert(0, engine_path)

# Now import from engine
from orchestrator import SynthesisOrchestrator, RunStatus


def main():
    """Main entry point"""
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s:%(name)s:%(message)s'
    )
    
    # Suppress overly verbose loggers
    logging.getLogger('anthropic').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    
    print("=" * 60)
    print("Level 3 Synthesis Engine V2")
    print("Branch-and-Save Architecture with Meta-Learning")
    print("=" * 60)
    
    try:
        # Initialize orchestrator
        orchestrator = SynthesisOrchestrator("config.yaml")
        
        # Run synthesis
        results = orchestrator.run_multiple_variations()
        
        # Save results
        orchestrator.save_results(results, "outputs/")
        
        # Print summary
        successful = len([r for r in results if r.status == RunStatus.SUCCESS])
        partial = len([r for r in results if r.status == RunStatus.PARTIAL_SUCCESS])
        total_archs = sum(len(r.all_architectures) for r in results)
        total_tokens = sum(r.token_usage.total_tokens for r in results)
        total_cost = sum(r.token_usage.estimated_cost for r in results)
        total_time = sum(r.run_time for r in results)
        
        print("\n" + "=" * 60)
        print("SYNTHESIS CAMPAIGN COMPLETE")
        print("=" * 60)
        print(f"Total runs: {len(results)}")
        print(f"Complete successes (L5): {successful}")
        print(f"Partial successes (L0-L4): {partial}")
        print(f"Success rate: {successful/len(results)*100:.1f}%")
        print(f"Total architectures explored: {total_archs}")
        print(f"Total tokens used: {total_tokens:,}")
        print(f"Total estimated cost: ${total_cost:.2f}")
        print(f"Total run time: {total_time:.1f}s ({total_time/60:.1f} min)")
        
        # Print architecture fingerprints
        print("\nArchitectures Explored:")
        for result in results:
            for arch in result.all_architectures:
                status = "✓ SUCCESS" if arch.get('failure_reason') == 'SUCCESS' else f"✗ {arch.get('failure_reason', 'Failed')[:30]}"
                print(f"  #{arch['architecture_id']}: {arch['fingerprint']} - {status}")
        
        print(f"\nResults saved to: outputs/")
        print("Check run_*.json files for detailed synthesis content")
        
    except Exception as e:
        import traceback
        print(f"\nERROR: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()