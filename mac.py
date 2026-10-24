#!/usr/bin/env python3
"""mac: reference compiler for the Macfile (Master Context File) format, spec v0.1.

Commands
  mac check FILE...   validate macfile(s) and print a token summary
  mac pack  FILE...   merge blocks for ONE target into a single payload
  mac split FILE...   write per-platform files (AGENTS.md, Modelfile, ...)

Several files can be given (e.g. global.macfile.md project.macfile.md).
Later files may use  overrides="<id>"  to replace, or  extends="<id>"  to
append to, a block defined by an earlier file.

Requires Python 3.9+, standard library only.
Token counts are a rough chars/4 estimate; swap estimate_tokens() for a real
tokenizer (e.g. tiktoken) if you need accuracy.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SPEC_VERSION = "0.1"
LAYERS = ("agent", "user", "project", "env")
LAYER_RANK = {name: i for i, name in enumerate(LAYERS)}
LOADS = ("always", "on_demand", "reference")

# A request for the key also matches blocks tagged with the value
# (e.g. packing for "ollama" accepts blocks tagged "local").
TARGET_ALIASES = {
    "claude-project": "web",
    "custom-gpt": "web",
    "copilot": "repo",
    "claude-code": "repo",
    "ollama": "local",
    "lm-studio": "local",
}
KNOWN_TARGETS = {"*", "web", "repo", "local"} | set(TARGET_ALIASES)

# ---------------------------------------------------------------- parsing ---

_ATTR = r'[\w.-]+\s*=\s*"[^"]*"'
BLOCK_RE = re.compile(r"<block\b((?:\s+" + _ATTR + r")*)\s*>(.*?)</block\s*>", re.S)
ATTR_RE = re.compile(r'([\w.-]+)\s*=\s*"([^"]*)"')
META_RE = re.compile(r"<meta>(.*?)</meta>", re.S)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)  # NOTE: also strips comments inside bodies


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4) if text else 0


class Diag:
    """Collects errors and warnings so we can report them all at once."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def emit(self) -> None:
        for w in self.warnings:
            print(f"warning: {w}", file=sys.stderr)
        for e in self.errors:
            print(f"error: {e}", file=sys.stderr)


@dataclass(eq=False)  # identity comparison: blocks are unique objects
class Block:
    id: str
    layer: str
    targets: list[str]
    load: str
    priority: int
    declared_tokens: Optional[int]
    src: Optional[str]
    body: str
    origin: Path
    order: int
    extends: Optional[str] = None
    overrides: Optional[str] = None
    src_text: Optional[str] = None

    @property
    def inline_text(self) -> str:
        return "\n\n".join(p for p in (self.body, self.src_text) if p)

    @property
    def tokens(self) -> int:
        # If the src file is unavailable, trust the declared estimate.
        if self.src and self.src_text is None and self.declared_tokens is not None:
            return self.declared_tokens
        return estimate_tokens(self.inline_text)


@dataclass
class Macfile:
    meta: dict[str, str] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)
    sources: list[Path] = field(default_factory=list)


def _int(value: str, label: str, where: str, diag: Diag) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        diag.err(f"{where}: {label} '{value}' is not an integer")
        return None


def make_block(attrs: dict, body: str, origin: Path, order: int, diag: Diag) -> Optional[Block]:
    bid = attrs.get("id", "").strip()
    layer = attrs.get("layer", "").strip()
    where = f"{origin.name}: block '{bid or '?'}'"
    ok = True

    if not bid:
        diag.err(f"{origin.name}: block without an id")
        ok = False
    if layer not in LAYERS:
        diag.err(f"{where}: layer '{layer}' must be one of {', '.join(LAYERS)}")
        ok = False
    elif bid and not bid.startswith(layer + "."):
        diag.err(f"{where}: id must start with '{layer}.'")
        ok = False

    load = attrs.get("load", "always").strip()
    if load not in LOADS:
        diag.err(f"{where}: load '{load}' must be one of {', '.join(LOADS)}")
        ok = False

    priority = _int(attrs.get("priority", "50"), "priority", where, diag)
    if priority is None:
        ok = False
    elif not 0 <= priority <= 100:
        diag.err(f"{where}: priority {priority} outside 0-100")
        ok = False

    declared = None
    if "tokens" in attrs:
        declared = _int(attrs["tokens"], "tokens", where, diag)
        if declared is None:
            ok = False

    targets = [t.strip() for t in attrs.get("targets", "*").split(",") if t.strip()] or ["*"]
    for t in targets:
        if t not in KNOWN_TARGETS:
            diag.warn(f"{where}: unknown target '{t}' (known: {', '.join(sorted(KNOWN_TARGETS))})")

    if not ok:
        return None
    return Block(
        id=bid, layer=layer, targets=targets, load=load, priority=priority,
        declared_tokens=declared, src=attrs.get("src") or None, body=body,
        origin=origin, order=order,
        extends=attrs.get("extends") or None, overrides=attrs.get("overrides") or None,
    )


def resolve_src(block: Block, diag: Diag) -> None:
    """Read block.src (relative to its macfile). Must stay inside that folder."""
    if not block.src:
        return
    base = block.origin.parent.resolve()
    path = (base / block.src).resolve()
    if not path.is_relative_to(base):
        diag.err(f"{block.origin.name}: block '{block.id}': src '{block.src}' escapes the macfile folder")
        return
    if path.is_file():
        block.src_text = path.read_text(encoding="utf-8").strip()
    else:
        diag.warn(f"{block.origin.name}: block '{block.id}': src not found: {block.src}")


def _join(a: Optional[str], b: Optional[str]) -> str:
    return "\n\n".join(p for p in (a, b) if p)


def load_files(paths: list[Path], diag: Diag) -> Macfile:
    mf = Macfile()
    order = 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            diag.err(f"cannot read {path}: {exc}")
            continue
        mf.sources.append(path)
        text = COMMENT_RE.sub("", text)

        meta_match = META_RE.search(text)
        if meta_match:
            for line in meta_match.group(1).splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    mf.meta[key.strip()] = value.strip()

        matches = list(BLOCK_RE.finditer(text))
        if len(re.findall(r"<block\b", text)) != len(matches):
            diag.err(f"{path.name}: malformed <block> (unclosed tag or unquoted attribute)")

        for m in matches:
            attrs = dict(ATTR_RE.findall(m.group(1)))
            body = textwrap.dedent(m.group(2)).strip()
            block = make_block(attrs, body, path, order, diag)
            order += 1
            if block is None:
                continue
            resolve_src(block, diag)
            by_id = {b.id: i for i, b in enumerate(mf.blocks)}

            if block.overrides:
                idx = by_id.get(block.overrides)
                if idx is None:
                    diag.warn(f"{path.name}: block '{block.id}' overrides unknown '{block.overrides}'; adding it")
                    mf.blocks.append(block)
                else:
                    block.order = mf.blocks[idx].order
                    mf.blocks[idx] = block
            elif block.extends:
                idx = by_id.get(block.extends)
                if idx is None:
                    diag.err(f"{path.name}: block '{block.id}' extends unknown '{block.extends}'")
                else:
                    base = mf.blocks[idx]
                    base.body = _join(base.body, block.body)
                    if block.src_text:
                        base.src_text = _join(base.src_text, block.src_text)
            elif block.id in by_id:
                diag.err(f"{path.name}: duplicate id '{block.id}' (use overrides=\"{block.id}\" to replace it)")
            else:
                mf.blocks.append(block)

    for b in mf.blocks:
        if not b.inline_text and not b.src:
            diag.warn(f"block '{b.id}' is empty")
    return mf


# --------------------------------------------------------------- planning ---

def matches(block: Block, target: Optional[str]) -> bool:
    if target is None:
        return True
    t = set(block.targets)
    return "*" in t or target in t or TARGET_ALIASES.get(target) in t


@dataclass
class Plan:
    inline: list[Block]    # bodies that go into the payload
    refs: list[Block]      # blocks only mentioned as pointers
    dropped: list[Block]   # removed to fit the budget

    @property
    def tokens(self) -> int:
        return sum(b.tokens for b in self.inline)


def make_plan(blocks: list[Block], target: Optional[str], layers=None,
              include_on_demand: bool = False, budget: int = 0) -> Plan:
    cand = [b for b in blocks if matches(b, target) and (not layers or b.layer in layers)]
    inline = [b for b in cand if b.load == "always" or (b.load == "on_demand" and include_on_demand)]
    refs = [b for b in cand if b not in inline]
    dropped: list[Block] = []

    # Budget: drop lowest-priority non-"always" blocks first, then lowest "always".
    while budget and inline and sum(b.tokens for b in inline) > budget:
        pool = [b for b in inline if b.load != "always"] or inline
        victim = min(pool, key=lambda b: (b.priority, -b.tokens))
        inline.remove(victim)
        dropped.append(victim)

    inline.sort(key=lambda b: (LAYER_RANK[b.layer], -b.priority, b.order))
    refs = [b for b in refs + dropped if b.src]  # pointer list needs a path to point at
    refs.sort(key=lambda b: (LAYER_RANK[b.layer], -b.priority, b.order))
    return Plan(inline, refs, dropped)


# -------------------------------------------------------------- rendering ---

def _hint(block: Block) -> str:
    for line in block.body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


def render_plain(plan: Plan, header: Optional[str] = None, refs: bool = True) -> str:
    parts = [header] if header else []
    parts += [b.inline_text for b in plan.inline]
    if refs and plan.refs:
        lines = ["## Reference material (load on demand)"]
        for b in plan.refs:
            hint = _hint(b)
            lines.append(f"- `{b.id}` -> `{b.src}`" + (f": {hint}" if hint else ""))
        parts.append("\n".join(lines))
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


def _attr(value: str) -> str:
    return value.replace('"', "'")


def _tag(b: Block, keep_src: bool) -> str:
    attrs = [("id", b.id), ("layer", b.layer), ("targets", ",".join(b.targets)),
             ("load", b.load), ("priority", str(b.priority))]
    if b.declared_tokens is not None:
        attrs.append(("tokens", str(b.declared_tokens)))
    if keep_src and b.src:
        attrs.append(("src", b.src))
    return "<block " + " ".join(f'{k}="{_attr(v)}"' for k, v in attrs) + ">"


def render_tagged(plan: Plan, meta: dict[str, str], target: str) -> str:
    """Round-trippable output. Inlined blocks have src materialised (and dropped)
    so re-reading the file never duplicates content."""
    out = [f'<macfile version="{SPEC_VERSION}">', "  <meta>"]
    for k, v in {**meta, "target": target}.items():
        out.append(f"    {k}: {v}")
    out.append("  </meta>")
    for b in plan.inline:
        out += ["", _tag(b, keep_src=False), b.inline_text, "</block>"]
    for b in plan.refs:
        out += ["", _tag(b, keep_src=True), b.body, "</block>"]
    out.append("</macfile>")
    return "\n".join(out) + "\n"


def render_modelfile(system_text: str, model: str) -> str:
    safe = system_text.strip().replace('"""', "'''")
    return f'# Generated by mac. Do not edit; rebuild from the macfile.\nFROM {model}\nSYSTEM """\n{safe}\n"""\n'


# ------------------------------------------------------------------ split ---

@dataclass
class Recipe:
    path: str
    target: Optional[str]
    layers: tuple
    kind: str = "md"          # md | modelfile | knowledge
    on_demand: bool = False
    refs: bool = True


def recipes_for(preset: str, include_agent: bool) -> list[Recipe]:
    repo_layers = ("project",) + (("agent",) if include_agent else ())
    table = {
        "repo": [Recipe(p, "repo", repo_layers) for p in
                 ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md")],
        "web": [
            Recipe("web/custom-instructions.md", "web", ("user",)),
            Recipe("web/project-instructions.md", "web", ("agent", "project")),
            Recipe("web/knowledge", "web", LAYERS, kind="knowledge"),
        ],
        "local": [
            Recipe("local/system-prompt.md", "local", LAYERS, refs=False),
            Recipe("local/Modelfile", "local", LAYERS, kind="modelfile", refs=False),
        ],
        "layers": [Recipe(f"layers/{name}.md", None, (name,), on_demand=True) for name in LAYERS],
    }
    if preset == "all":
        return table["repo"] + table["web"] + table["local"]
    return table[preset]


def _write(path: Path, text: str, dry: bool) -> None:
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def run_split(mf: Macfile, preset: str, out_dir: Path, budget: int,
              include_agent: bool, model: str, dry: bool) -> None:
    verb = "would write" if dry else "wrote"
    header = f"<!-- Generated by mac from {', '.join(p.name for p in mf.sources)}. Do not edit; rebuild instead. -->"

    for r in recipes_for(preset, include_agent):
        dest = out_dir / r.path
        if r.kind == "knowledge":
            files = [b for b in mf.blocks if matches(b, r.target) and b.load != "always"
                     and b.layer in r.layers and b.inline_text]
            for b in files:
                name = re.sub(r"[^\w.-]", "_", b.id) + ".md"
                _write(dest / name, b.inline_text + "\n", dry)
                print(f"{verb} {dest / name}  ({b.tokens} tok)")
            if not files:
                print(f"skipped {dest}/  (no on_demand/reference content)")
            continue

        plan = make_plan(mf.blocks, r.target, r.layers, r.on_demand, budget)
        if plan.dropped:
            print(f"  {r.path}: dropped to fit {budget} tok: {', '.join(b.id for b in plan.dropped)}",
                  file=sys.stderr)
        if not plan.inline:
            print(f"skipped {dest}  (no matching blocks)")
            continue
        if r.kind == "modelfile":
            text = render_modelfile(render_plain(plan, None, refs=False), model)
        else:
            text = render_plain(plan, header, refs=r.refs)
        _write(dest, text, dry)
        print(f"{verb} {dest}  ({plan.tokens} tok, {len(plan.inline)} blocks)")


# -------------------------------------------------------------------- CLI ---

def _budget(arg: Optional[int], meta: dict[str, str], diag: Diag) -> int:
    if arg is not None:
        return max(arg, 0)
    raw = meta.get("default_budget_tokens", "0")
    try:
        return max(int(raw), 0)
    except ValueError:
        diag.warn(f"meta default_budget_tokens '{raw}' is not an integer; ignoring")
        return 0


def _layers(arg: Optional[str], diag: Diag) -> Optional[tuple]:
    if not arg:
        return None
    chosen = tuple(x.strip() for x in arg.split(",") if x.strip())
    for x in chosen:
        if x not in LAYERS:
            diag.err(f"--layers: '{x}' is not one of {', '.join(LAYERS)}")
    return chosen


def _load_or_exit(files: list[Path]) -> tuple[Macfile, Diag]:
    diag = Diag()
    mf = load_files(files, diag)
    return mf, diag


def cmd_check(args) -> int:
    mf, diag = _load_or_exit(args.files)
    budget = _budget(args.budget, mf.meta, diag)
    if not diag.errors:
        print(f"{'id':<28} {'layer':<8} {'load':<10} {'prio':>4} {'tok':>6}  targets")
        for b in sorted(mf.blocks, key=lambda b: (LAYER_RANK[b.layer], -b.priority)):
            print(f"{b.id:<28} {b.layer:<8} {b.load:<10} {b.priority:>4} {b.tokens:>6}  {','.join(b.targets)}")
        print()
        for t in ("web", "repo", "local"):
            total = sum(b.tokens for b in mf.blocks if matches(b, t) and b.load == "always")
            note = ""
            if budget and total > budget:
                note = f"  OVER BUDGET ({budget})"
                diag.warn(f"target '{t}': always-loaded blocks total {total} tok, budget is {budget}")
            print(f"always-loaded for {t:<6} {total:>6} tok{note}")
    diag.emit()
    return 1 if diag.errors else 0


def cmd_pack(args) -> int:
    mf, diag = _load_or_exit(args.files)
    budget = _budget(args.budget, mf.meta, diag)
    layers = _layers(args.layers, diag)
    if diag.errors:
        diag.emit()
        return 1
    plan = make_plan(mf.blocks, args.target, layers, args.include_on_demand, budget)
    if plan.dropped:
        diag.warn(f"dropped to fit {budget} tok: {', '.join(b.id for b in plan.dropped)}")
    if not plan.inline:
        diag.warn(f"no blocks matched target '{args.target}'")
    if args.keep_tags:
        text = render_tagged(plan, mf.meta, args.target)
    else:
        header = (f"<!-- Generated by mac for target '{args.target}'. Do not edit. -->"
                  if args.header else None)
        text = render_plain(plan, header, refs=not args.no_refs)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}  ({plan.tokens} tok, {len(plan.inline)} blocks)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    diag.emit()
    return 0


def cmd_split(args) -> int:
    mf, diag = _load_or_exit(args.files)
    budget = _budget(args.budget, mf.meta, diag)
    if diag.errors:
        diag.emit()
        return 1
    run_split(mf, args.preset, args.out_dir, budget, args.include_agent, args.model, args.dry_run)
    diag.emit()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mac", description="Macfile reference compiler (spec v%s)" % SPEC_VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("files", nargs="+", type=Path, help="macfile(s); later files may override earlier ones")
        sp.add_argument("--budget", type=int, default=None,
                        help="token budget (0 = unlimited; default: meta default_budget_tokens)")

    c = sub.add_parser("check", help="validate and summarise")
    common(c)
    c.set_defaults(func=cmd_check)

    k = sub.add_parser("pack", help="merge blocks for one target into one payload")
    common(k)
    k.add_argument("-t", "--target", default="web", help="target tag, e.g. web, repo, local, claude-project (default: web)")
    k.add_argument("--layers", help="comma-separated subset of layers, e.g. agent,user")
    k.add_argument("--include-on-demand", action="store_true", help="inline on_demand blocks if the budget allows")
    k.add_argument("--keep-tags", action="store_true", help="emit a round-trippable macfile instead of plain markdown")
    k.add_argument("--no-refs", action="store_true", help="omit the pointer list of non-inlined blocks")
    k.add_argument("--header", action="store_true", help="prepend a 'generated' comment")
    k.add_argument("-o", "--output", type=Path, help="write to a file instead of stdout")
    k.set_defaults(func=cmd_pack)

    s = sub.add_parser("split", help="write per-platform files")
    common(s)
    s.add_argument("--preset", choices=["repo", "web", "local", "layers", "all"], default="all")
    s.add_argument("--out-dir", type=Path, default=Path("dist"))
    s.add_argument("--include-agent", action="store_true",
                   help="also put agent-layer blocks into repo files (off by default: keeps them private)")
    s.add_argument("--model", default="llama3.1", help="FROM model for the generated Modelfile")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_split)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
