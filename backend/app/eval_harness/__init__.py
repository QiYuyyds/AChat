"""
Aeval — Agent Evaluation Framework

A reusable, open-source evaluation framework for AI agents, driven by OTel traces.

Usage:
    from eval_harness import EvalRunner, EvalSuite

    runner = EvalRunner(agent_runner=my_runner)
    suite = EvalSuite.from_yaml("suite.yaml")
    result = await runner.run_suite(suite)
"""

__version__ = "0.1.0"
