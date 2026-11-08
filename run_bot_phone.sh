#!/data/data/com.termux/files/usr/bin/bash
# ফোনে FF লাইক বট চালানোর স্ক্রিপ্ট — GitHub Actions কোটা শেষ, তাই এখন ফোনই হোস্ট।
#
#   bash run_bot_phone.sh
#
# কেন ফোন: (১) GitHub-এর প্রাইভেট রেপোতে ফ্রি মিনিট শেষ, রানার শূন্য ধাপে ফেইল করছে;
# (২) Garena BD আইপি চায়, আর আপনার ফোনের মোবাইল ডেটাই আসল BD রেসিডেন্সিয়াল আইপি —
# তাই টানেল/প্রক্সি ছাড়াই সরাসরি চলবে। Ctrl-C দিলে বন্ধ হবে।
set -u
cd "$(dirname "$0")" || exit 1

# নিজের ফোনে অ্যাক্সেস-গেট বন্ধ (owner instruction), আর বটের ক্রেডেনশিয়াল
export FF_SKIP_ACCESS=1
if [ -f .env ]; then set -a; . ./.env; set +a; fi

echo "বট চালু হচ্ছে · $(date +%H:%M:%S)"
termux-wake-lock 2>/dev/null || true

RESTARTS=0
while true; do
  python3 bot.py
  code=$?
  RESTARTS=$((RESTARTS+1))
  echo "$(date +%H:%M:%S) বট থেমে গেছে (exit $code) · পুনরায় চালু · মোট রিস্টার্ট $RESTARTS"
  sleep 5
done
