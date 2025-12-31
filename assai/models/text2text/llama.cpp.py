import os
import subprocess

# ./dependencies/llama.cpp/build/bin/llama-cli \
#     --model /opt/milabench/data/hub/unsloth/Llama-4-Scout-17B-16E-Instruct-GGUF/UD-Q4_K_XL/Llama-4-Scout-17B-16E-Instruct-UD-Q4_K_XL-00001-of-00002.gguf \
#     --threads 32 \
#     --ctx-size 16384 \
#     --n-gpu-layers 99 \
#     -ot ".ffn_.*_exps.=CPU" \
#     --seed 3407 \
#     --prio 3 \
#     --temp 0.6 \
#     --min-p 0.01 \
#     --top-p 0.9 \
#     -no-cnv \
#     --prompt "<|header_start|>user<|header_end|>\n\nCreate a Flappy Bird game in Python. You must include these things:\n1. You must use pygame.\n2. The background color should be randomly chosen and is a light shade. Start with a light blue color.\n3. Pressing SPACE multiple times will accelerate the bird.\n4. The bird's shape should be randomly chosen as a square, circle or triangle. The color should be randomly chosen as a dark color.\n5. Place on the bottom some land colored as dark brown or yellow chosen randomly.\n6. Make a score shown on the top right side. Increment if you pass pipes and don't hit them.\n7. Make randomly spaced pipes with enough space. Color them randomly as dark green or light brown or a dark gray shade.\n8. When you lose, show the best score. Make the text inside the screen. Pressing q or Esc will quit the game. Restarting is pressing SPACE again.\nThe final game should be inside a markdown section in Python. Check your code for errors and fix them before the final markdown section.<|eot|><|header_start|>assistant<|header_end|>\n\n"



LLAMA_CPP = "/home/delaunao/workspace/assai/dependencies/llama.cpp"
LLAMA_CLI = os.path.join(LLAMA_CPP, "build", "bin", "llama-cli ")


def arguments(model)
    return [
        "--model", model
        "--threads", "32",
        "--ctx-size", "16384",
        "--n-gpu-layers", "99",
        "-ot", '".ffn_.*_exps.=CPU"',
        "--seed", "3407",
        "--prio", "3",
        "--temp", "0.6",
        "--min-p", "0.01",
        "--top-p", "0.9",
        "-no-cnv",
    ]

class ModelProcess
    def __init__(self, model_id):
        self.proc = Popen(
            LLAMA_CLI, + arguments(model_id)
        )

    def __call__(self, prompt):
        pass


def load(model_name):
    model = ModelProcess(model_name)
    return model
