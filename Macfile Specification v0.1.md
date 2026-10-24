# Macfile Specification v0.1

*Status: Draft · Last updated: 2026-10-24 · Reference implementation: `mac.py` (commands `check`, `pack`, `split`, `unpack`)*

## 1. Introduction

A **Macfile** (Master Context File) is a single declarative text file that holds the context an AI agent needs before it starts work: who the agent is, who the user is, what the project requires, and what the runtime can do. A compiler reads one or more macfiles and produces payloads for a particular destination, such as a Claude Project, a Custom GPT, an `AGENTS.md` in a repository, or a local model's system prompt.

**Goals**

- Separate context by audience (agent, user, project, environment) so personal preferences never leak into shared repositories.
- Work with both web LLM applications and local inference runtimes.
- Scale up (pack into one payload) and down (split into per-platform files) from a single source of truth.
- Survive any tokenizer by using plain markdown inside simple tags.

**Non-goals**

- No executable logic, templating, or conditionals.
- No retrieval or embedding semantics.
- No attempt to standardise what any vendor does with the generated output.

## 2. Conventions

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as described in RFC 2119.

- **Compiler**: any program that reads macfiles and writes outputs.
- **Payload**: the text produced for one destination.
- **Target**: a tag naming a destination class, such as `web`, `repo` or `local`.
- **Source order**: the order in which blocks appear, across all input files in the order they were given.

## 3. File format

### 3.1 Encoding and naming

A macfile MUST be UTF-8 text. Either LF or CRLF line endings are allowed. The recommended extension is `.macfile.md`, so editors render the markdown. No media type is registered.

### 3.2 Structure

```
<macfile version="0.1">
  <meta> ... </meta>
  <block ...> ... </block>
</macfile>
```

The `<macfile>` wrapper and the `<meta>` element are optional. A compiler MUST find `<block>` elements anywhere in the text and MUST ignore everything outside `<meta>` and `<block>`, so free-form notes may sit between blocks. Blocks MUST NOT be nested.

### 3.3 Comments

HTML comments (`<!-- ... -->`) MUST be removed before parsing, wherever they occur, including inside block bodies. A literal HTML comment therefore cannot appear in compiled output in v0.1.

### 3.4 Meta

`<meta>` contains lines of the form `key: value`. Lines without a colon are ignored. Only the first `<meta>` in a file is read. Keys are case-sensitive. Unknown keys MUST be preserved by compilers that re-emit meta.

| Key | Meaning |
| --- | --- |
| `name` | Human-readable label |
| `owner` | Free text |
| `updated` | ISO date, informational |
| `default_budget_tokens` | Integer. Budget used when none is given explicitly. 0 or absent means unlimited. |

When several files are loaded, later files override earlier ones key by key.

### 3.5 Block syntax

A block is `<block` followed by attributes, `>`, a body, and `</block>`.

- Attributes have the form `name="value"`. Names consist of letters, digits, `_`, `.` and `-`. Values MUST be in double quotes and MUST NOT contain a double quote; there is no escape mechanism. Attribute order is not significant.
- Compilers MUST ignore unknown attributes when interpreting a block, and SHOULD preserve them when re-emitting tagged output (section 8.7).
- The **body** is the text between the tags, after removing the leading whitespace common to all lines and trimming blank lines at both ends. It is ordinary markdown and MUST NOT contain the character sequence `</block`.
- An opening `<block` that does not form a complete, well-quoted element is a malformed block and MUST be reported as an error.

## 4. Layers

Every block belongs to exactly one layer.

| Layer | Rank | Contains | Changes when |
| --- | --- | --- | --- |
| `agent` | 0 | Role, workflow, tone, tool-use habits of the copilot | You change copilots or working style |
| `user` | 1 | Profile, preferences, expertise, language | You change |
| `project` | 2 | Stack, conventions, constraints, definitions of done | The project changes |
| `env` | 3 | OS, runtime, model limits, available tools | The machine or model changes |

**Privacy rule.** Only `project` blocks are intended for version control. A compiler writing repository outputs MUST include only `project` blocks by default, MUST require an explicit option before including `agent` blocks, and SHOULD NOT write `user` blocks to repository outputs at all.

## 5. Block attributes

| Attribute | Required | Default | Meaning |
| --- | --- | --- | --- |
| `id` | yes | — | Unique identifier, `<layer>.<name>` |
| `layer` | yes | — | One of `agent`, `user`, `project`, `env` |
| `targets` | no | `*` | Comma-separated destinations where the block may appear |
| `load` | no | `always` | `always`, `on_demand` or `reference` |
| `priority` | no | `50` | Integer 0 to 100; higher is kept longer under a budget |
| `tokens` | no | — | Author's size estimate |
| `src` | no | — | Relative path of an external file holding more content |
| `extends` | no | — | Id of an earlier block to append to (section 6) |
| `overrides` | no | — | Id of an earlier block to replace (section 6) |

### 5.1 id

The id MUST start with the block's layer followed by a dot, for example `project.rules`. Ids MUST be unique after composition (section 6). The part after the dot SHOULD use lowercase letters, digits and `-`.

### 5.2 targets

A comma-separated list. `*` means every destination. The vocabulary is:

- Generic targets: `web`, `repo`, `local`.
- Specific targets, each with a generic alias: `claude-project` and `custom-gpt` (alias `web`); `copilot` and `claude-code` (alias `repo`); `ollama` and `lm-studio` (alias `local`).

A block **matches** a requested target T when its list contains `*`, contains T, or contains the generic alias of T. Matching is one-directional: a request for `ollama` matches blocks tagged `local`, but a request for `local` does not match blocks tagged only `ollama`. When no target is requested, every block matches. Compilers SHOULD warn about targets outside the vocabulary but MUST NOT reject them.

### 5.3 load

- `always`: inlined whenever the block matches the target and survives the budget.
- `on_demand`: inlined only when the caller opts in and the budget allows. Otherwise it is listed as a pointer if it has a `src`, and omitted if it does not.
- `reference`: never inlined into a payload. Listed as a pointer if it has a `src`. Output profiles MAY write it as a separate knowledge file (section 9).

### 5.4 priority

Used only for budget trimming (section 8.4) and ordering (section 8.5). Values outside 0 to 100 are errors.

### 5.5 tokens

An estimate supplied by the author. A compiler uses it only when `src` is declared but the file is unavailable. Otherwise the compiler measures the actual text.

### 5.6 src

`src` is a path resolved against the directory of the macfile that contains the block.

- The resolved path MUST stay inside that directory. Absolute paths and paths that escape it with `..` are errors.
- A missing file is a warning, not an error.
- When the file exists, its content (UTF-8, trimmed) is appended to the body, separated by a blank line.

## 6. Composition

A compiler MAY accept several macfiles, typically a global file followed by a project file. They are processed in the order given, and blocks are resolved one at a time:

1. **overrides.** The new block replaces the block whose id matches `overrides`, keeping that block's position in source order. All attributes come from the new block. If no such block exists yet, the compiler warns and adds the new block normally.
2. **extends.** The body (and any `src` content) of the new block is appended, separated by a blank line, to the block whose id matches `extends`. The extending block still needs a valid id and layer, but its other attributes are ignored and it is not added to the block list. Extending an unknown id is an error.
3. **Otherwise** the block is appended. A duplicate id without `overrides` is an error.

## 7. Validation

**Errors.** A compiler MUST NOT write output while any error stands:

- unreadable input file
- malformed block
- missing `id` or `layer`; `layer` not in the list; `id` not prefixed by its layer
- `load` not in the list
- `priority` or `tokens` not an integer; `priority` outside 0 to 100
- duplicate id (section 6)
- `extends` naming an unknown block
- `src` escaping the macfile directory

**Warnings.** A compiler SHOULD report these and continue:

- target outside the vocabulary, or a macfile version the compiler does not support
- `src` file not found
- block with neither body nor `src`
- `overrides` naming an unknown block
- always-loaded blocks for a target exceed the budget
- blocks dropped to fit a budget

## 8. Compilation model

### 8.1 Selection

Given a requested target, an optional set of layers, and an option for on-demand blocks, the compiler selects the blocks that match the target and belong to the requested layers.

### 8.2 Inline set and reference set

Selected blocks with `load="always"` form the inline set, as do `on_demand` blocks when the caller opts in. All other selected blocks form the reference set. Blocks dropped by the budget (section 8.4) move to the reference set. A block in the reference set appears in output only as a pointer, and only if it has a `src`.

### 8.3 Token estimation

The RECOMMENDED estimate is the number of characters divided by 4, rounded up. Implementations MAY use a real tokenizer. Budgets are advisory approximations, not guarantees about any model's tokenizer.

### 8.4 Budget

The budget counts only the inline set; pointer lists and headers are not counted. A budget of 0 or none means unlimited. While the inline total exceeds the budget:

1. If any inline block is not `always`, choose among those blocks. Otherwise choose among all inline blocks.
2. Remove the block with the lowest priority. Among equal priorities, remove the one with more tokens first. Further ties are implementation-defined.

This means a single oversized `always` block can be dropped entirely if nothing else remains to remove.

### 8.5 Ordering

Inline blocks are ordered by layer rank (`agent`, `user`, `project`, `env`), then by priority descending, then by source order.

### 8.6 Plain rendering

The default payload is plain markdown: an optional header comment, then each inline block's text (body plus `src` content), separated by blank lines, ending in a single newline. Tags and attributes do not appear.

If the reference set is non-empty and pointers are enabled, a final section is added with the exact heading `## Reference material (load on demand)`. Each entry has the form:

```
- `<id>` -> `<src>`: <hint>
```

The hint is the first non-empty line of the block's body with leading `#` characters removed, and is omitted if the body is empty.

### 8.7 Tagged rendering

Tagged output is itself a valid macfile, so it can be loaded again.

- It has a `<macfile>` root and a `<meta>` section containing the input meta plus `target: <T>` when a target was requested.
- Each inline block is written with attributes `id`, `layer`, `targets`, `load`, `priority` (and `tokens` if declared). Its `src` is dropped and the file content is already part of the body, so loading the output never duplicates content.
- Each reference-set block is written with its original body and its `src` kept.
- Bodies are written at column 0 with no indentation. Unknown attributes are written after the known ones, in their original order.

## 9. Output profiles

A profile maps blocks to files. The reference implementation defines these. A budget, if given, applies to each output file independently.

| Profile | File | Layers | Target | Notes |
| --- | --- | --- | --- | --- |
| `repo` | `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` | `project` (plus `agent` only on request) | `repo` | Identical content in all three. Pointer list included. |
| `web` | `web/custom-instructions.md` | `user` | `web` | Paste into a profile or custom-instructions field |
| `web` | `web/project-instructions.md` | `agent`, `project` | `web` | Paste into Claude Projects or a Custom GPT |
| `web` | `web/knowledge/<id>.md` | all | `web` | One file per block whose load is not `always`, containing its body and `src` content. Characters outside letters, digits, `.`, `_` and `-` in the id become `_`. |
| `local` | `local/system-prompt.md` | all | `local` | No pointer list |
| `local` | `local/Modelfile` | all | `local` | `FROM <model>` and `SYSTEM` with the payload in triple double quotes. Any triple double quote in the text is replaced by three single quotes. |
| `layers` | `layers/<layer>.md` | one each | any | Includes `on_demand` blocks. A plain partition of the source. |

Markdown files written by a compiler SHOULD begin with a comment of the form `<!-- Generated by mac from <sources>. Do not edit; rebuild instead. -->`. Files are never to be edited by hand; the macfile is the single source of truth.

## 10. Importing existing files (informative)

A compiler MAY provide an importer that creates a starter macfile from existing instruction files. The reference implementation behaves as follows.

- **Splitting.** Sections are cut at headings of a chosen level. By default it prefers `##`, then `#`, `###`, `####`. Headings inside fenced code blocks are ignored. Text before the first cut becomes an `overview` block, unless it consists only of heading lines.
- **Layer.** The layer is guessed from keywords in the heading only. Role, persona, tone and behavior suggest `agent`. About me, profile and preferences suggest `user`. Runtime, hardware, context window and "environment" (but not "environment variables") suggest `env`. When no keyword matches, the file name decides: `Modelfile` and `system-prompt` files are `agent`, `custom-instructions` and profile files are `user`, and everything else is `project`.
- **Targets** default per layer: `agent` is `*`, `user` is `web,local`, `project` is `repo,web`, `env` is `local`.
- **Priority** starts at 100, 90, 80, 70 for `agent`, `user`, `project`, `env` and decreases by 5 for each further block in the same layer, to a minimum of 10.
- **Ids** are `<layer>.<slug of heading>`, with `-2`, `-3` and so on added to avoid collisions.
- **Duplicates.** A section whose text is identical (ignoring whitespace) to an earlier section is skipped.
- **Special inputs.** For a `Modelfile` only the `SYSTEM` text is read. Files carrying the generated header have that header and any pointer section removed.
- **Verification.** The importer re-parses its own output and warns if any block would change.

Importer output is a starting point. The author is expected to review layers and targets before relying on it.

## 11. Security and privacy considerations

- A macfile is declarative. It contains no executable logic, and compilers MUST NOT execute anything found in it.
- `src` is confined to the macfile's directory so that a shared macfile cannot pull in unrelated files such as credentials.
- Macfiles SHOULD NOT contain secrets. Payloads are sent to third-party services and may be stored.
- Instruction text is influential. A macfile or an `extends` / `overrides` block obtained from an untrusted source can change an agent's behavior, so such files should be reviewed like code.
- Keeping `user` and `agent` blocks out of repository outputs (section 4) is a privacy control, not only a convenience.

## 12. Conformance

A **conforming macfile** follows sections 3 to 6 and has no validation errors.

A **conforming compiler** parses macfiles as in section 3, applies the composition rules of section 6, reports the errors of section 7, and implements selection, budgeting and ordering as in sections 8.1 to 8.5. Rendering formats (sections 8.6 and 8.7) and output profiles (section 9) are RECOMMENDED but MAY be replaced by others, provided the privacy rule in section 4 is kept.

## 13. Known gaps and open questions

The reference implementation has no known deviations from this text. It is covered by a 103-test suite (test\_mac.py).

Open questions for v0.2:

- An escape mechanism so a body can contain `</block` and literal comments.
- Per-target or per-profile budgets in `<meta>`.
- A pluggable tokenizer.
- Glob patterns in `src` and a way to say "include this file's headings only".
- A section-level split in the importer for long flat rule lists.
- Machine-readable validation output (JSON).
- Registering a media type.

## Appendix A. Informal grammar

```
macfile  = [ "<macfile" { attr } ">" ] [ meta ] { block | text } [ "</macfile>" ]
meta     = "<meta>" { key ":" value NL } "</meta>"
block    = "<block" { attr } ">" body "</block>"
attr     = name "=" '"' value '"'      ; value has no double quote
name     = 1*( letter / digit / "_" / "." / "-" )
body     = markdown text not containing "</block"
```

## Appendix B. Example

```
<macfile version="0.1">
  <meta>
    name: my-master-context
    default_budget_tokens: 4000
  </meta>

  <block id="agent.role" layer="agent" targets="*" priority="100">
  ## Role
  You are a senior pair-programmer. Be concise.
  </block>

  <block id="project.rules" layer="project" targets="repo,web" priority="85">
  ## Rules
  - All endpoints need tests
  </block>

  <block id="project.api-reference" layer="project" targets="repo,web"
         load="on_demand" src="./docs/api.md" tokens="6000" priority="40">
  Pull in only when working on API routes.
  </block>
</macfile>
```

A project file can then refine a global block without copying it:

```
<block id="project.rules" layer="project" overrides="project.rules">
## Rules
- All endpoints need tests and an OpenAPI example
</block>
```

## Appendix C. Reference command line

| Command | Purpose |
| --- | --- |
| `mac check FILE...` | Validate, print a block table and per-target always-loaded totals |
| `mac pack FILE... -t TARGET [--budget N] [--layers L] [--include-on-demand] [--keep-tags] [--no-refs] [--header] [-o OUT]` | Produce one payload |
| `mac split FILE... [--preset repo\|web\|local\|layers\|all] [--out-dir DIR] [--include-agent] [--model M] [--dry-run]` | Write per-profile files |
| `mac unpack FILE... [--layer L] [--split-level N] [--targets T] [--name N] [-o OUT] [--force]` | Create a starter macfile |

Exit status is 0 on success and 1 when validation errors stand. Argument errors exit with 2. Diagnostics go to standard error and payloads to standard output.
