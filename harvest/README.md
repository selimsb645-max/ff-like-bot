# harvest/ — app-born guest factory

## Why this exists
Guest accounts created purely by API (guestgen/supply) are banned in-game
as "Cheat" within minutes: at birth they never produce the protection
telemetry (FairGuard / MSDK / GOP) the real app creates, and Garena flags
them under its "Protection_bypass" category. Every working public system
births accounts **inside the real app** and only then uses them via API.
This package does exactly that, on a GitHub Actions macOS runner —
no PC and no phone-side work for the owner beyond the usual proxy.

## How it works
1. Workflow (`.github/workflows/harvest-guests.yml`, manual dispatch)
   boots an ARM Android emulator on a macOS runner.
2. The runner joins the tailnet; `setup_bd_proxy.sh` chains the
   emulator's traffic through the owner's phone (microsocks) and
   **aborts unless the exit country is BD**.
3. `install_ff.sh` installs Free Fire from the `FF_APK_URL` repo
   variable (XAPK with OBB).
4. `fetch_frida_server.sh` starts a version-matched frida-server.
5. `harvest.py` clears the app, launches it, attaches `capture_guest.js`,
   taps the app's own guest-login flow, and collects each guest the app
   registered into `harvest/guests_harvested.json` (`{uid, password}`),
   which the workflow commits back to this repo.
6. From there the normal pipeline takes over: first API login, profile
   check, levelbot to level 30, then like duty.

## Owner setup (one time)
1. Create `.github/workflows/harvest-guests.yml` in this repo with the
   content of `harvest/harvest-guests.yml` (the connector cannot write
   workflow files, so this one file is pasted by hand).
2. Repo **variables**: set `FF_APK_URL` to a current Free Fire XAPK
   download URL (APKPure/APKCombo mirror of `com.dts.freefireth`).
3. Repo **secrets** (already used by the other workflows):
   `TAILSCALE_AUTHKEY`, `PHONE_PROXY_IP`.

## Test protocol (do not skip)
Run 1: dispatch with **count = 1**. Wait **24 hours**. The owner checks
the account in the real game (search its in-game UID). Only if it is
alive and unbanned do we raise the count and schedule regular runs.
