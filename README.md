# CV Automation

Keeps CV PDFs in sync with a Google Doc.

The CV is written in Google Docs. The script watches that doc, and whenever a change is detected:

- exports it as `LMT_CV_ACADEMIC.pdf` and drops it in a Drive folder, backing up whatever was there before
- makes a throwaway copy of the doc, cuts everything from the "VOLUNTEERING" heading down, exports that as `LMT_CV_PROFESSIONAL.pdf`, and uploads it too
- deletes backups older than two weeks so the folder doesn't fill up

It polls the doc once a minute and checks if `modifiedTime` changed.

## Running it

```
pip install -r requirements.txt
python main.py
```

Requires a Google Cloud OAuth client (Desktop app type, with the Drive and Docs APIs enabled). The client secret should be saved as `AutomationCVSecret.json` next to `main.py`. The first run opens a browser for the OAuth consent screen and writes `token.json`; after that it refreshes automatically.

## Or with Docker

```
docker compose up -d
```

`AutomationCVSecret.json` and `token.json` need to already exist in the folder — compose only mounts them in, it doesn't create them.

## Deploying

Pushing to `main` triggers the GitHub Action, which builds the image, pushes it to GHCR, copies `docker-compose.yml` to the server, and restarts the stack over SSH. See `.github/workflows/main.yml` for the secrets it expects.
