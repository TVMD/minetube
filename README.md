# Self-hosted Invidious for Car and Home TV

An ad-free YouTube frontend you host yourself, reachable from any basic browser (car head unit, smart TV, phone). Ads never appear because Invidious does not use YouTube's player or ad system. It fetches video data server-side and serves its own lightweight page.

## Architecture

```mermaid
flowchart LR
    Car[Car browser] -->|HTTPS| Nginx
    TV[Home TV browser] -->|HTTPS or LAN| Nginx
    Phone[Phone / laptop] -->|HTTPS| Nginx
    subgraph Home server (Docker)
        Nginx[nginx<br/>TLS + rate limit] --> Inv[Invidious<br/>web UI + API]
        Inv --> DB[(PostgreSQL<br/>accounts, subs, history)]
        Inv --> Comp[invidious-companion<br/>anti-bot + signatures]
    end
    Comp -->|InnerTube| YT[YouTube]
    Inv -.->|proxied video bytes| YT
```

| Component | Role |
|---|---|
| **nginx** | Only public entry point. Terminates HTTPS, rate-limits, streams video without buffering. |
| **Invidious** | Renders search, watch pages, mixes, subscriptions. Proxies video (`local: true`) so playback works from any network. |
| **invidious-companion** | Handles YouTube's anti-bot checks and stream signature decoding. Without it pages load but videos often fail. |
| **PostgreSQL** | Stores your account, subscriptions, playlists, watch history. Local only, not synced to Google. |

Only nginx publishes ports (80, 443). Invidious, companion and the database are reachable only inside the Docker network.

### Behavior to expect
- **Search:** same results an anonymous YouTube visitor gets.
- **Home feed:** no personalization. Use Trending, or subscribe to channels and use the Subscriptions feed.
- **Mix / radio:** a video-seeded mix (`list=RD<videoId>`) works. Account-based "My Mix" does not.
- **Autoplay:** reliable inside a playlist or mix, weaker (related-video drift) on a bare video.
- **Quality:** default is `small` (240p single-file stream), chosen for low bandwidth and no DASH requirement.

### TV quality while keeping the car default

Keep `quality: small` in `docker-compose.yml` for the car. On the TV, open [Preferences](https://minetube.ddns.net/preferences), set **Preferred video quality** to **DASH** and **Preferred DASH video quality** to **Best**, then save. No Docker restart is needed.

Logged-in preferences are saved to the account and shared across devices. Use separate car and TV accounts, or stay logged out on the TV to save its preferences only in that browser's cookies.

For a single-video test, append `&quality=dash&quality_dash=best` to an existing watch URL. `Best` selects the highest available quality; use `Auto` if playback buffers. Actual resolution depends on the video, TV browser support, and bandwidth. See the [Invidious quality parameters](https://docs.invidious.io/url-parameters/).

## Files

```
invidious/
  docker-compose.yml   # nginx, invidious, companion, postgres
  nginx.conf           # HTTPS reverse proxy
  certs.sh             # issue / renew / test Let's Encrypt certificate
  README.md
```

## Prerequisites

- A machine that stays on at home (Mac mini, Linux box) with Docker and Docker Compose.
- A domain name (for example `yt.example.com`) with DNS you control.
- Router access to forward ports 80 and 443 to the server.
- Use a **home IP**. YouTube blocks datacenter IPs aggressively.

## Deploy

### 1. Edit the config
In `docker-compose.yml` and `nginx.conf`:
1. Replace `yt.example.com` with your domain (compose: `domain`; nginx: both `server_name` lines and the certificate paths).
2. Replace `CHANGE_DB_PASSWORD` (appears twice: Invidious config and Postgres env, values must match).
3. Replace `CHANGE_ME_RANDOM_HMAC_KEY` with a long random string.
4. Replace `CHANGE_ME_16CHAR` (appears twice) with the same random **16-character** string.

Generate values:
```bash
openssl rand -hex 16   # 32 chars, use for hmac_key
openssl rand -hex 8    # 16 chars, use for the companion key
```

### 2. Network
1. Point the domain's DNS A record at your home public IP (use a DDNS service if your IP changes).
2. Forward router ports **80** and **443** to the server.
3. Give the server a fixed LAN IP.

### 3. Get a certificate
Edit `DOMAIN` and `EMAIL` at the top of `certs.sh` (or pass them as env vars), then run it once. Port 80 must be forwarded and free, and the stack should not be running yet:
```bash
chmod +x certs.sh
sudo ./certs.sh issue
```
Certificates land in `/etc/letsencrypt/live/<domain>/`, which compose mounts read-only into nginx.

`certs.sh` commands:

| Command | What it does |
|---|---|
| `issue` | First-time certificate (standalone challenge, briefly stops nginx if it is running). Skips if one already exists unless `FORCE=1`. |
| `renew` | Renews only if the certificate is within 30 days of expiry. Stops nginx, renews, starts nginx. Does nothing otherwise. |
| `test` | Dry-run renewal against Let's Encrypt staging. Issues nothing real. |
| `status` | Shows certificates and expiry dates. |

> **macOS note:** install certbot with `brew install certbot`. Docker Desktop must be allowed to mount `/etc/letsencrypt` (Settings, Resources, File sharing), or change the compose volume to a path Docker can already read, such as a folder in your home directory, and use `--config-dir`, `--work-dir`, `--logs-dir` in `certs.sh`.

### 4. Start
```bash
docker compose up -d
docker compose logs -f
```
Wait until Invidious reports it is listening and the database tables are created (`check_tables: true` does this automatically).

### 5. Create your account, then lock registration
1. Open `https://yt.example.com`, register your account.
2. In `docker-compose.yml` set `registration_enabled: false`.
3. Apply it: `docker compose up -d`.

### 6. Optional: basic auth in front
```bash
sudo apt install apache2-utils     # or: brew install httpd
htpasswd -c .htpasswd yourname
```
Uncomment the `.htpasswd` volume in compose and the two `auth_basic` lines in `nginx.conf`, then `docker compose up -d`.

### 7. Auto-renew with cron
Let's Encrypt certificates last 90 days. Run `renew` daily. It is a no-op until the certificate is within 30 days of expiry, so running it often is safe.

1. Prove the renewal path works first (dry run):
   ```bash
   sudo ./certs.sh test
   ```
2. Open root's crontab:
   ```bash
   sudo crontab -e
   ```
3. Add this line (adjust the path to where the project lives):
   ```cron
   17 3 * * * /opt/invidious/certs.sh renew >> /var/log/invidious-cert-renew.log 2>&1
   ```
   This runs at 03:17 every day. Use an odd minute rather than `:00` to avoid peak load on Let's Encrypt.
4. Confirm it is installed: `sudo crontab -l`.
5. After the first real renewal (about day 60), check the log and expiry:
   ```bash
   tail /var/log/invidious-cert-renew.log
   sudo ./certs.sh status
   ```

Renewal causes a few seconds of downtime while nginx is stopped for the port 80 challenge. If you want zero downtime, switch certbot to the webroot method instead (needs `/var/www/certbot` mounted into nginx, which `nginx.conf` already serves under `/.well-known/acme-challenge/`).

On macOS, cron works but may need Full Disk Access for `/usr/sbin/cron`. A `launchd` job is the more reliable choice there.

## Test

Test in this order so failures are easy to localize.

1. **Containers healthy**
   ```bash
   docker compose ps
   ```
   All four services should be `running`.
2. **Local response (on the server)**
   ```bash
   curl -I https://yt.example.com
   ```
   Expect `200`. If you see a certificate error, recheck the cert paths.
3. **Home LAN browser:** open the site, search for a video, press play. Confirm no ads and that 240p plays.
4. **Mix test:** open any video, then load `https://yt.example.com/watch?v=VIDEO_ID&list=RDVIDEO_ID` using the same ID twice. It should advance through the mix. Try two or three seeds.
5. **Subscriptions:** subscribe to a channel and open the Subscriptions feed.
6. **Outside network:** turn off Wi-Fi on your phone and open the site over cellular. Playback working here confirms video proxying and port forwarding.
7. **Car browser:** open the URL on the head unit, log in, start a mix. Check that autoplay works after the first tap.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Page loads, video fails | Companion not working or outdated. Check `docker compose logs companion`, confirm both keys match and are 16 chars, then pull newer images. |
| Login does not stick | `https_only: true` but you are using plain HTTP. Use HTTPS or set it to `false` for LAN tests. |
| Works at home, not on cellular | `local: true` missing for the account, or ports not forwarded. Check the Invidious preferences page. |
| 502 from nginx | Invidious not up yet. Check `docker compose logs invidious`. |
| Video stutters in the car | Home upload is limited, or `quality` is set too high. Keep `small`. |
| Player will not start in car browser | Old browser. Keep `quality: small`, avoid DASH, and try the simplest player style in preferences. |
| Cert expired / browser warning | Renewal cron did not run. Check `/var/log/invidious-cert-renew.log`, run `sudo ./certs.sh test`, and confirm port 80 is forwarded. |
| Everything broke suddenly | YouTube changed something. Update images (below). |

## Maintenance

YouTube changes its anti-bot and API behavior often. Most fixes are just newer images:
```bash
docker compose pull
docker compose up -d
```
Check the Invidious and invidious-companion release notes if an update does not fix it. Config key names have changed between versions, so compare with the official `docker-compose.yml` in the Invidious repository when upgrading.

Certificates are renewed by the cron job from step 7. Run `sudo ./certs.sh status` now and then to confirm the expiry date keeps moving forward.

## Security notes

- Do not leave registration open. Strangers will use your bandwidth, and your home IP can get flagged by YouTube.
- Consider basic auth or Cloudflare Access in front.
- Only nginx is exposed. Do not publish ports 3000, 8282, or 5432.
- Back up the `postgresdata` volume if you care about subscriptions and history.

## Alternatives for HTTPS

- **Cloudflare Tunnel:** no port forwarding or certbot, but heavy video traffic can conflict with Cloudflare's free-plan terms.
- **Cheap VPS tunnel:** your own cert on the VPS, no home port exposure.
- **HTTP on LAN only:** set `https_only: false`, `external_port: 80`, and remove the 443 block. Fine for home testing, not for internet access.

## Legal and safety

Using alternative YouTube frontends is against YouTube's Terms of Service. Keep it to personal use. Watching video while driving is unsafe and illegal in many places, so use it parked or as a passenger.
