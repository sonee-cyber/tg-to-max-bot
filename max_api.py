import os, logging, requests
logger = logging.getLogger(__name__)
BASE_URL = "https://platform-api.max.ru"
TOKEN = os.getenv("MAX_BOT_TOKEN")
if not TOKEN: raise RuntimeError("MAX_BOT_TOKEN не задан")
HEADERS = {"Authorization": TOKEN}

def _get_upload_url(t):
    r=requests.post(f"{BASE_URL}/uploads?type={t}", headers=HEADERS, timeout=30)
    r.raise_for_status(); return r.json()["url"]

def _upload(path, t):
    url=_get_upload_url(t)
    with open(path,"rb") as f:
        r=requests.post(url, files={"data": (os.path.basename(path), f)}, timeout=120)
        r.raise_for_status()
        data=r.json()
        logger.info(f"MAX upload raw: {str(data)[:200]}...")
        return data

def _send(chat_id, text="", attachments=None):
    payload={}
    if text: payload["text"]=text[:4000]; payload["format"]="markdown"
    if attachments: payload["attachments"]=attachments
    r=requests.post(f"{BASE_URL}/messages", params={"chat_id":chat_id}, headers=HEADERS, json=payload, timeout=30)
    if r.status_code!=200: logger.error(f"MAX API {r.status_code}: {r.text}")
    r.raise_for_status(); return r.json()

def send_text(c,t): return _send(c,text=t)
def send_photo(c,p,cap=""): return send_photos(c,[p],cap)

def send_photos(c, paths, cap=""):
    atts=[]
    for p in paths:
        d=_upload(p,"image")
        payload={"photos":d["photos"]} if "photos" in d else {"token":d.get("token")}
        atts.append({"type":"image","payload":payload})
    return _send(c,text=cap,attachments=atts)

def send_video(c,p,cap=""):
    d=_upload(p,"video")
    token=d.get("token")
    if not token: raise RuntimeError(f"MAX не вернул token: {d}")
    return _send(c,text=cap,attachments=[{"type":"video","payload":{"token":token}}])

def send_videos(c, paths, cap=""):
    atts=[]
    for p in paths:
        d=_upload(p,"video")
        token=d.get("token")
        if token: atts.append({"type":"video","payload":{"token":token}})
    return _send(c,text=cap,attachments=atts)

def send_document(c,p,cap=""):
    d=_upload(p,"file")
    return _send(c,text=cap,attachments=[{"type":"file","payload":{"token":d.get("token")}}])