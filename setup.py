#!/usr/bin/env python
from pathlib import Path

from setuptools import setup

version = "0.0.0"
with open("assai/server/__init__.py") as file:
    for line in file.readlines():
        if "version" in line:
            version = line.split("=")[1].strip().replace('"', "")
            break

extra_requires = {"plugins": ["importlib_resources"]}
extra_requires["all"] = sorted(set(sum(extra_requires.values(), [])))

if __name__ == "__main__":
    setup(
        name="assai",
        version=version,
        extras_require=extra_requires,
        description="Tool to test AI models",
        long_description=(Path(__file__).parent / "README.md").read_text(),
        author="Delaunay",
        author_email="pierre@delaunay.io",
        license="BSD 3-Clause License",
        url="https://assai.readthedocs.io",
        classifiers=[
            "License :: OSI Approved :: BSD License",
            "Programming Language :: Python :: 3.8",
            "Programming Language :: Python :: 3.9",
            "Programming Language :: Python :: 3.10",
            "Operating System :: OS Independent",
        ],
        packages=[
            "assai.server",
            "assai.models",
            "assai.cli",
            "assai.models.text2image",
        ],
        setup_requires=["setuptools"],
        install_requires=[
            "importlib_resources",
            "flask",
            "flask-socketio",
            "accelerate",
            "protobuf", # WHY
            "torchcompat",
            "voir",
            "nvidia-ml-py",
            "datasets",

            # Audio
            "librosa", # Audio
            "torchaudio",

            # Text
            "transformers",
            "sentencepiece",

            # Image
            "diffusers",

            # Video
            "kernels",
            "opencv-python"
            "imageio",
            "imageio-ffmpeg"
            "av",
        ],
        package_data={
            "assai.data": [
                "assai/data",
            ],
        },
    )
