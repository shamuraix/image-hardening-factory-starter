#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

REPO_TMPFS_MOUNT = "--mount=type=tmpfs,target=/etc/yum.repos.d"
REPO_CONFIG_MOUNT = (
    "--mount=type=secret,id=factory-repo,target=/etc/yum.repos.d/factory.repo,required=true"
)
BASE_CONTEXT_NAME = "factory-base"
RESERVED_BUILD_ARGS = frozenset({"BASE_REF", "BASE_MAJOR", "SOURCE_DATE_EPOCH", "BUILDKIT_SYNTAX"})


class DockerfileAdaptationError(ValueError):
    pass


def _logical_text(lines: list[str]) -> str:
    parts: list[str] = []
    for line in lines:
        text = line.rstrip("\r\n")
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        stripped = text.rstrip()
        if stripped.endswith("\\"):
            parts.append(stripped[:-1])
        else:
            parts.append(text)
    return " ".join(parts)


def _continues(line: str) -> bool:
    return line.rstrip("\r\n").rstrip().endswith("\\")


def _instruction_keyword(line: str) -> str | None:
    match = re.match(r"\s*([A-Za-z]+)(?:\s|$)", line)
    if not match:
        return None
    return match.group(1).upper()


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _heredoc_delimiters(text: str) -> list[tuple[str, bool]]:
    if "<<" not in text:
        return []
    delimiters: list[tuple[str, bool]] = []
    for match in re.finditer(r"<<(-?)([A-Za-z_][A-Za-z0-9_.-]*|'[^']+'|\"[^\"]+\")", text):
        delimiters.append((_strip_quotes(match.group(2)), match.group(1) == "-"))
    if not delimiters:
        raise DockerfileAdaptationError("unsupported ambiguous Dockerfile heredoc syntax")
    return delimiters


def _read_instruction(lines: list[str], index: int) -> tuple[list[str], int, str | None]:
    first = lines[index]
    if not first.strip() or first.lstrip().startswith("#"):
        return [first], index + 1, None

    keyword = _instruction_keyword(first)
    if keyword is None:
        raise DockerfileAdaptationError(f"unsupported Dockerfile instruction: {first.strip()}")

    collected = [first]
    index += 1
    continued = _continues(first)
    while continued:
        if index >= len(lines):
            raise DockerfileAdaptationError("unterminated Dockerfile line continuation")
        next_line = lines[index]
        collected.append(next_line)
        index += 1
        if not next_line.strip() or next_line.lstrip().startswith("#"):
            continue
        continued = _continues(next_line)

    if keyword in {"RUN", "ADD", "COPY"}:
        for delimiter, allow_tabs in _heredoc_delimiters(_logical_text(collected)):
            while True:
                if index >= len(lines):
                    raise DockerfileAdaptationError(
                        f"unterminated Dockerfile heredoc {delimiter!r}"
                    )
                body_line = lines[index]
                collected.append(body_line)
                index += 1
                candidate = body_line.rstrip("\r\n")
                if allow_tabs:
                    candidate = candidate.lstrip("\t")
                if candidate == delimiter:
                    break

    return collected, index, keyword


def _tokens(text: str, instruction: str) -> list[str]:
    try:
        return shlex.split(text, comments=False, posix=True)
    except ValueError as exc:
        raise DockerfileAdaptationError(f"unsupported {instruction} syntax: {exc}") from exc


def _validate_from(text: str, stage_aliases: set[str]) -> str | None:
    tokens = _tokens(text, "FROM")
    source: str | None = None
    alias: str | None = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            index += 2 if token in {"--platform"} else 1
            continue
        source = token
        index += 1
        break
    if source is None:
        raise DockerfileAdaptationError("FROM is missing a base image")
    if (
        source not in {"${BASE_REF}", "$BASE_REF", BASE_CONTEXT_NAME}
        and source not in stage_aliases
    ):
        raise DockerfileAdaptationError(
            "Dockerfile FROM must use ${BASE_REF} or an earlier local stage"
        )
    while index < len(tokens):
        if tokens[index].upper() == "AS" and index + 1 < len(tokens):
            alias = tokens[index + 1]
            break
        index += 1
    return alias


def _validate_copy(text: str, stage_aliases: set[str]) -> None:
    tokens = _tokens(text, "COPY")
    index = 1
    while index < len(tokens):
        token = tokens[index]
        from_value: str | None = None
        if token == "--from":
            if index + 1 >= len(tokens):
                raise DockerfileAdaptationError("COPY --from is missing a value")
            from_value = tokens[index + 1]
            index += 2
        elif token.startswith("--from="):
            from_value = token.split("=", 1)[1]
            index += 1
        else:
            index += 1
        if from_value is not None and not (
            from_value.isdigit() or from_value in stage_aliases or from_value == BASE_CONTEXT_NAME
        ):
            raise DockerfileAdaptationError("COPY --from may only reference local stages")


def _validate_add(text: str) -> None:
    tokens = _tokens(text, "ADD")
    body = text.split(None, 1)[1].strip() if len(text.split(None, 1)) == 2 else ""
    while body.startswith("--"):
        option, _, remainder = body.partition(" ")
        body = remainder.strip()
        if not option or not body:
            break
    sources: list[str]
    if body.startswith("["):
        try:
            add_args = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DockerfileAdaptationError(f"unsupported ADD JSON syntax: {exc}") from exc
        if not isinstance(add_args, list) or not all(isinstance(item, str) for item in add_args):
            raise DockerfileAdaptationError("unsupported ADD JSON syntax")
        sources = add_args[:-1]
    else:
        non_options = [token for token in tokens[1:] if not token.startswith("--")]
        sources = non_options[:-1] if len(non_options) >= 2 else []
    for source in sources:
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", source) or source.startswith("git@"):
            raise DockerfileAdaptationError("remote ADD sources are not allowed")


def _inject_run_mounts(lines: list[str]) -> list[str]:
    logical = _logical_text(lines)
    if "id=factory-repo" in logical or "/etc/yum.repos.d/factory.repo" in logical:
        raise DockerfileAdaptationError("Dockerfile RUN already uses the reserved repo secret")
    if "target=/etc/yum.repos.d" in logical:
        raise DockerfileAdaptationError("Dockerfile RUN already mounts /etc/yum.repos.d")

    first = lines[0]
    match = re.match(r"(\s*RUN)(\s*)(.*)", first, flags=re.IGNORECASE)
    if not match:
        raise DockerfileAdaptationError("internal parser error while adapting RUN")
    newline = ""
    if first.endswith("\r\n"):
        newline = "\r\n"
    elif first.endswith("\n"):
        newline = "\n"
    rest = match.group(3).rstrip("\r\n")
    adapted_first = f"{match.group(1)} {REPO_TMPFS_MOUNT} {REPO_CONFIG_MOUNT}"
    if rest:
        adapted_first = f"{adapted_first} {rest}"
    adapted_first = f"{adapted_first}{newline}"
    return [adapted_first, *lines[1:]]


def adapt_dockerfile_text(text: str) -> str:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    stage_aliases: set[str] = set()
    saw_base_from = False

    while index < len(lines):
        instruction_lines, index, keyword = _read_instruction(lines, index)
        if keyword is None:
            first = instruction_lines[0].lstrip()
            if re.match(r"#\s*syntax\s*=", first, flags=re.IGNORECASE):
                continue
            if re.match(r"#\s*escape\s*=", first, flags=re.IGNORECASE):
                raise DockerfileAdaptationError("unsupported Dockerfile escape directive")
            output.extend(instruction_lines)
            continue

        logical = _logical_text(instruction_lines)
        if keyword == "FROM":
            alias = _validate_from(logical, stage_aliases)
            if alias:
                stage_aliases.add(alias)
            if "${BASE_REF}" in logical or "$BASE_REF" in logical or BASE_CONTEXT_NAME in logical:
                saw_base_from = True
        elif keyword == "COPY":
            _validate_copy(logical, stage_aliases)
        elif keyword == "ADD":
            _validate_add(logical)
        elif keyword == "RUN":
            instruction_lines = _inject_run_mounts(instruction_lines)
        output.extend(instruction_lines)

    if not saw_base_from:
        raise DockerfileAdaptationError("Dockerfile must consume the pipeline-supplied BASE_REF")
    return "".join(output)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "adapt-dockerfile":
        print("usage: python3 -m factory.buildkit adapt-dockerfile INPUT", file=sys.stderr)
        return 2
    try:
        sys.stdout.write(adapt_dockerfile_text(Path(args[1]).read_text(encoding="utf-8")))
    except DockerfileAdaptationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
