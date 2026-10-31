#!/data/data/com.termux/files/usr/bin/bash
# Auto-start the farm after a phone reboot.
#
# One-time setup (needs the "Termux:Boot" app from F-Droid, opened once):
#   mkdir -p ~/.termux/boot
#   cp ~/ff-like-bot/unfair_glory/termux_boot.sh ~/.termux/boot/ffboot.sh
#   chmod +x ~/.termux/boot/ffboot.sh
#
# Then the phone can be left alone: after any reboot the farm comes back up
# by itself and Telegram control works again.

termux-wake-lock 2>/dev/null
sleep 20                      # let Wi-Fi / mobile data come up after boot
cd "$(dirname "$0")" 2>/dev/null
cd "$HOME/ff-like-bot/unfair_glory" || exit 1

# controller (token read from a local file so it survives reboots)
if [ -f .tgtoken ]; then
  export FF_TG_TOKEN="$(cat .tgtoken)"
  export FF_TG_CHAT="${FF_TG_CHAT:-8012564523}"
  pgrep -f tg_control.py >/dev/null || \
    nohup python3 tg_control.py >> tg.log 2>&1 &
fi

# farm
./run24.sh
