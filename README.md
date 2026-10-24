# mac: Macfile reference compiler

`mac.py` compiles a **Macfile** (Master Context File, spec v0.1) into the context payloads different AI tools expect. You write your agent role, personal preferences, project rules and environment notes once, in one tagged-markdown file. `mac` then **packs** them into a single paste-able payload or **splits** them into per-platform files (`AGENTS.md`, `CLAUDE.md`, Copilot instructions, Claude Projects fields, an Ollama `Modelfile`, and so on). Already have those files? `mac` can **unpack** them into a starter macfile.

- Single file, Python 3.9+, standard library only
- Declarative input: a macfile contains no executable logic
- Single source of truth: edit the macfile, rebuild, never hand-edit outputs

## Quick start

```bash
python3 mac.py unpack AGENTS.md CLAUDE.md -o my.macfile.md   # start from files you already have
python3 mac.py check example.macfile.md
python3 mac.py pack  example.macfile.md -t web
python3 mac.py split example.macfile.md --out-dir dist
```

## The Macfile format

A macfile is markdown wrapped in `<block>` tags. Everything inside a block is ordinary markdown.

```markdown
<macfile version="0.1">
  <meta>
    name: my-master-context
    default_budget_tokens: 4000
  </meta>

  <block id="agent.role" layer="agent" targets="*" load="always" priority="100">
  ## Role
  You are a senior pair-programmer. Be concise.
  </block>

  <block id="project.api-reference" layer="project" targets="repo,web"
         load="on_demand" src="./docs/api.md" tokens="6000" priority="40">
  Pull in only when working on API routes.
  </block>
</macfile>
```

The `<macfile>` wrapper and `<meta>` section are optional. `mac` finds blocks anywhere in the file.

### Layers

Each block belongs to exactly one layer, so private and shared context never mix.

| Layer | Contains | Share it? |
|---|---|---|
| `agent` | Copilot role, workflow, tone, tool habits | Private |
| `user` | Your profile and preferences | Private |
| `project` | Stack, conventions, constraints | Safe to commit |
| `env` | OS, runtime, model limits, tools | Depends on the machine |

### Block attributes

| Attribute | Required | Default | Meaning |
|---|---|---|---|
| `id` | yes | n/a | Unique, dotted, and must start with `<layer>.` (e.g. `project.rules`) |
| `layer` | yes | n/a | `agent`, `user`, `project` or `env` |
| `targets` | no | `*` | Comma-separated list of where the block may appear (see below) |
| `load` | no | `always` | `always`: inlined. `on_demand`: inlined only when requested and the budget allows. `reference`: never inlined, only pointed to |
| `priority` | no | `50` | 0 to 100. Lowest priorities are dropped first when over budget |
| `tokens` | no | estimated | Declared size, used only when the `src` file can't be read |
| `src` | no | n/a | Path to an external file, relative to the macfile. Its contents are appended to the block body |
| `overrides` | no | n/a | Replace the block with this id from an earlier file |
| `extends` | no | n/a | Append this block's body to the block with this id from an earlier file |

### Targets

Common targets are `web`, `repo`, `local`, and `*` (everywhere). A request for a specific tool also matches its general tag:

| Requested | Also matches blocks tagged |
|---|---|
| `claude-project`, `custom-gpt` | `web` |
| `copilot`, `claude-code` | `repo` |
| `ollama`, `lm-studio` | `local` |

The match goes one way: a block tagged only `claude-project` is **not** included when you ask for `web`.

## Commands

### `mac check FILE...`

Validates the files and prints a table of blocks (layer, load mode, priority, token estimate, targets). It also reports the total always-loaded tokens per target and warns if any exceeds the budget. Exits `1` on errors.

### `mac pack FILE... [options]`

Produces one payload for one target. Blocks are filtered by target, ordered by layer (`agent`, `user`, `project`, `env`) then priority (high first), trimmed to the budget, and written to stdout or a file.

| Option | Description |
|---|---|
| `-t`, `--target` | Target tag (default `web`) |
| `--layers a,b` | Only include these layers |
| `--budget N` | Token budget. `0` means unlimited. Defaults to `default_budget_tokens` from `<meta>` |
| `--include-on-demand` | Inline `on_demand` blocks if the budget allows |
| `--keep-tags` | Emit a round-trippable macfile instead of plain markdown |
| `--no-refs` | Omit the pointer list of blocks that weren't inlined |
| `--header` | Prepend a "generated" HTML comment |
| `-o`, `--output` | Write to a file instead of stdout |

Blocks that aren't inlined but have a `src` appear as a short pointer list at the end:

```markdown
## Reference material (load on demand)
- `project.api-reference` -> `./docs/api.md`: Pull in only when working on API routes.
```

### `mac split FILE... [options]`

Writes a set of files under `--out-dir` (default `dist/`).

| Preset | Output | Built from |
|---|---|---|
| `repo` | `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` | `project` blocks for `repo` (add `--include-agent` to include `agent` blocks) |
| `web` | `web/custom-instructions.md` | `user` blocks for `web` |
| | `web/project-instructions.md` | `agent` + `project` blocks for `web` |
| | `web/knowledge/<id>.md` | One file per `on_demand` / `reference` block, for upload as project knowledge |
| `local` | `local/system-prompt.md`, `local/Modelfile` | All layers for `local` (no pointer list, since local models can't open files) |
| `layers` | `layers/<layer>.md` | One file per layer, ignoring targets |
| `all` (default) | `repo` + `web` + `local` | |

| Option | Description |
|---|---|
| `--preset` | `repo`, `web`, `local`, `layers` or `all` |
| `--out-dir DIR` | Output folder (default `dist`) |
| `--budget N` | Applied to **each output file separately** |
| `--include-agent` | Put `agent` blocks into repo files (off by default to keep them private) |
| `--model NAME` | Base model for the generated `Modelfile` (default `llama3.1`) |
| `--dry-run` | Show what would be written without writing |

Generated markdown files start with a comment naming their source macfiles and asking you not to edit them.

### `mac unpack FILE... [options]`

The reverse of `split`: turns existing instruction files (`AGENTS.md`, `CLAUDE.md`, `copilot-instructions.md`, a `Modelfile`, pasted custom instructions) into a starter macfile. Review the result, then run `mac check` on it.

```bash
python mac.py unpack AGENTS.md CLAUDE.md -o my.macfile.md
python mac.py unpack custom-instructions.txt --layer user --targets web,local
python mac.py unpack Modelfile .github/copilot-instructions.md --name acme -o acme.macfile.md
```

| Option | Description |
|---|---|
| `--layer` | `auto` (default), or force every section into `agent`, `user`, `project` or `env` |
| `--split-level N` | Heading level to split at, 1 to 4 (default: auto, preferring `##`) |
| `--targets a,b` | Override targets for every block |
| `--name NAME` | `name` in the new macfile's `<meta>` |
| `-o`, `--output` | Write to a file instead of stdout |
| `--force` | Overwrite `--output` if it exists |

How it decides:

- **Splitting:** each file is cut at headings. Headings inside fenced code blocks are ignored. A preamble that is only a title line is dropped.
- **Layer (`auto`):** first by keywords in the heading ("Role", "Tone" give `agent`; "About me", "Preferences" give `user`; "Runtime", "Context window" give `env`). Otherwise by file name (`Modelfile` and `system-prompt` give `agent`; `custom-instructions` gives `user`). Anything left is `project`. "Environment variables" stays `project`.
- **IDs and priorities:** ids come from the heading (`agent.role`, `project.stack-2`). Priorities start at 100/90/80/70 for `agent`/`user`/`project`/`env` and step down by 5 in file order, so later sections are trimmed first.
- **Targets:** default per layer (`agent` `*`, `user` `web,local`, `project` `repo,web`, `env` `local`).
- **Duplicates:** sections with identical content are skipped, so unpacking `AGENTS.md` and `CLAUDE.md` together doesn't double them.
- **Generated files:** the "Generated by mac" header and the "Reference material" pointer list are dropped, so `split` then `unpack` returns the original content.
- **Modelfile:** the text inside `SYSTEM """..."""` is extracted.

Each section is printed to stderr with its layer and how it was chosen (`keyword`, `default` or `forced`). Check the `default` ones first, since they are guesses. A section that mixes personal preferences with project rules stays one block; split it by hand. Unpack always inlines text and never creates `src` pointers.

## Working with multiple files

Keep a global macfile for your `agent` and `user` blocks, and one per repo for `project` blocks. Pass them together; later files can adjust earlier ones.

```bash
python mac.py pack ~/global.macfile.md ./project.macfile.md -t web
```

```markdown
<!-- project.macfile.md -->
<block id="agent.role" layer="agent" overrides="agent.role">
## Role
You are a code reviewer for this repo. Be strict about tests.
</block>

<block id="user.profile.tz" layer="user" extends="user.profile">
- Timezone: EAT
</block>
```

- `overrides` replaces the earlier block entirely.
- `extends` appends to it. The extending block's own id and attributes are discarded.
- Reusing an id without either is an error.

## Budget trimming

When a payload exceeds the token budget:

1. Drop the lowest-priority non-`always` block (`on_demand`) and repeat.
2. If still over, drop the lowest-priority `always` block and repeat.

Ties go to the larger block. Dropped blocks are listed on stderr, and any with a `src` become pointers. Token counts are estimated as characters ÷ 4.

## Round-tripping

`pack --keep-tags` writes a valid macfile that `mac` can read again, so a packed file can later be split apart. Inlined blocks have their `src` contents copied into the body and the `src` attribute removed, so content is never duplicated on re-read.

`split` followed by `unpack` also round-trips: the generated files can be read back into a macfile with the same block content (layers and targets are re-guessed, so review them).

## Validation

`mac` reports **errors** (exit code 1, nothing is written) for:

- missing `id` or invalid `layer`, or an `id` that doesn't start with its layer
- invalid `load`, non-integer `tokens`, or `priority` outside 0 to 100
- duplicate ids, `extends` of an unknown id, or an unclosed `<block>`
- a `src` path that points outside the macfile's folder
- `unpack` input that contains a literal `</block`, or an existing `--output` without `--force`

and **warnings** (processing continues) for unknown targets, missing `src` files, empty blocks, `overrides` of an unknown id, always-loaded content over budget, and `unpack` blocks that change when re-parsed.

## Limitations

- Token counts are approximate. Replace `estimate_tokens()` with a real tokenizer such as `tiktoken` if you need accuracy.
- HTML comments (`<!-- -->`) are stripped before parsing, including inside block bodies.
- A literal `</block>` inside a block body breaks parsing.
- `src` files are read as UTF-8 text and must live inside the macfile's folder.
- `--budget` in `split` applies per output file, not across the whole set.
- `unpack` layer detection is keyword-based and only looks at headings.

## Files

| File | Purpose |
|---|---|
| `mac.py` | The compiler |
| `example.macfile.md` | A starter macfile covering all four layers |
