"""PersonalHealthBench: synthetic data generator, scorers and benchmark runner (M14).

Design: docs/06_EVALUATION_FRAMEWORK.md (metrics) and
benchmarks/personal_health_bench/README.md (dataset and generator spec).
Nothing in this package may import services.verification or services.safety to SCORE
them: the benchmark must not grade the system with the system's own rules.
"""
