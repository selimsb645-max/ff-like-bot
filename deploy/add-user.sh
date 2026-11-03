#!/bin/bash
# Add one person to a shared server. Run as root (or with sudo).
#
#   sudo ./deploy/add-user.sh rahim
#   sudo ./deploy/add-user.sh rahim --pat ghp_xxx   clone with a given read-only token
#
# Creates /srv/ffbot/rahim, clones the repo there with the token it is given,
# then runs join.py inside so that person's own bot token and access code land
# on this machine. Each directory is a separate licence: separate token,
# separate device fingerprint, separately revocable.
set -u

NAME="${1:-}"
PAT=""
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --pat) PAT="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

if [ -z "$NAME" ]; then
  echo "usage: sudo $0 <name> [--pat <github-token>]"
  exit 1
fi

REPO="github.com/selimsb645-max/ff-like-bot.git"
DIR="/srv/ffbot/$NAME"

id ffbot >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash ffbot

if [ -d "$DIR/.git" ]; then
  echo "already cloned at $DIR, pulling instead"
  sudo -u ffbot git -C "$DIR" pull --rebase
else
  mkdir -p "$DIR"
  chown ffbot:ffbot "$DIR"
  URL="https://github.com/$REPO"
  if [ -n "$PAT" ]; then URL="https://x-access-token:$PAT@$REPO"; fi
  sudo -u ffbot git clone "$URL" "$DIR" || { echo "clone failed (repo is private: pass --pat)"; exit 1; }
fi

chown -R ffbot:ffbot "$DIR"
chmod 700 "$DIR"

echo
echo "--- now set up $NAME's own bot token and access code ---"
echo "    run this in a terminal you can answer:"
echo
echo "      sudo -u ffbot bash -c 'cd $DIR && python3 join.py --no-start'"
echo
echo "    then start the service:"
echo
echo "      sudo cp $DIR/deploy/ffbot@.service /etc/systemd/system/"
echo "      sudo systemctl daemon-reload"
echo "      sudo systemctl enable --now ffbot@$NAME"
echo "      sudo journalctl -fu ffbot@$NAME"
