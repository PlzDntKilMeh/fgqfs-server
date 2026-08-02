# fgqfs-server

Local FG:QfS private server for phone use.

## Setup

Install Python, then run:

```bat
setup_env.bat
```

This creates a repo-local `.venv` and installs the needed packages.

## APK Proxy Patch

The APK must trust the user-installed proxy certificate. This is the only APK change needed for the proxy setup.

Use APK Easy Tool to decompile the APK, then run:

```bat
patch_apk_proxy.bat
```

Enter the decompiled APK folder path when asked. If it says the patch succeeded, recompile the APK with APK Easy Tool, sign it, and install it on the phone.

## Start Server

Run:

```bat
start_server_and_proxy.bat
```

Default ports:

- server/admin/API: `6767`
- phone proxy: `6769`

Keep both server windows open. If Windows asks, allow Python and mitmproxy through the firewall. The startup window prints your PC LAN IP and the cert URL.

## Install Cert On Phone

The phone and PC must be on the same network. While the proxy is running, open this on the phone:

```text
http://YOUR_PC_IP:6769/
```

Tap the certificate link on that setup page, or open the direct certificate URL:

```text
http://YOUR_PC_IP:6769/cert
```

Install the certificate, then set the phone's manual HTTP proxy:

- host: your PC LAN IP
- port: `6769`

The certificate is served by mitmproxy itself, so the Python server on port `6767` does not need to be running just to install the certificate. If the full server is running, the old URL also works:

```text
http://YOUR_PC_IP:6767/cert
```

## Use The Server

Open the game with the phone proxy enabled.

Admin dashboard:

```text
http://YOUR_PC_IP:6767/admin
```

The admin dashboard can upload saves, download the active save, restore older revisions, and download a live save with an email account.

Do not expose the server/admin port to the internet.

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
