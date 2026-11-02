"""Reset first-run setup state — for dev/e2e runs of the onboarding wizard.

Two layers must be reset:
  1. The bridge process (in-memory config singleton + provider manager) —
     via its HTTP API: DELETE every installed provider.
  2. config.json on disk — setup_complete + EVERY onboarding field the
     /welcome wizard collects: assistant_name (root + desktop), wake_word,
     and the full voice group (voice_enabled, stt_*, tts_*). Without this
     the wizard is skipped on relaunch even with zero providers.

The bridge reads setup_complete from its live singleton, so after this
script the bridge must be restarted (dev: kill + relaunch; the script
prints the reminder). NEVER touches memory: vault, orbits, skills,
skill_* tables, timelines, checkpoints, goals. Pass --full to ALSO wipe
conversations (delegates to scripts/wipe_history.py).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("UNIVERSE_BRIDGE_PORT", "8738"))
BRIDGE = f"http://127.0.0.1:{PORT}"
CFG_PATH = os.path.expanduser("~/.universe/config.json")


def _auth_header() -> dict[str, str]:
    """The header the bridge's auth middleware expects:
    `Authorization: Bearer <token>` (universe_desktop_bridge/auth.py).
    The token is the one Electron main put in UNIVERSE_BRIDGE_TOKEN —
    without it the fail-closed gate rejects every request with 401."""
    token = os.environ.get("UNIVERSE_BRIDGE_TOKEN", "")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _api(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(f"{BRIDGE}{path}", method=method,
                                 headers=_auth_header())
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main(argv: list[str] | None = None) -> int:
    """Reset first-run state. DESTRUCTIVE (deletes providers + onboarding
    fields) — requires --yes in non-interactive use; otherwise prompts."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true",
                    help="confirm the destructive reset (required)")
    ap.add_argument("--full", action="store_true",
                    help="also wipe conversations/goals (wipe_history.py)")
    args = ap.parse_args(argv)
    if not args.yes:
        try:
            answer = input("Delete all providers + onboarding state? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("aborted — pass --yes to confirm.")
            return 2
    # 1) providers out through the live bridge (removes keys + routes)
    try:
        installed = _api("/api/providers")["providers"]
        for p in installed:
            _api(f"/api/providers/{urllib.parse.quote(p['name'])}", method="DELETE")
            print(f"provider removed: {p['name']}")
    except urllib.error.HTTPError as e:
        # fail-closed auth: never silently fall back to disk-only, or the
        # user ends up "reset" with live providers still installed
        if e.code == 401:
            print("bridge rejected the token (401 unauthorized) — set "
                  "UNIVERSE_BRIDGE_TOKEN to the token the bridge was started "
                  "with (or 'dev' in dev mode) and retry")
            return 1
        print(f"bridge HTTP error ({e.code} {e.reason}) — removing via disk "
              "config only")
    except urllib.error.URLError as e:
        # connection refused = the bridge is simply not running: the disk
        # fallback is the honest path (a restart reloads the singleton anyway)
        print(f"bridge not reachable ({e.reason}) — removing via disk config only")
    except Exception as e:
        print(f"bridge not reachable ({e}) — removing via disk config only")

    # 2) config.json: clear setup_complete + EVERY wizard-collected field.
    # The wizard (Welcome.tsx) writes: assistant_name (via /api/setup/complete
    # into desktop), wake_word, voice_enabled, stt_provider/stt_api_key/
    # stt_model/stt_cloud_model(/stt_language), tts_provider/tts_api_key/
    # tts_voice/tts_model. Any leftover (e.g. a name) makes the relaunch
    # look half-onboarded, so drop the whole desktop onboarding group —
    # but keep unrelated desktop prefs (none exist today; allowlist below
    # is the guard if any are added later).
    if not os.path.exists(CFG_PATH):
        print(f"error: {CFG_PATH} not found — nothing to reset (Universe was "
              "never set up on this machine)")
        return 1
    with open(CFG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.pop("setup_complete", None)
    cfg.pop("setup_at", None)
    cfg.pop("assistant_name", None)  # root-level legacy copy, if any
    desktop = cfg.get("desktop") or {}
    for key in ("assistant_name", "wake_word", "voice_enabled",
                "stt_provider", "stt_api_key", "stt_model",
                "stt_cloud_model", "stt_language",
                "tts_provider", "tts_api_key", "tts_voice", "tts_model"):
        desktop.pop(key, None)
    cfg["desktop"] = desktop
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print("config: setup_complete + onboarding fields cleared (memory kept)")

    if args.full:
        import subprocess
        print("--- also wiping conversations (--full) ---")
        # --yes flows through: wipe_history.py is already explicitly
        # confirmed here and a subprocess prompt would read OUR stdin
        subprocess.run([sys.executable,
                        str(Path(__file__).resolve().parent.parent.parent
                            / "scripts" / "wipe_history.py"),
                        *(["--yes"] if args.yes else [])],
                       check=False)

    print("NOTE: restart the bridge so its in-memory singleton reloads.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
