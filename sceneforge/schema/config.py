from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from sceneforge.schema.prompt_book import PromptBook
from sceneforge.schema.responses_api import ReasoningEffort

PROMPTS = {
    "GENERATE": set(),
    "GENERATE_MEASURED": set(),
    "TARGET": set(),
    "TARGET_PROPORTIONS": set(),
    "REWRITE_VIEWS": set(),
    "REWRITE": {"focus", "rejected", "source"},
    "REWRITE_MEASURED": {"focus", "rejected", "brief", "measurements", "source"},
    "FOCUS_SHAPE": set(),
    "FOCUS_DETAILS": set(),
    "FOCUS_MATERIALS": set(),
    "REPAIR": {"errors", "source"},
    "SELECT_CLOSE": set(),
    "SELECT_POSE": set(),
    "BRIEF": set(),
    "BRIEF_BLOCK": {"main_object", "items", "features", "pose", "true_colors"},
    "CRITIC": set(),
    "CRITIC_INPUT": {"table", "findings", "rejected"},
    "REWRITE_PLAN": {"focus", "rejected", "plan", "source"},
    "REJECTED": {"attempts"},
    "REJECTED_ATTEMPT": {"number", "result"},
    "GENERATE_EDITS": set(),
    "REWRITE_EDITS": {"focus", "rejected", "source"},
    "CRITIQUE": set(),
    "CRITIQUE_INPUT": set(),
    "MERGE": {"modules"},
    "MERGE_MODULE": {"role", "label", "strengths", "weaknesses", "source"},
}


class Start(str, Enum):
    PLAIN = "plain"
    PROPORTIONS = "proportions"


class Focus(str, Enum):
    NONE = ""
    SHAPE = "shape"
    DETAILS = "details"
    MATERIALS = "materials"


class Draft(BaseModel):
    start: Start
    model: str
    effort: ReasoningEffort


class Rewrite(BaseModel):
    model: str
    effort: ReasoningEffort


class Op(str, Enum):
    REWRITE = "rewrite"
    CRITIC_REWRITE = "critic_rewrite"


class Step(BaseModel):
    op: Op = Op.REWRITE
    critic: Rewrite | None = Field(default=None)
    rewrites: list[Rewrite] = Field(default=[])
    lineage: list[Rewrite] = Field(default=[])
    keep: int = Field(default=0, ge=0)
    focus: list[Focus] = Field(default=[])
    each_lineage: bool = Field(default=False)
    edits: bool = Field(default=False)


class Plan(BaseModel):
    drafts: list[Draft] = Field(min_length=1, max_length=49)
    steps: list[Step] = Field(max_length=39)
    draft_keep: int = Field(default=0, ge=0)
    draft_each_start: bool = Field(default=False)
    draft_merge: Rewrite | None = Field(default=None)
    pose: bool = Field(default=True)
    fill: float = Field(default=0.99, gt=0.0, le=1.0)
    glow: float = Field(default=0.0, ge=0.0, lt=0.6)
    measured: bool = Field(default=False)
    outline_fit: bool = Field(default=False)
    outline_fit_last: bool = Field(default=False)
    photo_tilt: bool = Field(default=False)
    flat_tilt: bool = Field(default=True)
    fill_step: Step | None = Field(default=None)
    fill_max: int = Field(default=0, ge=0)
    rejected_shown: int = Field(default=0, ge=0)
    stall_after: int = Field(default=0, ge=0)
    stall_step: Step | None = Field(default=None)


class LintConfig(BaseModel):
    dark_shift: int | None = Field(default=45)
    dark_min_permille: int = Field(default=10, ge=0)
    light_shift: int | None = Field(default=45)
    invisible: bool = Field(default=True)
    tiny_permille: int | None = Field(default=4)
    hairline_px: int | None = Field(default=1)
    black_grid_luma: int | None = Field(default=35)
    near_white_luma: int | None = Field(default=238)
    zfight_share: float | None = Field(default=0.3)
    same_color_l1: int | None = Field(default=30)


class CallsConfig(BaseModel):
    select_model: str
    select_effort: ReasoningEffort
    select_max_output: int = Field(gt=0)
    draft_select: list[Rewrite] = Field(default=[])
    draft_tie_break: bool = Field(default=False)
    brief_max_output: int = Field(gt=0)
    module_max_output: int = Field(gt=0)
    effort_max_output: dict[ReasoningEffort, int] = Field()
    astra_model: str = Field()
    astra_max_output: int = Field(gt=0)
    measured_astra_max_output: int = Field(gt=0)
    repair_model: str = Field()
    critic_max_output: int = Field(default=8192, gt=0)
    ladder: list[Rewrite] = Field(default=[])


class SelectionConfig(BaseModel):
    parent_margin: float = Field(ge=0.0)
    grid_weight: float = Field(ge=0.0)
    garbage_penalty: float = Field(ge=0.0)
    pose_margin: float = Field(ge=0.0)
    match_weight: float = Field(ge=0.0)
    pose_search_gain: float = Field(ge=0.0)


class FinishConfig(BaseModel):
    fit_view_limit: float = Field(gt=0.0)
    metalness: float = Field(ge=0.0, le=1.0)
    roughness: float = Field(ge=0.0, le=1.0)


class Reservation(BaseModel):
    model: str
    max_output_tokens: int = Field(gt=0)
    reserve: int = Field(gt=0)


class BudgetConfig(BaseModel):
    safety: float = Field(ge=0.0, lt=1.0)
    chars_per_token: float = Field(gt=0.0)
    tokens_per_image: int = Field(gt=0)
    reserve_output: list[Reservation] = []


class MinerConfig(BaseModel):
    prompts: PromptBook = Field()
    plan: Plan
    calls: CallsConfig
    lints: LintConfig = LintConfig()
    selection: SelectionConfig
    finish: FinishConfig
    budget: BudgetConfig

    @classmethod
    def load(cls, config_json: str, files: Mapping[str, str], models: set[str]) -> MinerConfig:
        return cls.model_validate_json(config_json, context={"files": files, "models": models})

    @field_validator("prompts", mode="before")
    @classmethod
    def _load_book(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        files = (info.context or {}).get("files")
        if files is None:
            raise ValueError("missing `files` validation context")
        return PromptBook.load(value, files)

    @model_validator(mode="after")
    def _check(self, info: ValidationInfo) -> MinerConfig:
        models = (info.context or {}).get("models")
        if models is None:
            raise ValueError("missing `models` validation context")
        used = [draft.model for draft in self.plan.drafts]
        spare = [step for step in (self.plan.fill_step, self.plan.stall_step) if step is not None]
        used += [rewrite.model for step in (*self.plan.steps, *spare)
                 for rewrite in (*step.rewrites, *step.lineage, *([step.critic] if step.critic else []))]
        used += [self.calls.select_model, self.calls.repair_model,
                 *[rewrite.model for rewrite in (*self.calls.ladder, *self.calls.draft_select)],
                 *([self.plan.draft_merge.model] if self.plan.draft_merge else [])]
        unknown = sorted({model for model in used if model not in models})
        if unknown:
            raise ValueError(f"the rate table has no rate for {unknown}")
        for name, filled in PROMPTS.items():
            if name not in self.prompts:
                raise ValueError(f"prompt book {self.prompts.path} lacks {name}")
            if self.prompts.placeholders(name) != filled:
                raise ValueError(f"prompt {name} has placeholders {sorted(self.prompts.placeholders(name))}, "
                                 f"expected {sorted(filled)}")
        steps = [*self.plan.steps, *([self.plan.fill_step] * self.plan.fill_max if self.plan.fill_step else [])]
        if len(self.calls.draft_select) > 4:
            raise ValueError("the draft selection takes at most 4 models")
        if len(steps) > 39:
            raise ValueError("at most 39 steps and fill steps")
        if self.plan.draft_merge is not None and not self.plan.draft_keep:
            raise ValueError("draft_merge needs draft_keep")
        if bool(self.plan.stall_after) != (self.plan.stall_step is not None):
            raise ValueError("stall_after and stall_step go together")
        stall = self.plan.stall_step
        if stall is not None and not ((stall.rewrites or stall.lineage) and stall.each_lineage):
            raise ValueError("the stall step needs rewrites and each_lineage")
        survivors = self.plan.draft_keep or len(self.plan.drafts)
        for number, step in enumerate(steps):
            for name, form in ((f"step {number}", step), *([("the stall step", stall)] if stall else [])):
                if form.rewrites and form.lineage:
                    raise ValueError(f"{name} has both rewrites and a lineage")
                if (form.op == Op.CRITIC_REWRITE) != (form.critic is not None):
                    raise ValueError(f"{name}: a critic_rewrite step needs a critic, and only it takes one")
                if form.edits and (form.op == Op.CRITIC_REWRITE or self.plan.measured):
                    raise ValueError(f"{name}: edits go with neither a critic nor a measured plan")
                if form.critic is not None and len(form.rewrites) > 9:
                    raise ValueError(f"{name}: a critic step takes at most 9 rewrites")
                if survivors > 10 or len(form.rewrites) > 10:
                    raise ValueError(f"{name} has more than 10 survivors or rewrites per survivor")
            survivors = step.keep or survivors
        return self
