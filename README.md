assai
=====


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
   