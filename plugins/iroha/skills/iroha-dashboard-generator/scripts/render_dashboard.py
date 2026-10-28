#!/usr/bin/env python3
"""Render Iroha roadmap.md and status.md as an issue-board style HTML dashboard.

Usage: render_dashboard.py <iroha_dir> <output_html> [--branch NAME]

Both files are read from origin/<branch>, not from the working tree. Without
--branch, the active branch is the remote branch with the latest merge commit;
ties go to origin/HEAD, then to the alphabetically first name.

Board lanes:
  Backlog       roadmap outcomes not edited in the active window.
  In progress   roadmap outcomes whose row changed in the active window.
  Implemented   statements added to the status.md "Current state" column in
                the done window and still present, without open-work words.
"""

import argparse
import difflib
import html
import json
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ACTIVE_DAYS = 14
DONE_DAYS = 30
CHART_WEEKS = 8
SWEEP_SHARE = 0.4
SIMILARITY = 0.6

SECTIONS = {
    "Architecture and build": ("Architecture", "#5b7cfa"),
    "Ledger, consensus and configuration": ("Ledger", "#e0803a"),
    "SDK, native and physical delivery": ("SDK", "#2ea37a"),
    "Cryptography and VM": ("Cryptography", "#b45fd0"),
    "Services and deployment": ("Services", "#d1477a"),
    "Candidate sealing and release": ("Release", "#7d8798"),
}
EXPECTED_ROWS = {"Architecture and build": 8, "Ledger, consensus and configuration": 14,
                 "SDK, native and physical delivery": 12, "Cryptography and VM": 9,
                 "Services and deployment": 13, "Candidate sealing and release": 3}
OPEN_WORDS = re.compile(r"\b(remain\w*|pending|unqualified|not|fail\w*|exposed|open|requires?|required)\b", re.I)
SENTENCE = re.compile(r"(?<=[.;])\s+(?=[A-Z\[`])")
ROW = re.compile(r"^\|\s*([A-Z]\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$", re.M)
NEXUS_ROW = re.compile(r"^\|\s*(\d+)\s+—\s+([^|;]+)", re.M)
GOAL_FILES = re.compile(r"^specs/[^/]*(_goals|_completion|configuration_simplification)\.md$")
GOAL_STATUS_HEADERS = ("status", "state", "current state")
GOAL_TITLE_HEADERS = ("goal", "outcome", "outcome and owner", "owner / outcome", "milestone")
GOAL_LANES = ("Blocked", "Open", "In progress", "Done")
GOAL_COLORS = ("#5b7cfa", "#e0803a", "#2ea37a", "#b45fd0", "#d1477a", "#c9a227", "#3aa6c9", "#7d8798", "#8f6b4a")
OPEN_HINT = re.compile(r"\b(open|remain\w*|pending|unfinished|missing|required|not|queued|unqualified|unresolved)\b", re.I)
DONE_HINT = re.compile(r"\b(complete\w*|implemented|passed|passing|delivered|recorded|removed|done)\b", re.I)


def git(repo, *args):
    """Run git in the repo and return stdout."""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout


def detect_branch(repo):
    """Pick the remote branch with the latest merge; break ties by origin/HEAD, then name."""
    head = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
    best = []
    for ref in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").split():
        if ref in ("origin", "origin/HEAD"):
            continue
        ct = git(repo, "log", "-1", "--merges", "--format=%ct", ref).strip()
        if ct:
            best.append((int(ct), ref))
    if not best:
        return head or "origin/main"
    top = max(t for t, _ in best)
    tied = sorted(r for t, r in best if t == top)
    return head if head in tied else tied[0]


def web_url(repo):
    """Return the https URL of a GitHub origin, or an empty string."""
    url = git(repo, "remote", "get-url", "origin").strip()
    m = re.match(r"^(?:git@github\.com:|https://github\.com/)(.+?)(?:\.git)?$", url)
    return f"https://github.com/{m.group(1)}" if m else ""


def blame_rows(repo, ref, path):
    """Map each outcome ID in the file to the (date, sha) of the commit that last changed its row."""
    out, sha, day = {}, None, None
    for line in git(repo, "blame", "--line-porcelain", ref, "--", path).splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            sha = line[:40]
        elif line.startswith("committer-time "):
            day = date.fromtimestamp(int(line.split()[1])).isoformat()
        elif line.startswith("\t"):
            m = ROW.match(line[1:])
            if m:
                out[m.group(1)] = (day, sha)
    return out


def blame_lines(repo, ref, path):
    """Map each line text of the file to the (date, sha) of the commit that last changed it."""
    out, sha, day = {}, None, None
    for line in git(repo, "blame", "--line-porcelain", ref, "--", path).splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            sha = line[:40]
        elif line.startswith("committer-time "):
            day = date.fromtimestamp(int(line.split()[1])).isoformat()
        elif line.startswith("\t"):
            out[line[1:]] = (day, sha)
    return out


def split_cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def classify_goal(status):
    """Sort a goal status text into one of GOAL_LANES; return (lane, is_guess)."""
    text = plain(status).strip()
    low = text.lower()
    if re.search(r"\bblocked\b", low):
        return "Blocked", False
    if re.match(r"(open|pending|not started|todo|unfinished|missing|queued)", low):
        return "Open", False
    if re.match(r"(in progress|active|partial)", low):
        return "In progress", False
    if re.match(r"(complete|done|closed|implemented|repaired and passing|recorded)", low):
        return ("In progress", False) if OPEN_HINT.search(low) else ("Done", False)
    if OPEN_HINT.search(low):
        return "In progress", True
    if DONE_HINT.search(low):
        return "Done", True
    return "In progress", True


def goal_rows(repo, ref):
    """Return goal rows from spec tracker tables that have a status column."""
    rows = []
    paths = [p for p in git(repo, "ls-tree", "--name-only", ref, "specs/").split() if GOAL_FILES.match(p)]
    for path in sorted(paths):
        text = show(repo, ref, path)
        lines = text.splitlines()
        heading = next((l[2:].strip() for l in lines if l.startswith("# ")), path)
        blame = None
        for i, line in enumerate(lines[:-1]):
            if not (line.startswith("|") and re.match(r"^\|\s*:?-{3}", lines[i + 1])):
                continue
            header = [h.lower() for h in split_cells(line)]
            status_col = next((k for k, h in enumerate(header) if h in GOAL_STATUS_HEADERS), None)
            if status_col is None:
                continue
            title_col = next((k for k, h in enumerate(header) if h in GOAL_TITLE_HEADERS and k), None)
            crit_col = next((k for k, h in enumerate(header)
                             if re.search(r"criteri|acceptance|evidence|gate", h)), None)
            blame = blame or blame_lines(repo, ref, path)
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                cells = split_cells(row)
                if len(cells) <= status_col:
                    continue
                first = plain(cells[0])
                key, rest = (re.split(r" — |: ", first, maxsplit=1) + [""])[:2]
                if len(key) > 12:
                    key, rest = first, ""
                if title_col is not None and title_col < len(cells):
                    title = plain(cells[title_col])
                elif rest:
                    title = rest
                else:
                    key, title = ("", first) if len(first) > 12 else (first, first)
                if title_col is None and not rest and len(first) <= 12:
                    title = short(cells[crit_col], 80) if crit_col is not None and crit_col < len(cells) else first
                status = cells[status_col]
                lane, guess = classify_goal(status)
                day, sha = blame.get(row, ("unknown", ""))
                rows.append({
                    "path": path, "tracker": heading, "key": key, "title": title, "status": plain(status),
                    "criteria": plain(cells[crit_col]) if crit_col is not None and crit_col < len(cells) else "",
                    "lane": lane, "guess": guess, "day": day, "sha": sha,
                    "status_header": split_cells(line)[status_col],
                })
    return rows


def goal_card(row, color, source, today):
    key = f'<span class="tag">{html.escape(row["key"])}</span>' if row["key"] else ""
    guess = '<span class="guess" title="The status text has no clear status word">status unclear</span>' \
        if row["guess"] else ""
    detail = html.escape(f'Status: {row["status"]}' + (f'\n\nCompletion criteria: {row["criteria"]}'
                                                      if row["criteria"] else ""))
    search = html.escape(f'{row["key"]} {row["title"]} {row["tracker"]} {row["status"]} {row["criteria"]}'.lower())
    place = (f'table row \u201c{short(row["key"] or row["title"], 40)}\u201d, '
             f'column \u201c{html.escape(row["status_header"])}\u201d')
    return (f'<article class="card" style="--c:{color}" data-board="goals" data-area="{html.escape(row["tracker"])}" '
            f'data-search="{search}" data-title="{html.escape((row["key"] + " · " if row["key"] else "") + row["title"])}" '
            f'data-detail="{detail}">'
            f'<div class="row">{key}<span class="area">{html.escape(row["tracker"])}</span>{guess}</div>'
            f'<h4>{html.escape(short(row["title"], 90))}</h4>'
            f'<p class="status">{html.escape(short(row["status"], 110))}</p>'
            f'<div class="row foot"><span class="ago">changed {days_ago(row["day"], today) if row["day"] != "unknown" else "unknown"}</span></div>'
            f'{source(row["path"], place, row["day"], row["sha"])}</article>')


class Source:
    """Render the source line of a card: the file, the place in it and the commit."""

    def __init__(self, repo, ref):
        self.web = web_url(repo)
        self.repo = repo
        self.ref = ref

    def __call__(self, path, place, day, sha):
        if self.web:
            file_link = f'<a href="{self.web}/blob/{sha}/{path}" target="_blank">{path}</a>'
            commit = f'<a href="{self.web}/commit/{sha}" target="_blank">commit {sha[:10]}</a>'
        else:
            file_link = f'<a href="{html.escape((self.repo / path).as_uri())}">{path}</a>'
            commit = f"commit {sha[:10]}"
        return (f'<div class="src">Source: {file_link}, {html.escape(place)}<br>'
                f'{commit} on {self.ref.removeprefix("origin/")}, {day}</div>')


def history(repo, ref, path, since_days):
    """Return (sha, date) for first-parent commits touching path, oldest first."""
    out = git(repo, "log", "--first-parent", f"--since={since_days}.days", "--format=%H %cs", ref, "--", path)
    return [tuple(line.split()) for line in reversed(out.splitlines())]


def show(repo, sha, path):
    return git(repo, "show", f"{sha}:{path}")


def roadmap_rows(text):
    """Map outcome ID to (outcome, owner, criteria, section)."""
    rows, section = {}, ""
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        m = ROW.match(line)
        if m and m.group(1) != "ID":
            rows[m.group(1)] = (m.group(2), m.group(3), m.group(4), section)
    return rows


def status_areas(text):
    """Map status area name to (current state, remaining qualification)."""
    areas = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if line.startswith("|") and len(cells) == 3 and cells[0] not in ("Area", "---") \
                and not cells[0].startswith("-"):
            areas[cells[0]] = (cells[1], cells[2])
    return areas


def priorities(text):
    """Return the numbered items of the Current priorities section."""
    block = text.split("## Current priorities", 1)[-1].split("\n## ", 1)[0].split("\n### ", 1)[0]
    items, cur = [], None
    for line in block.splitlines():
        m = re.match(r"^\d+\.\s+(.*)", line)
        if m:
            cur = [m.group(1)]
            items.append(cur)
        elif cur is not None and line.startswith(" "):
            cur.append(line.strip())
        elif not line.strip():
            cur = None
    return [" ".join(i) for i in items]


def priority_ids(text):
    """Return roadmap IDs named by the SORA Nexus payment milestones."""
    ids = set()
    for m in NEXUS_ROW.finditer(text):
        ids.update(re.findall(r"\b([ANSCPR]\d{1,2})\b", m.group(2)))
    return ids


def risks(text):
    """Return (lead, first sentence) for each Remaining production risks bullet."""
    block = text.split("## Remaining production risks", 1)[-1]
    out = []
    for m in re.finditer(r"^- \*\*([^*]+?):?\*\*:?\s*(.+?)(?=^- |\n\n|\Z)", block, re.M | re.S):
        out.append((m.group(1), plain(SENTENCE.split(" ".join(m.group(2).split()))[0])))
    return out


def plain(text):
    """Strip inline markdown."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"[`*]|<(https?://[^>]+)>", lambda m: m.group(1) or "", text)


def short(text, limit):
    text = plain(text)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def is_new(sentence, old):
    return not old or max(difflib.SequenceMatcher(None, sentence, o).ratio() for o in old) < SIMILARITY


def week_index(day, today):
    return (today - date.fromisoformat(day)).days // 7


def roadmap_activity(repo, ref):
    """Return per-ID edit dates and removed IDs, skipping sweep commits."""
    edits, removed, prev = {}, [], None
    for sha, day in history(repo, ref, "roadmap.md", CHART_WEEKS * 7):
        cur = roadmap_rows(show(repo, sha, "roadmap.md"))
        if prev is not None:
            changed = [k for k in cur if k in prev and prev[k][2] != cur[k][2]] + [k for k in cur if k not in prev]
            gone = [k for k in prev if k not in cur]
            if changed and len(changed) <= SWEEP_SHARE * len(cur):
                for k in changed:
                    edits.setdefault(k, []).append((day, sha))
            removed += [(day, k, prev[k], sha) for k in gone]
        prev = cur
    return edits, removed


def implemented_events(repo, ref, final):
    """Return new Current-state statements still present now, with the date they were added."""
    events, prev = [], None
    alive = {(a, s) for a, (state, _) in final.items() for s in SENTENCE.split(state)}
    for sha, day in history(repo, ref, "status.md", CHART_WEEKS * 7):
        cur = status_areas(show(repo, sha, "status.md"))
        if prev is not None:
            for area, (state, _) in cur.items():
                old = SENTENCE.split(prev[area][0]) if area in prev else []
                for s in SENTENCE.split(state):
                    if (area, s) in alive and not OPEN_WORDS.search(s) and is_new(s, old):
                        events.append((day, area, s, sha))
        prev = cur
    latest = {}
    for day, area, s, sha in events:
        latest[(area, s)] = (day, sha)
    return latest


def sparkline(days, today, color):
    """Return a small SVG bar strip of weekly edits, newest on the right."""
    counts = [0] * CHART_WEEKS
    for d, _ in days:
        w = week_index(d, today)
        if w < CHART_WEEKS:
            counts[w] += 1
    peak = max(counts) or 1
    bars = "".join(
        f'<rect x="{(CHART_WEEKS - 1 - i) * 7}" y="{14 - max(2, 14 * c / peak) if c else 13}" width="5" '
        f'height="{max(2, 14 * c / peak) if c else 1}" fill="{color if c else "var(--line)"}" rx="1"/>'
        for i, c in enumerate(counts))
    return f'<svg class="spark" viewBox="0 0 {CHART_WEEKS * 7} 14" aria-label="changes per week">{bars}</svg>'


def days_ago(day, today):
    n = (today - date.fromisoformat(day)).days
    return "today" if n <= 0 else "1 day ago" if n == 1 else f"{n} days ago"


def outcome_card(key, row, edits, today, hot, source):
    outcome, owner, criteria, section = row
    name, color = SECTIONS.get(section, (section, "#888"))
    owners = "".join(f"<em>{html.escape(o.strip())}</em>" for o in owner.split("/")[:3])
    last = edits[-1][0] if edits else None
    meta = (f'<span class="ago">changed {days_ago(last, today)}</span>' if last
            else f'<span class="ago">not changed in {CHART_WEEKS} weeks</span>')
    flame = '<span class="flame" title="The SORA Nexus payment plan needs this item">🔥</span>' if hot else ""
    detail = html.escape(plain(criteria))
    search = html.escape(f"{key} {outcome} {owner} {name} {plain(criteria)}".lower())
    return (f'<article class="card" style="--c:{color}" data-board="roadmap" data-search="{search}" data-area="{html.escape(name)}" '
            f'data-title="{key} · {html.escape(outcome)}" data-detail="{detail}">'
            f'<div class="row"><span class="tag">{key}</span>{flame}<span class="area">{html.escape(name)}</span></div>'
            f'<h4>{html.escape(outcome)}</h4><div class="owners">{owners}</div>'
            f'<div class="row foot">{sparkline(edits, today, color)}{meta}</div>{source}</article>')


def done_card(area, day, sentences, today, source):
    lead = short(sentences[0], 90)
    more = f'<span class="more">+{len(sentences) - 1} more</span>' if len(sentences) > 1 else ""
    detail = html.escape(" ".join(plain(s) for s in sentences))
    search = html.escape(f"{area} {detail}".lower())
    return (f'<article class="card done" data-board="roadmap" data-search="{search}" data-area="status" '
            f'data-title="{html.escape(area)} · {day}" data-detail="{detail}">'
            f'<div class="row"><span class="check">✓</span><span class="area">{html.escape(area)}</span>{more}</div>'
            f'<p>{html.escape(lead)}</p><div class="row foot"><span class="ago">added {days_ago(day, today)}</span></div>'
            f'{source}</article>')


def build(repo, ref, today):
    """Collect board data and return (html, summary)."""
    roadmap_text = show(repo, ref, "roadmap.md")
    status_text = show(repo, ref, "status.md")
    rows = roadmap_rows(roadmap_text)
    areas = status_areas(status_text)
    hot = priority_ids(roadmap_text)
    edits, removed = roadmap_activity(repo, ref)
    implemented = implemented_events(repo, ref, areas)
    cutoff_active = (today - timedelta(days=ACTIVE_DAYS)).isoformat()
    cutoff_done = (today - timedelta(days=DONE_DAYS)).isoformat()
    active = {k for k in rows if edits.get(k) and edits[k][-1][0] >= cutoff_active}
    blame = blame_rows(repo, ref, "roadmap.md")
    source = Source(repo, ref)

    def outcome_source(k):
        day, sha = edits[k][-1] if edits.get(k) else blame.get(k, ("unknown", ""))
        return source("roadmap.md", f"row {k} in section \u201c{rows[k][3]}\u201d", day, sha)

    warnings = []
    for section, count in EXPECTED_ROWS.items():
        found = sum(1 for r in rows.values() if r[3] == section)
        if found != count:
            warnings.append(f"roadmap.md section '{section}' has {found} items, but the script expects {count}. "
                        f"The section was renamed or changed. Update EXPECTED_ROWS in the script.")
    if len(active) > 25:
        warnings.append(f"{len(active)} roadmap items are in progress. A large edit of roadmap.md can cause this. "
                        f"Check the item changes in git history.")

    order = list(rows)
    by_recency = sorted(active, key=lambda k: (edits[k][-1][0], k in hot), reverse=True)
    backlog = sorted((k for k in rows if k not in active), key=lambda k: (k not in hot, order.index(k)))

    grouped = {}
    for (area, s), (day, sha) in implemented.items():
        if day >= cutoff_done:
            entry = grouped.setdefault((day, area), [[], sha])
            entry[0].append(s)
    done_cards = [done_card(area, day, ss, today,
                            source("status.md", f"table \u201cCurrent implementation\u201d, row \u201c{area}\u201d, "
                                   f"column \u201cCurrent state\u201d", day, sha))
                  for (day, area), (ss, sha) in sorted(grouped.items(), reverse=True)]
    done_cards += [done_card(f"Roadmap {k}: {r[0]}", day, ["Outcome removed from the roadmap."], today,
                             source("roadmap.md", f"row {k} was deleted", day, sha))
                   for day, k, r, sha in removed if day >= cutoff_done and k not in rows]

    prio_chips = "".join(
        f'<li title="{html.escape(plain(p))}"><b>{i}</b>{html.escape(short(re.sub(r"\s+below\b", "", re.split(r"[,;]", p)[0]), 70))}</li>'
        for i, p in enumerate(priorities(roadmap_text), 1))
    risk_chips = "".join(f'<span class="chip" title="{html.escape(t)}">⚠ {html.escape(l)}</span>'
                         for l, t in risks(status_text))
    filters = "".join(f'<button class="chip" data-board="roadmap" data-f="{n}" style="--c:{c}">{n}</button>'
                      for n, c in SECTIONS.values())
    goals = goal_rows(repo, ref)
    trackers = list(dict.fromkeys(g["tracker"] for g in goals))
    tracker_color = {t: GOAL_COLORS[i % len(GOAL_COLORS)] for i, t in enumerate(trackers)}
    goal_filters = "".join(f'<button class="chip" data-board="goals" data-f="{html.escape(t)}" '
                           f'style="--c:{tracker_color[t]}">{html.escape(t)}</button>' for t in trackers)
    goal_lanes = "".join(
        f'<section class="lane"><h2><span class="dot" style="background:var(--{lane.split()[0].lower()})"></span>'
        f'{lane}<span class="n">{sum(1 for g in goals if g["lane"] == lane)}</span></h2><div class="cards">'
        + "".join(goal_card(g, tracker_color[g["tracker"]], source, today)
                  for g in sorted(goals, key=lambda g: g["day"], reverse=True) if g["lane"] == lane)
        + "</div></section>" for lane in GOAL_LANES)
    reviewed = re.search(r"Reviewed (\d{4}-\d{2}-\d{2})", status_text)
    tip = git(repo, "log", "-1", "--format=%h %cs", ref).split()
    merge = git(repo, "log", "-1", "--merges", "--format=%cs", ref).strip()

    page = TEMPLATE.format(
        css=CSS, js=JS, branch=html.escape(ref.removeprefix("origin/")), tip_date=tip[1],
        merge=merge or "none", reviewed=reviewed.group(1) if reviewed else "unknown",
        warn="".join(f'<div class="warn">⚠ {html.escape(w)}</div>' for w in warnings),
        prio=prio_chips, risks=risk_chips, filters=filters, goal_filters=goal_filters, goal_lanes=goal_lanes,
        n_active=len(active), n_backlog=len(backlog), n_done=len(done_cards),
        active_days=ACTIVE_DAYS, done_days=DONE_DAYS, weeks=CHART_WEEKS,
        active="".join(outcome_card(k, rows[k], edits.get(k, []), today, k in hot, outcome_source(k))
                       for k in by_recency),
        backlog="".join(outcome_card(k, rows[k], edits.get(k, []), today, k in hot, outcome_source(k))
                        for k in backlog),
        done="".join(done_cards),
        roadmap_url=html.escape((repo / "roadmap.md").as_uri()), status_url=html.escape((repo / "status.md").as_uri()),
    )
    summary = {"branch": ref, "commit": tip[0], "open_outcomes": len(rows), "in_progress": len(active),
               "backlog": len(backlog), "implemented_cards": len(done_cards),
               "goal_cards": {lane: sum(1 for g in goals if g["lane"] == lane) for lane in GOAL_LANES},
               "goal_trackers": len(trackers), "structure_warnings": warnings}
    return page, summary


CSS = """
:root{--bg:#f4f5f8;--panel:#fff;--ink:#1c2230;--muted:#667085;--line:#e3e6ed;--lane:#eceef3;
--prog:#2f6fe4;--back:#a3acbd;--done:#1f9d55;--in:#2f6fe4;--open:#a3acbd;--blocked:#d64545;--warn:#fdecec;--accent:#2f6fe4}
@media (prefers-color-scheme:dark){:root{--bg:#111419;--panel:#1b1f27;--ink:#e7eaf0;--muted:#97a0b2;
--line:#2b313d;--lane:#161a21;--prog:#6b9bff;--back:#5c6577;--done:#4cc988;--in:#6b9bff;--open:#5c6577;--blocked:#ff7070;--warn:#3a1e1e;--accent:#8ab0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1400px;margin:0 auto;padding:16px}a{color:var(--accent)}
.top{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px}.top h1{font-size:22px;margin:0}
.pill{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--muted)}
.pill b{color:var(--ink)}.branch b{color:var(--prog)}
.strip{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
.prio{list-style:none;display:flex;gap:8px;flex-wrap:wrap;padding:0;margin:0}
.prio li{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:5px 10px;font-size:13px;max-width:330px}
.prio b{display:inline-block;background:var(--prog);color:#fff;border-radius:50%;width:18px;height:18px;
text-align:center;font-size:11px;line-height:18px;margin-right:6px}
.label{font-size:12px;font-weight:600;color:var(--muted);margin-right:4px}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:3px 10px;font-size:12px;cursor:default}
button.chip{cursor:pointer;border-left:4px solid var(--c)}button.chip.on{background:var(--c);color:#fff}
.tools{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:8px 0}
.tab{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px;padding:6px 14px;font-size:14px;cursor:pointer}
.tab.on{background:var(--prog);color:#fff;border-color:var(--prog)}
.tools input{margin-left:auto;min-width:200px;padding:6px 10px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink)}
.board{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-items:start}
.board.four{grid-template-columns:repeat(4,minmax(0,1fr))}
h2.section{font-size:18px;margin:28px 0 4px}.sub{color:var(--muted);font-size:13px;margin:0 0 8px}
.card p.status{color:var(--muted)}.guess{margin-left:auto;font-size:10px;color:#c9a227;border:1px solid #c9a227;border-radius:4px;padding:0 4px}
@media (max-width:900px){.board,.board.four{grid-template-columns:1fr}}
.lane{background:var(--lane);border-radius:12px;padding:10px}
.lane>h2{display:flex;align-items:center;gap:8px;font-size:14px;margin:2px 4px 10px}
.lane>h2 .n{background:var(--panel);border-radius:999px;padding:0 8px;font-size:12px}
.lane>h2 .dot{width:10px;height:10px;border-radius:50%}.lane>h2 small{color:var(--muted);font-weight:400;margin-left:auto}
.cards{display:flex;flex-direction:column;gap:8px}
.card{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--c,var(--done));border-radius:10px;
padding:9px 11px;cursor:pointer;transition:transform .1s}.card:hover{transform:translateY(-1px);box-shadow:0 2px 8px #0002}
.card h4{margin:4px 0 4px;font-size:14px}.card p{margin:4px 0;font-size:13px}
.row{display:flex;align-items:center;gap:6px}.foot{justify-content:space-between;margin-top:4px}
.tag{font-weight:700;font-size:12px;color:var(--c)}.area{font-size:11px;color:var(--muted)}
.owners{display:flex;flex-wrap:wrap;gap:4px}.owners em{font-style:normal;font-size:11px;background:var(--lane);border-radius:4px;padding:0 5px;color:var(--muted)}
.ago{font-size:11px;color:var(--muted)}.spark{width:56px;height:14px}
.check{color:var(--done);font-weight:700}.more{margin-left:auto;font-size:11px;color:var(--muted)}
.flame{font-size:12px}.hidden{display:none!important}
.warn{background:var(--warn);border-radius:8px;padding:6px 12px;margin:8px 0;font-size:13px}
dialog{max-width:640px;width:calc(100% - 32px);border:1px solid var(--line);border-radius:12px;background:var(--panel);color:var(--ink);padding:18px}
dialog::backdrop{background:#0006}dialog h3{margin:0 0 8px}dialog p{white-space:pre-wrap}
dialog button{float:right;border:0;background:none;font-size:20px;color:var(--muted);cursor:pointer}
footer{margin:18px 0;color:var(--muted);font-size:12px}
.src{margin-top:6px;padding-top:6px;border-top:1px dashed var(--line);font-size:11px;color:var(--muted)}
.notice{background:var(--lane);border-left:4px solid #d9a400;border-radius:8px;padding:8px 12px;margin:10px 0;font-size:13px}
dialog .src{font-size:12px}
"""

JS = """
const q=document.getElementById('q');const sel={};
function apply(){const t=q.value.trim().toLowerCase();
document.querySelectorAll('.card').forEach(c=>{const f=sel[c.dataset.board];const hideA=f&&c.dataset.area!=='status'&&c.dataset.area!==f;
c.classList.toggle('hidden',hideA||(t&&!c.dataset.search.includes(t)))});
document.querySelectorAll('.lane').forEach(l=>l.querySelector('.n').textContent=l.querySelectorAll('.card:not(.hidden)').length);}
q.addEventListener('input',apply);
document.querySelectorAll('button.chip').forEach(b=>b.addEventListener('click',()=>{const k=b.dataset.board;
sel[k]=sel[k]===b.dataset.f?null:b.dataset.f;
document.querySelectorAll(`button.chip[data-board="${k}"]`).forEach(x=>x.classList.toggle('on',x.dataset.f===sel[k]));apply();}));
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>{
document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===b));
document.querySelectorAll('.view').forEach(v=>v.classList.toggle('hidden',v.id!=='v-'+b.dataset.view));}));
const dlg=document.getElementById('dlg');
document.querySelectorAll('.card').forEach(c=>c.addEventListener('click',()=>{
dlg.querySelector('h3').textContent=c.dataset.title;dlg.querySelector('p').textContent=c.dataset.detail;
dlg.querySelector('.dsrc').innerHTML=c.querySelector('.src').innerHTML;dlg.showModal();}));
document.querySelectorAll('.card a').forEach(a=>a.addEventListener('click',e=>e.stopPropagation()));
dlg.querySelector('button').addEventListener('click',()=>dlg.close());
dlg.addEventListener('click',e=>{if(e.target===dlg)dlg.close();});
"""

TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Iroha 3 Board</title>
<style>{css}</style></head><body><div class="wrap">
<div class="top"><h1>Iroha 3 board</h1>
<span class="pill branch">branch <b>{branch}</b>, last commit {tip_date}</span>
<span class="pill">last merge <b>{merge}</b></span><span class="pill">status.md reviewed <b>{reviewed}</b></span></div>
<div class="notice">⚠ An AI agent (Claude, a large language model) made this report with a script.
The script sorts items with simple rules. The agent and the rules can make mistakes,
and the next run can give a different result. Check the source of a card before you act on it.</div>
{warn}
<div class="tools"><button class="tab on" data-view="roadmap">Roadmap</button>
<button class="tab" data-view="goals">Goals from specs</button>
<input id="q" type="search" placeholder="Find a word in cards"></div>
<div class="view" id="v-roadmap">
<div class="strip"><span class="label">Roadmap priorities</span><ol class="prio">{prio}</ol></div>
<div class="strip"><span class="label">Risks from status.md</span>{risks}</div>
<div class="strip">{filters}</div>
<div class="board">
<section class="lane"><h2><span class="dot" style="background:var(--back)"></span>Backlog<span class="n">{n_backlog}</span>
<small>roadmap item not changed in {active_days} days</small></h2><div class="cards">{backlog}</div></section>
<section class="lane"><h2><span class="dot" style="background:var(--prog)"></span>In progress<span class="n">{n_active}</span>
<small>roadmap item changed in {active_days} days</small></h2><div class="cards">{active}</div></section>
<section class="lane"><h2><span class="dot" style="background:var(--done)"></span>Done recently<span class="n">{n_done}</span>
<small>added to status.md in {done_days} days</small></h2><div class="cards">{done}</div></section>
</div>
</div>
<div class="view hidden" id="v-goals">
<p class="sub">Goal tables in the specs folder that have a status column. The lane comes from the status text.
"Status unclear" means the text has no clear status word, so the lane is a guess.</p>
<div class="strip">{goal_filters}</div>
<div class="board four">{goal_lanes}</div>
</div>
<footer>🔥 The SORA Nexus payment plan needs this item.
Small bars on a card show changes to the item in each of the last {weeks} weeks.
Click a card to read the full text.
Sources: <a href="{roadmap_url}">roadmap.md</a>, <a href="{status_url}">status.md</a> at origin/{branch} ({tip_date}).</footer>
</div><dialog id="dlg"><button aria-label="Close">×</button><h3></h3><p></p><div class="src dsrc"></div></dialog>
<script>{js}</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("iroha_dir")
    parser.add_argument("output_html")
    parser.add_argument("--branch")
    args = parser.parse_args()
    repo = Path(args.iroha_dir).resolve()
    ref = f"origin/{args.branch}" if args.branch else detect_branch(repo)
    if not show(repo, ref, "roadmap.md"):
        sys.exit(f"roadmap.md not found at {ref}")
    page, summary = build(repo, ref, date.today())
    out = Path(args.output_html).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    summary["output"] = str(out)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
