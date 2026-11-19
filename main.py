from __future__ import annotations

from typing import TYPE_CHECKING

from sceneforge.pipeline.orchestrator import generate_prompt

if TYPE_CHECKING:
    from witness_client import AsyncWitnessClient


async def generate(image: bytes, seed: int, witness: AsyncWitnessClient) -> str | None:
    return await generate_prompt(image, seed, witness)
