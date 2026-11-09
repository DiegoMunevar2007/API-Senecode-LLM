"""Carga/actualiza en la base los modelos que el master ofrece a los nodos."""

import asyncio

from tortoise import Tortoise

from src.config import TORTOISE_CONFIG
from src.local_models import Modelo

MODELOS = [
    {
        "modelo_id": "gpt-oss-20b-q5",
        "filename": "gpt-oss-20b-Q5_K_S.llamafile",
        "sha256": "be16430d2e8bd30f74a40b353210549e5ab9db542f012b89da4968a281444e93",
        "size_bytes": 11922260755,
        "download_url": (
            "https://huggingface.co/mozilla-ai/llamafile_0.10/resolve/main/"
            "gpt-oss-20b-Q5_K_S.llamafile"
        ),
        "context_size": 16384,
        "parallel": 1,
        "extra_args": [],
        "enabled": True,
    },
    {
        "modelo_id": "qwen3.8-27b-q4",
        "filename": "Qwen3.8-27B-UD-Q4_K_XL.llamafile",
        "sha256": "d5a367c996054bc360bfeebdc46a1a5f597b3de90adaad3eaa98519fdbdc5582",
        "size_bytes": 19065029719,
        "download_url": (
            "https://huggingface.co/mozilla-ai/llamafile_0.10/resolve/main/"
            "Qwen3.8-27B-UD-Q4_K_XL.llamafile"
        ),
        "context_size": 8192,
        "parallel": 1,
        "extra_args": [],
        "enabled": True,
    },
    {
        "modelo_id": "gemma-4-31b-q4",
        "filename": "gemma-4-31b-it-q4_k_m.llamafile",
        "sha256": "04ad460e1b08322b1a1c1867dc54a89033c4a08e24defd43010813e099b5f4e8",
        "size_bytes": 19055395940,
        "download_url": (
            "https://huggingface.co/diegocrea/gemma-4-31b-it-q4_k_m.llamafile/"
            "resolve/main/gemma-4-31b-it-q4_k_m.llamafile"
        ),
        "context_size": 8192,
        "parallel": 1,
        "extra_args": [],
        "enabled": True,
    },
]


async def run() -> None:
    await Tortoise.init(config=TORTOISE_CONFIG)
    try:
        for data in MODELOS:
            defaults = {k: v for k, v in data.items() if k != "modelo_id"}
            await Modelo.update_or_create(modelo_id=data["modelo_id"], defaults=defaults)
        print("modelos:", ", ".join(m["modelo_id"] for m in MODELOS))
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(run())
