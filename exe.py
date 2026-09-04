#!/usr/bin/env python

import importlib
import os
import re
import sys
from inspect import Parameter, signature
from json import loads
from typing import Any, Literal, TypeVar

import msgspec

from app.context import create_app_context
from app.error import AppError
from app.message import convert_message
from app.types import AppContextT

CmdToken = int | float | str | bool | None
CmdTokenType = TypeVar("CmdTokenType", bound=CmdToken)


def split_tokens(cmd: str) -> list[CmdToken]:
  """
  Split a string into tokens with automatic type conversion.

  Rules:
    - Double-quoted strings (e.g. "Hello World") are kept as a single string token.
    - Unquoted tokens that are valid integers become int.
    - Unquoted tokens that are valid floats become float.
    - Unquoted "true" / "false" become bool.
    - Unquoted "null" becomes None.
    - Everything else stays as str.

  Examples:
      >>> split_tokens("1 2 3")
      [1, 2, 3]
      >>> split_tokens("a b c")
      ['a', 'b', 'c']
      >>> split_tokens('"Hello World" 1 2 3')
      ['Hello World', 1, 2, 3]
      >>> split_tokens('"2"')
      ['2']
      >>> split_tokens("true")
      [True]
      >>> split_tokens("null")
      [None]
      >>> split_tokens("x=1")
      ['x', '=', 1]
      >>> split_tokens("x= 1")
      ['x', '=', 1]
      >>> split_tokens("x = 1")
      ['x', '=', 1]
      >>> split_tokens('x="hi"')
      ['x', '=', 'hi']
  """
  raw_tokens = re.findall(
    r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|=|[^\s=]+', cmd
  )

  result: list = []
  for token in raw_tokens:
    if token == "true":
      result.append(True)
    elif token == "false":
      result.append(False)
    elif token == "null":
      result.append(None)
    elif token.startswith('"') and token.endswith('"'):
      result.append(loads(token))
    else:
      try:
        result.append(int(token))
        continue
      except ValueError:
        pass
      try:
        result.append(float(token))
        continue
      except ValueError:
        pass
      result.append(token)

  return result


class CmdArgsBuilder:
  def __init__(self, txt: str) -> None:
    tokens = split_tokens(txt)
    self._cursor = 0
    self._equal = "="
    self._scoper = "."
    self._tokens = tokens
    self._scope_suffix = ":"
    self.current_scope = ""
    self._length = len(tokens)
    self._array_placeholder = "$"
    self._args: list[CmdToken] = []
    self._kw_args: dict[str, Any] = {"": {}}
    self._possible_lists: dict[str, bool] = {}

  def to_params(self, type: type[msgspec.Struct]):
    args, kwargs = self._build()
    if len(args) != 0:
      fields = msgspec.structs.fields(type)
      for field, value in zip(fields, args):
        kwargs[field.name] = value
    return convert_message(kwargs, type)

  def _build(self):
    while self._cursor < self._length:
      key = self._tokens[self._cursor]
      self._cursor += 1
      if not isinstance(key, str):
        self._args.append(key)
        continue
      if key.endswith(self._scope_suffix):
        self._change_scope(key)
        continue
      if (
        self._cursor == self._length
        or self._tokens[self._cursor] != self._equal
      ):
        self._args.append(key)
        continue
      self._handle_key_value(key)
    self._build_lists()
    return self._args, self._kw_args[""]

  def _handle_key_value(self, key: str):
    if self._tokens[self._cursor] != self._equal:
      raise AppError("value-required", f"value is required for {key}")
    self._cursor += 1
    if self._cursor >= self._length:
      raise AppError("value-required", f"value is required for {key}")
    value = self._tokens[self._cursor]
    self._cursor += 1
    if len(self.current_scope) != 0:
      key = self.current_scope + "." + key
    self._apply_key(key, value)

  def _change_scope(self, key: str):
    key = key[: -len(self._scope_suffix)]
    if key.endswith(self._array_placeholder):
      parts = key.split(self._scoper)
      scope = self._get_value_for_scope(parts)
      key = key[: -len(self._array_placeholder)] + str(len(scope))
    self.current_scope = key

  def _build_lists(self):
    keys = sorted(self._possible_lists.keys(), reverse=True)
    for key in keys:
      value = self._kw_args[key]
      required = self._possible_lists[key]
      if self._can_be_valid_list(value):
        self._build_list(key, value)
      elif required:
        raise AppError("invalid-cmd-token-for-json")

  def _can_be_valid_list(self, value: dict[str, Any]):
    for i in range(len(value)):
      if str(i) not in value:
        return False
    return True

  def _build_list(self, key: str, value: dict[str, Any]):
    li = [value[str(i)] for i in range(len(value))]
    if len(key) == 0:
      self._kw_args[""] = li
    else:
      parts = key.split(self._scoper)
      scope, key = self._scoper.join(parts[:-1]), parts[-1]
      self._kw_args[scope][key] = [value[str(i)] for i in range(len(value))]

  def _add_to_lists(self, parts: list[str], required: bool):
    key = self._scoper.join(parts)
    value = self._possible_lists.get(key, False)
    self._possible_lists[key] = value or required

  def _apply_key(self, key: str, value: CmdToken):
    parts = key.split(self._scoper)
    scope = self._get_value_for_scope(parts)
    key = parts[-1]
    if key == self._array_placeholder:
      scope[str(len(scope))] = value
      self._add_to_lists(parts[:-1], True)
      self._possible_lists[self._scoper.join(parts[:-1])]
    else:
      scope[key] = value
      if key.isdigit():
        self._add_to_lists(parts[:-1], False)

  def _get_value_for_scope(self, parts: list[str]) -> dict[str, Any]:
    cursor = 0
    length = len(parts) - 1
    value = self._kw_args[""]
    while cursor < length:
      part = parts[cursor]
      cursor += 1
      key = self._scoper.join(parts[:cursor])
      if part not in value:
        subvalue = {}
        value[part] = subvalue
        self._kw_args[key] = subvalue
        if part.isdigit():
          # todo: verify this when cursor is 0
          self._add_to_lists(parts[: cursor - 1], False)
      else:
        subvalue = value[part]
        if not isinstance(subvalue, dict):
          raise AppError("invalid-cmd-token-for-json")
      value = subvalue
    return value


def get_module_name(argv: list[str]) -> str:
  path = os.path.relpath(argv[1])[:-3]
  module = []
  while path:
    path, tail = os.path.split(path)
    if tail != "":
      module.append(tail)
  module.reverse()
  module = ".".join(module)

  assert module != "main"
  return module


type ParamsType = tuple[Literal["single", "multiple"], type]


def get_params_type(method: str, fn: Any) -> tuple[bool, ParamsType]:
  s = signature(fn)
  parameters = s.parameters
  names = list(parameters.keys())
  requires_context = (
    len(names) > 0
    and names[0] == "ctx"
    and (
      parameters["ctx"].annotation is AppContextT
      or parameters["ctx"].annotation == "AppContextT"
    )
  )
  is_single = (
    requires_context and len(s.parameters) == 2 and names[1] == "params"
  )
  if is_single:
    return True, ("single", s.parameters["params"].annotation)
  if requires_context:
    names = names[1:]
  fields = [
    (name, parameters[name].annotation, parameters[name].default)
    if parameters[name].default is not Parameter.empty
    else (name, parameters[name].annotation)
    for name in names
  ]
  struct = msgspec.defstruct(
    method.title().replace("_", "") + "Params",
    fields,
  )
  return requires_context, ("multiple", struct)


def load_argv() -> list[str]:
  argv = [item for item in sys.argv]
  try:
    with open("etc/argv") as f:
      lines = f.readlines()
  except OSError:
    return argv
  lines = [line.strip() for line in lines]
  lines = [line for line in lines if not line.startswith("#")]
  lines = [line.strip() for line in lines]
  lines = [line for line in lines if line]
  if len(argv) >= len(lines):
    return argv
  for i, arg in enumerate(argv):
    lines[i] = arg
  return lines


def main():
  argv = load_argv()
  if len(argv) < 2:
    raise RuntimeError("filename is required")
  if not argv[1].endswith(".py"):
    raise RuntimeError("filename must end with .py")
  module_name = get_module_name(argv)
  module = importlib.import_module(module_name)

  fn_name = argv[2] if len(argv) > 2 else "main"
  fn = getattr(module, fn_name, None)
  if fn is None:
    fn = getattr(module, "main", None)
  if fn is None:
    raise RuntimeError(f"{fn_name} is not definend in {module_name}")
  requires_context, params_type = get_params_type(fn_name, fn)
  data = CmdArgsBuilder(" ".join(argv[3:])).to_params(params_type[1])
  params: dict[str, Any] = (
    {"params": data}
    if params_type[0] == "single"
    else msgspec.structs.asdict(data)
  )
  if requires_context:
    params["ctx"] = create_app_context()
  result = fn(**params)
  if callable(result):
    result = result()
  if result is not None:
    print(result)


if __name__ == "__main__":
  main()
