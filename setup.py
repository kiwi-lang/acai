#!/usr/bin/env python
from pathlib import Path

from setuptools import find_packages, setup

if __name__ == "__main__":
    setup(
        name="assai",
        version="0.1.0",
        description="AI agent swarm orchestrator for coding",
        long_description=(Path(__file__).parent / "README.md").read_text(),
        long_description_content_type="text/markdown",
        author="Delaunay",
        author_email="pierre@delaunay.io",
        license="BSD 3-Clause License",
        url="https://assai.readthedocs.io",
        python_requires=">=3.10",
        classifiers=[
            "License :: OSI Approved :: BSD License",
            "Programming Language :: Python :: 3.10",
            "Programming Language :: Python :: 3.11",
            "Programming Language :: Python :: 3.12",
            "Operating System :: OS Independent",
        ],
        packages=find_packages(exclude=["tests", "tests.*"]),
        package_data={
            "assai": [
                "prompts/*.md",
                "data/*.json",
                "agents/*/definition.json",
                "agents/*/system.j2",
            ],
        },
        install_requires=[
            "argklass",
            "fastapi",
            "uvicorn[standard]",
            "python-socketio",
            "flask",
            "flask-socketio",
            "requests",
            "sqlalchemy",
            "pyyaml",
            "importlib_resources",
            "cantilever",
            "voir",
            "qwen_vl_utils",
            "torchvision",
            "vllm"
        ],
        extras_require={
            "models": [
                "accelerate",
                "torch",
                "torchcompat",
                "transformers",
                "sentencepiece",
                "diffusers",
                "datasets",
                "librosa",
                "torchaudio",
                "opencv-python",
                "imageio",
                "imageio-ffmpeg",
                "av",
            ],
            "dev": [
                "pytest",
                "pytest-cov",
                "ruff",
            ],
        },
        entry_points={
            "console_scripts": [
                "assai=assai.cli:main",
            ],
        },
    )
