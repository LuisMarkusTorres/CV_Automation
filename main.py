import io
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
]
CLIENT_SECRET_FILE = './AutomationCVSecret.json'
TOKEN_FILE = './token.json'
FILE_ID = '19RH76fwAjJUlnkFIdCLUF-9xZVEFa9qjnFwxjaCm-cU'
FOLDER_ID= '1-sy6eMWlVuHhk_f4JDFHZhJnD0wLS7Kg'

CUTOFF_TEXT = 'VOLUNTEERING'
ORIGINAL_NAME = 'LMT_CV_ACADEMIC'
COPY_NAME = 'LMT_CV_PROFESSIONAL'
BACKUP_SUFFIX = '_backup'
BACKUP_MAX_AGE_DAYS = 14
POLL_INTERVAL_SECONDS = 60

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def load_credentials() -> Credentials:
    cred = None
    try:
        cred = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    except (FileNotFoundError, ValueError):
        cred = None

    if cred and cred.valid:
        return cred

    if cred and cred.expired and cred.refresh_token:
        cred.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
        cred = flow.run_local_server(port=0)

    with open(TOKEN_FILE, 'w') as token_file:
        token_file.write(cred.to_json())

    return cred


try:
    cred = load_credentials()
except FileNotFoundError:
    logger.error('OAuth client secret file not found: %s', CLIENT_SECRET_FILE)
    raise SystemExit(1)

drive_service = build('drive', 'v3', credentials=cred)
docs_service = build('docs', 'v1', credentials=cred)

def get_modified_time(fileId: str) -> str:
    metadata = drive_service.files().get(fileId=fileId, fields='modifiedTime').execute()
    return metadata['modifiedTime']

def get_file_name(fileId: str) -> str:
    metadata = drive_service.files().get(fileId=fileId, fields='name').execute()
    return metadata['name']

def export_pdf_bytes(fileId: str) -> bytes:
    request = drive_service.files().export_media(fileId=fileId, mimeType='application/pdf')
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def find_files_by_name(name: str, folder_id: str) -> list:
    escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
    query = f"name = '{escaped_name}' and '{folder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, fields='files(id, name)').execute()
    return results.get('files', [])


def rename_file(fileId: str, new_name: str) -> None:
    drive_service.files().update(fileId=fileId, body={'name': new_name}).execute()


def backup_existing_file(name: str, folder_id: str) -> None:
    base_name, ext = os.path.splitext(name)
    backup_name = f'{base_name}{BACKUP_SUFFIX}{ext}'

    # Only the most recent version is kept as a backup, so a prior backup
    # is forcibly removed before the current file takes its place.
    for old_backup in find_files_by_name(backup_name, folder_id):
        delete_file(old_backup['id'])
        logger.info('Deleted previous backup "%s"', backup_name)

    for existing in find_files_by_name(name, folder_id):
        rename_file(existing['id'], backup_name)
        logger.info('Renamed existing file "%s" to "%s"', name, backup_name)


def upload_pdf(name: str, content: bytes, folder_id: str) -> None:
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype='application/pdf', resumable=False)
    body = {'name': name, 'parents': [folder_id]}
    drive_service.files().create(body=body, media_body=media, fields='id').execute()
    logger.info('Uploaded "%s" to folder %s', name, folder_id)


def cleanup_old_backups(folder_id: str, max_age_days: int = BACKUP_MAX_AGE_DAYS) -> None:
    escaped_suffix = BACKUP_SUFFIX.replace("\\", "\\\\").replace("'", "\\'")
    query = f"name contains '{escaped_suffix}' and '{folder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, fields='files(id, name, modifiedTime)').execute()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    for existing in results.get('files', []):
        modified_time = datetime.fromisoformat(existing['modifiedTime'].replace('Z', '+00:00'))
        if modified_time < cutoff:
            delete_file(existing['id'])
            logger.info('Deleted expired backup "%s"', existing['name'])

def copy_file(fileId: str) -> str:
    body = {
        'name': COPY_NAME
    }
    driveResponse = drive_service.files().copy(
        fileId=fileId,
        body=body
    ).execute()
    documentCopyId = driveResponse.get('id')
    return documentCopyId

def delete_file(fileId: str) -> None:
    drive_service.files().delete(fileId=fileId).execute()

def remove_sections(fileId: str) -> None:
    document = docs_service.documents().get(documentId=fileId).execute()
    content = document.get('body', {}).get('content', [])

    cut_start_index = None
    for element in content:
        paragraph = element.get('paragraph')
        if not paragraph:
            continue
        text = ''.join(
            run.get('textRun', {}).get('content', '')
            for run in paragraph.get('elements', [])
        )
        if CUTOFF_TEXT in text:
            cut_start_index = element['startIndex']
            break

    if cut_start_index is None:
        logger.warning('Marker "%s" not found, skipping section removal', CUTOFF_TEXT)
        return

    document_end_index = content[-1]['endIndex']
    # The final index of a document body is reserved for the trailing newline
    # and cannot be included in a delete range.
    delete_end_index = document_end_index - 1

    if delete_end_index <= cut_start_index:
        logger.warning('Nothing to remove after marker "%s"', CUTOFF_TEXT)
        return

    docs_service.documents().batchUpdate(
        documentId=fileId,
        body={
            'requests': [{
                'deleteContentRange': {
                    'range': {
                        'startIndex': cut_start_index,
                        'endIndex': delete_end_index,
                    }
                }
            }]
        }
    ).execute()
    logger.info('Removed sections from "%s" onward', CUTOFF_TEXT)

def process_update(fileId: str) -> None:
    cleanup_old_backups(FOLDER_ID)

    file_name = get_file_name(fileId)
    if file_name != ORIGINAL_NAME:
        raise ValueError(
            f'Expected source file to be named "{ORIGINAL_NAME}", found "{file_name}"'
        )

    original_pdf_name = f'{ORIGINAL_NAME}.pdf'
    professional_pdf_name = f'{COPY_NAME}.pdf'

    # Each file's backup is taken immediately before its own upload, so an
    # in-progress run never leaves two targets sharing the same backup slot.
    backup_existing_file(original_pdf_name, FOLDER_ID)
    original_pdf_bytes = export_pdf_bytes(fileId)
    upload_pdf(original_pdf_name, original_pdf_bytes, FOLDER_ID)

    copy_id = copy_file(fileId)
    try:
        remove_sections(copy_id)
        professional_pdf_bytes = export_pdf_bytes(copy_id)
        backup_existing_file(professional_pdf_name, FOLDER_ID)
        upload_pdf(professional_pdf_name, professional_pdf_bytes, FOLDER_ID)
    finally:
        delete_file(copy_id)

def watch_file(fileId: str, interval_seconds: int = POLL_INTERVAL_SECONDS) -> None:
    logger.info('Watching file %s (checking every %ss)', fileId, interval_seconds)
    last_modified = None
    while True:
        try:
            modified_time = get_modified_time(fileId)
            if last_modified is None:
                last_modified = modified_time
            elif modified_time != last_modified:
                logger.info('Change detected, processing update')
                last_modified = modified_time
                process_update(fileId)
        except HttpError as error:
            logger.error('Google API error while watching file: %s', error)
        except Exception:
            # Keep the watch loop alive regardless of the failure so a single
            # bad response or unexpected document shape doesn't kill the daemon.
            logger.exception('Unexpected error while processing update')

        time.sleep(interval_seconds)

def main():
    try:
        watch_file(FILE_ID)
    except KeyboardInterrupt:
        logger.info('Stopped watching file')
    except HttpError as error:
        logger.error('Google API error: %s', error)

if __name__ == "__main__":
    main()
