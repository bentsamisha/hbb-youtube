"""YouTube upload (Data API v3) and Google Drive read access for the scripts. Used by hbb.py.

Logins live in HBB/System/secrets/ (never committed, never shared):
  google_client.json   OAuth "Desktop app" client from Google Cloud (see Guides/Setup Steps.md)
  youtube_token.json   written by `hbb.py auth youtube` (pick the HoneyBadger and Bees channel)
  drive_token.json     written by `hbb.py auth drive`   (read-only, only to export the script docs)
"""
import json
import os
from datetime import datetime, timezone

PIPE = os.path.dirname(os.path.realpath(__file__))
SECRETS = os.path.join(os.path.dirname(PIPE), "secrets")

YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
             "https://www.googleapis.com/auth/youtube",           # thumbnail, playlist, change a scheduled video
             "https://www.googleapis.com/auth/youtube.force-ssl"]  # caption upload
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class PublishError(Exception):
    pass


class NeedsAuth(PublishError):
    pass


def _path(name):
    return os.path.join(SECRETS, name)


def _load(name):
    try:
        with open(_path(name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def _save(name, data):
    os.makedirs(SECRETS, exist_ok=True)
    with open(_path(name), "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(_path(name), 0o600)


def _client_file():
    for n in ("google_client.json", "youtube_client.json"):
        if os.path.exists(_path(n)):
            return _path(n)
    raise NeedsAuth("put the OAuth client file from Google Cloud at System/secrets/google_client.json first")


def auth(kind):
    from google_auth_oauthlib.flow import InstalledAppFlow
    scopes = YT_SCOPES if kind == "youtube" else DRIVE_SCOPES
    flow = InstalledAppFlow.from_client_secrets_file(_client_file(), scopes)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    _save("%s_token.json" % kind, json.loads(creds.to_json()))
    if kind == "youtube":
        ch = youtube().channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
        return "YouTube connected: %s" % ch
    return "Google Drive connected (read-only)"


def _service(kind, api, version):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    data = _load("%s_token.json" % kind)
    if not data:
        raise NeedsAuth("run: hbb.py auth %s" % kind)
    creds = Credentials.from_authorized_user_info(data, YT_SCOPES if kind == "youtube" else DRIVE_SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as e:
            raise NeedsAuth("%s login expired (%s): run hbb.py auth %s" % (kind, e, kind))
        _save("%s_token.json" % kind, json.loads(creds.to_json()))
    return build(api, version, credentials=creds, cache_discovery=False)


def youtube():
    return _service("youtube", "youtube", "v3")


def drive():
    return _service("drive", "drive", "v3")


def youtube_upload(video, yt, publish_at_utc, thumbnail=None, captions=None, category="27", playlist_id=""):
    """Upload as private with a publish time: YouTube makes it public itself at publish_at_utc, even with the Mac off.
    yt: {"title", "description", "tags"}. Returns {"id", "url", "publish_at", ...}."""
    from googleapiclient.http import MediaFileUpload
    api = youtube()
    status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": bool(yt.get("synthetic"))}
    if publish_at_utc > datetime.now(timezone.utc):
        status["publishAt"] = publish_at_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        status["privacyStatus"] = "public"
    body = {"snippet": {"title": yt["title"][:100], "description": yt["description"][:4900], "tags": yt["tags"],
                        "categoryId": category, "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": status}
    req = api.videos().insert(part="snippet,status", body=body,
                              media_body=MediaFileUpload(video, mimetype="video/mp4", chunksize=16 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    st = resp.get("status", {})
    res = {"id": vid, "url": "https://youtu.be/%s" % vid, "publish_at": status.get("publishAt", "now")}
    if st.get("privacyStatus") == "private" and "publishAt" not in st and "publishAt" in status:
        res["warning"] = ("YouTube kept it private without the schedule: the Google Cloud project still needs the "
                          "YouTube API audit. Set the schedule by hand in YouTube Studio this time.")
    if thumbnail and os.path.exists(thumbnail):
        try:
            api.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumbnail, mimetype="image/jpeg")).execute()
            res["thumbnail"] = "set"
        except Exception as e:
            res["thumbnail"] = "failed: %s" % str(e)[:200]
    if captions and os.path.exists(captions):
        try:
            api.captions().insert(part="snippet", body={"snippet": {"videoId": vid, "language": "en", "name": "English",
                                                                     "isDraft": False}},
                                  media_body=MediaFileUpload(captions, mimetype="application/octet-stream")).execute()
            res["captions"] = "uploaded"
        except Exception as e:
            res["captions"] = "failed: %s" % str(e)[:200]
    if playlist_id:
        try:
            api.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": playlist_id,
                                       "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
            res["playlist"] = playlist_id
        except Exception as e:
            res["playlist"] = "failed: %s" % str(e)[:200]
    return res


def youtube_status(video_id):
    r = youtube().videos().list(part="status", id=video_id).execute()
    return (r.get("items") or [{}])[0].get("status", {})


def youtube_reschedule(video_id, publish_at_utc):
    api = youtube()
    api.videos().update(part="status", body={"id": video_id, "status": {
        "privacyStatus": "private", "publishAt": publish_at_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "selfDeclaredMadeForKids": False}}).execute()
