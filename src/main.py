import os
import asyncio
import logging
import random
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, Response
from fastapi.requests import Request


from models import Message, MessageWithId

app = FastAPI()
templates = Jinja2Templates(directory="templates")
logging.getLogger("uvicorn").setLevel(logging.ERROR)
logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

FOLLOWERS_URL = {
    "http://follower1:8000/follower": "Follower 1",
    "http://follower2:8000/follower": "Follower 2"
}

MAX_RETRIES = 5

messages = []
follower_status = {}
read_only_mode = False
message_ids = set()
message_id = 0
QUORUM = (len(FOLLOWERS_URL) + 1) // 2 + 1
lock = asyncio.Lock()

async def check_follower_health(url: str, interval: int = 10):
    global follower_status, read_only_mode

    async with httpx.AsyncClient() as client:
        while True:
            try:
                resp = await client.get(f"{url}/health", timeout=3)
                follower_status[url] = resp.status_code == 200
            except Exception:
                follower_status[url] = False

            alive_count = 1 + sum(follower_status.values())

            if alive_count < QUORUM:
                if not read_only_mode:
                    read_only_mode = True
            else:
                if read_only_mode:
                    read_only_mode = False

            await asyncio.sleep(interval)

async def send_to_follower(url: str, message: dict) -> bool:
    async with httpx.AsyncClient() as client:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await client.post(f"{url}/append-message", json=message, timeout=40)
                logger.info(f"Message sent to {FOLLOWERS_URL[url]}")
                return True
            except Exception:
                base_delay = min(2 ** (attempt - 1), 30)
                jitter = random.uniform(0, 5)
                wait_time = base_delay + jitter
                logger.warning(
                    f"Failed to send to {FOLLOWERS_URL[url]} (attempt {attempt}/{MAX_RETRIES}). "
                )
                await asyncio.sleep(wait_time)

        logger.error(f"Could not send message to {FOLLOWERS_URL[url]} after {MAX_RETRIES} attempts.")
        return False


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    container_name = os.getenv("CONTAINER_NAME")

    async with lock:
        messages_copy = messages.copy()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "container_name": container_name,
            "messages": messages_copy,
        },
    )


@app.post("/master/append-message")
async def append_message(msg: Message):
    global message_id, read_only_mode

    if read_only_mode:
        logger.warning("Write rejected: quorum not reached.")
        return Response(
            status_code=503,
            content="Quorum not reached. Master is in read-only mode."
        )

    if os.getenv("CONTAINER_NAME") != "Master":
        raise HTTPException(
            status_code=403, detail="Only Master container can append messages"
        )
    logger.info(f"Message {msg.message} received in Master node")

    write_concern = msg.w

    async with lock:
        message_id += 1
        entry = {"id": message_id, "message": msg.message}
        messages.append(entry)
        logger.info(f"Message stored in Master node")

    active_followers = [url for url, alive in follower_status.items() if alive]

    replication_tasks = [
        asyncio.create_task(
            send_to_follower(url, entry)
        ) for url in active_followers
    ]

    write_concern -= 1

    if write_concern == 0:
        return {"status": "ok"}

    for future in asyncio.as_completed(replication_tasks):
        result = await future
        if result:
            write_concern -= 1

        if write_concern == 0:
            break

    return {"status": "ok"}


@app.post("/follower/append-message")
async def add_message_follower(msg: MessageWithId):
    if delay_seconds := int(os.getenv("DELAY_SECONDS")):
        await asyncio.sleep(delay_seconds)

    async with lock:
        if msg.id in message_ids:
            logger.info(f"Duplicate message with id={msg.id} ignored in {os.getenv('CONTAINER_NAME')}")
            return {"status": "message_ignored"}

        messages.append(msg.model_dump())
        message_ids.add(msg.id)

    logger.info(f"Message stored in {os.getenv("CONTAINER_NAME")} node")
    return {"status": "ok"}


@app.get("/list-messages")
async def get_all_messages():
    return {"messages": messages.copy()}

@app.get("/follower/health")
async def health():
    return {"status": "ok"}

@app.on_event("startup")
async def start_healthchecks():
    if os.getenv("CONTAINER_NAME") != "Master":
        return

    initial_cooldown = 10
    await asyncio.sleep(initial_cooldown)

    for url in FOLLOWERS_URL:
        asyncio.create_task(check_follower_health(url))
