import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from inspect import Parameter, Signature, signature
from types import NoneType
from typing import Any, Literal, TypeVar

import msgspec

from app.error import AppError
from app.message import (
  convert_message,
  encode_message,
  schema_hook,
)
from app.types import AppContextT

Params = TypeVar("Params")
CmdToken = int | float | str | bool | None
CmdTokenType = TypeVar("CmdTokenType", bound=CmdToken)
type CommandHandler = Callable
ParamsFactory = Callable[[Any], Params]
CmdFunction = TypeVar("CmdFunction", bound=CommandHandler)
type ParamsType = tuple[
  Literal["single", "multiple"], type[msgspec.Struct | None]
]
cmds: dict[str, "Command | Literal[True]"] = {}
fns: dict[Callable, str] = {}
cmd_name_scoper = "_"


@dataclass
class Argv:
  items: list[CmdToken]
  _cursor = 0

  def get(self):
    if self._cursor < len(self.items):
      return self.items[self._cursor]
    return None

  def next(self, typ: type[CmdTokenType]) -> CmdTokenType:
    value = self.get()
    if not isinstance(value, typ):
      raise AppError("invalid_cmd_type")
    self._cursor += 1
    return value

  def check(self, typ: type[CmdTokenType]) -> tuple[CmdTokenType, bool]:
    value = self.get()
    if not isinstance(value, typ):
      return None, False  # type: ignore
    self._cursor += 1
    return value, True

  def read(self) -> CmdToken:
    value = self.get()
    self._cursor += 1
    return value

  def count(self):
    return len(self.items)

  def to_params(self, typ: type[msgspec.Struct | None]):
    args, kwargs = CmdArgsBuilder(self.items[self._cursor :]).build()
    if typ is NoneType:
      if len(args) == 0 and len(kwargs) == 0:
        return
      else:
        raise AppError("no-args-are-allowed")
    elif len(args) == 0:
      return kwargs
    else:
      fields = msgspec.structs.fields(typ)
      for field, value in zip(fields, args):
        kwargs[field.name] = value
      return kwargs


@dataclass
class Command:
  name: str
  return_type: type
  requires_context: bool
  handler: CommandHandler
  params_type: ParamsType

  def validate(self, args: Any):
    if self.params_type[1] is not NoneType:
      try:
        params = convert_message(args, self.params_type[1])  # type: ignore
      except msgspec.ValidationError as e:
        print(e, self.params_type, args)
        raise AppError("invalid-command-params")
    elif args:
      raise AppError("invalid-command-params")
    else:
      params = None
    return params

  def execute(self, ctx: AppContextT, args: Any):
    params = self.validate(args)
    return self._execute_work(ctx, params)

  def _execute_work(self, ctx: Any, argv: Any) -> Any:
    params = (
      {"params": argv}
      if self.params_type[0] == "single"
      else {}
      if argv is None
      else msgspec.structs.asdict(argv)
    )
    if self.requires_context:
      params["ctx"] = ctx
    result = self.handler(**params)
    if callable(result):
      result = result()
    return result


def cmd(*names: str):
  def wrapper(fn: CmdFunction) -> CmdFunction:
    cmd_names = names if len(names) != 0 else fn.__name__.split("_")
    name = cmd_names[0]
    for i in range(1, len(cmd_names)):
      if name not in cmds:
        cmds[name] = True
      name = name + cmd_name_scoper + cmd_names[i]
    if cmds.get(name, True) is not True:
      raise AppError("unique-command-error", f"{name} is already registered")
    s = signature(fn)
    return_type = s.return_annotation
    requires_context, params_type = _get_params_type(name, s)
    cmds[name] = Command(
      name=name,
      handler=fn,
      params_type=params_type,
      return_type=return_type,
      requires_context=requires_context,
    )
    fns[fn] = name
    return fn

  return wrapper


def get_command_by_fn(fn: Callable) -> None | Command:
  name = fns.get(fn)
  if name is None:
    return
  cmd = cmds[name]
  assert isinstance(cmd, Command)
  return cmd


def _get_params_type(method: str, s: Signature) -> tuple[bool, ParamsType]:
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
  struct = (
    msgspec.defstruct(
      method.title().replace(cmd_name_scoper, "") + "Params",
      fields,
    )
    if len(fields) != 0
    else NoneType
  )
  return requires_context, ("multiple", struct)


def _match(txt: str):
  argv = _split_tokens(txt)
  names: list[str] = []
  for it in argv:
    if not isinstance(it, str) or it in ("--", "="):
      break
    names.append(it)
  if len(names) == 0:
    raise AppError("invalid-command", f"invalid command: {txt}")
  index = 0
  cmd: Command | None = None
  for i, it in enumerate(names, 1):
    key = cmd_name_scoper.join(names[:i])
    c = cmds.get(key)
    if c is None:
      break
    if isinstance(c, Command):
      cmd = c
      index = i
  if not isinstance(cmd, Command):
    raise AppError("invalid-command", txt)
  return cmd, argv[index:]


def validate_txt(txt: str):
  cmd, argv = _match(txt)
  cmd.validate(Argv(argv).to_params(cmd.params_type[1]))


def execute_txt(ctx: AppContextT, txt: str):
  cmd, argv = _match(txt)
  cmd.execute(ctx, Argv(argv).to_params(cmd.params_type[1]))


def execute_with_message(ctx: AppContextT, name: str, message: bytes) -> bytes:
  cmd = cmds.get(name)
  if not isinstance(cmd, Command):
    raise AppError("cmd-not-found", f"{name} is not a cmd")
  result = cmd.execute(ctx, convert_message(message, cmd.params_type[1]))
  return encode_message(result)


class CmdArgsBuilder:
  def __init__(self, tokens: list[CmdToken]) -> None:
    self.cursor = 0
    self.equal = "="
    self.scoper = "."
    self.tokens = tokens
    self.scope_suffix = ":"
    self.current_scope = ""
    self.length = len(tokens)
    self.array_placeholder = "$"
    self.args: list[CmdToken] = []
    self.values: dict[str, Any] = {"": {}}
    self.possible_lists: dict[str, bool] = {}

  def build(self):
    while self.cursor < self.length:
      key = self.tokens[self.cursor]
      self.cursor += 1
      if not isinstance(key, str):
        self.args.append(key)
        continue
      if key.endswith(self.scope_suffix):
        self._change_scope(key)
        continue
      if self.cursor == self.length or self.tokens[self.cursor] != self.equal:
        self.args.append(key)
        continue
      self._handle_key_value(key)
    self._build_lists()
    return self.args, self.values[""]

  def _handle_key_value(self, key: str):
    if self.tokens[self.cursor] != self.equal:
      raise AppError("value-required", f"value is required for {key}")
    self.cursor += 1
    if self.cursor >= self.length:
      raise AppError("value-required", f"value is required for {key}")
    value = self.tokens[self.cursor]
    self.cursor += 1
    if len(self.current_scope) != 0:
      key = self.current_scope + "." + key
    self._apply_key(key, value)

  def _change_scope(self, key: str):
    key = key[: -len(self.scope_suffix)]
    if key.endswith(self.array_placeholder):
      parts = key.split(self.scoper)
      scope = self._get_value_for_scope(parts)
      key = key[: -len(self.array_placeholder)] + str(len(scope))
    self.current_scope = key

  def _build_lists(self):
    keys = sorted(self.possible_lists.keys(), reverse=True)
    for key in keys:
      value = self.values[key]
      required = self.possible_lists[key]
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
      self.values[""] = li
    else:
      parts = key.split(self.scoper)
      scope, key = self.scoper.join(parts[:-1]), parts[-1]
      self.values[scope][key] = [value[str(i)] for i in range(len(value))]

  def _add_to_lists(self, parts: list[str], required: bool):
    key = self.scoper.join(parts)
    value = self.possible_lists.get(key, False)
    self.possible_lists[key] = value or required

  def _apply_key(self, key: str, value: CmdToken):
    parts = key.split(self.scoper)
    scope = self._get_value_for_scope(parts)
    key = parts[-1]
    if key == self.array_placeholder:
      scope[str(len(scope))] = value
      self._add_to_lists(parts[:-1], True)
      self.possible_lists[self.scoper.join(parts[:-1])]
    else:
      scope[key] = value
      if key.isdigit():
        self._add_to_lists(parts[:-1], False)

  def _get_value_for_scope(self, parts: list[str]) -> dict[str, Any]:
    cursor = 0
    length = len(parts) - 1
    value = self.values[""]
    while cursor < length:
      part = parts[cursor]
      cursor += 1
      key = self.scoper.join(parts[:cursor])
      if part not in value:
        subvalue = {}
        value[part] = subvalue
        self.values[key] = subvalue
        if part.isdigit():
          # todo: verify this when cursor is 0
          self._add_to_lists(parts[: cursor - 1], False)
      else:
        subvalue = value[part]
        if not isinstance(subvalue, dict):
          raise AppError("invalid-cmd-token-for-json")
      value = subvalue
    return value


def _split_tokens(cmd: str) -> list[CmdToken]:
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
      result.append(json.loads(token))
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


def cmd_to_params(txt: str, typ: type[None | msgspec.Struct]):
  return Argv(_split_tokens(txt)).to_params(typ)


def cmd_to_args(txt: str):
  return CmdArgsBuilder(_split_tokens(txt)).build()


def test_cmd_to_json_with_file(filename: str):
  with open(filename, "r") as f:
    txt = f.read()
  print(cmd_to_args(txt))


def test_cmd_to_json():
  assert cmd_to_args("1 2 3") == ([1, 2, 3], {})
  assert cmd_to_args("x=1 y=1") == ([], {"x": 1, "y": 1})
  assert cmd_to_args("x.y=1") == ([], {"x": {"y": 1}})
  assert cmd_to_args("x.0=1 x.1=1") == ([], {"x": [1, 1]})
  assert cmd_to_args("x.$=1 x.$=1") == ([], {"x": [1, 1]})
  assert cmd_to_args("x: $=1 $=1") == ([], {"x": [1, 1]})
  assert cmd_to_args("0.x=1 0.y=2 1.x=3 1.y=4") == (
    [],
    [
      {"x": 1, "y": 2},
      {"x": 3, "y": 4},
    ],
  )
  assert cmd_to_args("0: x=1 y=2 1: x=3 y=4") == (
    [],
    [
      {"x": 1, "y": 2},
      {"x": 3, "y": 4},
    ],
  )
  assert cmd_to_args("$: x=1 y=2 $: x=3 y=4") == (
    [],
    [
      {"x": 1, "y": 2},
      {"x": 3, "y": 4},
    ],
  )
  assert cmd_to_args("a.$: x=1 y=2 a.$: x=3 y=4") == (
    [],
    {
      "a": [
        {"x": 1, "y": 2},
        {"x": 3, "y": 4},
      ]
    },
  )


def install_cmd():
  import importlib

  importlib.import_module("app.cmds")
  importlib.import_module("app.diode")
  importlib.import_module("app.plane")
  importlib.import_module("app.optics")
  importlib.import_module("app.record")
  importlib.import_module("app.capture")
  importlib.import_module("app.autoled")
  importlib.import_module("app.miniature")
  importlib.import_module("app.controller")


def generate_rpc_types():
  import copy
  import subprocess

  install_cmd()

  remote_procedures: list[tuple[str, type]] = []
  for name, cmd in cmds.items():
    if not isinstance(cmd, Command):
      continue
    if cmd.return_type is Parameter.empty:
      continue
    method = msgspec.defstruct(
      name,
      [("params", cmd.params_type[1]), ("result", cmd.return_type)],
      forbid_unknown_fields=True,
    )
    remote_procedures.append((name, method))
  schema = msgspec.json.schema(
    msgspec.defstruct(
      "RemoteProcedures", remote_procedures, forbid_unknown_fields=True
    ),
    schema_hook=schema_hook,
  )
  if root := schema.get("$ref"):
    assert isinstance(root, str)
    assert root.startswith("#/$defs/")
    root_type = schema["$defs"][root[len("#/$defs/") :]]
    assert isinstance(root_type, dict)
    root_type = copy.deepcopy(root_type)
    root_type["$defs"] = schema["$defs"]
    schema = root_type
  process = subprocess.run(
    ["bunx", "-y", "json-schema-to-typescript"],
    check=True,
    capture_output=True,
    input=json.dumps(schema).encode("utf-8"),
  )
  with open("./src/rpc.methods.ts", "wb") as f:
    f.write(process.stdout)
