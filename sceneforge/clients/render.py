from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Protocol

from witness_client import RenderRejected, RenderUnavailable

from sceneforge.pipeline.diagnostics import log

if TYPE_CHECKING:
    from witness_client import AsyncWitnessClient, RenderOptions

RENDER_ATTEMPTS = 3
RENDER_WAITS = (5, 20)
RENDER_TIMEOUT_RETRIES = 2
UNDELIVERED_LIMIT = 18
RENDER_TIMEOUT = ("RENDER_TIMEOUT: the renderer could not finish this module within its 30-second limit. The usual "
                  "cause is the triangle count with every InstancedMesh instance counted (instances times the "
                  "triangles of their geometry), which the validator does not limit. Keep the whole scene well under "
                  "300,000 triangles counting instances: thousands of instances need a geometry of a few dozen "
                  "triangles, and fine repeated relief (knit stitches, weave, scales, sequins, grain) belongs in a "
                  "DataTexture or in a few hundred larger elements, not one mesh or instance per element. Also lower "
                  "segment counts and merge small meshes.")


class Renderer(Protocol):
    async def render(self, source: str, options: RenderOptions) -> bytes: ...


class RendererLost(RuntimeError):
    pass


def rejection_detail(rejection: RenderRejected) -> str:
    return json.dumps(rejection.details, separators=(",", ":"), ensure_ascii=False)


class PatientRenderer:
    def __init__(self, witness: AsyncWitnessClient, tag: str = ""):
        self._witness = witness
        self._tag = tag
        self._undelivered: set[tuple[str, RenderOptions]] = set()

    async def render(self, source: str, options: RenderOptions) -> bytes:
        key = (source, options)
        if key in self._undelivered:
            raise RenderRejected(json.dumps({"error": RENDER_TIMEOUT}))
        timeouts = 0
        failures = 0
        while True:
            try:
                return await self._witness.render(source, options)
            except RenderRejected as rejection:
                timeouts += 1
                if "TIMEOUT_EXCEEDED" not in rejection_detail(rejection) or timeouts > RENDER_TIMEOUT_RETRIES:
                    raise
            except RenderUnavailable as error:
                failures += 1
                if failures < RENDER_ATTEMPTS:
                    await asyncio.sleep(RENDER_WAITS[min(failures, len(RENDER_WAITS)) - 1])
                    continue
                self._undelivered.add(key)
                reason = (str(error).splitlines() or [type(error).__name__])[0][:160]
                log(self._tag, f"render not delivered in {failures} attempts ({reason}): "
                               f"undelivered render {len(self._undelivered)} of this prompt")
                if len(self._undelivered) > UNDELIVERED_LIMIT:
                    raise RendererLost(f"{len(self._undelivered)} renders were not delivered") from error
                raise RenderRejected(json.dumps({"error": RENDER_TIMEOUT})) from error
