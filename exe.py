#!/usr/bin/env python

import importlib
import os
import sys
from inspect import Parameter, signature
from typing import Any, Literal

import msgspec

from app.cmd import cmd_to_params
from app.context import create_app_context
from app.message import convert_message
from app.types import AppContextT


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
  data = cmd_to_params(" ".join(argv[3:]), params_type[1])
  struct = convert_message(data, params_type[1])
  params = (
    {"params": struct}
    if params_type[0] == "single"
    else msgspec.structs.asdict(struct)
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
