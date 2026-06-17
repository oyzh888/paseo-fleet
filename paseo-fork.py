#!/usr/bin/env python3
"""paseo-fork — fork a Claude Code session at a chosen turn into a NEW Paseo agent.

Paseo (and Claude Code) have no native "fork from turn N". But a Claude session
is just a jsonl file under ~/.claude/projects/<cwd-hash>/<uuid>.jsonl, where each
line carries uuid + parentUuid (a linked list). So we can:

  1. copy the prefix up to a chosen fork point,
  2. rewrite every line's `sessionId` to a fresh UUID,
  3. write it as a new <new-uuid>.jsonl in the SAME project dir,
  4. `paseo import <new-uuid>` so it shows up as a fresh tab.

The original session is never touched.

Usage:
  # list turns (pick a fork point):
  paseo-fork.py <session-uuid> --cwd <path> --list

  # fork (semantic B = keep THROUGH turn #N answered, then continue):
  paseo-fork.py <session-uuid> --cwd <path> --turn 35
  paseo-fork.py <session-uuid> --cwd <path> --turn 35 --before   # semantic A
  paseo-fork.py <session-uuid> --cwd <path> --turn 35 --label fork@35-captioning
  paseo-fork.py <session-uuid> --cwd <path> --turn 35 --dry-run  # don't import, just write file

Notes:
  * Must run on the machine that holds the session file (the daemon reads it locally).
  * cwd MUST match the original `claude` cwd — that's how the project dir is derived.
  * Fork point lands on a USER turn boundary so the tool_use/tool_result chain stays closed.
"""
import argparse, json, os, subprocess, sys, uuid as uuidlib


def project_dir(cwd: str) -> str:
    # Claude derives the dir by replacing every non-alnum run with '-'
    # Empirically it's: replace '/' and '_' and '.' -> '-' (leading '/' -> leading '-').
    # Match the observed scheme: every char not [A-Za-z0-9] becomes '-'.
    safe = "".join(c if c.isalnum() else "-" for c in cwd)
    return os.path.expanduser(f"~/.claude/projects/{safe}")


def load_lines(path: str):
    rows = []
    with open(path) as f:
        for ln in f:
            ln = ln.rstrip("\n")
            if not ln.strip():
                continue
            try:
                rows.append((ln, json.loads(ln)))
            except json.JSONDecodeError:
                rows.append((ln, None))
    return rows


def user_text(d: dict) -> str:
    msg = d.get("message", {})
    c = msg.get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "tool_result":
                    return "[tool_result]"
        return " ".join(parts).strip()
    return ""


def real_user_turns(rows):
    """Return [(turn_no, line_idx0, uuid, preview)] for genuine user turns."""
    turns = []
    for i, (_, d) in enumerate(rows):
        if not d or d.get("type") != "user":
            continue
        t = user_text(d)
        if not t or t.startswith("[tool_result]"):
            continue
        turns.append((len(turns) + 1, i, (d.get("uuid") or "")[:8], t[:80].replace("\n", " ")))
    return turns


def main():
    ap = argparse.ArgumentParser(description="Fork a Claude session into a new Paseo agent")
    ap.add_argument("session", help="source session UUID")
    ap.add_argument("--cwd", required=True, help="original claude cwd (must match)")
    ap.add_argument("--turn", type=int, help="fork point: turn number from --list")
    ap.add_argument("--before", action="store_true",
                    help="semantic A: cut BEFORE turn N (default is B: keep through N answered)")
    ap.add_argument("--list", action="store_true", help="just list user turns")
    ap.add_argument("--label", help="optional label for the new agent")
    ap.add_argument("--provider", default="claude")
    ap.add_argument("--dry-run", action="store_true", help="write the file but don't import")
    args = ap.parse_args()

    pdir = project_dir(args.cwd)
    src = os.path.join(pdir, f"{args.session}.jsonl")
    if not os.path.exists(src):
        sys.exit(f"ERROR: session file not found:\n  {src}\n(check --cwd matches the original claude cwd)")

    rows = load_lines(src)
    turns = real_user_turns(rows)

    if args.list or args.turn is None:
        print(f"{len(turns)} user turns in {args.session}\n")
        for n, idx, uid, prev in turns:
            print(f"#{n:>3}  line{idx + 1:>5}  {uid}  {prev}")
        if args.turn is None and not args.list:
            print("\n→ pick one with --turn N")
        return

    if args.turn < 1 or args.turn > len(turns):
        sys.exit(f"ERROR: --turn {args.turn} out of range (1..{len(turns)})")

    # Determine cut index (exclusive end, 0-based over rows).
    if args.before:
        # semantic A: cut right before turn N's user line
        cut = turns[args.turn - 1][1]
    else:
        # semantic B: keep through turn N answered = cut right before turn N+1,
        # then trim trailing non-assistant lines so we end on the final assistant msg.
        if args.turn < len(turns):
            cut = turns[args.turn][1]
        else:
            cut = len(rows)
        # walk back to last assistant line to close the tool chain
        j = cut - 1
        while j >= 0:
            _, d = rows[j]
            if d and d.get("type") == "assistant":
                break
            j -= 1
        if j >= 0:
            cut = j + 1

    if cut <= 0:
        sys.exit("ERROR: nothing before fork point (try a later turn or --before on turn>1)")

    new_uuid = str(uuidlib.uuid4())
    dst = os.path.join(pdir, f"{new_uuid}.jsonl")

    written = 0
    with open(dst, "w") as out:
        for raw, d in rows[:cut]:
            if d is None:
                out.write(raw + "\n")
                continue
            if "sessionId" in d:
                d["sessionId"] = new_uuid
            out.write(json.dumps(d, ensure_ascii=False) + "\n")
            written += 1

    sem = "A (before turn)" if args.before else "B (through turn answered)"
    print(f"forked {args.session}  @turn #{args.turn} [{sem}]")
    print(f"  kept {written}/{len(rows)} lines")
    print(f"  new session: {new_uuid}")
    print(f"  file: {dst}")

    if args.dry_run:
        print("\n(dry-run: not imported. To import manually:)")
        print(f"  paseo import {new_uuid} --provider {args.provider} --cwd {args.cwd}")
        return

    cmd = ["paseo", "import", new_uuid, "--provider", args.provider, "--cwd", args.cwd, "--json"]
    if args.label:
        cmd += ["--label", f"fork={args.label}"]
    print("\nimporting into Paseo ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        sys.exit(f"import failed (rc={r.returncode})")
    print("\n✅ done — refresh Paseo, the forked tab is there.")


if __name__ == "__main__":
    main()
