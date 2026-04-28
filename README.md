# Main-IPTV
My Main IPTV project

I am creating this for my mom (old lady) who are not good in IT.
Banyak sangat apps for each channel. (which is mengarut. takkan nak switch between apps untuk tukar channel?)
Contoh, TONTON untuk Media Prima, RTM Klik untuk RTM.
I am creating this to compile all the free channels in malaysia and more.
Untuk memudahkan penggunaan.
IPTV ini dibuat untuk kegunaan sendiri.
Project ini inspired by project-project IPTV creator yang ada di github (banyak yg tak boleh pakai sebab expired).
Project ini juga di inpirasikan dari MYIPTV4U (free version) tetapi sebab dah tak boleh guna (even untuk channel free malaysia) project ini dilakukan.
Main purpose of this project to compile free live TV malaysia into 1 apps or IPTV player.
----------------------------------------------------------------------------------------------------------------------------------------------------
📺 Personal Malaysian IPTV Playlist (Private)
⚠️ IMPORTANT: This repository is PRIVATE.
The playlist URL inside is not intended for public distribution. Sharing it publicly may lead to channel blocks or legal issues. Use it only for personal, non‑commercial viewing.

🎯 Purpose
This is my personal, auto‑maintained M3U playlist of free, publicly available Malaysian live TV streams (RTM, TV3, Astro Awani, etc.).

One playlist, one IPTV player.

No more switching between TONTON, RTM Klik, and other apps.

Private — because public playlists get blocked or go stale quickly.

🔧 How I Use It
The playlist file is stored in this private repo:
playlist.m3u

I copy the raw URL of that file into my IPTV player (VLC, TiviMate, OTT Navigator, etc.).

GitHub Actions (private workflows) automatically validate and clean the playlist weekly.

🔄 Maintenance Automation (Private)
Even though the repo is private, I still run GitHub Actions to keep the playlist healthy:

Workflow	What it does
update.yml	Runs weekly, checks each channel URL, removes dead links
format.yml	Ensures M3U syntax is correct after any manual edit
validate.yml	Quick syntax check on every push
These workflows are inspired by IPTV‑org but adapted for personal use only.

📱 My Recommended Players
Platform	App	Notes
Android TV	TiviMate	Best for family / non‑technical users
Android phone	OTT Navigator	Free, reliable
Windows / Mac / Linux	VLC	Paste playlist URL → Media > Open Network Stream
iOS	GSE SMART IPTV	Works with private URLs
🛡️ Why Private?
Copyright / legal caution — Even though I only link to free, official streams, broadcasters sometimes object to being aggregated in public playlists.

Avoid blocks — Public M3U links are often targeted by ISPs or channel owners.

Longer link life — Private means fewer people hammering the streams, so the original URLs survive longer.

⚠️ Personal Disclaimer
This repository contains no video files — only text URLs of publicly accessible streams.

The streams are the property of their respective broadcasters (RTM, Media Prima, Astro, etc.).

I do not encourage redistribution of this playlist outside my own devices or trusted family members.

If a channel owner requests removal, I will comply immediately.

🔒 Keeping It Private
Do not make the repo public.

Do not share the raw playlist URL on forums, social media, or GitHub public gists.

If I need to share with family, I give them direct access to the private repo or manually give them the URL via a secure message.

🙏 Acknowledgments
My mom — for inspiring the original idea.

IPTV-org — For the automated validation concepts (used privately).

The many expired public playlists that taught me what not to do.

📝 Notes to Myself
Update the playlist manually by editing playlist.m3u when I find new working URLs.

Dead URLs are automatically removed by the weekly workflow.

Backup – this repo is my backup. If local files get corrupted, I just clone again.

Never commit login credentials or paid subscription URLs.

🌟 Keep it private, keep it working.
