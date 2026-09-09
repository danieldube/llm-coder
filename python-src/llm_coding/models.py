"""Reviewed model-to-runtime contracts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """All model-owned settings, including its RunPod hardware contract."""

    key: str
    runtime_image_tag: str
    vllm_version: str
    vllm_cuda_version: str
    model_id: str
    model_revision: str
    served_model_name: str
    display_name: str
    context_size: int
    max_output_tokens: int
    gpu_memory_utilization: float
    kv_cache_dtype: str
    enforce_eager: bool
    language_model_only: bool
    max_num_seqs: int
    reasoning_parser: str
    tool_call_parser: str
    runpod_gpu_type: str
    runpod_gpu_count: int
    tensor_parallel_size: int


MODEL_SPECS: dict[str, ModelSpec] = {
    'qwen3-coder-30b-a3b-fp8': ModelSpec(
        key='qwen3-coder-30b-a3b-fp8',
        runtime_image_tag='latest',
        vllm_version='0.28.0',
        vllm_cuda_version='129',
        model_id='Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8',
        model_revision='e8ab3f2db9e388999a004eea5a31c16a8b517bc0',
        served_model_name='qwen3-coder',
        display_name='Qwen3-Coder 30B A3B FP8',
        context_size=65536,
        max_output_tokens=16384,
        gpu_memory_utilization=0.92,
        kv_cache_dtype='auto',
        enforce_eager=False,
        language_model_only=False,
        max_num_seqs=256,
        reasoning_parser='',
        tool_call_parser='qwen3_xml',
        runpod_gpu_type='NVIDIA L40S',
        runpod_gpu_count=1,
        tensor_parallel_size=1,
    ),
    'qwen3-coder-next-fp8': ModelSpec(
        key='qwen3-coder-next-fp8',
        runtime_image_tag='latest',
        vllm_version='0.28.0',
        vllm_cuda_version='129',
        model_id='Qwen/Qwen3-Coder-Next-FP8',
        model_revision='da6e2ed27304dd39abadd9c82ef50e8de67bdd4c',
        served_model_name='qwen3-coder-next',
        display_name='Qwen3-Coder-Next FP8',
        context_size=32768,
        max_output_tokens=16384,
        gpu_memory_utilization=0.90,
        kv_cache_dtype='auto',
        enforce_eager=False,
        language_model_only=False,
        max_num_seqs=256,
        reasoning_parser='',
        tool_call_parser='qwen3_coder',
        runpod_gpu_type='NVIDIA H200',
        runpod_gpu_count=1,
        tensor_parallel_size=1,
    ),
    'qwen3.8-nvfp4': ModelSpec(
        key='qwen3.8-nvfp4',
        runtime_image_tag='latest',
        vllm_version='0.28.0',
        vllm_cuda_version='129',
        model_id='Inferact/Qwen3.8-27B-NVFP4',
        model_revision='cb12525975f2527d9fefbe7b13de65546db30f9a',
        served_model_name='qwen38-coder',
        display_name='Inferact Qwen3.8 27B NVFP4',
        context_size=32768,
        max_output_tokens=16384,
        gpu_memory_utilization=0.92,
        kv_cache_dtype='fp8',
        enforce_eager=True,
        language_model_only=True,
        max_num_seqs=8,
        reasoning_parser='qwen3',
        tool_call_parser='qwen3_coder',
        runpod_gpu_type='NVIDIA GeForce RTX 5090',
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
