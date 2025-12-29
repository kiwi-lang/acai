import torch

model_id = ["purplesmartai/pony-v7-base"]


def load(model_name):
    import torch
    from diffusers import BitsAndBytesConfig as DiffusersBitsAndBytesConfig, AuraFlowTransformer2DModel, AuraFlowPipeline
    from transformers import BitsAndBytesConfig as BitsAndBytesConfig, UMT5EncoderModel

    # quant_config = BitsAndBytesConfig(load_in_8bit=True)
    text_encoder_8bit = UMT5EncoderModel.from_pretrained(
        model_name,
        subfolder="text_encoder",
        # quantization_config=quant_config,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
    )

    quant_config = DiffusersBitsAndBytesConfig(load_in_8bit=True)
    transformer_8bit = AuraFlowTransformer2DModel.from_pretrained(
        model_name,
        subfolder="transformer",
        # quantization_config=quant_config,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
    )

    pipe = AuraFlowPipeline.from_pretrained(
        model_name,
        text_encoder=text_encoder_8bit,
        transformer=transformer_8bit,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
    )

    return pipe