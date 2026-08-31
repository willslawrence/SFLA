#!/usr/bin/env python3
"""
Create the missing Airtable rows for pads that exist in geometry.json.

WHY THIS EXISTS
    import_kmz.py writes geometry.json (so a pad renders) but leaves the Airtable
    row to a --airtable flag that wants a raw AIRTABLE_TOKEN. A pad without a row
    renders fine and cannot accept a status tap — it is invisible to the tracker's
    Suitable/Unsuitable buttons. That gap is easy to leave behind and hard to see.

WHY NO AIRTABLE TOKEN
    The Worker already holds the Airtable token server-side and already exposes
    POST {action:"create", pin, records:[...]}. So this needs only the shared
    WRITE_PIN — the same one pilots use to tap Suitable — which is a far narrower
    secret than a base-scoped Airtable PAT. Cloudflare secrets are write-only, so
    the PIN cannot be read back from Wrangler; it lives in the macOS Keychain.

ONE-TIME SETUP (run this yourself; the value never passes through Claude)
    security add-generic-password -a "$USER" -s thc-sfla-write-pin -w

USAGE
    python3 add_airtable_rows.py --dry-run              # show what is missing
    python3 add_airtable_rows.py                        # create every missing row
    python3 add_airtable_rows.py M376 M377              # only these
    python3 add_airtable_rows.py --note "19 Aug drive"  # set the Notes field

The create endpoint skips names that already exist, so re-running is harmless.
"""
import json, os, subprocess, sys, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = "https://sfla-write.willslawrence.workers.dev/"
KEYCHAIN_SERVICE = "thc-sfla-write-pin"


def pin():
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except subprocess.CalledProcessError:
        sys.exit(f"""No WRITE_PIN in the Keychain.

Store it once (you type the value, it is never printed or logged):
    security add-generic-password -a "$USER" -s {KEYCHAIN_SERVICE} -w

It is the same shared PIN pilots use to tap Suitable/Unsuitable — not the
Airtable token, which stays server-side in the Worker.""")


# Cloudflare 403s the default python-urllib agent, so every call sets one.
UA = "thc-sfla-tooling/1.0"


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    args = [a for a in sys.argv[1:]]
    dry = "--dry-run" in args
    note = ""
    if "--note" in args:
        i = args.index("--note"); note = args[i + 1]; del args[i:i + 2]
    args = [a for a in args if not a.startswith("--")]

    geom = json.load(open(os.path.join(HERE, "geometry.json")))
    live = get_json(WORKER).get("sites", {})
    missing = [n for n in (args or geom) if n in geom and n not in live]

    if not missing:
        print("Nothing to do — every pad in geometry.json already has an Airtable row.")
        return

    records = [{"name": n,
                "status": "New SFLA",
                "areas": geom[n].get("areas") or [],
                **({"notes": note} if note else {})}
               for n in sorted(missing)]

    print(f"{len(records)} row(s) to create:")
    for r in records:
        print(f"  {r['name']:<8} areas={', '.join(r['areas']) or '(none)'}")
    if dry:
        print("\n--dry-run: nothing sent.")
        return

    body = json.dumps({"action": "create", "pin": pin(), "records": records}).encode()
    req = urllib.request.Request(WORKER, data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": UA},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")
        # never echo the request body — it carries the PIN
        sys.exit(f"Worker refused the write: HTTP {e.code} {detail}")

    print("\ncreated:", res.get("created") or [])
    if res.get("skipped"): print("skipped (already existed):", res["skipped"])
    if res.get("failed"):  print("FAILED:", res["failed"])
    print("\nNow rebuild so the pads pick up live status:")
    print("  python3 build.py && python3 sync_master_kmz.py && python3 build_foreflight_pack.py")


if __name__ == "__main__":
    main()
