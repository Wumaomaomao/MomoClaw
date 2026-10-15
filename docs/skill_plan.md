# MomoClaw Skill Execution Plan

This document plans how to add skill execution capability to MomoClaw without
changing the generic agent loop.

## Goal

A skill is an application-level capability package made of task instructions,
optional metadata, optional activation rules, and eventually optional tools.

MomoClaw should support skills as a coding-agent feature:

- Load skill instructions from the workspace.
- Merge selected skill instructions into the system prompt.
- Keep permission control in the existing read-only/read-write model.
- Allow future extension to built-in skills, automatic activation, and
  skill-owned tools.

The first implementation should be conservative: prompt-only skills with
progressive loading. Session startup loads only lightweight skill metadata.
Full `SKILL.md` instructions are loaded by an explicit read-only `skill` tool,
or by manual preloading when the user passes `--skill`.

## Non-Goals

The first version should not:

- Change `momoclaw.agent` core loop semantics.
- Automatically grant write, edit, or bash permissions.
- Execute arbitrary skill Python code.
- Auto-activate skills based on model judgment.
- Add a new tool execution path outside `AgentTool`.

## Proposed Structure

Workspace skills live under `.momoclaw/skills/`:

```text
.momoclaw/
  skills/
    code_review/
      SKILL.md
      skill.json
    pytest_fix/
      SKILL.md
      skill.json
```

`SKILL.md` contains the task workflow and instructions. `skill.json` contains
metadata:

```json
{
  "name": "code_review",
  "description": "Review code changes and report bugs, risks, and missing tests.",
  "activation": {
    "manual_names": ["code_review", "review"],
    "keywords": ["review", "code review", "代码审查"]
  },
  "permissions": {
    "read": true,
    "write": false,
    "edit": false,
    "bash": false
  }
}
```

`skill.json` should be optional in the first version. If it is missing,
MomoClaw can infer the skill name from the directory name and load `SKILL.md`.

## Data Model

Add a new module:

```text
momoclaw/coding_agent/skills.py
```

Suggested dataclasses:

```python
@dataclass
class SkillPermissions:
    read: bool = True
    write: bool = False
    edit: bool = False
    bash: bool = False


@dataclass
class Skill:
    name: str
    description: str = ""
    instructions: str = ""
    permissions: SkillPermissions = field(default_factory=SkillPermissions)
    path: Path | None = None
```

Suggested loader API:

```python
class SkillLoader:
    def __init__(self, workspace_dir: str | Path) -> None: ...

    def load(self, name: str) -> Skill: ...

    def list(self) -> list[Skill]: ...
```

The loader should:

- Resolve all skill paths inside `workspace/.momoclaw/skills`.
- Require `SKILL.md` for a loadable skill.
- Parse `skill.json` if present.
- Return clear errors for missing or malformed skills.

## Session Factory Integration

Extend `CreateAgentSessionOptions`:

```python
skills: list[Skill] = field(default_factory=list)
skill_names: list[str] = field(default_factory=list)
```

The factory should:

1. Load `skill_names` through `SkillLoader`.
2. Combine loaded skills with explicitly provided `skills`.
3. Pass skill instructions into `build_system_prompt`.
4. Keep built-in tool enabling controlled by `read_only_mode` and
   `enabled_builtin_tools`.

Skill permissions should never elevate user permissions. A skill can only
restrict available tools or describe expected usage.

Recommended first-version rule:

- If `read_only_mode=True`, only `read` may be enabled.
- If `read_only_mode=False`, built-in tools may be enabled as today.
- Skill permissions do not automatically add tools.
- A later version may use skill permissions to narrow enabled tools.

## System Prompt Integration

Extend `SystemPromptOptions`:

```python
skills: list[Skill] = field(default_factory=list)
```

Append skill sections after the base MomoClaw instructions and before custom
project prompt text:

```text
Active skills:

## code_review
Review code changes and report bugs, risks, and missing tests.

<SKILL.md content>
```

Keep ordering stable and match CLI order.

## CLI Integration

Add manual skill selection:

```bash
uv run momoclaw --skill code_review
uv run momoclaw --skill code_review --skill pytest_fix
```

CLI changes:

- Add `--skill`, `action="append"`, default `[]`.
- Pass selected skill names to `CreateAgentSessionOptions`.
- Add a `/skills` command to show loaded and available skills.

First version can load skills only at session creation. Dynamic skill switching
can be added later.

## Permission Rules

Skills must not bypass MomoClaw safety controls.

Rules:

- Skill instructions are prompt content, not executable authority.
- Skill metadata cannot enable write/edit/bash in read-only mode.
- Skill-owned tools are not part of the first version.
- If skill-owned tools are added later, they must be adapted to `AgentTool` and
  pass through the same validation, logging, and approval path as all tools.
- Any future automatic activation must not change tool permissions.

## Future Skill-Owned Tools

A later version may support:

```text
.momoclaw/skills/example/
  SKILL.md
  skill.json
  tools.py
```

`tools.py` could expose:

```python
def get_tools(workspace_dir: Path) -> list[AgentTool]:
    ...
```

This should be treated as high risk because loading a Python file executes
local code. Requirements before adding it:

- Explicit user opt-in.
- Tool namespacing, for example `skill.example.my_tool`.
- Permission policy per tool.
- Tool execution audit events.
- Clear failure isolation.

## Future Automatic Activation

Automatic activation can be added after manual skills are stable.

Possible activation sources:

- `skill.json.activation.keywords`
- explicit user syntax such as `/skill code_review`
- future classifier or model-assisted router

Automatic activation should only add instructions. It must not enable mutating
tools or shell access.

## Test Plan

Add tests for:

- Loading a skill from `.momoclaw/skills/<name>/SKILL.md`.
- Loading metadata from `skill.json`.
- Clear error when a requested skill does not exist.
- Clear error when `SKILL.md` is missing.
- Skill instructions appear in the generated system prompt.
- Multiple skills preserve CLI order.
- Default read-only mode remains `read` only when skills are active.
- `--read-write` behavior remains unchanged unless future permission narrowing
  is implemented.
- `/skills` shows loaded and available skill names.

## Implementation Phases

### Phase 1: Prompt-Only Manual Skills

- Add `momoclaw/coding_agent/skills.py`.
- Add `Skill`, `SkillPermissions`, and `SkillLoader`.
- Add `skills` or `skill_names` to session options.
- Merge skill instructions into system prompt.
- Add CLI `--skill`.
- Add focused tests.

### Phase 2: Skill Listing and UX

- Add `/skills`.
- Show active skills in session startup or command output.
- Support missing-skill diagnostics with available names.

### Phase 3: Built-In Skills

- Add package-provided skills.
- Load order:
  1. workspace `.momoclaw/skills`
  2. package built-ins
- Workspace skills override built-ins with the same name.

### Phase 4: Automatic Activation

- Add keyword-based activation.
- Keep activation prompt-only.
- Add tests proving auto-activation does not alter permissions.

### Phase 5: Skill-Owned Tools

- Add explicit opt-in loader for skill tool modules.
- Adapt loaded tools to `AgentTool`.
- Add namespace, permission, approval, and audit controls.

## Implemented First Scope

The first scope includes Phase 1 with progressive loading:

- Prompt-only skills.
- Manual skill selection through `--skill`.
- Description-driven selection through the read-only `skill` tool.
- Optional compatibility keyword preloading through
  `skill.json.activation.keywords`.
- Lightweight available-skill summaries in the base system prompt.
- Full `SKILL.md` instructions loaded only when the model calls the `skill`
  tool, or when the user explicitly preloads a skill.
- No skill-owned tools.
- No permission elevation from skill metadata.
- No changes to `momoclaw.agent`.

This keeps the feature aligned with the existing architecture: `momoclaw.agent`
remains a generic model/tool execution loop, while `momoclaw.coding_agent`
continues to own workspace-specific behavior, tools, prompts, and permissions.
