# Life Dashboard Companion — free, open-source Apple Health push

An alternative to pairing `ios/HealthSyncHelper` (this repo's own app, built and signed by you in
Xcode): [Life Dashboard Companion](https://github.com/owen282000/life-dashboard-companion-app-ios)
(MIT license, not developed in this repo) is a free, open-source iPhone app that pushes Apple
Health data to a webhook URL you configure. `phctx companion-server` receives it. Use whichever
app you'd rather maintain — they write to the same store, distinguished by source id.

## On the Mac: set up the receiver

1. `phctx companion-secret` — creates a random HMAC secret in the Keychain (or prints the existing
   one if you've already run this) and prints the webhook URL and secret to paste into the app.
2. Start the receiver: `phctx companion-server` in a terminal to try it, or
   `ops/install.sh --with-companion` to install it as a launchd job that starts at login and
   restarts if it crashes (`com.personalhealthcontext.companion`). It listens on port 47823 on
   every network interface (plain HTTP — the app cannot pin TLS on a LAN address that changes), so
   only run it on a network you trust; the HMAC signature is what stops an untrusted sender on that
   network from writing data, not the transport.

## Install the app for free with your own Apple ID

A free (non-paid) Apple ID can build and run your own source code on your own iPhone via Xcode at
no cost, using a personal-team signing certificate. The one real limitation: **an app installed
this way stops working after 7 days and must be reinstalled from Xcode** — Apple's free-tier
provisioning profiles expire weekly. A paid Apple Developer account ($99/yr) removes that limit.

1. Install Xcode from the Mac App Store, open it once to accept the license, then in a terminal:
   `sudo xcodebuild -license accept`.
2. Xcode → Settings → Accounts → add your Apple ID (the same iCloud account is fine; no paid
   membership required).
3. Clone the app and open it: `git clone https://github.com/owen282000/life-dashboard-companion-app-ios`,
   then open `LifeDashboardCompanion.xcodeproj` in Xcode.
4. Select the `LifeDashboardCompanion` target → Signing & Capabilities. Set **Team** to your Apple
   ID. Change **Bundle Identifier** away from the upstream `com.owen282000.lifedashboard` to
   something unique to you (e.g. `com.<yourname>.lifedashboard`) — a free account can't sign
   someone else's bundle id.
5. Connect your iPhone by cable, select it as the run destination (top of the Xcode window).
6. On the iPhone: Settings → Privacy & Security → Developer Mode → enable it, then confirm the
   restart prompt.
7. In Xcode, press Run. The first launch will ask you to trust the developer certificate: on the
   iPhone go to Settings → General → VPN & Device Management and trust it, then relaunch the app.

**Every 7 days** (free account only): reconnect the phone and press Run again in Xcode to
re-install before the old build expires. If you'd rather not do this manually, see the SideStore
route below.

### Optional: SideStore for automatic weekly re-signing

[SideStore](https://docs.sidestore.io) is a separate open-source sideloading tool that can
re-sign a free-provisioned app over Wi-Fi on a schedule so you don't have to reconnect it to a Mac
every week. It was not installed or verified as part of this work — its setup and reliability are
between you and its own documentation; treat it as an optional convenience, not a requirement.

## In the app: point it at the receiver

Open the app's main **Health Data** tab:

1. Under **Health Data**, enable the data types you want synced (steps, heart rate, sleep, etc.).
2. Under **Webhook URLs**, add the URL `phctx companion-secret` printed
   (`http://<your-mac-lan-ip>:47823/companion`). Your Mac's LAN IP can change if your router
   reassigns it; if the app starts failing to reach the Mac, re-run `phctx companion-secret` to
   check the current IP and update the URL in the app.
3. Under **HMAC Signing Secret**, paste the secret `phctx companion-secret` printed. The app then
   sends `X-Signature: sha256=HMAC-SHA256(secret, body)` on every request, which the Mac verifies
   before storing anything.
4. Grant HealthKit permission when prompted, and enable background sync so the app pushes new
   samples without being opened. iOS schedules background execution opportunistically — expect
   delivery within minutes to hours, not instantly, and use the app's Logs screen to confirm
   webhook deliveries are succeeding (status 200 from the Mac).
