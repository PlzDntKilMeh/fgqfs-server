# fgqfs-server

Local FG:QfS private server for phone use.

## Setup

Install Python, then run:

```bat
setup_env.bat
```

This creates a repo-local `.venv` and installs the needed packages.

## Android APK Proxy Patch

Android APKs usually do not trust user-installed certificates by default. Patch the APK so it trusts the proxy certificate. This is Android-only; iPhone does not need an app mod for this setup.

Use APK Easy Tool to decompile the APK, then run:

```bat
patch_apk_proxy.bat
```

Enter the decompiled APK folder path when asked. If it says the patch succeeded, recompile the APK with APK Easy Tool, sign it, and install it on the phone.

## Install Cert On Phone

The phone and PC must be on the same Wi-Fi network. While the proxy is running, open this on the phone:

```text
http://YOUR_PC_IP:6769/
```

Tap **Download proxy certificate**. The direct URL is:

```text
http://YOUR_PC_IP:6769/cert
```

The certificate page is served by mitmproxy on port `6769`, so the Python server on port `6767` does not need to be running just to install the certificate.

### iPhone

1. Download the certificate from `http://YOUR_PC_IP:6769/`.
2. Go to `Settings > General > VPN & Device Management` and install the downloaded profile.
3. Go to `Settings > General > About > Certificate Trust Settings`.
4. Enable full trust for the mitmproxy certificate.
5. Go to `Settings > Wi-Fi`, tap the info button for your network, and set **HTTP Proxy** to **Manual**:

```text
Server: YOUR_PC_IP
Port: 6769
Authentication: Off
```

### Android

1. Use the patched APK from the Android APK Proxy Patch section.
2. Download the certificate from `http://YOUR_PC_IP:6769/`.
3. Install it as a user CA certificate in Android settings.
4. In your Wi-Fi network settings, set the manual proxy:

```text
Proxy hostname: YOUR_PC_IP
Proxy port: 6769
```

## Get A Save From A Phone

For Facebook/OAuth accounts:

1. Log in without the proxy.
2. Close the game.
3. Run `start_live_capture_proxy.bat`.
4. If the proxy certificate is not installed yet, open `http://YOUR_PC_IP:6769/` on the phone and install it.
5. Enable the phone proxy to your PC on port `6769`.
6. Reopen the game and let the town load.
7. Close the game.
8. Run:

```bat
import_live_save.bat
```

That imports the newest captured save into shared gameplay slot `0`.
The importer chooses the newest save-bearing record in `captures\live\traffic.jsonl`, whether it came from a client `saveV3` request or a server `getGameStatePB` response.

## Download A Live Email Save

```bat
download_live_save.bat --email you@example.com --password yourpass --activate
```

This downloads the official server save and activates it into the shared gameplay slot.

## Assets

The repo does not include CDN assets. If the game ever shuts down these will not be accessible anymore so people should archive them 
To populate local catalogs/assets:

```bat
download_assets.bat
```

Lazy fetch is controlled by `server_settings.json`. Lazy-fetched files go to:

```text
content\cdn_assets_lazy\
```

Bulk-downloaded files go to:

```text
content\cdn_assets\files\
```
## Start Server

Run:

```bat
start_server_and_proxy.bat
```

Default ports:

- server/admin/API: `6767`
- phone proxy: `6769`

Keep both server windows open. If Windows asks, allow Python and mitmproxy through the firewall. The startup window prints your PC LAN IP and the cert URL.

## Use The Server

Open the game with the phone proxy enabled.

Admin dashboard:

```text
http://YOUR_PC_IP:6767/admin
```

The admin dashboard can upload saves, download the active save, restore older revisions, and download a live save with an email account.

Do not expose the server/admin port to the internet.

## Settings

Default settings live in `server_settings.json`.

Common settings:

```json
{
  "server_port": 6767,
  "proxy_port": 6769,
  "use_shared_save": true,
  "shared_save_pid": "0",
  "lazy_fetch": true
}
```
## Related Tools

- LTA viewer: https://plzdntkilmeh.github.io/lta-viewer
- Save Editor: https://fgqfs-shop.pages.dev/

Generated databases, captures, certs, logs, and CDN downloads are intentionally ignored by git.
