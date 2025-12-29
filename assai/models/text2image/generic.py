


import torch


def load():
    from diffusers import DiffusionPipeline

    pipe = DiffusionPipeline.from_pretrained(
        model_name, 
        torch_dtype=torch.bfloat16,
        device_map="cuda",
    )

    return pipe