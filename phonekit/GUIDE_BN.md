# ফোনেই guest জন্ম — Termux + Shizuku গাইড (ধাপে ধাপে)

লক্ষ্য: তোমার আসল ফোনের আসল Free Fire app-এ guest account তৈরি হবে
(আসল app, আসল device — তাই ban-এর ঝুঁকি সবচেয়ে কম), আর script
uid/password তুলে নেবে। PC লাগবে না, root লাগবে না।

## ধাপ ১ — Termux বসাও
- Play Store-এর Termux পুরনো — নেবে না। GitHub থেকে নাও:
  github.com/termux/termux-app/releases → সর্বশেষ APK install করো।
- Termux খুলে চালাও:  `pkg update -y && pkg install -y python curl`

## ধাপ ২ — Shizuku বসাও আর চালু করো
- Play Store থেকে "Shizuku" install করো।
- ফোনের **Settings → Developer options**-এ **Wireless debugging** চালু করো
  (Developer options না দেখলে: About phone-এ Build number-এ ৭ বার tap)।
- Shizuku app খুলে **"Start via Wireless debugging"** → pair code দিয়ে
  জোড়া লাগাও → Start। "Shizuku is running" দেখলেই হয়েছে।
- Shizuku-র ভেতরে **Termux-কে Authorized** করো।

## ধাপ ৩ — Termux-এ rish বসাও (Shizuku-র shell সেতু)
- Shizuku app → **"Use Shizuku in terminal apps"** → Termux বেছে
  **Export files** → দুটো ফাইল (rish, rish_shizuku.dex) Termux-এর
  home-এ যাবে। Termux-এ চালাও:  `chmod +x ~/rish`
- যাচাই:  `~/rish -c "id"` → uid=2000(shell) দেখালে সেতু তৈরি।

## ধাপ ৪ — script দুটো নামাও
Termux-এ:
```
curl -O https://raw.githubusercontent.com/selimsb645-max/ff-like-bot/main/phonekit/probe.sh
curl -O https://raw.githubusercontent.com/selimsb645-max/ff-like-bot/main/phonekit/harvest_phone.sh
chmod +x probe.sh harvest_phone.sh
termux-setup-storage
```

## ধাপ ৫ — হাতে ONEটা guest বানাও + probe চালাও  ★ প্রথম মাইলফলক
1. আসল FF app খুলে **Guest** দিয়ে ঢুকে একটা নতুন guest account বানাও
   (লবিতে পৌঁছালেই হবে, খেলতে হবে না)।
2. FF থেকে বেরিয়ে Termux-এ চালাও:  `./probe.sh`
3. শেষে তৈরি হওয়া **/sdcard/ff_probe_result.txt** ফাইলটা পাঠাও
   (বা output-এর screenshot)।

ওই probe-ই বলে দেবে এই build-এ guest-এর uid/password কোন ফাইলে,
root ছাড়া পড়া যায় কিনা। ফল দেখে harvest loop-টা তোমার ফোনের জন্য
চূড়ান্ত করে দেব — তার আগে বড় আকারে কিছু চলবে না।

## ধাপ ৬ — (probe পাশ হলে) স্বয়ংক্রিয় harvest
`./harvest_phone.sh 1` দিয়ে প্রথমে একটা, যাচাই হলে সংখ্যা বাড়ানো।
সংগৃহীত guest জমা হবে /sdcard/ff_guests.json-এ — ফাইলটা পাঠালেই
pool-এ তোলা, level 30, like — বাকি সব আগের pipeline-এই চলবে।

আটকে গেলে যেকোনো ধাপের screenshot পাঠাও — বলে দেব কোনটা চাপতে হবে।
