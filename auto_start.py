#!/usr/bin/env python3
"""ONE-COMMAND LAUNCHER: give only the guild id, everything else is automatic.

    python3 auto_start.py 3106233034            # create 4 bots, join guild, farm
    python3 auto_start.py 3106233034 --count 2  # smaller first test
    python3 auto_start.py 3106233034 --loop     # keep creating batches forever

What happens per bot (all inside app.py, nothing manual):
  register guest -> MajorRegister -> MajorLogin -> ChooseRegion -> MajorLogin #2
  -> GetLoginData -> POST /RequestJoinClan(guild) -> TCP online -> match loop.

The web dashboard still runs at http://localhost:25567 so you can watch.
"""
import argparse
import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

ap = argparse.ArgumentParser()
ap.add_argument("guild_id", help="your guild (clan) id, e.g. 3106233034")
ap.add_argument("--region", default="BD")
ap.add_argument("--count", type=int, default=4, help="bots per batch (max 4)")
ap.add_argument("--password", default="ANON")
ap.add_argument("--loop", action="store_true", help="keep making new batches")
ap.add_argument("--every", type=int, default=1800, help="seconds between batches with --loop")
args = ap.parse_args()

# the module sets up everything at import time (web server, state, loops)
import app as bot  # noqa: E402

bot.CLAN_ID_DEFAULT = str(args.guild_id)


async def runner():
    # start the dashboard + background watchers exactly like `python3 app.py`
    boot = asyncio.create_task(bot.main())
    await asyncio.sleep(6)          # let the web server and autoload finish
    batch = 1
    while True:
        print(f"\n=== batch {batch}: creating {args.count} bots for guild {args.guild_id} ===", flush=True)
        while bot.bot_state.creating:
            await asyncio.sleep(2)
        made = await bot.create_four_accounts(args.region, str(args.guild_id),
                                              max(1, min(4, args.count)), args.password)
        print(f"=== batch {batch}: {len(made)} bots created and launched ===", flush=True)
        if not args.loop:
            break
        batch += 1
        await asyncio.sleep(args.every)
    await boot                      # keep the process (and workers) alive


if __name__ == "__main__":
    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        print("stopped")
