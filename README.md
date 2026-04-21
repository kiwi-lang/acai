acai
=====

* Test for AI model in inference 

Generative AI
   - [X] Text to Text      (Regular LLM)
   - [X] Text to image     (Diffusion)
   - [X] Text to Video     ()

Vision
   - [ ] Depth Estimation
   - [ ] Key point detection
   - [ ] Object Detection
   - [ ] Image Segmentation

3D
   - [X] Image to Mesh     ()

Audio
   - [X] Text to Speech    (TTS)
   - [X] Speech to Text    (Whisper)
   - [ ] Text to audio

Graph
   - [ ] Graph Machine Learning
      - Protein-Protein Interaction Prediction
      - Molecular Property Prediction



```mermaid
flowchart TD
    User --> IN_Speech[Speech]
    User --> IN_Text[Text]
    User --> IN_Image[Photo]

    IN_Speech --> MODEL_S2T[Speech to Text]--> IN_Text
    IN_Image[Photo] --> MODEL_LLM[Text to Text]

    IN_Text --> MODEL_LLM[Text to Text] --> OUT_Text[Text] --> MODEL_TTS[Text to Speech] --> OUT_Speech[Speech]
    IN_Text --> MODEL_T2I[Text to Image] --> OUT_Image[Image]
    IN_Text --> MODEL_T2A[Text to Audio] --> OUT_Audio[Audio]

    User@{shape: doc}

    IN_Speech@{shape: lean-r}
    IN_Text@{shape: lean-r}

    OUT_Text@{shape: lean-l}
    OUT_Image@{shape: lean-l}
    OUT_Audio@{shape: lean-l}
    OUT_Speech@{shape: lean-l}

    MODEL_S2T@{shape: lin-rect}
    MODEL_LLM@{shape: lin-rect}
    MODEL_TTS@{shape: lin-rect}
    MODEL_T2I@{shape: lin-rect}
    MODEL_T2A@{shape: lin-rect}

```




* Audio
   * AudioClassification
   * AutomaticSpeechRecognition
   * TextToAudio
   * ZeroShotAudioClassification
* Computer Vision
   * Depth Estimation
   * Image Classification
   * Image Segmentation
   * Image to Image
   * KeypointMatching
   * Object Detection
   * VideoClassification
   * ZeroShotImageClassification
   * ZeroShotObjectDetection
* NLP
   * QuestionAnswering
   * Summarization
   * TextClassification
   * TextGeneration
   * Translaation
* MultiModal
   * Image to Text




Models

* Text To Image
   * Black Forest Lab: Flux.1 | Flux.1 Krea | Flux.1 Kontext | Flux.2
      * 57Go
   * Google: Imagen4 | Nano Banana
   * OpenAI
   * Pony: Pony Diffusion | Pony Diffusion V7
      * https://huggingface.co/purplesmartai/pony-v7-base:  60Go
      * 
   * SDXL:  Ilustrious | NoobAI
      * https://huggingface.co/stablediffusionapi/nova-anime-xl-v8.0-ilustrious: 7Go
      * https://huggingface.co/CabalResearch/NoobAI-Flux2VAE-RectifiedFlow: 70Go
   * SD: Stable Diffusion 1.X | Stable Diffusion XL
      * https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0: 80Go
      * 
   * ZImageTurbo
      * Tongyi-MAI/Z-Image-Turbo: 32Go
   * Chroma: 
      * lodestones/Chroma1-Base:    45 Go
      * lodestones/Chroma1-Radiance: 57 Go
   * HiDream
      * https://huggingface.co/HiDream-ai/HiDream-E1-Full/tree/main: 47Go
   * Seedream
      * https://huggingface.co/tm-hf-repo/seedream-pixar
   * Qwen
      * https://huggingface.co/Qwen/Qwen-Image: 57 Go

* Text to Video
   * Sora 2
   * Google VEO 3
   * Vidu Q1
   * Hailuo by MiniMax
   * Kling
      * https://huggingface.co/KlingTeam/SVG-T2I/tree/main: 107Go
   * Lightricks
      * https://huggingface.co/Lightricks/LTX-Video/tree/main: 254Go
   * Mochi
      * https://huggingface.co/genmo/mochi-1-preview: 134Go
   * Hunyuan
      * https://huggingface.co/tencent/HunyuanVideo-1.5/tree/main: 372Go
   * Wan
      * https://huggingface.co/Wan-AI/Wan2.2-I2V-A14B-Diffusers/tree/main: 126Go

# Wan-AI/Wan2.2-T2V-A14B-Diffusers
# Wan-AI/Wan2.2-T2V-A14B-Diffusers : Text to Video
# Wan-AI/Wan2.2-Animate-14B        : Video to Video
# Wan-AI/Wan2.2-S2V-14B            : Speech-to-Video
# Wan-AI/Wan2.2-I2V-A14B           : Image to Video
# Wan-AI/Wan2.2-TI2V-5B            : Text | Image to Video
# 


# https://www.reddit.com/r/StableDiffusion/comments/1q08ro5/qwenimage2512_released_on_huggingface/

Unsloth
<!-- 
BF16 & FP8 by Comfy-Org https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/tree/main/split_files/diffusion_models
GGUF's: https://huggingface.co/unsloth/Qwen-Image-2512-GGUF
4-step Turbo lora: https://huggingface.co/Wuli-art/Qwen-Image-2512-Turbo-LoRA -->