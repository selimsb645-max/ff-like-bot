/*
 * capture_guest.js — harvest helper for the ff-like-bot pool pipeline.
 *
 * Loaded into the real Free Fire app (emulator) while the APP ITSELF
 * registers a fresh guest account. The script only OBSERVES the values
 * the app stores for its own guest login (SharedPreferences writes) and
 * appends {uid, password, token} to guest_accounts.json in the app's
 * external media directory, where the host driver can pull it via adb.
 *
 * It does not modify any value the app writes, and it does not block or
 * reroute any app behaviour: registration, MSDK and protection traffic
 * all happen exactly as the stock app performs them.
 */
'use strict';

Java.perform(function () {
    var TAG = '[harvest] ';
    var captured = {};        // key -> latest value seen this registration
    var guests = [];          // all guests captured in this app process
    var saveTimer = null;

    function mediaDir() {
        var ActivityThread = Java.use('android.app.ActivityThread');
        var ctx = ActivityThread.currentApplication().getApplicationContext();
        var dirs = ctx.getExternalMediaDirs();
        return dirs[0].getAbsolutePath();
    }

    function loadExisting() {
        try {
            var File = Java.use('java.io.File');
            var FileReader = Java.use('java.io.FileReader');
            var BufferedReader = Java.use('java.io.BufferedReader');
            var f = File.$new(mediaDir() + '/guest_accounts.json');
            if (!f.exists()) return;
            var r = BufferedReader.$new(FileReader.$new(f));
            var s = '', line;
            while ((line = r.readLine()) !== null) s += line;
            r.close();
            var parsed = JSON.parse(s);
            if (Array.isArray(parsed)) guests = parsed;
            console.log(TAG + 'loaded ' + guests.length + ' existing guest(s)');
        } catch (e) {
            console.log(TAG + 'no readable existing file: ' + e);
        }
    }

    function persist() {
        try {
            var File = Java.use('java.io.File');
            var FileWriter = Java.use('java.io.FileWriter');
            var BufferedWriter = Java.use('java.io.BufferedWriter');
            var path = mediaDir() + '/guest_accounts.json';
            var w = BufferedWriter.$new(FileWriter.$new(File.$new(path), false));
            var text = JSON.stringify(guests, null, 2);
            var chars = Java.array('char', text.split(''));
            w.write(chars, 0, chars.length);
            w.close();
            console.log(TAG + 'saved ' + guests.length + ' guest(s) to ' + path);
        } catch (e) {
            console.log(TAG + 'write failed: ' + e);
        }
    }

    function maybeSave() {
        if (saveTimer !== null) clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
            if (!captured.uid || !captured.password) return;
            var g = {
                uid: String(captured.uid),
                password: String(captured.password),
                token: captured.token ? String(captured.token) : ''
            };
            var dup = guests.some(function (x) { return x.uid === g.uid; });
            if (!dup) {
                guests.push(g);
                console.log(TAG + 'guest captured: uid=' + g.uid);
                persist();
            }
            captured = {};
        }, 400);
    }

    function classify(key, value) {
        if (typeof key !== 'string' || typeof value !== 'string') return;
        var k = key.toLowerCase();
        if (k.indexOf('guest_uid') !== -1 || k === 'uid' || k.endsWith('.uid')) {
            if (/^\d{6,}$/.test(value)) { captured.uid = value; maybeSave(); }
        } else if (k.indexOf('guest_password') !== -1 || k.indexOf('guest_pwd') !== -1) {
            if (value.length >= 16) { captured.password = value; maybeSave(); }
        } else if (k.indexOf('guest_token') !== -1 || k === 'com.garena.msdk.token') {
            captured.token = value; maybeSave();
        }
    }

    // Observe every SharedPreferences write the app makes.
    var EditorImpl = Java.use('android.app.SharedPreferencesImpl$EditorImpl');
    EditorImpl.putString.overload('java.lang.String', 'java.lang.String')
        .implementation = function (key, value) {
            try { classify(key, value); } catch (e) { console.log(TAG + e); }
            return this.putString(key, value);
        };

    loadExisting();
    console.log(TAG + 'hooks installed, waiting for the app to register a guest');
});
