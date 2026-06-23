# Deploy to VPS — Step by Step

## 1. Install Docker on your VPS (one time)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in so the group change takes effect
```

## 2. Copy the project to your VPS

From your local machine:
```bash
scp -r reel-app/ user@YOUR_VPS_IP:~/reel-app
```
Or if you use Git:
```bash
git clone https://github.com/your-repo/reel-app ~/reel-app
```

## 3. Build and start the container

```bash
cd ~/reel-app
docker compose up -d --build
```

That's it. The app is now running on port 5000.  
Test it: `curl http://localhost:5000` — you should get back HTML.

## 4. Point Nginx at it (so users hit port 80/443)

```bash
sudo apt install nginx -y
sudo cp nginx.conf /etc/nginx/sites-available/reels
```

Edit the file and replace `your-domain.com` with your actual domain or IP:
```bash
sudo nano /etc/nginx/sites-available/reels
```

Enable it:
```bash
sudo ln -s /etc/nginx/sites-available/reels /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Now visit `http://your-domain.com` in a browser — the UI should appear.

## 5. (Optional) HTTPS via Let's Encrypt

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com
```
Certbot auto-renews. Done.

---

## Day-to-day commands

| Task | Command |
|------|---------|
| View live logs | `docker compose logs -f` |
| Restart the app | `docker compose restart` |
| Stop the app | `docker compose down` |
| Rebuild after code change | `docker compose up -d --build` |
| Check container status | `docker compose ps` |

---

## How the logo persists

The `uploads/` folder is mounted as a Docker volume (`./uploads:/app/uploads`).  
Uploading a logo once saves it to `~/reel-app/uploads/logo.png` on the VPS.  
It survives container restarts and rebuilds — users only need to upload it once.

## Output files

Finished ZIPs land in `~/reel-app/output/` on the VPS and are served directly  
through the download link in the UI. You can clean this folder periodically:

```bash
# Delete ZIPs older than 7 days
find ~/reel-app/output -name "*.zip" -mtime +7 -delete
```

Or add a cron job:
```bash
crontab -e
# Add this line:
0 3 * * * find /root/reel-app/output -name "*.zip" -mtime +7 -delete
```
