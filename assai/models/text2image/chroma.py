import torch

model_id = "lodestones/Chroma1-Base"


def load():
    import torch
    from diffusers import ChromaPipeline

    pipe = ChromaPipeline.from_pretrained(
        model_id,
        device_map="cuda",
    )

    return pipe