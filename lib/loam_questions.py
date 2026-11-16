"""A question a provider asks the person, in one shape, and the answer back in
the provider's.

Added 2026-10-01. A permission card asks "may this run?"; a question asks
"which one?" or "what should it be called?", and an Allow with no answer is a
question answered with nothing. Three vendor formats ask questions, and the
page draws one card for all of them, so the broker owns every format both
ways. This module is that translation and nothing else: no process, no I/O,
so each direction is tested from recorded shapes.

The one shape (``broker.permission_request`` with ``kind: "question"``)::

    {"id": "...", "header": "...", "question": "...",
     "options": [{"label": "...", "description": "...", "preview": null}],
     "multi": false, "other": true, "secret": false, "required": true}

and the answer, on the existing permission route::

    {"behavior": "allow", "answers": {"<id>": ["<label or typed text>", ...]}}

The three formats in, each read from the pinned build:

- **Claude Code ``AskUserQuestion``** (CLI 2.1.281). The CLI keys answers by
  the question TEXT, so that is the id. A host's ``updatedInput`` may add
  ``answers`` (``{question: string}``, or a list for a multi-select, which
  the CLI joins itself with ``", "``, quoting a label that holds a comma) and
  must not change a field the model wrote. "Other" is always offered by the
  CLI's own prompt. An extended question may carry ``kind`` ``"text"`` or
  ``"number"`` and no options: free text here.
- **Codex ``item/tool/requestUserInput``** (app-server 0.159.3 schema).
  ``questions[]`` of ``id, header, question, options|null, isOther,
  isSecret``; the answer is ``{"answers": {id: {"answers": [..]}}}``. The
  schema has no way to decline, so a deny is empty answers.
- **Codex ``mcpServer/elicitation/request``**, form mode only, and only when
  the form has fields. Codex's own approval before an MCP tool call is also
  an elicitation (``_meta.codex_approval_kind``, an empty form), and that
  stays an approval card: ``from_elicitation`` returns None for it.
"""

from __future__ import annotations

import math

CLAUDE_TOOL = "AskUserQuestion"
# What the model is told when the person declines, in the CLI's own words (the
# line its prompt draws for a declined AskUserQuestion, 2.1.281).
CLAUDE_DECLINED = "User declined to answer questions"

YES, NO = "Yes", "No"


class AnswerError(ValueError):
    """An answer that does not fit its question. The route makes it a 400,
    and nothing is sent to the provider."""


def _question(qid: str, header, text, options, *, multi=False, other=False,
              secret=False, required=True) -> dict:
    return {"id": qid, "header": header if isinstance(header, str) else "",
            "question": text if isinstance(text, str) else "",
            "options": options, "multi": bool(multi), "other": bool(other),
            "secret": bool(secret), "required": bool(required)}


def _option(label, description=None, preview=None) -> dict:
    return {"label": str(label),
            "description": description if isinstance(description, str) else None,
            "preview": preview if isinstance(preview, str) else None}


# -- in ----------------------------------------------------------------------

def from_claude(tool_input: dict) -> list[dict] | None:
    """``AskUserQuestion``'s input as questions, or None if it holds none."""
    raw = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(raw, list) or not raw:
        return None
    questions = []
    for q in raw:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str):
            continue
        options = [_option(o.get("label"), o.get("description"), o.get("preview"))
                   for o in q.get("options") or []
                   if isinstance(o, dict) and isinstance(o.get("label"), str)]
        free = q.get("kind") in ("text", "number") or not options
        questions.append(_question(
            q["question"], q.get("header"), q["question"],
            [] if free else options,
            multi=q.get("multiSelect") is True and not free,
            other=True))
    return questions or None


def from_codex_input(params: dict) -> list[dict] | None:
    """``item/tool/requestUserInput``'s questions, field for field."""
    raw = params.get("questions") if isinstance(params, dict) else None
    if not isinstance(raw, list) or not raw:
        return None
    questions = []
    for q in raw:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str):
            continue
        options = [_option(o.get("label"), o.get("description"))
                   for o in q.get("options") or []
                   if isinstance(o, dict) and isinstance(o.get("label"), str)]
        # A question without options is free text, so it takes typed text
        # whatever `isOther` says.
        questions.append(_question(
            q["id"], q.get("header"), q.get("question"), options,
            other=q.get("isOther") is True or not options,
            secret=q.get("isSecret") is True))
    return questions or None


def _field_choices(prop: dict) -> tuple[list[tuple[str, object]], bool] | None:
    """An elicitation field's choices as ``(label, value)`` pairs and whether
    it is a multi-select, or None if it is free text."""
    def titled(items) -> list[tuple[str, object]]:
        return [(str(i.get("title") or i.get("const")), i.get("const"))
                for i in items if isinstance(i, dict) and "const" in i]

    kind = prop.get("type")
    if kind == "boolean":
        return [(YES, True), (NO, False)], False
    if kind == "array" and isinstance(prop.get("items"), dict):
        items = prop["items"]
        if isinstance(items.get("anyOf"), list):
            return titled(items["anyOf"]), True
        if isinstance(items.get("enum"), list):
            return [(str(v), v) for v in items["enum"]], True
        return None
    if isinstance(prop.get("oneOf"), list):
        return titled(prop["oneOf"]), False
    if isinstance(prop.get("enum"), list):
        names = prop.get("enumNames")
        if isinstance(names, list) and len(names) == len(prop["enum"]):
            return [(str(n), v) for n, v in zip(names, prop["enum"])], False
        return [(str(v), v) for v in prop["enum"]], False
    return None


def from_elicitation(params: dict) -> list[dict] | None:
    """A form elicitation's fields as questions, one per property, or None
    when it is not a question: Codex's own MCP tool-call approval, a URL
    elicitation, or a form with no fields."""
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    if meta.get("codex_approval_kind"):
        return None
    if params.get("mode") not in ("form", "openai/form", "openaiForm"):
        return None
    schema = params.get("requestedSchema")
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict) or not props:
        return None
    required = set(schema.get("required") or [])
    questions = []
    for name, prop in props.items():
        if not isinstance(prop, dict):
            prop = {}
        choices = _field_choices(prop)
        options = [_option(label) for label, _ in choices[0]] if choices else []
        questions.append(_question(
            name, prop.get("title") or name, prop.get("description") or "",
            options, multi=bool(choices and choices[1]),
            other=not choices, required=name in required))
    return questions


# -- checked ----------------------------------------------------------------

def check(questions: list[dict], answers) -> dict[str, list[str]]:
    """The page's answers, checked against the questions they answer.

    Raises ``AnswerError`` naming what is wrong; returns the answers with
    blank strings dropped and only questions that were asked. Nothing is
    sent to a provider before this has passed."""
    if answers is None:
        raise AnswerError("`answers` is required to allow a question: "
                          "{\"<question id>\": [\"<label or text>\", ...]}")
    if not isinstance(answers, dict):
        raise AnswerError("`answers` is an object of question id to a list of strings")
    by_id = {q["id"]: q for q in questions}
    unknown = [k for k in answers if k not in by_id]
    if unknown:
        raise AnswerError(f"`answers` names a question that was not asked: {unknown[0]!r}")
    clean: dict[str, list[str]] = {}
    for qid, q in by_id.items():
        given = answers.get(qid, [])
        if isinstance(given, str):
            given = [given]
        if not isinstance(given, list) or not all(isinstance(v, str) for v in given):
            raise AnswerError(f"`answers[{qid!r}]` is a list of strings")
        values = []
        for v in given:
            if v.strip() and v not in values:
                values.append(v)
        if not values:
            if q["required"]:
                raise AnswerError(f"`answers[{qid!r}]` is missing; the question "
                                  f"{q['header'] or qid!r} needs an answer")
            continue
        if not q["multi"] and len(values) > 1:
            raise AnswerError(f"`answers[{qid!r}]` takes one answer, not {len(values)}")
        labels = {o["label"] for o in q["options"]}
        typed = [v for v in values if v not in labels]
        if typed and not q["other"]:
            raise AnswerError(f"`answers[{qid!r}]`: {typed[0]!r} is not one of the options")
        if len(typed) > 1:
            raise AnswerError(f"`answers[{qid!r}]` takes one typed value, not {len(typed)}")
        clean[qid] = values
    return clean


# -- out ---------------------------------------------------------------------

def to_claude(tool_input: dict, questions: list[dict],
              answers: dict[str, list[str]]) -> dict:
    """``updatedInput`` for the control_response: the model's input as it
    was, plus ``answers`` keyed by question text. A single answer is the
    string; a multi-select is the list, which the CLI joins itself exactly as
    its own prompt does."""
    multi = {q["id"]: q["multi"] for q in questions}
    out = {qid: (values if multi.get(qid) else values[0])
           for qid, values in answers.items()}
    return {**tool_input, "answers": out}


def to_codex_input(answers: dict[str, list[str]]) -> dict:
    """``ToolRequestUserInputResponse``. An empty ``answers`` is a deny."""
    return {"answers": {qid: {"answers": values} for qid, values in answers.items()}}


def _cast(prop: dict, value: str):
    kind = prop.get("type")
    if kind == "integer":
        try:
            return int(value.strip())
        except ValueError:
            pass
        try:
            number = float(value.strip())
        except ValueError:
            number = math.nan
        if number.is_integer():
            return int(number)
        raise AnswerError(f"{value!r} is not a whole number")
    if kind == "number":
        try:
            number = float(value.strip())
        except ValueError:
            raise AnswerError(f"{value!r} is not a number") from None
        if not math.isfinite(number):
            raise AnswerError(f"{value!r} is not a number")
        return int(number) if number.is_integer() and "." not in value else number
    return value


def to_elicitation(params: dict, answers: dict[str, list[str]]) -> dict:
    """``{"action": "accept", "content": {...}}``, each value in its schema
    type: an option back to its own value (a boolean to ``true``/``false``,
    a titled option to its ``const``), a number parsed, a multi-select a
    list. Raises ``AnswerError`` for a number that does not parse."""
    props = (params.get("requestedSchema") or {}).get("properties") or {}
    content = {}
    for name, values in answers.items():
        prop = props.get(name) if isinstance(props.get(name), dict) else {}
        choices = _field_choices(prop)
        if choices:
            lookup = dict(choices[0])
            picked = [lookup.get(v, v) for v in values]
            content[name] = picked if choices[1] else picked[0]
        else:
            try:
                content[name] = _cast(prop, values[0])
            except AnswerError as exc:
                raise AnswerError(f"`answers[{name!r}]`: {exc}") from None
    return {"action": "accept", "content": content}
