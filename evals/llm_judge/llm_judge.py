#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_PROVIDER_MODELS = {
    "anthropic": "claude-3-opus-20240229",
    "openai": "gpt-4o-mini",
}

MAX_DIFF_CHARS = 16000
MAX_FILE_SNIPPET = 1200


def load_prompt_text(prompt_path: Path) -> str:
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")

    alt = prompt_path.parent / "project" / prompt_path.name
    if alt.exists():
        return alt.read_text(encoding="utf-8")

    raise FileNotFoundError(f"Prompt file not found at {prompt_path} or {alt}")


def load_trace(trace_path: Path) -> list[Any]:
    if not trace_path.exists():
        return []

    events: list[Any] = []
    for line in trace_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _skills_from_mapping(data: dict[str, Any], obj_type: str) -> set[str]:
    lowered = {k.lower(): v for k, v in data.items()}
    skills: set[str] = set()

    for key in ("skills", "skills_used"):
        value = lowered.get(key, [])
        if isinstance(value, list):
            skills.update(item for item in value if isinstance(item, str))

    for key in ("skill", "skill_name", "skillname", "invoked_skill"):
        value = lowered.get(key)
        if isinstance(value, str):
            skills.add(value)

    name = lowered.get("name")
    if isinstance(name, str) and any(tok in obj_type.lower() for tok in ("skill", "tool", "function")):
        skills.add(name)

    return skills


def _collect_skill_tokens(obj: Any, parent_type: str | None = None) -> set[str]:
    if isinstance(obj, list):
        return {skill for item in obj for skill in _collect_skill_tokens(item, parent_type)}

    if not isinstance(obj, dict):
        return set()

    obj_type = str(obj.get("type", parent_type) or "")
    skills = _skills_from_mapping(obj, obj_type)
    for value in obj.values():
        skills.update(_collect_skill_tokens(value, obj_type))
    return skills


def extract_skills(events: list[Any]) -> list[str]:
    skills: set[str] = set()
    for event in events:
        skills.update(_collect_skill_tokens(event))
    return sorted(skills)


def _run_git_command(project_dir: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_dir), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip()


def describe_project_changes(project_dir: Path) -> str:
    if (project_dir / ".git").exists():
        status = _run_git_command(project_dir, ["status", "--short"])
        diff_stat = _run_git_command(project_dir, ["diff", "--stat"])
        diff = _run_git_command(project_dir, ["diff"])

        sections = [
            "## Git status",
            status if status else "(clean working tree)",
            "",
            "## Git diff --stat",
            diff_stat if diff_stat else "(no diff)",
            "",
            "## Git diff",
            (diff[:MAX_DIFF_CHARS] + "\n\n[diff truncated]" if len(diff) > MAX_DIFF_CHARS else diff)
            if diff
            else "(no diff)",
        ]

        untracked = [
            line.split(maxsplit=1)[1]
            for line in status.splitlines()
            if line.startswith("?? ") and len(line.split(maxsplit=1)) == 2
        ]
        if untracked:
            sections += ["", "## Untracked file snippets"]
            for rel in untracked:
                path = project_dir / rel
                if not path.is_file():
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                snippet = content[:MAX_FILE_SNIPPET]
                suffix = "\n\n[truncated]" if len(content) > MAX_FILE_SNIPPET else ""
                sections.append(f"### {rel}\n{snippet}{suffix}")

        return "\n".join(sections)

    snapshots = []
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(project_dir)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        snippet = content[:MAX_FILE_SNIPPET]
        suffix = "\n\n[truncated]" if len(content) > MAX_FILE_SNIPPET else ""
        snapshots.append(f"### {rel}\n{snippet}{suffix}")

    if not snapshots:
        return "No project files found to review."

    return "## Project file snapshots\n" + "\n\n".join(snapshots)


def build_review_prompt(
    prompt_text: str,
    skills: list[str],
    change_context: str,
) -> str:
    skill_text = ", ".join(skills) if skills else "No skills recorded."
    return (
        "You are an expert code reviewer judging an autonomous coding agent's output.\n"
        "Use the provided task prompt, agent skills trace, and project changes to produce a concise review.\n\n"
        "Task prompt:\n"
        f"{prompt_text}\n\n"
        "Agent skills observed:\n"
        f"{skill_text}\n\n"
        "Project changes:\n"
        f"{change_context}\n\n"
        "Review format (markdown):\n"
        "- Overview: Short description of the delivered solution and whether it meets the prompt.\n"
        "- Skills Observed: Summarize how the listed skills appeared in the work; add any obvious missing skills.\n"
        "- Code Review: Bullet issues grouped by severity (High/Med/Low). Focus on correctness, completeness, and clarity. Mention files.\n"
        "- Testing Gaps: Note missing or insufficient tests relevant to the task.\n"
        "- Verdict: Pass/Fail with one-line justification."
    )


def call_openai(model: str, prompt: str, max_tokens: int) -> str:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError("openai package is required for OpenAI provider") from exc

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": "Provide concise, actionable code reviews."},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""


def call_anthropic(model: str, prompt: str, max_tokens: int) -> str:
    try:
        import anthropic
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError("anthropic package is required for Anthropic provider") from exc

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in message.content:
        if hasattr(block, "text"):
            parts.append(str(block.text))
        elif isinstance(block, dict) and "text" in block:
            parts.append(str(block["text"]))
    return "\n".join(parts)


def pick_provider(provider: str | None) -> str:
    if provider:
        return provider
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    raise RuntimeError(
        "Set LLM_JUDGE_PROVIDER to 'anthropic' or 'openai', or provide ANTHROPIC_API_KEY / OPENAI_API_KEY."
    )


def write_metadata(
    path: Path,
    *,
    provider: str,
    model: str,
    prompt_file: Path,
    trace_file: Path,
    project_dir: Path,
    skills: list[str],
) -> None:
    metadata = {
        "provider": provider,
        "model": model,
        "prompt_file": str(prompt_file),
        "trace_file": str(trace_file),
        "project_dir": str(project_dir),
        "skills": skills,
    }
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LLM-as-a-judge evaluation shard.")
    parser.add_argument("--project", default=os.getenv("JUDGE_PROJECT_DIR", "project"))
    parser.add_argument("--prompt", default=os.getenv("JUDGE_PROMPT_FILE", "prompt.md"))
    parser.add_argument("--output-json", default=os.getenv("JUDGE_OUTPUT_JSON", "output.json"))
    parser.add_argument("--review", default=os.getenv("JUDGE_REVIEW_PATH", "llm_judge_review.md"))
    parser.add_argument(
        "--prompt-dump", default=os.getenv("JUDGE_PROMPT_DUMP", "llm_judge_prompt.txt")
    )
    parser.add_argument("--provider", default=os.getenv("LLM_JUDGE_PROVIDER"))
    parser.add_argument("--model", default=os.getenv("LLM_JUDGE_MODEL"))
    parser.add_argument("--max-tokens", type=int, default=int(os.getenv("LLM_JUDGE_MAX_TOKENS", "1200")))
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("LLM_JUDGE_DRY_RUN") == "1",
    )
    args = parser.parse_args(argv)

    project_dir = Path(args.project)
    prompt_file = Path(args.prompt)
    trace_file = Path(args.output_json)
    review_file = Path(args.review)
    prompt_dump = Path(args.prompt_dump)

    prompt_text = load_prompt_text(prompt_file)
    events = load_trace(trace_file)
    skills = extract_skills(events)
    change_context = describe_project_changes(project_dir)
    review_prompt = build_review_prompt(prompt_text, skills, change_context)

    prompt_dump.write_text(review_prompt, encoding="utf-8")

    if args.dry_run:
        print("Dry run enabled; review prompt written to", prompt_dump)
        return 0

    provider = pick_provider(args.provider)
    # Normalize provider and validate against supported providers to avoid KeyError
    provider = provider.lower()
    if provider not in DEFAULT_PROVIDER_MODELS:
        allowed_providers = ", ".join(sorted(DEFAULT_PROVIDER_MODELS.keys()))
        raise ValueError(
            f"Unsupported provider {provider!r}. Allowed providers: {allowed_providers}"
        )
    model = args.model or DEFAULT_PROVIDER_MODELS[provider]

    write_metadata(
        Path("llm_judge_meta.json"),
        provider=provider,
        model=model,
        prompt_file=prompt_file,
        trace_file=trace_file,
        project_dir=project_dir,
        skills=skills,
    )

    if provider == "anthropic":
        review = call_anthropic(model, review_prompt, args.max_tokens)
    elif provider == "openai":
        review = call_openai(model, review_prompt, args.max_tokens)
    else:
        raise RuntimeError(f"Unsupported provider {provider}")

    review_file.write_text(review, encoding="utf-8")
    print(f"Review written to {review_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
