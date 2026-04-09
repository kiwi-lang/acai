import torch
import traceback
from multiprocessing import Pipe, Process, set_start_method


from assai.tools.remote import RemoteModel

#

#
model_id = [
    # "Qwen/Qwen3-VL-235B-A22B-Thinking-FP8" # even FP8 is too big for us
    "Qwen/Qwen3-Next-80B-A3B-Thinking-FP8", # 80Go
]


#
# git clone https://github.com/sgl-project/sglang.git
# cd sglang/sgl-kernel
# export TORCH_CUDA_ARCH_LIST=12.1
# IT TAKES AL LOT OF RAM
# make build MAX_JOBS=2
#
#
# cd /opt/milabench/dependencies/sglang/sgl-kernel/
# . /home/delaunao/workspace/assai/.venv/bin/activate



# MLA_ATTENTION_BACKENDS = [
#     "aiter",
#     "flashinfer",
#     "fa3",
#     "fa4",
#     "triton",
#     "flashmla",
#     "cutlass_mla",
#     "trtllm_mla",
#     "ascend",
#     "nsa",
# ]

# CHUNKED_PREFIX_CACHE_SUPPORTED_ATTENTION_BACKENDS = [
#     "flashinfer",
#     "fa3",
#     "fa4",
#     "flashmla",
#     "cutlass_mla",
#     "trtllm_mla",
# ]


class QwenModel:
    def __init__(self, model_name):
        from qwen_vl_utils import process_vision_info
        from transformers import AutoProcessor

        import sglang.srt.entrypoints.engine as engine
        from sglang import Engine

        # from sglang import Streamer

        self.processor = AutoProcessor.from_pretrained(model_name)
        self.process_vision_info = process_vision_info
        # Cannot work as because this use signal :(
        self.llm = Engine(
            model_path=model_name,
            enable_multimodal=True,
            mem_fraction_static=0.9,
            tp_size=torch.cuda.device_count(),
            attention_backend="triton",
            # attention_backend="fa3"
        )

    def __call__(self, text):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                ],
            }
        ]

        image_inputs, _ = self.process_vision_info(messages, image_patch_size=self.processor.image_processor.patch_size)

        text = self.processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        # sampling_params = {"max_new_tokens": 1024}
        # response = llm.generate(prompt=text, image_data=image_inputs, sampling_params=sampling_params)
        # return response["text"]

        streamer = self.llm.stream_generate(
            prompt=text,
            image_data=image_inputs,
            max_new_tokens=8192
        )

        for token in streamer:
            yield token  # yield each token as it arrives

 

def load(model_name):
    pipe = RemoteModel(QwenModel, model_name)

    return pipe

