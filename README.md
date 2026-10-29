# SDI plugins

Personal Claude Code plugins for Iroha 3 and CBDC Android work.

## Plugins

| Plugin | Skills |
| --- | --- |
| `iroha` | `iroha-dashboard-generator`: issue-board HTML dashboard from `roadmap.md`, `status.md` and spec goal files |
| `plain-text` | Mod: a side pane that shows the last Agent reply again in simple English |

### Dashboard generator

The skill reads `roadmap.md`, `status.md` and the goal tables in `specs/` on the active Iroha branch.
It then builds one HTML page that shows the current progress as an issue board.
Roadmap items go to the Backlog, In progress and Done recently columns.
Each card links to its source row and to the last commit that changed it.

Ask Claude Code for "iroha board" or "roadmap dashboard" to run it.

> [!WARNING]
> The dashboard is not a source of truth.
> An LLM and simple sort rules make the page, and both can make mistakes.
> Use the page only as a starting point for deeper research.
> Check the source of a card before you act on it.

![Iroha 3 board](docs/iroha-dashboard.png)

### Plain text pane

The mod opens a `Plain text` pane at the start of each session.
After each long Agent reply, Haiku writes the reply again in simple English.
The rewrite uses the rules from the `plain-english` skill and the `plain-report` output style.
The pane also shows the clarity check from the `ask-jev` skill.
The `/plain-text` command opens the pane again.

The mod reads these files in `~/.claude`.
Without them, the pane shows less or shows the original reply:

- `skills/plain-english/SKILL.md` and its `references/` files
- `output-styles/plain-report.md`
- `skills/ask-jev/scripts/ask_jev.sh`

### Jev setup

Jev is a small review model on OpenRouter.
It answers multiple-choice questions about a text, for example "Is this text clear?".
The pane shows the Jev answers for each long Agent reply.
Without this setup, the pane shows `Jev: no answer` and the reason.

1. Make an API key at [openrouter.ai/keys](https://openrouter.ai/keys).
   Set a spend limit on the key.
   Each Jev check costs a fraction of a cent.
2. Add the key to `~/.zshrc`:

   ```bash
   export OPENROUTER_API_KEY=sk-or-...
   ```

   The mod reads the key from the environment first.
   If the key is not there, the mod reads this line in `~/.zshrc`.
   The mod does not start an interactive shell to get the key.
3. Make sure that `jq` and `curl` are installed:

   ```bash
   jq --version && curl --version
   ```

4. Save the helper script below as `~/.claude/skills/ask-jev/scripts/ask_jev.sh`.
   Then make it executable with `chmod +x`.

   ```bash
   #!/bin/bash
   set -u
   request=${1:?Usage: ask_jev.sh REQUEST.json}
   key=${OPENROUTER_API_KEY:-$(sed -nE "s/^export OPENROUTER_API_KEY=[\"']?([^\"' ]+).*/\1/p" ~/.zshrc 2>/dev/null | head -n 1)}
   set -eo pipefail
   : "${key:?OPENROUTER_API_KEY is not set}"
   payload=$(jq 'if (.state | type) == "string" and (.questions | length) > 0
       then .model = "~typesafe/jev-latest"
       else error("Request needs state text and questions") end' "$request")
   printf '%s' "$payload" |
     curl --silent --show-error --fail-with-body --max-time 60 \
       --header @<(printf 'Authorization: Bearer %s\n' "$key") \
       --header 'Content-Type: application/json' \
       --data-binary @- \
       https://openrouter.ai/api/alpha/decisions |
     jq .
   ```

5. Check the setup with one request:

   ```bash
   echo '{"state":"Work stopped.","questions":{"clarity":{"type":"choice","instructions":"Is this clear?","criteria":{"clear":"Clear","unclear":"Unclear"}}}}' |
     ~/.claude/skills/ask-jev/scripts/ask_jev.sh /dev/stdin
   ```

   The answer is JSON with an `answers` field.
   An HTTP 401 error means that the key is wrong.

## Install

Run from the root of a local clone of this repository:

```bash
claude plugin marketplace add .
claude plugin install iroha@sdi-plugins
claude plugin install plain-text@sdi-plugins
```

After a change in this repository, update the installed copy:

```bash
claude plugin marketplace update sdi-plugins
claude plugin update iroha@sdi-plugins
claude plugin update plain-text@sdi-plugins
```

## Add a plugin

1. Create `plugins/<name>/.claude-plugin/plugin.json` and `plugins/<name>/skills/<skill>/SKILL.md`.
2. Add the plugin to `.claude-plugin/marketplace.json`.
3. Run `claude plugin validate .` from the repository root.
