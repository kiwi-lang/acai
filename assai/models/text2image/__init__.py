from __future__ import annotations

import torch
from diffusers import FluxPipeline


def routes(app: ASSAI):

    model = 'black-forest-labs/FLUX.1-dev' # DEV
    # Kontext 
    # Schnell
    # Krea Dev
    # Fill 
    # Depth

    pipe = FluxPipeline.from_pretrained(
        model, 
        torch_dtype=torch.bfloat16,
        device_map="cuda"
    )
    pipe.enable_model_cpu_offload()

    prompt = "A cat holding a sign that says hello world"
    image = pipe(
        prompt,
        height=1024,
        width=1024,
        guidance_scale=3.5,
        num_inference_steps=50,
        max_sequence_length=512,
        generator=torch.Generator("cpu").manual_seed(0)
    ).images[0]
    image.save("flux-dev.png")