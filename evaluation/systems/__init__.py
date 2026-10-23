"""The systems the benchmark can run, by name.

The four systems of the framework register here as they are built (YGH-68 to YGH-70):
direct_llm (done), naive_rag, structured, full. The two model-free fakes exercise the runner
end to end.
"""

from evaluation.systems.direct_llm import DirectLLMSystem
from evaluation.systems.fakes import AbstainSystem, EchoSystem

REGISTRY = {
    AbstainSystem.name: AbstainSystem,
    EchoSystem.name: EchoSystem,
    DirectLLMSystem.name: DirectLLMSystem,
}


def available() -> list:
    return sorted(REGISTRY)


def get_system(name: str):
    if name not in REGISTRY:
        raise KeyError(f"unknown system {name!r}; available: {', '.join(available())}")
    return REGISTRY[name]()
