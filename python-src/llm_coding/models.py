"""Reviewed model-to-runtime contracts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """All model-owned settings, including its RunPod hardware contract."""

    key: str
    model_id: str
    model_revision: str
    served_model_name: str
    display_name: str
    context_size: int
    max_output_tokens: int
    gpu_memory_utilization: float
    tool_call_parser: str
    runpod_gpu_type: str
    runpod_gpu_count: int
    tensor_parallel_size: int


MODEL_SPECS: dict[str, ModelSpec] = {
    'qwen3-coder-30b-a3b-fp8': ModelSpec(
        key='qwen3-coder-30b-a3b-fp8',
        model_id='Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8',
        model_revision='e8ab3f2db9e388999a004eea5a31c16a8b517bc0',
        served_model_name='qwen3-coder',
        display_name='Qwen3-Coder 30B A3B FP8',
        context_size=65536,
        max_output_tokens=16384,
        gpu_memory_utilization=0.92,
        tool_call_parser='qwen3_xml',
        runpod_gpu_type='NVIDIA L40S',
        runpod_gpu_count=1,
        tensor_parallel_size=1,
    ),
    'qwen3-coder-next-fp8': ModelSpec(
        key='qwen3-coder-next-fp8',
        model_id='Qwen/Qwen3-Coder-Next-FP8',
        model_revision='da6e2ed27304dd39abadd9c82ef50e8de67bdd4c',
        served_model_name='qwen3-coder-next',
        display_name='Qwen3-Coder-Next FP8',
        context_size=32768,
        max_output_tokens=16384,
        gpu_memory_utilization=0.90,
        tool_call_parser='qwen3_coder',
        runpod_gpu_type='NVIDIA H200',
        runpod_gpu_count=1,
        tensor_parallel_size=1,
    ),
}


def model_spec(key: str) -> ModelSpec:
    """Return a reviewed model contract or raise a configuration error."""
    try:
        return MODEL_SPECS[key]
    except KeyError as exc:
        supported = ', '.join(sorted(MODEL_SPECS))
        raise ValueError(
            f'MODEL must name a supported model: {supported}'
        ) from exc
