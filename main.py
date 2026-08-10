import io
import logging
import time

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/documents',
]
KEY_FILE = './AutomationCVSecret.json'
FILE_ID = '19RH76fwAjJUlnkFIdCLUF-9xZVEFa9qjnFwxjaCm-cU'
FOLDER_ID= '1-sy6eMWlVuHhk_f4JDFHZhJnD0wLS7Kg'

CUTOFF_TEXT = 'VOLUNTEERING'
COPY_NAME = 'LMT_CV_PROFESSIONAL'
POLL_INTERVAL_SECONDS = 60

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

cred = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=cred)
docs_service = build('docs', 'v1', credentials=cred)

def get_modified_time(fileId: str) -> str:
    metadata = drive_service.files().get(fileId=fileId, fields='modifiedTime').execute()
    return metadata['modifiedTime']

def get_file_name(fileId: str) -> str:
    metadata = drive_service.files().get(fileId=fileId, fields='name').execute()
    return metadata['name']

def export_pdf(fileId: str, destination_path: str) -> None:
    request = drive_service.files().export_media(fileId=fileId, mimeType='application/pdf')
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    with open(destination_path, 'wb') as f:
        f.write(buffer.getvalue())
    logger.info('Exported %s', destination_path)

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
    file_name = get_file_name(fileId)
    export_pdf(fileId, f'{file_name}.pdf')

    copy_id = copy_file(fileId)
    try:
        remove_sections(copy_id)
        export_pdf(copy_id, f'{COPY_NAME}.pdf')
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
        except OSError as error:
            logger.error('Filesystem error while processing update: %s', error)

        time.sleep(interval_seconds)

def main():
    try:
        watch_file(FILE_ID)
    except KeyboardInterrupt:
        logger.info('Stopped watching file')
    except FileNotFoundError:
        logger.error('Service account key file not found: %s', KEY_FILE)
    except HttpError as error:
        logger.error('Google API error: %s', error)

if __name__ == "__main__":
    main()
