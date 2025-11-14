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

messages = []
follower_status = {}
read_only_mode = False
last_message_id = 0
unordered_msg_buffer = {}
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
    global follower_status
    attempt = 0
    async with httpx.AsyncClient() as client:

        while True:
            follower_is_available = follower_status[url]

            if follower_is_available:
                try:
                    await client.post(f"{url}/append-message", json=message, timeout=30)
                    logger.info(f"Message sent to {FOLLOWERS_URL[url]}")
                    break
                except Exception:
                    logger.warning(f"Failed to send to {FOLLOWERS_URL[url]}. ")

            logger.warning(f"{FOLLOWERS_URL[url]} not available. Retrying.")

            base_delay = min(2 ** attempt, 100)
            jitter = random.uniform(0, 5)
            wait_time = base_delay + jitter
            attempt +=1
            await asyncio.sleep(wait_time)

        return True



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

    replication_tasks = [
        asyncio.create_task(
            send_to_follower(url, entry)
        ) for url, _ in FOLLOWERS_URL.items()
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
    global last_message_id, unordered_msg_buffer
    if delay_seconds := int(os.getenv("DELAY_SECONDS")):
        await asyncio.sleep(delay_seconds)

    async with lock:
        if msg.id in message_ids:
            logger.info(f"Duplicate message with id={msg.id} ignored in {os.getenv('CONTAINER_NAME')}")
            return {"status": "message_ignored"}

        if msg.id == last_message_id + 1:
            store_message(msg)
            while (next_id := last_message_id + 1) in unordered_msg_buffer:
                buffered_msg = unordered_msg_buffer.pop(next_id)
                store_message(buffered_msg)

        if msg.id > last_message_id + 1:
            unordered_msg_buffer[msg.id] = msg

    return {"status": "ok"}


def store_message(msg: MessageWithId):
    global last_message_id
    messages.append(msg.model_dump())
    message_ids.add(msg.id)
    last_message_id = msg.id
    logger.info(f"Message stored with id={msg.id} in {os.getenv("CONTAINER_NAME")} node")


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
