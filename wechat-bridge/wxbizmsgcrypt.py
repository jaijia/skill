import base64
import hashlib
import random
import struct
import socket
from Crypto.Cipher import AES


class WXBizMsgCrypt:
    """企业微信消息加解密"""

    def __init__(self, token, encoding_aes_key, corp_id):
        self.token = token
        self.corp_id = corp_id.encode("utf-8")
        self.key = base64.b64decode(encoding_aes_key + "=")

    def verify_url(self, msg_signature, timestamp, nonce, echostr):
        """验证回调 URL"""
        signature = self._sha1(self.token, timestamp, nonce, echostr)
        if signature != msg_signature:
            return None
        return self._aes_decrypt(echostr).decode("utf-8")

    def decrypt_msg(self, msg_signature, timestamp, nonce, encrypted_xml):
        """解密消息，返回明文 XML 字符串"""
        from xml.etree.ElementTree import fromstring

        root = fromstring(encrypted_xml)
        encrypt = root.find("Encrypt").text
        signature = self._sha1(self.token, timestamp, nonce, encrypt)
        if signature != msg_signature:
            raise ValueError("签名验证失败")
        return self._aes_decrypt(encrypt).decode("utf-8")

    def encrypt_msg(self, reply_xml, nonce, timestamp=None):
        """加密回复，返回加密后的 XML"""
        timestamp = timestamp or str(int(__import__("time").time()))
        encrypt = self._aes_encrypt(reply_xml)
        signature = self._sha1(self.token, timestamp, nonce, encrypt)
        return (
            f"<xml>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            f"</xml>"
        )

    @staticmethod
    def _sha1(token, timestamp, nonce, encrypt):
        args = sorted([token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(args).encode("utf-8")).hexdigest()

    def _aes_decrypt(self, ciphertext):
        cipher = AES.new(self.key, AES.MODE_CBC, iv=self.key[:16])
        plaintext = cipher.decrypt(base64.b64decode(ciphertext))
        pad = plaintext[-1]
        content = plaintext[16:-pad]
        xml_len = socket.ntohl(struct.unpack("I", content[:4])[0])
        return content[4 : 4 + xml_len]

    def _aes_encrypt(self, plaintext):
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        random_bytes = random.getrandbits(128).to_bytes(16, "big")
        msg_len = struct.pack("!I", len(plaintext))
        raw = random_bytes + msg_len + plaintext + self.corp_id
        pad = 32 - len(raw) % 32
        raw += bytes([pad] * pad)
        cipher = AES.new(self.key, AES.MODE_CBC, iv=self.key[:16])
        return base64.b64encode(cipher.encrypt(raw)).decode()
