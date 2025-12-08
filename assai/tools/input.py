from dataclasses import dataclass



@dataclass
class Input:
    kind: str   
    encoding: str
    data: str


@dataclass
class Message:
    role: str
    content: Input


@dataclass
class Conversation:
    messages: list[Message]


def text(text) -> Input:
    return {
        "kind": "text",
        "encoding": "utf8",
        "data": text
    }


def image_b64(img) -> Input:
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {
        "kind": "image",
        "encoding": "data_url",
        "data": f"data:image/png;base64,{b64}"
    }


def audio_b64(audio) -> Input:
    buffer = BytesIO()

    return {
        "kind": "audio",
        "encoding": "data_url",
        "data": f"data:audio/mp3;base64,{b64}"
    }

def video_b64(audio) -> Input:
    buffer = BytesIO()

    return {
        "kind": "video",
        "encoding": "data_url",
        "data": f"..."
    }