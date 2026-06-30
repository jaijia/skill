"""
企业微信 → OpenCode 峰哥 Skill 桥接服务

架构:
  企业微信消息 → POST /callback → 解密 → opencode CLI + 峰哥 skill → 加密 → 回复

启动前配置:
  1. 复制 .env.example 为 .env
  2. 填入企业微信后台的 Token、EncodingAESKey、CorpID
  3. pip install -r requirements.txt
  4. python server.py
  5. 用 ngrok/cpolar 等工具暴露公网 URL
  6. 在企业微信应用后台配置回调 URL: https://你的域名/callback
"""

import os
import re
import subprocess
import time
import logging
import traceback
import threading
import requests as http_requests
from xml.etree.ElementTree import fromstring, Element, SubElement, tostring
from flask import Flask, request
from dotenv import load_dotenv
from wxbizmsgcrypt import WXBizMsgCrypt

load_dotenv()

app = Flask(__name__)

TOKEN = os.getenv("WECOM_TOKEN")
AES_KEY = os.getenv("WECOM_ENCODING_AES_KEY")
CORP_ID = os.getenv("WECOM_CORP_ID")
SKILL_PATH = os.path.expandvars(
    os.getenv(
        "SKILL_PATH",
        r"%USERPROFILE%\.config\opencode\skills\fengge-wangmingtianya-perspective",
    )
)

_wecom_ready = all([TOKEN, AES_KEY, CORP_ID]) and TOKEN != "myToken123" and "改成" not in AES_KEY
crypt = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID) if _wecom_ready else None

# 消息缓存：{msg_id: {"future": thread_handle, "result": None | reply_text}}
_reply_futures: dict = {}
_futures_lock = threading.Lock()


def _find_opencode():
    """查找 opencode 可执行文件，优先 .exe"""
    candidates = [
        os.path.expandvars(r"%APPDATA%\npm\node_modules\opencode-ai\bin\opencode.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    import shutil
    return shutil.which("opencode") or "opencode"


def call_fengge(user_message: str) -> str:
    """调用 OpenCode CLI，使用峰哥 skill 生成回复"""
    opencode_bin = _find_opencode()
    prompt = (
        f"用峰哥亡命天涯的视角和风格回答以下问题。"
        f"保持简短直接，先下结论再解释，黑色幽默但要给出可执行的建议。"
        f"不要超过150字。"
        f"\n\n问题：{user_message}"
    )

    try:
        result = subprocess.run(
            [
                opencode_bin,
                "run",
                "--model", os.getenv("OPENCODE_MODEL", "opencode/gpt-5-nano"),
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
        )
        output = result.stdout.strip()
        if not output:
            output = "兄弟，这会儿脑子转不动了，你等会儿再问一次。"
        return output
    except subprocess.TimeoutExpired:
        return "兄弟，想了有点久，你换个问法试试。"
    except FileNotFoundError:
        return "（OpenCode 没找到，请先安装：npm i -g opencode-ai）"
    except Exception as e:
        return f"出错了兄弟，你等等再试。错误：{str(e)[:50]}"


def _get_access_token():
    """获取企微 access_token"""
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={os.getenv('WECOM_SECRET', '')}"
    r = http_requests.get(url, timeout=10)
    return r.json().get("access_token", "")


def _send_wecom_msg(user_id: str, content: str):
    """主动推送消息给用户"""
    token = _get_access_token()
    if not token:
        logging.error("Failed to get access_token")
        return
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    body = {
        "touser": user_id,
        "msgtype": "text",
        "agentid": int(os.getenv("WECOM_AGENT_ID", "0")),
        "text": {"content": content},
    }
    r = http_requests.post(url, json=body, timeout=10)
    logging.info(f"Send msg result: {r.json()}")


def _async_reply(msg: dict, user_text: str):
    """后台线程处理并回复"""
    try:
        reply = call_fengge(user_text)
        logging.info(f"Fengge reply: {reply[:80]}")
        _send_wecom_msg(msg["from_user"], reply)
    except Exception as e:
        logging.error(f"Async reply error: {traceback.format_exc()}")


def parse_message(xml_text: str) -> dict:
    """解析企微回调的明文 XML"""
    root = fromstring(xml_text)
    return {
        "to_user": root.find("ToUserName").text,
        "from_user": root.find("FromUserName").text,
        "create_time": root.find("CreateTime").text,
        "msg_type": root.find("MsgType").text,
        "content": root.find("Content").text if root.find("Content") is not None else "",
        "msg_id": root.find("MsgId").text if root.find("MsgId") is not None else "",
    }


def build_reply_xml(msg: dict, reply_content: str) -> str:
    """构建回复 XML"""
    xml = Element("xml")
    SubElement(xml, "ToUserName").text = msg["from_user"]
    SubElement(xml, "FromUserName").text = msg["to_user"]
    SubElement(xml, "CreateTime").text = str(int(time.time()))
    SubElement(xml, "MsgType").text = "text"
    SubElement(xml, "Content").text = reply_content
    return tostring(xml, encoding="unicode")


@app.route("/test", methods=["POST"])
def test():
    """本地测试：直接发 JSON，不走企微加解密"""
    raw = request.get_data(as_text=True)
    data = __import__("json").loads(raw)
    user_text = data.get("message", "").strip()
    if not user_text:
        return {"error": "请提供 message 字段"}, 400
    reply = call_fengge(user_text)
    return {"reply": reply}


@app.route("/ping", methods=["GET"])
def ping():
    status = "wecom_ready" if _wecom_ready else "test_mode"
    return {"status": status, "model": os.getenv("OPENCODE_MODEL", "N/A")}


@app.route("/callback", methods=["GET", "POST"])
def callback():
    """企微消息回调，支持 GET(验证)和 POST(接收消息)"""
    import logging
    logging.basicConfig(filename=os.path.expandvars(r"%TEMP%\wecom-callback.log"), level=logging.INFO, format="%(asctime)s %(message)s")
    logging.info(f"CALLBACK: method={request.method}, args={dict(request.args)}, data_len={len(request.data)}")

    if not _wecom_ready:
        return "wecom not configured", 503

    signature = request.args.get("msg_signature", "")
    timestamp = request.args.get("timestamp", "")
    nonce = request.args.get("nonce", "")

    if request.method == "GET":
        echostr = request.args.get("echostr", "")
        logging.info(f"GET verify: sig={signature[:10]}...")
        try:
            result = crypt.verify_url(signature, timestamp, nonce, echostr)
            logging.info(f"Verify OK")
            return result or "verify failed", 200
        except Exception as e:
            logging.error(f"Verify error: {e}")
            return f"verify error: {e}", 403
    try:
        raw_xml = request.data.decode("utf-8")
        logging.info(f"Raw XML ({len(raw_xml)} bytes)")
        plain_xml = crypt.decrypt_msg(signature, timestamp, nonce, raw_xml)
        logging.info(f"Decrypted: {plain_xml[:200]}")
        msg = parse_message(plain_xml)
        logging.info(f"Parsed: type={msg['msg_type']}, from={msg['from_user']}, content={msg['content'][:100]}")

        if msg["msg_type"] != "text":
            logging.info(f"Non-text message, ignoring")
            return "", 200

        user_text = msg["content"].strip()
        logging.info(f"Calling fengge with: {user_text[:80]}")

        # 先回一个占位消息，防止企微超时
        placeholder = "峰哥正在想，稍等..."
        placeholder_xml = build_reply_xml(msg, placeholder)
        encrypted_placeholder = crypt.encrypt_msg(placeholder_xml, nonce)

        # 后台异步获取真实回复
        def _do_reply():
            try:
                real = call_fengge(user_text)
                logging.info(f"Fengge real reply: {real[:80]}")
                _send_wecom_msg(msg["from_user"], real)
            except Exception as e:
                logging.error(f"Async reply error: {e}")

        threading.Thread(target=_do_reply, daemon=True).start()

        return encrypted_placeholder, 200, {"Content-Type": "application/xml; charset=utf-8"}

    except Exception as e:
        logging.error(f"Callback error: {traceback.format_exc()}")
        return f"error: {e}", 500


if __name__ == "__main__":
    print(f"[server] Starting on port {os.getenv('PORT', 8080)}, wecom_ready={_wecom_ready}")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
