#!/usr/bin/env python3
"""paseo-fleet — Omnara-fleet-style butler for Paseo daemons across all machines.

Each machine runs a Paseo daemon reachable via an *offer URL* (serverId +
daemonPublicKey + relay endpoint, E2EE over relay.paseo.sh). The offer is the
credential — analogous to an Omnara PAT, but per-daemon and end-to-end encrypted.

Registry: /sensei-fs-3/users/zouyang/.secrets/paseo-fleet.json  (chmod 600)
Transport: wraps the local `paseo` CLI with `--host <offer>`.

Commands:
  overview                       table of every machine x its agents
  machines                       list registered machines + reachability
  sessions <machine>             agents on one machine (json)
  logs <machine> <agent> [-n N]  timeline of one agent
  send <machine> <agent> "msg"   send a follow-up prompt (WRITE)
  run  <machine> "prompt" [--cwd P --provider X --mode bypass]   launch new agent (WRITE)
  triage                         agents that requiresAttention / are running
  raw  <machine> -- <paseo args> escape hatch: any paseo subcommand on that daemon
"""
import argparse, concurrent.futures as cf, json, os, select, subprocess, sys, time

def _default_registry():
    env = os.environ.get("PASEO_FLEET_REGISTRY")
    if env:
        return env
    for p in (
        os.path.expanduser("~/.paseo/paseo-fleet.json"),
        os.path.expanduser("~/.config/paseo-fleet/fleet.json"),
    ):
        if os.path.exists(p):
            return p
    return os.path.expanduser("~/.paseo/paseo-fleet.json")


REGISTRY = _default_registry()
TIMEOUT = int(os.environ.get("PASEO_FLEET_TIMEOUT", "45"))


def load_fleet_or_die():
    if not os.path.exists(REGISTRY):
        sys.exit(f"no fleet registry at {REGISTRY}\n"
                 "create one (see examples/paseo-fleet.example.json): "
                 "{'fleet':[{'name','offer','server_id'}]}")
    return load_fleet()


def load_fleet():
    with open(REGISTRY) as f:
        data = json.load(f)
    return data["fleet"]


def find(machines, name):
    name_l = name.lower()
    for m in machines:
        if m["name"].lower() == name_l or m["server_id"] == name:
            return m
    # prefix match
    cands = [m for m in machines if m["name"].lower().startswith(name_l)]
    if len(cands) == 1:
        return cands[0]
    sys.exit(f"machine '{name}' not found (have: {', '.join(m['name'] for m in machines)})")


def paseo(offer, args, timeout=TIMEOUT, capture=True):
    cmd = ["paseo", *args, "--host", offer]
    return subprocess.run(
        cmd, capture_output=capture, text=True, timeout=timeout,
    )


# --- relay-linger workaround -------------------------------------------------
# Over relay, a oneshot `paseo` returns its data in ~1.5s but the process then
# hangs ~20-30s on TCP socket linger before exiting (confirmed: data is complete
# and valid long before exit). So for relay reads we stream stdout and kill the
# process the moment we have what we need, instead of waiting for clean exit.
QUIET_S = float(os.environ.get("PASEO_FLEET_QUIET", "1.5"))  # idle gap = "done"


def paseo_stream(offer, args, done=None, hard_timeout=TIMEOUT):
    """Run `paseo ... --host offer`, collect stdout, kill early.

    `done(buf)` -> True means we have a complete result; kill immediately.
    Otherwise kill after QUIET_S of no new bytes (text streams). Returns
    (stdout_str, killed_early_bool, err_str_or_None).
    """
    cmd = ["paseo", *args, "--host", offer]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buf = b""
    t0 = time.time()
    last = t0
    try:
        while True:
            if time.time() - t0 > hard_timeout:
                p.kill()
                return buf.decode(errors="replace"), False, "timeout (offline?)"
            r, _, _ = select.select([p.stdout], [], [], 0.2)
            now = time.time()
            if r:
                chunk = p.stdout.read1(65536)
                if chunk:
                    buf += chunk
                    last = now
                    if done and done(buf):
                        p.kill()
                        return buf.decode(errors="replace"), True, None
                elif p.poll() is not None:  # real EOF + exited
                    return buf.decode(errors="replace"), False, None
            else:
                if p.poll() is not None:
                    return buf.decode(errors="replace"), False, None
                if buf and now - last > QUIET_S:  # streamed, then went quiet
                    p.kill()
                    return buf.decode(errors="replace"), True, None
    finally:
        try: p.kill()
        except Exception: pass


def _json_complete(buf):
    try:
        json.loads(buf.decode())
        return True
    except Exception:
        return False


def ls_json(m):
    """Return all agents, including completed/stopped and every cwd root."""
    try:
        out, _early, err = paseo_stream(m["offer"], ["ls", "-a", "-g", "--json"],
                                        done=_json_complete)
        if err:
            return m, None, err
        return m, json.loads(out or "[]"), None
    except Exception as e:  # noqa
        return m, None, str(e)


def scan_all(machines):
    """Concurrent ls across all machines. Returns list of (m, agents, err)."""
    out = {}
    with cf.ThreadPoolExecutor(max_workers=len(machines) or 1) as ex:
        futs = {ex.submit(ls_json, m): m["name"] for m in machines}
        for fut in cf.as_completed(futs):
            m, agents, err = fut.result()
            out[m["name"]] = (m, agents, err)
    # preserve registry order
    return [out[m["name"]] for m in machines]


def cmd_overview(machines, _args):
    rows = scan_all(machines)
    total = running = 0
    for m, agents, err in rows:
        print(f"\n━━━ {m['name']}  ({m.get('note','')}) ━━━")
        if agents is None:
            print(f"  ❌ {err}")
            continue
        run = [a for a in agents if a.get("status") == "running"]
        total += len(agents); running += len(run)
        print(f"  {len(agents)} agents, {len(run)} running")
        for a in agents:
            flag = "🟢" if a.get("status") == "running" else "⚪"
            print(f"  {flag} {a.get('status',''):8} {(a.get('name') or '?')[:44]:44} | "
                  f"{a.get('provider','')} | {a.get('created','')}")
    print(f"\n═══ FLEET: {total} agents, {running} running across "
          f"{sum(1 for _,a,_ in rows if a is not None)}/{len(rows)} reachable ═══")


def cmd_machines(machines, _args):
    rows = scan_all(machines)
    for m, agents, err in rows:
        status = f"✅ {len(agents)} agents" if agents is not None else f"❌ {err}"
        print(f"  {m['name']:16} {m['server_id']:20} {status}")


def cmd_sessions(machines, args):
    m = find(machines, args.machine)
    _, agents, err = ls_json(m)
    if agents is None:
        sys.exit(f"❌ {err}")
    print(json.dumps(agents, indent=2, ensure_ascii=False))


def cmd_find(machines, args):
    """Cross-machine agent search by name substring / status / provider."""
    q = args.query.lower()
    rows = scan_all(machines)
    hits = []
    for m, agents, err in rows:
        if not agents:
            continue
        for a in agents:
            name = (a.get("name") or "").lower()
            if (q in name or q in (a.get("shortId") or "").lower()
                    or q in (a.get("provider") or "").lower()
                    or q in (a.get("cwd") or "").lower()):
                hits.append((m["name"], a))
    if not hits:
        print(f"no agent matching '{args.query}' across the fleet.")
        return
    print(f"{len(hits)} match(es) for '{args.query}':\n")
    for mname, a in hits:
        flag = "🟢" if a.get("status") == "running" else "⚪"
        print(f"  {flag} [{mname}] {a.get('shortId',''):8} {(a.get('name') or '?')[:42]:42}"
              f" | {a.get('provider','')} | {a.get('cwd','')}")
    print("\n→ read:  paseo-fleet logs <machine> <shortId>"
          "\n→ send:  paseo-fleet send <machine> <shortId> \"msg\"")


def resolve_agent(m, ref):
    """Accept an agent id/prefix OR a (case-insensitive substring of) its name."""
    _, agents, err = ls_json(m)
    if agents is None:
        sys.exit(f"❌ {err}")
    # exact id / shortId / prefix first
    for a in agents:
        if a.get("id") == ref or a.get("shortId") == ref or (a.get("id") or "").startswith(ref):
            return a["id"]
    # name substring
    rl = ref.lower()
    cands = [a for a in agents if rl in (a.get("name") or "").lower()]
    if len(cands) == 1:
        return cands[0]["id"]
    if not cands:
        sys.exit(f"no agent matching '{ref}' on {m['name']}")
    sys.exit("ambiguous: " + ", ".join(f"{a['shortId']}={a.get('name')}" for a in cands))


def cmd_logs(machines, args):
    m = find(machines, args.machine)
    aid = resolve_agent(m, args.agent)
    extra = ["--tail", str(args.n)] if args.n else []
    if args.filter:
        extra += ["--filter", args.filter]
    out, _early, err = paseo_stream(m["offer"], ["logs", aid] + extra)
    sys.stdout.write(out)
    if err:
        sys.stderr.write(err + "\n")


def cmd_send(machines, args):
    m = find(machines, args.machine)
    aid = resolve_agent(m, args.agent)
    out, _e, err = paseo_stream(m["offer"], ["send", aid, args.message])
    sys.stdout.write(out)
    if err:
        sys.stderr.write(err + "\n"); sys.exit(1)
    print(f"✅ sent to {m['name']}/{aid[:7]}")


def cmd_run(machines, args):
    m = find(machines, args.machine)
    pa = ["run", args.prompt]
    if args.cwd: pa += ["--cwd", args.cwd]
    if args.provider: pa += ["--provider", args.provider]
    if args.mode: pa += ["--mode", args.mode]
    pa += ["--detach"]
    out, _e, err = paseo_stream(m["offer"], pa, hard_timeout=90)
    sys.stdout.write(out)
    if err:
        sys.stderr.write(err + "\n"); sys.exit(1)


def cmd_triage(machines, _args):
    rows = scan_all(machines)
    hits = []
    for m, agents, err in rows:
        if not agents:
            continue
        for a in agents:
            if a.get("status") == "running" or a.get("requiresAttention"):
                hits.append((m["name"], a))
    if not hits:
        print("✨ nothing running, nothing waiting on you.")
        return
    print("Agents running / needing attention:\n")
    for mname, a in hits:
        why = "⚠️ ATTENTION" if a.get("requiresAttention") else "🟢 running"
        print(f"  [{mname}] {why}  {(a.get('name') or '?')[:48]}  ({a.get('shortId','')})")


def cmd_raw(machines, args):
    m = find(machines, args.machine)
    r = paseo(m["offer"], args.rest, timeout=90)
    sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
    sys.exit(r.returncode)


def main():
    p = argparse.ArgumentParser(prog="paseo-fleet", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("overview").set_defaults(fn=cmd_overview)
    sub.add_parser("machines").set_defaults(fn=cmd_machines)
    sub.add_parser("triage").set_defaults(fn=cmd_triage)

    sp = sub.add_parser("find"); sp.add_argument("query",
        help="match agent name / shortId / provider / cwd across all machines")
    sp.set_defaults(fn=cmd_find)

    sp = sub.add_parser("sessions"); sp.add_argument("machine"); sp.set_defaults(fn=cmd_sessions)

    sp = sub.add_parser("logs"); sp.add_argument("machine"); sp.add_argument("agent")
    sp.add_argument("-n", "--tail", dest="n", type=int, default=20)
    sp.add_argument("--filter", help="tools|text|errors|permissions")
    sp.set_defaults(fn=cmd_logs)

    sp = sub.add_parser("send"); sp.add_argument("machine"); sp.add_argument("agent")
    sp.add_argument("message"); sp.set_defaults(fn=cmd_send)

    sp = sub.add_parser("run"); sp.add_argument("machine"); sp.add_argument("prompt")
    sp.add_argument("--cwd"); sp.add_argument("--provider"); sp.add_argument("--mode")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("raw"); sp.add_argument("machine")
    sp.add_argument("rest", nargs=argparse.REMAINDER); sp.set_defaults(fn=cmd_raw)

    args = p.parse_args()
    machines = load_fleet_or_die()
    args.fn(machines, args)


if __name__ == "__main__":
    main()
