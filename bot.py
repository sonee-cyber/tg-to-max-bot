import os, tempfile, logging, time, requests, telebot, threading
from telebot.types import Message
from telebot.apihelper import ApiTelegramException

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")
LOCAL_API = os.getenv("BOT_API_URL", "https://api.telegram.org").rstrip("/")

if "railway.internal" in LOCAL_API:
    telebot.apihelper.API_URL = LOCAL_API + "/bot{0}/{1}"
    telebot.apihelper.FILE_URL = LOCAL_API + "/file/bot{0}/{1}"

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

album_buffer = {}
album_lock = threading.Lock()

def download_tg_file(file_id: str):
    try:
        r = requests.get(f"{LOCAL_API}/bot{TG_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=60)
        data = r.json()
        if not data.get("ok"):
            if "file is too big" in data.get("description","").lower():
                logger.warning(f"File {file_id} >20MB, нужен локальный Bot API.")
                return None
            raise RuntimeError(data)
        fp = data["result"]["file_path"]
        logger.info(f"getFile returned file_path: {fp}")
        ext = os.path.splitext(fp)[-1] or ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp.close()

        candidates = []
        candidates.append(fp)
        candidates.append(fp.lstrip("/"))
        norm = fp.lstrip("/")
        if norm.startswith("var/lib/telegram-bot-api/"):
            norm = norm[len("var/lib/telegram-bot-api/"):]
            candidates.append(norm)
            if "/" in norm and ":" in norm.split("/")[0]:
                candidates.append(norm.split("/", 1)[1])

        seen = set()
        uniq = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                uniq.append(c)

        bases = [LOCAL_API.rstrip("/")]
        if ":8081" not in bases[0]:
            bases.append(bases[0] + ":8081")

        last_exc = None
        for attempt in range(3):
            for base in bases:
                for cand in uniq:
                    url = f"{base}/file/bot{TG_BOT_TOKEN}/{cand}"
                    url = url.replace("://", "___PROTO___").replace("//", "/").replace("___PROTO___", "://")
                    logger.info(f"[attempt {attempt+1}/3] Trying download: {url}")
                    try:
                        with requests.get(url, stream=True, timeout=600) as resp:
                            resp.raise_for_status()
                            with open(tmp.name, "wb") as f:
                                for ch in resp.iter_content(1024*1024):
                                    if ch: f.write(ch)
                        size = os.path.getsize(tmp.name)
                        logger.info(f"Successfully downloaded via {url} size={size}")
                        if size == 0:
                            raise RuntimeError("0 bytes")
                        return tmp.name
                    except Exception as e:
                        last_exc = e
                        logger.warning(f"Fail {url}: {e}")
                        continue
            if attempt < 2:
                time.sleep(3)
                try:
                    r2 = requests.get(f"{LOCAL_API}/bot{TG_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=60)
                    logger.info(f"Re-getFile: {r2.json().get('result', {}).get('file_path')}")
                except:
                    pass

        try:
            short_cands = [c for c in uniq if "var/lib" not in c]
            if short_cands:
                cand = short_cands[-1]
                url = f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{cand}"
                logger.info(f"Trying official fallback: {url}")
                with requests.get(url, stream=True, timeout=600) as resp:
                    resp.raise_for_status()
                    with open(tmp.name, "wb") as f:
                        for ch in resp.iter_content(1024*1024):
                            if ch: f.write(ch)
                return tmp.name
        except Exception as e:
            logger.warning(f"Official fallback failed: {e}")

        if last_exc:
            raise last_exc
        return None
    except Exception as e:
        if "file is too big" in str(e).lower():
            return None
        raise

def send_to_max_multi(file_paths, caption=None):
    if isinstance(file_paths, str):
        file_paths = [file_paths]