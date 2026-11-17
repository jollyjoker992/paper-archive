# Security

This tool handles personal health data (scans and transcriptions of medical documents) and Medplum client
secrets. Please report a vulnerability privately through GitHub: **Security → Report a vulnerability** on
this repository. Do not open a public issue for it.

## Design rules the code follows

- Health data lives only in the data directory (`data/` by default, `PAPER_ARCHIVE_DIR`), which git
  ignores. Tests, fixtures, examples and documentation use made-up people only.
- Secrets live only in `.env`, ignored by git. No secret is printed or logged. CI scans the full git
  history with gitleaks on every push and pull request.
- Nothing reaches the FHIR server without a person's approval of exactly the transcription that is loaded:
  the approval is bound to the SHA-256 of `extraction.json`, and any later change voids it.
- A document is checked against the person it is filed under (name, birth date, age, sex), so a paper of
  one family member is not loaded into another's record.
- The only network calls are `paper_archive.py load` (to the configured FHIR server), `backup` and
  `restore` (to the storage the owner configured) and `tools/loinc_check.py` (LOINC numbers only, to the
  NLM search API).
- A backup is never uploaded readable by omission: `backup` stops unless `PAPER_BACKUP_KEY` (encrypted
  with rclone crypt, names included) or `PAPER_BACKUP_PLAIN=true` is set. Storage credentials and the key
  reach rclone in its environment or on stdin, never on a command line. Nothing is deleted from a backup
  target, and restore writes only into a new folder, checks every file's sha256 and takes no path from
  the target.
- The AI agent that transcribes a document necessarily sees it. Which agent and provider that is, is the
  owner's choice; the tool records it on every document (`transcription` extension).

## If you leaked something

A Medplum client secret: delete the client in Medplum and create a new one. A storage key or token:
revoke it at the provider and create a new one. The backup key (`PAPER_BACKUP_KEY`): the encrypted copies
are readable by whoever has it and access to the storage; make a new key and back up to a new bucket or
prefix, then delete the old one. A scan or transcription pushed
to a repository: removing it from git history is not enough on its own; treat it as disclosed.
