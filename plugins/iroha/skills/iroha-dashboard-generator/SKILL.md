---
name: iroha-dashboard-generator
description: Render Iroha roadmap.md, status.md and spec goal files as an issue-board HTML dashboard with active and recently implemented work. Use when user says "roadmap dashboard", "iroha roadmap", "iroha board", "iroha status", "what is open in iroha", "what is done in iroha".
---

# Iroha Dashboard Generator

Build one self-contained HTML board from `roadmap.md` and `status.md` on the active Iroha branch.

## Data rules

- Files come from `origin/<branch>` through `git show`, not from the working tree.
- Active branch: the remote branch with the latest merge commit. Ties go to `origin/HEAD`. Today this is `optimizations`. Pass `--branch NAME` to override.
- `roadmap.md` lists only open work. Each outcome row (A1, N5, S6, …) is one card.
  - **In progress**: the row changed in the last 14 days.
  - **Backlog**: no row change in the last 14 days.
  - Commits that rewrite more than 40% of rows are bulk edits. They do not count as activity.
  - 🔥 marks outcomes named by a SORA Nexus payment milestone.
- **Done recently**: statements added to the status.md "Current state" column in the last 30 days. They must still be present. Statements with open-work words (remain, pending, fail, requires, …) are skipped. One card per area per day.
- **Goals from specs** tab: tables in `specs/*_goals.md`, `specs/*_completion.md` and `specs/configuration_simplification.md` that have a "Status", "State" or "Current state" column. Each row is one card in a lane: Blocked, Open, In progress or Done. The lane comes from the first words of the status text. When the text has no clear status word, the card shows "status unclear" and the lane is a guess. Cards are sorted by the date of the last change to the row.
- Nothing in the files states a completion percentage. The board shows counts only.
- Each card shows its source: the file, the place in the file, and the commit that last changed it. Roadmap cards without a change in 8 weeks use `git blame` for the commit. Links go to GitHub when the origin is on GitHub.
- A notice at the top says that an AI agent made the report and that it can contain mistakes. Keep it.

## Workflow

1. Resolve the Iroha checkout. Use the current checkout when the current directory is inside `iroha/`. Otherwise walk up for the workspace `iroha/`. Do not read `iroha.dir` from `local.properties`.
   ```bash
   resolve_iroha_dir() {
     dir="$PWD"
     while [ "$dir" != "/" ]; do
       [ -f "$dir/roadmap.md" ] && [ -f "$dir/status.md" ] && { echo "$dir"; return 0; }
       [ -e "$dir/iroha/.git" ] && { echo "$dir/iroha"; return 0; }
       dir=$(dirname "$dir")
     done
     [ -e "$HOME/work/cbdc-android/iroha/.git" ] && { echo "$HOME/work/cbdc-android/iroha"; return 0; }
     echo "ERROR: iroha checkout not found up from $PWD" >&2; return 1
   }
   ```
2. Fetch, so the branch choice uses the latest merges:
   ```bash
   git -C "$IROHA_DIR" fetch origin --quiet
   ```
3. Render. It takes about 10 seconds.
   ```bash
   python3 <skill_dir>/scripts/render_dashboard.py "$IROHA_DIR" "$HOME/.cache/iroha-dashboard/dashboard.html"
   ```
   The script prints the branch, lane counts and `structure_warnings` as JSON.
4. If `structure_warnings` is not empty, read the changed part of the markdown. Update `EXPECTED_ROWS` or `SECTIONS` in the script when the change is legitimate. Edit the source clone of `sdi-plugins`, not the installed plugin copy. The page still renders and shows the warnings at the top.
5. Open the page for Human: `open "$HOME/.cache/iroha-dashboard/dashboard.html"`.
6. Report in chat: branch, lane counts and any warnings. Do not repeat the board content.

## Notes

- Output goes to `~/.cache/iroha-dashboard/`, outside every repository. Do not commit it.
- The installed plugin is a copy. After a change in the source clone of `sdi-plugins`, run `claude plugin marketplace update sdi-plugins` and `claude plugin update iroha@sdi-plugins`.
- The page has no network dependencies.
- Tune windows and thresholds with the constants at the top of the script.
