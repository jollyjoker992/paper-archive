# Cloud backup

`paper_archive.py backup` copies what cannot be made again (the scans, `document.pdf`, the transcription,
your approval, `people.toml`) to AWS S3, Cloudflare R2, Google Drive or any rclone remote. A target is
turned on by putting its credentials in `.env` (or the environment): every target whose credentials are set
gets a copy. It follows the backup of garmin-archive and healthkit-archive.

```sh
python3 paper_archive.py backup                    # every configured target; --target aws for one
python3 paper_archive.py backup status             # "backup r2 (encrypted): 28/28 document(s), up to date, checked …"
python3 paper_archive.py restore --from r2 --out ~/paper-restored
```

`backup` is not part of `intake` or `approve`: run it after you worked on the archive, or on a schedule
(cron has a minimal `PATH`; set `PAPER_RCLONE_BIN=/opt/homebrew/bin/rclone` if rclone is not found).

## What is uploaded

```
<target>/encrypted/   or   <target>/plain/
  documents/<doc id>/<doc id>.<key>.tar     one archive per version of a document
  extras/people.<key>.toml                  people.toml
  extras/load-manifest.<key>.json           fhir/load-manifest.json (what is in Medplum, and under which ids)
  manifest.json                             the current version of every document and extra file, with sha256
```

- A document's archive holds `original/*`, `document.pdf`, `meta.json`, `extraction.json` and `review.json`:
  the files of its folder that cannot be rebuilt. `fhir.ndjson` and `fhir/` are not uploaded; `map` writes
  them again.
- A document is backed up from intake on. When its transcription or approval changes, or its PDF is
  rebuilt, it gets a new archive; `<key>` is the start of the archive's sha256.
- **A file on a target is never overwritten and never deleted.** Older versions stay, and so does a
  document you removed with `remove`: it only leaves `manifest.json`, and restore does not bring it back.
  To delete it from a target too, delete `documents/<doc id>/` there yourself (in an encrypted backup the
  names are not readable: see "Without this tool").
- `manifest.json` is the only file that changes. It is written last and names only files the target holds,
  so a run that fails part way leaves the previous, complete backup. While a new version of a document is
  not uploaded yet, the manifest keeps naming the last one.
- Each run lists the target first: a file deleted there is uploaded again. `<data dir>/backup/state.json`
  only caches what was hashed and uploaded; if it is lost, the next run finds everything again in the
  listing and uploads nothing twice. `<data dir>/backup/` is not part of the backup.

## Encrypted or plain

Choose one in `.env`. With neither, `backup` stops (exit 2) and uploads nothing: a missing setting never
means a readable upload.

**Encrypted** (recommended; these are medical records):

```sh
python3 paper_archive.py backup new-key      # prints PAPER_BACKUP_KEY=… once
```

```sh
PAPER_BACKUP_KEY=…      # 52 characters; only keys made by `backup new-key` are accepted
```

Files go to `<target>/encrypted/` through rclone's [crypt](https://rclone.org/crypt/) (XSalsa20-Poly1305;
file and folder names are encrypted too). This tool adds no cryptography of its own. The provider sees how
many files there are and about how large they are, nothing else. The data directory on your computer stays
unencrypted.

**Keep a copy of the key outside this computer** (password manager, paper). Without it the encrypted backup
cannot be read by anyone. On another computer, put the same key in `.env` and run `restore`. A key that is
not the one the target was encrypted with is refused (`… holds files that PAPER_BACKUP_KEY cannot read`)
and nothing is changed. To change the key, use a new bucket or prefix.

**Plain:**

```sh
PAPER_BACKUP_PLAIN=true
```

Files go to `<target>/plain/` as they are: the provider (and anyone with access to the bucket or the Drive
folder) can read them, and you can open an archive there without this tool.

Switching from one to the other uploads everything again into the other folder and leaves the first one
where it is. Delete the old folder yourself (`rclone purge`, or in the provider's console; on Drive also
empty the bin).

## Install rclone

Uploads go through [rclone](https://rclone.org/install/) (`brew install rclone`, `apt install rclone`); set
`PAPER_RCLONE_BIN` if it is not on `PATH`. You do not need `rclone config`: the remotes are defined from the
variables below in rclone's environment (never on a command line), and a private
`<data dir>/backup/rclone.conf` is used, so your own rclone configuration is not touched.

## AWS S3

1. Create a bucket (S3 console, Block all public access on, default encryption on). Bucket names are
   global: if creation fails with `BucketAlreadyExists`, pick another name.
2. Create an IAM user with an access key and only this policy (replace `BUCKET`):

   ```json
   {"Version": "2012-10-17", "Statement": [
     {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::BUCKET"},
     {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": "arn:aws:s3:::BUCKET/*"}]}
   ```

3. In `.env`:

   ```sh
   PAPER_AWS_ACCESS_KEY_ID=AKIA…
   PAPER_AWS_SECRET_ACCESS_KEY=…
   PAPER_AWS_BUCKET=my-health-backup/paper     # bucket or bucket/prefix
   PAPER_AWS_REGION=ap-southeast-1             # the bucket's region (default us-east-1)
   ```

Objects are stored in the bucket's default class (STANDARD). Do not add a lifecycle rule that moves them to
Glacier or Deep Archive: restore reads the files directly.

## Cloudflare R2

1. R2 → Create bucket (e.g. `paper-backup`).
2. R2 → Manage API tokens → Create API token: permission **Object Read & Write**, applied to that bucket
   only. Copy the Access Key ID and Secret Access Key (shown once).
3. In `.env` (the account id is in the R2 overview, 32 hex characters):

   ```sh
   PAPER_R2_ACCOUNT_ID=0123456789abcdef0123456789abcdef
   PAPER_R2_ACCESS_KEY_ID=…
   PAPER_R2_SECRET_ACCESS_KEY=…
   PAPER_R2_BUCKET=paper-backup                # or bucket/prefix
   ```

## Google Drive

rclone's shared Google client id is being retired during 2026, so Drive needs your own sign-in app (an
OAuth client). It is free and takes about 10 minutes, once. If you made one for healthkit-archive, use the
same client id, secret and token here and skip to C. Google renames console menus from time to time; rclone
keeps the current names in <https://rclone.org/drive/#making-your-own-client-id>.

**A. Create the sign-in app** (any Google account; it does not have to be the one that stores the backup)

1. Open <https://console.cloud.google.com/>, then the project picker at the top → **New project** (e.g.
   `paper-backup`) → Create, and select it.
2. **APIs & Services → Enable APIs and services**, search **Google Drive API**, open it, **Enable**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform) → **Get started**: app name
   `paper-backup`, your email as user support email → Next → Audience **External** → your email as contact →
   agree → **Create**.
4. **Data Access → Add or remove scopes**, tick `https://www.googleapis.com/auth/drive` (or paste it in
   "Manually add scopes" → Add to table) → Update → **Save**.
5. **Audience → Add users**: add the Google account that will hold the backup as a test user → Save.
6. **Audience → Publish app** → Confirm. Without this the app stays in "Testing" and Google expires its
   tokens after 7 days, so backups would stop every week. You do not need Google's verification for
   personal use.
7. **Clients → Create client**: application type **Desktop app** → **Create**. Copy the **Client ID** and
   **Client secret** (treat the secret like a password).

**B. Get a token** (on a computer with a browser and rclone)

```sh
rclone authorize "drive" "CLIENT_ID" "CLIENT_SECRET"
```

A browser opens. Sign in with the account that should hold the backup. Google shows **"Google hasn't
verified this app"**: it is your own app, so click **Advanced → Go to paper-backup (unsafe)**, allow access
to Drive, and return to the terminal. rclone prints a JSON token between `--->` and `<---`.

**C. Put it in `.env`** (the token on one line, exactly as printed)

```sh
PAPER_GDRIVE_CLIENT_ID=….apps.googleusercontent.com
PAPER_GDRIVE_CLIENT_SECRET=…
PAPER_GDRIVE_TOKEN={"access_token":"…","token_type":"Bearer","refresh_token":"…","expiry":"…"}
PAPER_GDRIVE_FOLDER=paper-archive        # folder in My Drive, created on first upload (the default)
```

rclone renews the access token on each run and keeps the renewed one in `<data dir>/backup/rclone.conf`.
If Google revokes the token (password change, access removed under Google Account → Security → Third-party
access, app back in Testing), repeat B and replace `PAPER_GDRIVE_TOKEN`.

## Any other rclone remote, or a disk

```sh
PAPER_BACKUP_RCLONE_REMOTE=nas:backups/paper     # a remote from your own rclone config
PAPER_BACKUP_RCLONE_REMOTE=/Volumes/Backup/paper # or a folder
```

## Restore

```sh
python3 paper_archive.py restore --from r2 --out ~/paper-restored
```

It reads the target's `manifest.json`, downloads only the files it names, checks every one against its
sha256, and writes a data directory: `people.toml`, `documents/<doc id>/…` and `fhir/load-manifest.json`.
`--out` must be a new or empty folder; nothing is ever written into an existing archive. A document is
written whole or not at all, and each one that could not be restored is listed under `errors` (exit 1).
Use the same mode as the backup: `PAPER_BACKUP_KEY` for an encrypted one, `PAPER_BACKUP_PLAIN=true` for a
plain one. The restored folder is not encrypted.

To use it:

```sh
PAPER_ARCHIVE_DIR=~/paper-restored python3 paper_archive.py status
PAPER_ARCHIVE_DIR=~/paper-restored python3 paper_archive.py map      # writes the FHIR files again
```

Approvals come back with the documents: `review.json` is bound to the bytes of `extraction.json`, and both
are restored unchanged.

### Without this tool

A plain backup:

```sh
rclone copy r2remote:paper-backup/plain ./paper-plain
less paper-plain/manifest.json                                   # "documents" names each document's file
tar xf paper-plain/documents/d_…/d_….<key>.tar                   # -> d_…/document.pdf, original/, …
```

An encrypted backup: make a crypt remote with `rclone config` (new remote, type `crypt`, remote
`r2remote:paper-backup/encrypted`, filename encryption `standard`, directory name encryption `true`, your
key as the password, no second password), then use it in the same commands (`rclone copy papercrypt: …`).
With a wrong key rclone lists nothing and still exits 0. rclone does not notice a file cut short at a
64 KiB boundary, so compare each archive's `shasum -a 256` with `sha256` in `manifest.json` (`restore`
does this itself).

## Exit codes

`0` done. `1` something is retried on the next run: a target that could not be reached (the others go on),
a file that was not uploaded, a document that could not be read or restored. `2` a setting is wrong and
nothing was uploaded: no target, a target with only some of its variables set, neither
`PAPER_BACKUP_KEY` nor `PAPER_BACKUP_PLAIN`, another key than the target's, rclone not found.

## Known limits

- Someone who can write to the bucket or folder can damage or replace files. Restore checks every file
  against `manifest.json`, but in a plain backup the same person can change `manifest.json` too. Use the
  encrypted mode, and keep more than one target.
- `backup status` reads only `<data dir>/backup/state.json`: it shows what the last run found, not what
  the target holds now.
- Two `backup` runs at the same time are not prevented. Both upload the same files under the same names,
  so nothing is damaged, but one run's state can replace the other's until the next run.

## Troubleshooting

| Message | Cause and fix |
|---|---|
| `choose how the backup is stored` | Set `PAPER_BACKUP_KEY` or `PAPER_BACKUP_PLAIN=true`. |
| `aws backup: set PAPER_AWS_BUCKET …` | Only some of the target's variables are set. Set the rest or remove them. |
| `rclone (rclone) not found` | Install rclone or set `PAPER_RCLONE_BIN`. |
| `… holds files that PAPER_BACKUP_KEY cannot read` | The key in `.env` is not the one this target was encrypted with. |
| `cannot list aws: … AccessDenied` | The key lacks `s3:ListBucket`/`GetObject`/`PutObject` on that bucket (AWS), or the R2 token is not for that bucket. |
| `cannot list gdrive: … invalid_grant` | The Google token was revoked or expired (an app in "Testing"). Get a new one (Google Drive, B). |
| `… rateLimitExceeded` | Google throttling; the next run continues where this one stopped. |
| `N file(s) not uploaded; retried next run` | The upload did not complete. The target keeps its previous complete backup; the rest goes up next run. |
| `no backup found in encrypted/` | The target has no `manifest.json` in that folder: wrong bucket, prefix or mode, or nothing was backed up yet. |
