#!/usr/bin/env python

import os
import sys
import importlib
from inspect import Parameter, signature


def get_module_name(argv: list[str]) -> str:
  assert len(argv) >= 2, "Must specify .py file."
  assert argv[1].endswith(".py"), "Must specify .py file."

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


def parse_parameters_from_argv(argv: list[str]):
  if len(argv) < 4:
    return [], {}

  argv = argv[3:]
  args, kwargs = [], {}

  cursor = 0
  while cursor < len(argv):
    arg = argv[cursor]
    if arg.startswith("--"):
      assert cursor + 1 < len(argv), "'{}' does not have a value".format(arg)
      kwargs[arg[2:]] = argv[cursor + 1]
      cursor += 2
    else:
      args.append(arg)
      cursor += 1
  return args, kwargs


def get_fn_parameters_from_argv(fn, argv: list[str]):
  args, kwargs = parse_parameters_from_argv(argv)
  parameters = {}
  for index, param in enumerate(signature(fn).parameters.values()):
    assert (
      param.kind == Parameter.POSITIONAL_OR_KEYWORD
    ), "Parameter '{}' is not supported".format(param.name)

    annotation = param.annotation
    if annotation == Parameter.empty:
      annotation = str
    if index < len(args):
      parameters[param.name] = annotation(args[index])
    elif param.name in kwargs:
      parameters[param.name] = annotation(kwargs[param.name])
    elif param.default == Parameter.empty:
      raise Exception("Parameter '{}' is required".format(param.name))
  return parameters


def load_argv() -> list[str]:
  argv = [item for item in sys.argv]
  try:
    with open("etc/argv") as f:
      lines = f.readlines()
  except Exception:
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
  module_name = get_module_name(argv)
  module = importlib.import_module(module_name)

  fn_name = argv[2] if len(argv) > 2 else "main"
  fn = getattr(module, fn_name, None)
  assert fn is not None, "{} is not defiend in {}".format(fn_name, module_name)

  parameters = get_fn_parameters_from_argv(fn, argv)
  result = fn(**parameters)
  if result is not None:
    print(result)


if __name__ == "__main__":
  main()
