"""
Akinator Game — Powered by OpenGradient TEE
"""
import os, json, threading, asyncio
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import opengradient as og
from web3 import Web3
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder="static")
CORS(app)

PRIVATE_KEY = os.environ.get("OG_PRIVATE_KEY")

# ── Web3 wallet ───────────────────────────────────────────────────────────────
w3b   = Web3(Web3.HTTPProvider("https://sepolia.base.org"))
acct  = w3b.eth.account.from_key(PRIVATE_KEY)
WALLET = acct.address

OPG_TOKEN = Web3.to_checksum_address("0x240b09731D96979f50B2C649C9CE10FcF9C7987F")
ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf",
     "outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals",
     "outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
]
token = w3b.eth.contract(address=OPG_TOKEN, abi=ERC20_ABI)

def get_opg_balance():
    try:
        raw = token.functions.balanceOf(WALLET).call()
        dec = token.functions.decimals().call()
        return round(raw / 10**dec, 4)
    except Exception as e:
        return f"error({e})"

# ── Thread runner (mirrors ChainCatcher) ─────────────────────────────────────
def run_in_thread(fn, timeout=120):
    result_box = [None]
    error_box  = [None]
    def run():
        try: result_box[0] = fn()
        except Exception as e: error_box[0] = e
    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive(): raise Exception("Timed out after 120s")
    if error_box[0]: raise error_box[0]
    return result_box[0]

# ── OpenGradient client (mirrors ChainCatcher) ────────────────────────────────
client = og.LLM(private_key=PRIVATE_KEY)

print("\n" + "="*55)
print("  Akinator Oracle — OpenGradient TEE")
print("="*55)
print(f"  Wallet      : {WALLET}")
print(f"  OPG balance : {get_opg_balance()}")
try:
    approval = run_in_thread(lambda: client.ensure_opg_approval(min_allowance=5.0))
    print(f"  OPG approval: {approval.allowance_after/1e18:.2f} OPG ✓")
except Exception as e:
    print(f"  OPG warning : {e}")
print("="*55)
print("  Open: http://localhost:8080")
print("="*55 + "\n")

# ── Retry helper (mirrors ChainCatcher) ──────────────────────────────────────
def llm_chat_with_retry(fn, retries=3, delay=1.5):
    last_error = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_error = e
            msg = str(e)
            if "TEE LLM chat request failed" in msg or "TEE LLM chat failed" in msg or "Invalid response" in msg:
                print(f"[Retry {attempt+1}/{retries}] LLM error: {msg}")
                time.sleep(delay * (attempt + 1))
                continue
            else:
                raise e
    raise last_error

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an Akinator-style oracle AI. The player is thinking of a {category}.
Ask clever yes/no questions to deduce what they're thinking of, then guess.
RULES:
- Ask ONE question at a time: Yes/No/Maybe/Probably Yes/Probably No/I don't know
- After 15-20 questions (or confidence > 80%), make your final guess
- Guessing: respond ONLY with JSON (nothing else): {{"type":"guess","name":"X","description":"Y","confidence":85}}
- Asking: respond ONLY with JSON (nothing else): {{"type":"question","text":"Q?","confidence":30}}"""

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/status")
def status():
    return jsonify({"wallet": WALLET, "balance": get_opg_balance(), "model": "CLAUDE_SONNET_4_6"})

@app.route("/api/ask", methods=["POST", "OPTIONS"])
def ask():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data         = request.get_json(force=True) or {}
    category     = data.get("category", "thing")
    history      = data.get("history", [])
    question_num = data.get("question_num", 1)

    clean = [
        {"role": m["role"], "content": str(m["content"])}
        for m in history
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ]
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(category=category)}] + clean
    print(f"[TEE] Asking q#{question_num}...")

    try:
        result = run_in_thread(lambda: llm_chat_with_retry(
            lambda: asyncio.run(client.chat(
                model=og.TEE_LLM.CLAUDE_SONNET_4_6,
                messages=messages,
                max_tokens=300,
                temperature=0.3,
                x402_settlement_mode=og.x402SettlementMode.PRIVATE
            ))
        ))

        raw   = (result.chat_output or {}).get("content", "") or ""
        phash = getattr(result, "payment_hash", None)
        print(f"[TEE] Done. tx:{phash} | raw:{raw[:80]}")

        try:
            s = raw.strip()
            if "```" in s:
                s = s.split("```")[1].lstrip("json").strip()
            parsed = json.loads(s)
        except Exception:
            parsed = {"type": "question", "text": raw or "Is it a living thing?", "confidence": 10}

        return jsonify({
            "success":      True,
            "response":     parsed,
            "payment_hash": phash,
            "question_num": question_num
        })

    except Exception as e:
        msg = str(e)
        print(f"[TEE] ERROR: {msg}")
        return jsonify({"success": False, "error": msg}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host="0.0.0.0", port=port)
