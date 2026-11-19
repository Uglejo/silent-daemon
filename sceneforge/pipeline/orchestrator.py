from __future__ import annotations

import asyncio
import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openai import APIConnectionError, APIError, APITimeoutError
from witness_client import ExchangePending, RenderOptions, RenderRejected, RenderUnavailable, WitnessError

from sceneforge.clients.llm import Llm, ProviderFailed, RequestRejected
from sceneforge.clients.render import RENDER_TIMEOUT, PatientRenderer, Renderer, rejection_detail
from sceneforge.analysis import parts
from sceneforge.pipeline import photo, pose, tilt
from sceneforge.pipeline.assets import CONFIG_PATH, PLACEHOLDER_PATH, PRICING_PATH, PROMPTS_DIR, load_config, rate_table
from sceneforge.pipeline.budget import PromptBudget
from sceneforge.pipeline.diagnostics import log
from sceneforge.pipeline.edits import edit_blocks, edited
from sceneforge.pipeline.images import data_url, object_closeup, to_jpeg, view_sheet, views_match, zoomed_views
from sceneforge.pipeline.placeholder import placeholder_module
from sceneforge.pipeline.wrapper import (chain_wrapper, extract_source, normalize, rotated, stood_up, tilted,
                                         turned_module, with_metal_pass)
from sceneforge.schema.config import Focus, MinerConfig, Op, Rewrite, Start, Step
from sceneforge.schema.judgement import (FRONT_VIEWS, brief_format, critic_format, critique_format,
                                         select_close_format)
from sceneforge.schema.llm_reply import LlmReply
from sceneforge.schema.messages import ImageDetail, InputImage, InputText, UserMessage
from sceneforge.schema.rates import RateTable
from sceneforge.schema.responses_api import Reasoning, ReasoningEffort, ResponsesRequest, TokenUsage

if TYPE_CHECKING:
    from witness_client import AsyncWitnessClient

DRAFT_ID = 1
REDRAFT_ID = 50
REWRITE_BASE = 100
CRITIC_OFFSET = 9
REPAIR_OFFSET = 5000
SELECT_ID = 9000
DRAFT_TIE_ID = 9008
CRITIQUE_ID = 9700
MERGE_ID = 9710
FINAL_SELECT_ID = 9800
POSE_ID = 9900
BRIEF_ID = 9990
REDRAFTS = 3
POSE_PITCHES = (45, 90)
JOIN_ATTEMPTS = 6
OVERLOAD_WAITS = (5, 15, 30, 60)
OVERLOADED = ("Unusable provider response", "currently overloaded", "server_is_overloaded", "HTTP 429")
UNJUDGED = 99.0
TIE = 1e-9
VIEW_NAMES = {"front_left": "30 degrees left", "front_right": "30 degrees right", "front_below": "from below",
              "front_above": "from above", "grid": "grid"}

FRONT_JUDGE = RenderOptions(thetas=(30, 330, 0, 0), phis=(0, 0, 15, -30), img_size=1024, gap=0, bg_color="ffffff",
                            lighting="neutral")
GRID_VIEW = RenderOptions(lighting="neutral")
LEVEL_VIEW = RenderOptions(thetas=(0,), phis=(0,), img_size=384, gap=0, bg_color="ffffff", lighting="neutral")

INFRASTRUCTURE_FAILURES = (APIConnectionError, WitnessError, ExchangePending, RenderUnavailable)


class CallFailed(Exception):
    pass


@dataclass
class Attempt:
    result: str
    front: bytes


@dataclass
class Candidate:
    name: str
    source: str
    front: bytes
    sides: bytes
    raw: str = ""
    views: list[bytes] = field(default_factory=list)
    parent: Candidate | None = None
    match: float = 0.0
    measurements: str = ""
    overlay: bytes = b""
    front_png: bytes = b""
    grid_png: bytes = b""
    rejected: list[Attempt] = field(default_factory=list)


@dataclass
class Call:
    request_id: int
    model: str
    instructions: str
    content: list[InputText | InputImage]
    effort: ReasoningEffort
    max_output_tokens: int
    text: dict | None = None
    estimate: float = 0.0


def image_input(image: bytes, detail: ImageDetail = ImageDetail.HIGH) -> InputImage:
    return InputImage(image_url=data_url(image), detail=detail)


class Pipeline:
    def __init__(self, config: MinerConfig, rates: RateTable, image: bytes, seed: int, llm: Llm,
                 renderer: Renderer):
        self.config = config
        self.plan = config.plan
        self.image = image
        self.seed = seed
        self.llm = llm
        self.renderer = renderer
        self.budget = PromptBudget(config.budget, rates)
        self.digest = hashlib.sha256(image).hexdigest()
        self.tag = self.digest[:10]
        self.target = image_input(image)
        self.best_so_far: str | None = None
        self.wrapper = chain_wrapper(self.plan.fill, self.plan.glow, self.plan.outline_fit or self.plan.measured,
                                     self.plan.flat_tilt)
        self.closeup: list[InputImage] = []
        self.instructions = self.text("GENERATE")
        self.astra_cap = config.calls.astra_max_output
        self.photo: photo.Appearance | None = None
        self.brief = ""
        self.penalties: dict[str, dict[str, float]] = {}
        self.render_timeouts = 0

    def text(self, name: str, **values: str) -> str:
        book = self.config.prompts
        return (book.fill(name, **values) if values else book[name]).rstrip("\n")


    async def run_stage(self, calls: list[Call]) -> list[LlmReply | None]:
        admitted = []
        for call in calls:
            call.estimate = self.worst(call)
            admitted.append(self.budget.reserve(call.estimate))
            if not admitted[-1]:
                log(self.tag, f"call {call.request_id}: {call.estimate:.0f} credits do not fit after "
                              f"{self.budget.calls} calls and {self.budget.credits:.0f} credits")
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(self.dispatch(call)) if ok else None
                         for call, ok in zip(calls, admitted, strict=True)]
        except ExceptionGroup as failures:
            raise failures.exceptions[0] from None
        return [task.result() if task is not None else None for task in tasks]

    def worst(self, call: Call) -> float:
        chars = len(call.instructions) + sum(len(item.text) for item in call.content if isinstance(item, InputText))
        images = sum(isinstance(item, InputImage) for item in call.content)
        return self.budget.worst(call.model, chars, images, call.max_output_tokens)

    async def dispatch(self, call: Call) -> LlmReply | None:
        usage: TokenUsage | None = None
        try:
            reply = await self.create(call)
            usage = reply.usage
            if usage is None:
                raise CallFailed(f"call {call.request_id}: reply without usage")
            return reply
        except ProviderFailed as error:
            usage = error.usage
            if usage is None:
                raise CallFailed(f"call {call.request_id}: {error}") from error
            log(self.tag, f"call {call.request_id}: {error}")
            return None
        except RequestRejected as error:
            usage = TokenUsage(input_tokens=0, output_tokens=0)
            log(self.tag, f"call {call.request_id}: {error}")
            return None
        except APIConnectionError:
            raise
        except APIError as error:
            raise CallFailed(f"call {call.request_id}: {error}") from error
        finally:
            self.budget.settle(call.estimate, call.model, usage)

    async def create(self, call: Call) -> LlmReply:
        request = ResponsesRequest(model=call.model, reasoning=Reasoning(effort=call.effort),
                                   max_output_tokens=call.max_output_tokens, instructions=call.instructions,
                                   input=[UserMessage(content=call.content)], text=call.text)
        waits = iter(OVERLOAD_WAITS)
        for attempt in range(JOIN_ATTEMPTS + len(OVERLOAD_WAITS)):
            try:
                return await self.llm.call(request, request_id=call.request_id)
            except APITimeoutError:
                if attempt == JOIN_ATTEMPTS - 1:
                    raise
            except APIError as error:
                wait = next(waits, None)
                if wait is None or not any(sign in str(error) for sign in OVERLOADED):
                    raise
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    def module_call(self, request_id: int, model: str, effort: ReasoningEffort, extra: list[InputText | InputImage],
                    start: Start = Start.PLAIN, note: str = "", instructions: str = "") -> Call:
        target = self.text("TARGET_PROPORTIONS" if start == Start.PROPORTIONS else "TARGET")
        if note:
            target = f"{target}\n\n{note}"
        if self.brief and not extra:
            target = f"{target}\n\n{self.brief}"
        calls = self.config.calls
        cap = self.astra_cap if model == calls.astra_model else calls.effort_max_output.get(effort,
                                                                                             calls.module_max_output)
        return Call(request_id, model, instructions or self.instructions,
                    [InputText(text=target), self.target, *self.closeup, *extra], effort, cap)

    def rewrite_call(self, request_id: int, model: str, effort: ReasoningEffort, parent: Candidate,
                     focus: Focus, plan: str = "", edits: bool = False) -> Call:
        views = [image_input(view) for view in parent.views]
        focus_text = "" if focus == Focus.NONE else "\n\n" + self.text(f"FOCUS_{focus.name}")
        rejected, attempts = self.earlier(parent)
        if plan:
            return self.module_call(request_id, model, effort, [
                InputText(text=self.text("REWRITE_VIEWS")), *views, image_input(parent.sides),
                InputText(text=self.text("REWRITE_PLAN", source=parent.raw, focus=focus_text, rejected=rejected,
                                         plan=plan)), *attempts])
        if self.photo is not None or self.brief:
            overlay = [image_input(parent.overlay, ImageDetail.LOW)] if parent.overlay else []
            text = self.text("REWRITE_MEASURED", source=parent.raw, focus=focus_text, rejected=rejected,
                             brief=self.brief, measurements=parent.measurements)
            return self.module_call(request_id, model, effort,
                                    [*views, image_input(parent.sides), *overlay, InputText(text=text), *attempts])
        if edits:
            return self.module_call(request_id, model, effort, [
                InputText(text=self.text("REWRITE_VIEWS")), *views, image_input(parent.sides),
                InputText(text=self.text("REWRITE_EDITS", source=parent.raw, focus=focus_text, rejected=rejected)),
                *attempts], instructions=self.text("GENERATE_EDITS"))
        return self.module_call(request_id, model, effort, [
            InputText(text=self.text("REWRITE_VIEWS")), *views, image_input(parent.sides),
            InputText(text=self.text("REWRITE", source=parent.raw, focus=focus_text, rejected=rejected)), *attempts])

    def earlier(self, parent: Candidate) -> tuple[str, list[InputText | InputImage]]:
        shown = list(enumerate(parent.rejected, 1))[-self.plan.rejected_shown:] if self.plan.rejected_shown else []
        if not shown:
            return "", []
        lines = [self.text("REJECTED_ATTEMPT", number=str(number), result=attempt.result) for number, attempt in shown]
        images = [item for number, attempt in shown
                  for item in (InputText(text=f"DISCARDED ATTEMPT {number}, FRONT VIEWS:"), image_input(attempt.front))]
        return "\n\n" + self.text("REJECTED", attempts="\n".join(lines)), images

    def critic_call(self, request_id: int, critic: Rewrite, parent: Candidate, analysis: parts.PartAnalysis) -> Call:
        findings = parts.lints(analysis, self.config.lints)
        rejected, attempts = self.earlier(parent)
        content: list[InputText | InputImage] = [
            InputText(text="TARGET:"), self.target, *self.closeup,
            InputText(text="MODEL, FOUR CLOSE VIEWS:"), *[image_input(view) for view in parent.views],
            InputText(text="MODEL, GRAY GRID:"), image_input(parent.sides),
            InputText(text="PARTS:"), image_input(to_jpeg(parts.sheet(analysis))),
            InputText(text=self.text("CRITIC_INPUT", table=parts.table(analysis), rejected=rejected,
                                     findings="\n".join(f"- {finding}" for finding in findings) or "- (none)")),
            *attempts]
        return Call(request_id, critic.model, self.text("CRITIC"), content, critic.effort,
                    self.config.calls.critic_max_output, text=critic_format())

    @staticmethod
    def edit_plan(reply: LlmReply | None) -> str:
        try:
            edits = json.loads(reply.text)["edits"] if reply is not None else []
            lines = [f"{number}. {edit['part']}: {edit['problem']} -> {edit['change']}"
                     for number, edit in enumerate(edits[:10], 1)]
        except (ValueError, KeyError, TypeError):
            return ""
        return "\n".join(lines)

    async def critic_plans(self, survivors: list[Candidate], step: Step, index: int) -> list[str]:
        calls, owners = [], []
        for position, parent in enumerate(survivors):
            if not parent.raw:
                continue
            try:
                analysis = await parts.analyze(self.renderer, parent.raw, self.wrapper, parent.front_png,
                                               parent.grid_png, FRONT_JUDGE, GRID_VIEW)
            except RenderRejected:
                log(self.tag, f"step {index}: no part analysis of {parent.name}")
                continue
            calls.append(self.critic_call(REWRITE_BASE * (index + 1) + 10 * position + CRITIC_OFFSET, step.critic,
                                          parent, analysis))
            owners.append(position)
        plans = [""] * len(survivors)
        for position, reply in zip(owners, await self.run_stage(calls), strict=True):
            plans[position] = self.edit_plan(reply)
            log(self.tag, f"step {index}: critic plan for {survivors[position].name}: "
                          f"{plans[position].count(chr(10)) + 1 if plans[position] else 0} edits")
        return plans


    async def render_module(self, name: str, source: str, raw: str = "") -> Candidate | tuple[str, str]:
        try:
            front = await self.renderer.render(source, FRONT_JUDGE)
            views = zoomed_views(front)
            grid_png = await self.renderer.render(source, GRID_VIEW)
        except RenderRejected as rejection:
            return source, rejection_detail(rejection)
        candidate = Candidate(name, source, view_sheet(views), to_jpeg(grid_png), raw, views, front_png=front,
                              grid_png=grid_png)
        if self.photo is not None:
            candidate.match = photo.grid_score(front, self.photo)
            candidate.measurements, candidate.overlay = photo.feedback(front, self.photo)
        return candidate

    def reply_module(self, reply: LlmReply | None, base: str, name: str) -> str | None:
        if reply is None:
            return None
        if not base or not edit_blocks(reply.text):
            return extract_source(reply.text)
        source, applied, skipped = edited(base, reply.text)
        log(self.tag, f"{name}: {applied} edits applied, {skipped} skipped")
        return extract_source(source) if source is not None else None

    async def modules(self, calls: list[Call], names: list[str],
                      bases: list[str] | None = None) -> list[Candidate | None]:
        results: list[Candidate | None] = [None] * len(calls)
        rejected: list[tuple[int, str, str]] = []
        for index, reply in enumerate(await self.run_stage(calls)):
            raw = self.reply_module(reply, bases[index] if bases else "", names[index])
            if raw is None:
                continue
            outcome = await self.render_module(names[index], normalize(raw, self.wrapper), raw)
            if isinstance(outcome, Candidate):
                results[index] = outcome
            else:
                rejected.append((index, raw, outcome[1]))
                self.render_timeouts += RENDER_TIMEOUT in outcome[1]
        repairs = [self.module_call(calls[index].request_id + REPAIR_OFFSET, self.config.calls.repair_model,
                                    calls[index].effort,
                                    [InputText(text=self.text("REPAIR", errors=errors, source=raw))])
                   for index, raw, errors in rejected]
        for (index, _, _), reply in zip(rejected, await self.run_stage(repairs), strict=True):
            raw = extract_source(reply.text) if reply is not None else None
            if raw is not None:
                outcome = await self.render_module(names[index], normalize(raw, self.wrapper), raw)
                if isinstance(outcome, Candidate):
                    results[index] = outcome
        log(self.tag, f"modules: {sum(c is not None for c in results)} of {len(calls)} rendered "
                      f"({len(rejected)} repaired): " + ", ".join(c.name for c in results if c is not None))
        return results


    def score_calls(self, candidates: list[Candidate], request_id: int, instructions: str = "SELECT_CLOSE",
                    judge: Rewrite | None = None, slot: int = 0) -> tuple[list[dict[str, Candidate]], list[Call]]:
        ordered = candidates.copy()
        random.Random(f"{self.seed}:{self.digest}:{request_id}").shuffle(ordered)
        model, effort = ((judge.model, judge.effort) if judge is not None
                         else (self.config.calls.select_model, self.config.calls.select_effort))
        shown, calls = [], []
        for offset, order in enumerate((ordered, ordered[::-1])):
            labels = {chr(65 + i): candidate for i, candidate in enumerate(order)}
            content: list[InputText | InputImage] = [InputText(text="TARGET:"), self.target, *self.closeup]
            if self.brief:
                content.append(InputText(text=self.brief))
            for label, candidate in labels.items():
                content += [InputText(text=f"CANDIDATE {label} FRONT VIEWS:"), image_input(candidate.front),
                            InputText(text=f"CANDIDATE {label} GRID:"), image_input(candidate.sides)]
            calls.append(Call(request_id + 2 * slot + offset, model, self.text(instructions), content, effort,
                              self.config.calls.select_max_output, text=select_close_format(list(labels))))
            shown.append(labels)
        return shown, calls

    async def scores(self, candidates: list[Candidate], request_id: int, instructions: str = "SELECT_CLOSE",
                     judges: list[Rewrite] | None = None) -> dict[str, float]:
        if not judges:
            shown, calls = self.score_calls(candidates, request_id, instructions)
            judgements = self.parse_scores(shown, await self.run_stage(calls))
        else:
            calls = []
            for slot, judge in enumerate(judges):
                shown, pair = self.score_calls(candidates, request_id, instructions, judge, slot)
                calls += pair
            replies = await self.run_stage(calls)
            standardized: dict[str, list[float]] = {}
            for slot in range(len(judges)):
                for name, score in self.standardized(self.parse_scores(shown, replies[2 * slot:2 * slot + 2])).items():
                    standardized.setdefault(name, []).append(score)
            judgements = {name: sum(values) / len(values) for name, values in standardized.items()}
        log(self.tag, f"scores {request_id}: " + ", ".join(f"{name} {score:.2f}" for name, score in judgements.items()))
        return judgements

    @staticmethod
    def standardized(scores: dict[str, float]) -> dict[str, float]:
        if not scores:
            return {}
        mean = sum(scores.values()) / len(scores)
        spread = (sum((score - mean) ** 2 for score in scores.values()) / len(scores)) ** 0.5
        return {name: (score - mean) / spread if spread else 0.0 for name, score in scores.items()}

    def parse_scores(self, shown: list[dict[str, Candidate]], replies: list[LlmReply | None]) -> dict[str, float]:
        selection = self.config.selection
        scores: dict[str, list[float]] = {}
        views: dict[str, list[dict[str, int]]] = {}
        for labels, reply in zip(shown, replies, strict=True):
            if reply is None:
                continue
            try:
                entries = json.loads(reply.text)["candidates"]
            except (ValueError, KeyError, TypeError):
                continue
            seen = set()
            for entry in entries:
                label = entry.get("label")
                if label not in labels or label in seen:
                    continue
                seen.add(label)
                try:
                    seen_views = {view: min(10, max(0, int(entry[view]))) for view in FRONT_VIEWS}
                    score = sum(seen_views.values()) / 4
                    if int(entry["garbage_sides"]) >= 3:
                        score += selection.garbage_penalty
                    if "grid" in entry:
                        seen_views["grid"] = min(10, max(0, int(entry["grid"])))
                        score += selection.grid_weight * seen_views["grid"]
                except (KeyError, TypeError, ValueError):
                    continue
                scores.setdefault(labels[label].name, []).append(score)
                views.setdefault(labels[label].name, []).append(seen_views)
        for name, replies_views in views.items():
            self.penalties[name] = {
                view: sum(values) / len(values) for view in VIEW_NAMES
                if (values := [reply_views[view] for reply_views in replies_views if view in reply_views])}
        return {name: sum(values) / len(values) for name, values in scores.items()}

    def attempt(self, child: Candidate, parent: Candidate) -> Attempt:
        mine, theirs = self.penalties.get(child.name, {}), self.penalties.get(parent.name, {})
        result = ", ".join(f"{name} {mine[view]:g} vs {theirs[view]:g}" for view, name in VIEW_NAMES.items()
                           if view in mine and view in theirs)
        return Attempt(result, child.front)

    async def select(self, candidates: list[Candidate], parents: set[str], keep: int, request_id: int,
                     judges: list[Rewrite] | None = None, tie_id: int | None = None) -> list[Candidate]:
        if len(candidates) <= keep:
            return candidates
        judgements = await self.scores(candidates, request_id, judges=judges)
        if not judgements and parents:
            return ([c for c in candidates if c.name in parents]
                    + [c for c in candidates if c.name not in parents])[:keep]
        selection = self.config.selection

        def rank(candidate: Candidate) -> float:
            score = judgements.get(candidate.name, UNJUDGED)
            return (score - selection.match_weight * candidate.match
                    + (selection.parent_margin if candidate.name in parents else 0.0))

        ordered = sorted(candidates, key=rank)
        tied = [candidate for candidate in ordered if rank(candidate) - rank(ordered[0]) < TIE]
        if tie_id is not None and len(tied) > 1:
            log(self.tag, f"scores {request_id}: {', '.join(c.name for c in tied)} tie for the best")
            settled = await self.scores(tied, tie_id)
            ordered[:len(tied)] = sorted(tied, key=lambda candidate: settled.get(candidate.name, UNJUDGED))
        return ordered[:keep]

    @staticmethod
    def best(candidates: list[Candidate], judgements: dict[str, float]) -> Candidate:
        return min(enumerate(candidates), key=lambda item: (judgements.get(item[1].name, UNJUDGED), item[0]))[1]


    async def run(self) -> str:
        plan = self.plan
        closeup = object_closeup(self.image)
        if closeup is not None:
            self.closeup = [image_input(closeup)]
        if plan.measured:
            self.instructions = self.text("GENERATE_MEASURED")
            self.astra_cap = self.config.calls.measured_astra_max_output
            self.photo = photo.photo_appearance(self.image)
            self.brief = await self.photo_brief()
        calls = [self.module_call(DRAFT_ID + i, draft.model, draft.effort, [], draft.start)
                 for i, draft in enumerate(plan.drafts)]
        survivors = [c for c in await self.modules(calls, [f"d{i}" for i in range(len(calls))]) if c is not None]
        if not survivors:
            note = RENDER_TIMEOUT if self.render_timeouts else ""
            calls = [self.module_call(REDRAFT_ID + i, self.config.calls.repair_model, ReasoningEffort.HIGH, [],
                                      draft.start, note)
                     for i, draft in enumerate(plan.drafts[:REDRAFTS])]
            survivors = [c for c in await self.modules(calls, [f"e{i}" for i in range(len(calls))]) if c is not None]
        if not survivors:
            log(self.tag, "no draft rendered: placeholder")
            return placeholder_module(self.image, PLACEHOLDER_PATH)
        self.best_so_far = survivors[0].source
        if plan.draft_keep:
            drafts = survivors
            survivors = await self.drafts_kept(drafts)
            self.best_so_far = survivors[0].source
            if plan.draft_merge is not None:
                survivors = [await self.merged(drafts, survivors[0]), *survivors[1:]]
                self.best_so_far = survivors[0].source
        searched = False
        steps = [*plan.steps, *[plan.fill_step] * plan.fill_max] if plan.fill_step is not None else plan.steps
        for index, planned in enumerate(steps):
            step = self.stall_step(survivors, planned)
            if step is not planned:
                log(self.tag, f"step {index}: stalled, {min(len(c.rejected) for c in survivors)} rewrites discarded "
                              f"in a row; the stall step")
            if index >= len(plan.steps) and self.resolve(survivors, step) is None:
                log(self.tag, f"fill: stopped before step {index}; {self.budget.calls} calls, "
                              f"{self.budget.credits:.0f} credits")
                break
            survivors = await self.advance(survivors, step, index)
            self.best_so_far = survivors[0].source
            log(self.tag, f"step {index}: kept {', '.join(c.name for c in survivors)}; "
                          f"{self.budget.calls} calls, {self.budget.credits:.0f} credits")
            if plan.measured and len(survivors) == 1 and not searched:
                survivors[0], searched = await self.posed_by_search(survivors[0]), True
        winner = (await self.select(survivors, set(), 1, FINAL_SELECT_ID))[0]
        self.best_so_far = winner.source
        final = await self.posed(winner) if plan.pose and not plan.measured else winner
        if plan.outline_fit_last and final is winner:
            source = await self.fitted_last(winner)
            if plan.photo_tilt:
                source = await self.photo_tilted(source)
        else:
            source = final.source
        source = await self.stronger_metal(source)
        log(self.tag, f"done: {final.name}; {self.budget.calls} calls, {self.budget.credits:.0f} credits")
        return source

    def stall_step(self, survivors: list[Candidate], step: Step) -> Step:
        plan = self.plan
        if (plan.stall_step is None or not (step.rewrites or step.lineage)
                or any(len(candidate.rejected) < plan.stall_after for candidate in survivors)):
            return step
        return plan.stall_step

    def resolve(self, survivors: list[Candidate], step: Step) -> tuple[bool, int] | None:
        own = self.step_rewrites(survivors, step, 0)
        own_cost = sum(self.worst(call) for call in own)
        rungs = [0] + [rung for rung in range(1, len(self.config.calls.ladder) + 1)
                       if sum(self.worst(call) for call in self.step_rewrites(survivors, step, rung)) < own_cost]
        _, selection = self.score_calls([*survivors, *survivors[:1] * len(own)], 0)
        forms = [(True, 0)] if step.op == Op.CRITIC_REWRITE else []
        for with_critic, rung in [*forms, *[(False, rung) for rung in rungs]]:
            calls = [*self.step_rewrites(survivors, step, rung), *selection]
            if with_critic:
                calls += [Call(0, step.critic.model, self.text("CRITIC"),
                               [self.target, *self.closeup, *[image_input(view) for view in parent.views],
                                image_input(parent.sides), image_input(parent.sides),
                                InputText(text=self.text("CRITIC_INPUT", table="", findings="",
                                                         rejected=self.earlier(parent)[0])),
                                *self.earlier(parent)[1]],
                               step.critic.effort, self.config.calls.critic_max_output) for parent in survivors]
            if self.budget.allows([self.worst(call) for call in calls]):
                return with_critic, rung
        return None

    def rung_rewrites(self, step: Step, position: int, rung: int) -> list[Rewrite]:
        own = [step.lineage[position % len(step.lineage)]] if step.lineage else step.rewrites
        return own if rung == 0 else [self.config.calls.ladder[rung - 1]] * len(own)

    def step_rewrites(self, survivors: list[Candidate], step: Step, rung: int, plans: list[str] | None = None,
                      index: int = -1) -> list[Call]:
        calls = []
        for position, parent in enumerate(survivors):
            focus = step.focus[position % len(step.focus)] if step.focus else Focus.NONE
            for number, rewrite in enumerate(self.rung_rewrites(step, position, rung)):
                calls.append(self.rewrite_call(REWRITE_BASE * (index + 1) + 10 * position + number, rewrite.model,
                                               rewrite.effort, parent, focus, plans[position] if plans else "",
                                               step.edits))
        return calls

    async def drafts_kept(self, drafts: list[Candidate]) -> list[Candidate]:
        plan = self.plan
        each_start = plan.draft_each_start and len(drafts) > plan.draft_keep
        ordered = await self.select(drafts, set(), len(drafts) - 1 if each_start else plan.draft_keep, SELECT_ID,
                                    self.config.calls.draft_select,
                                    DRAFT_TIE_ID if self.config.calls.draft_tie_break else None)
        if not each_start:
            return ordered
        kept, starts = [], set()
        for draft in ordered:
            start = plan.drafts[int(draft.name[1:])].start
            if start not in starts:
                starts.add(start)
                kept.append(draft)
        kept += [draft for draft in ordered if draft not in kept]
        return kept[:plan.draft_keep]

    async def merged(self, drafts: list[Candidate], picked: Candidate) -> Candidate:
        merge, calls = self.plan.draft_merge, self.config.calls
        if merge is None or len(drafts) < 2:
            return picked
        labels = [chr(65 + number) for number in range(len(drafts))]
        by = dict(zip(labels, drafts, strict=True))
        content: list[InputText | InputImage] = [InputText(text="TARGET:"), self.target, *self.closeup]
        for label, draft in by.items():
            content += [InputText(text=f"CANDIDATE {label} FRONT VIEWS:"), image_input(draft.front),
                        InputText(text=f"CANDIDATE {label} GRID:"), image_input(draft.sides)]
        content.append(InputText(text=self.text("CRITIQUE_INPUT")))
        reply = (await self.run_stage([Call(CRITIQUE_ID, calls.select_model, self.text("CRITIQUE"), content,
                                            calls.select_effort, calls.select_max_output,
                                            text=critique_format(labels))]))[0]
        try:
            critique = json.loads(reply.text) if reply is not None else None
            notes = {entry["label"]: entry for entry in critique["candidates"] if entry["label"] in by}
            base = critique["base"] if critique["base"] in by else labels[drafts.index(picked)]
            others = sorted((label for label in labels if label != base),
                            key=lambda label: -len(notes[label]["strengths"]) if label in notes else 0)[:2]
            blocks = [self.text("MERGE_MODULE", role=role, label=label, source=by[label].raw,
                                strengths="; ".join(notes[label]["strengths"]) if label in notes else "",
                                weaknesses="; ".join(notes[label]["weaknesses"]) if label in notes else "")
                      for role, label in [("BASE", base), *[("OTHER", other) for other in others]]]
        except (ValueError, TypeError, KeyError):
            log(self.tag, "merge: no usable critique; the picked draft stays")
            return picked
        cap = calls.effort_max_output.get(merge.effort, calls.module_max_output)
        call = Call(MERGE_ID, merge.model, self.instructions,
                    [InputText(text=self.text("MERGE", modules="\n\n".join(blocks))), self.target, *self.closeup],
                    merge.effort, cap)
        child = (await self.modules([call], ["dm"]))[0]
        log(self.tag, f"merge: base {by[base].name}, with {', '.join(by[other].name for other in others)}; "
                      + ("merged" if child is not None else f"no module, {picked.name} stays"))
        return child if child is not None else picked

    async def advance(self, survivors: list[Candidate], step: Step, index: int) -> list[Candidate]:
        select_id = SELECT_ID + 10 * (index + 1)
        if not step.rewrites and not step.lineage:
            parents = [c.parent for c in survivors if c.parent is not None]
            return await self.select([*survivors, *parents], {p.name for p in parents}, step.keep or 1, select_id)
        form = self.resolve(survivors, step)
        if form is None:
            log(self.tag, f"step {index}: no form of it fits after {self.budget.calls} calls and "
                          f"{self.budget.credits:.0f} credits; skipped")
            return survivors
        with_critic, rung = form
        if rung or (step.op == Op.CRITIC_REWRITE and not with_critic):
            log(self.tag, f"step {index}: " + ("without its critic, " if step.op == Op.CRITIC_REWRITE
                                                 and not with_critic else "")
                          + (f"rewritten by {self.config.calls.ladder[rung - 1].model} "
                             f"{self.config.calls.ladder[rung - 1].effort.value}" if rung else "own rewrites")
                          + " to fit the budget")
        plans = await self.critic_plans(survivors, step, index) if with_critic else [""] * len(survivors)
        calls = self.step_rewrites(survivors, step, rung, plans, index)
        names, owners = [], []
        for position in range(len(survivors)):
            for number in range(len(self.rung_rewrites(step, position, rung))):
                names.append(f"r{index}.{position}.{number}")
                owners.append(position)
        children = await self.modules(calls, names, [survivors[owner].raw for owner in owners] if step.edits else None)
        for owner, child in zip(owners, children, strict=True):
            if child is not None:
                child.parent = survivors[owner]
        if step.each_lineage:
            if all(child is None for child in children):
                return survivors
            judgements = await self.scores([*[c for c in children if c is not None], *survivors], select_id)
            kept = list(survivors)
            margin = self.config.selection.parent_margin
            for owner, child in zip(owners, children, strict=True):
                if child is None:
                    continue
                mine, parent = judgements.get(child.name), judgements.get(survivors[owner].name)
                if mine is not None and parent is not None and mine <= parent + margin:
                    kept[owner] = child
                elif mine is not None and parent is not None:
                    survivors[owner].rejected.append(self.attempt(child, survivors[owner]))
            return kept
        if step.keep:
            valid = [child for child in children if child is not None]
            return await self.select([*valid, *survivors], {c.name for c in survivors}, step.keep, select_id)
        newest = list(survivors)
        for owner, child in zip(owners, children, strict=True):
            if child is not None:
                newest[owner] = child
        return newest

    async def posed(self, incumbent: Candidate) -> Candidate:
        try:
            sources = []
            yaw = await pose.best_yaw(self.image, incumbent.source, self.renderer)
            if yaw:
                sources.append(rotated(incumbent.source, yaw))
            if await pose.is_flat(incumbent.source, self.renderer):
                sources += [stood_up(incumbent.source, pitch) for pitch in POSE_PITCHES]
        except RenderRejected:
            return incumbent
        variants = []
        for index, source in enumerate(sources):
            outcome = await self.render_module(f"p{index}", source)
            if isinstance(outcome, Candidate):
                variants.append(outcome)
        if not variants:
            return incumbent
        judgements = await self.scores([incumbent, *variants], POSE_ID, "SELECT_POSE")
        best = self.best(variants, judgements)
        current, offered = judgements.get(incumbent.name), judgements.get(best.name)
        if current is not None and offered is not None and offered + self.config.selection.pose_margin <= current:
            log(self.tag, f"pose: {best.name} replaces {incumbent.name}")
            return best
        return incumbent

    async def posed_by_search(self, incumbent: Candidate) -> Candidate:
        if self.photo is None:
            return incumbent
        selection = self.config.selection
        turn, best, start = await photo.search_pose(incumbent.source, self.photo, self.renderer)
        if best - start < selection.pose_search_gain or not any(turn):
            return incumbent
        raw = turned_module(incumbent.raw, *turn)
        outcome = await self.render_module(incumbent.name + "t", normalize(raw, self.wrapper), raw)
        if not isinstance(outcome, Candidate):
            return incumbent
        judgements = await self.scores([incumbent, outcome], POSE_ID, "SELECT_POSE")
        current, offered = judgements.get(incumbent.name), judgements.get(outcome.name)
        if current is None or offered is None:
            return incumbent
        now = current - selection.match_weight * incumbent.match
        if offered - selection.match_weight * outcome.match + selection.parent_margin <= now:
            log(self.tag, f"pose search: turned {turn}")
            return outcome
        return incumbent

    async def photo_brief(self) -> str:
        calls = self.config.calls
        call = Call(BRIEF_ID, calls.select_model, self.text("BRIEF"), [self.target, *self.closeup],
                    calls.select_effort, calls.brief_max_output, text=brief_format())
        reply = (await self.run_stage([call]))[0]
        try:
            brief = json.loads(reply.text) if reply is not None else None
            return self.text("BRIEF_BLOCK", main_object=brief["main_object"], items="; ".join(brief["items"]),
                             features="; ".join(brief["features"]), pose=brief["pose"],
                             true_colors="; ".join(brief["true_colors"]))
        except (TypeError, KeyError, ValueError):
            return ""


    async def fitted_last(self, winner: Candidate) -> str:
        if not winner.raw:
            return winner.source
        source = normalize(winner.raw, chain_wrapper(self.plan.fill, self.plan.glow, True, self.plan.flat_tilt))
        try:
            front = await self.renderer.render(source, FRONT_JUDGE)
        except RenderRejected:
            return winner.source
        if views_match(zoomed_views(front), winner.views, self.config.finish.fit_view_limit):
            return source
        log(self.tag, "outline fit dropped: the views changed")
        return winner.source

    async def photo_tilted(self, source: str) -> str:
        try:
            angle = await tilt.best_tilt(self.image, source, lambda s: self.renderer.render(s, LEVEL_VIEW))
            if angle is None:
                return source
            turned = tilted(source, angle)
            await self.renderer.render(turned, FRONT_JUDGE)
        except RenderRejected:
            return source
        log(self.tag, f"tilt: {angle} degrees")
        return turned

    async def stronger_metal(self, source: str) -> str:
        finish = self.config.finish
        metal = with_metal_pass(source, finish.metalness, finish.roughness)
        if metal is None:
            return source
        try:
            await self.renderer.render(metal, FRONT_JUDGE)
        except RenderRejected:
            return source
        log(self.tag, "metal pass applied")
        return metal


async def generate_prompt(image: bytes, seed: int, witness: AsyncWitnessClient) -> str | None:
    rates = rate_table(PRICING_PATH)
    config = load_config(CONFIG_PATH, PROMPTS_DIR, rates)
    tag = hashlib.sha256(image).hexdigest()[:10]
    pipeline = Pipeline(config, rates, image, seed, Llm(witness), PatientRenderer(witness, tag))
    try:
        return await pipeline.run()
    except (*INFRASTRUCTURE_FAILURES, CallFailed):
        raise
    except Exception as error:
        log(pipeline.tag, f"failed: {error!r}; returning the best module so far")
        return pipeline.best_so_far or placeholder_module(image, PLACEHOLDER_PATH)
