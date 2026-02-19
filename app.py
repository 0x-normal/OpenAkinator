"""
Akinator Game — Powered by OpenGradient SDK
"""
import os, json, threading

# Load .env file locally if present (ignored on Railway/Render where env vars are set directly)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import opengradient as og
from web3 import Web3

app = Flask(__name__, static_folder="static")
CORS(app)

PRIVATE_KEY = os.environ.get("OG_PRIVATE_KEY")
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

def run_in_thread(fn, timeout=90):
    """Run any callable in a fresh thread (fixes Windows asyncio conflicts)."""
    result_box = [None]
    error_box  = [None]
    def run():
        try: result_box[0] = fn()
        except Exception as e: error_box[0] = e
    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive(): raise Exception(f"Timed out after {timeout}s")
    if error_box[0]: raise error_box[0]
    return result_box[0]

# ── Startup ───────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
print(f"  Akinator Oracle — OpenGradient SDK")
print(f"{'='*55}")
print(f"  SDK version    : {og.__version__ if hasattr(og,'__version__') else 'unknown'}")
print(f"  Wallet         : {WALLET}")
print(f"  OPG balance    : {get_opg_balance()}")

# Init client (uses default llm.opengradient.ai endpoint)
client = og.Client(private_key=PRIVATE_KEY)

# ensure_opg_approval (available in 0.7.1+)
print(f"\n  Running ensure_opg_approval...")
try:
    approval = run_in_thread(lambda: client.llm.ensure_opg_approval(opg_amount=5.0))
    print(f"  Before : {approval.allowance_before/1e18:.4f} OPG")
    print(f"  After  : {approval.allowance_after/1e18:.4f} OPG")
    print(f"  Tx     : {approval.tx_hash or 'none (already approved)'}")
    print(f"  ✓ Permit2 approved")
except AttributeError:
    print(f"  ✗ ensure_opg_approval not found — run: pip install opengradient --upgrade")
except Exception as e:
    print(f"  ✗ Error: {e}")

# Test models
MODEL_PRIORITY = [
    ("CLAUDE_3_5_HAIKU", og.TEE_LLM.CLAUDE_3_5_HAIKU),
    ("GPT_4O",           og.TEE_LLM.GPT_4O),
    ("GEMINI_2_0_FLASH", og.TEE_LLM.GEMINI_2_0_FLASH),
    ("GROK_3_MINI_BETA", og.TEE_LLM.GROK_3_MINI_BETA),
]
ACTIVE_MODEL = ACTIVE_MODEL_NAME = None
print(f"\n  Testing models...")
for name, model in MODEL_PRIORITY:
    print(f"  {name}... ", end="", flush=True)
    try:
        r = run_in_thread(lambda m=model: client.llm.chat(
            model=m,
            messages=[{"role":"system","content":"Reply with one word: OK"},
                      {"role":"user","content":"Ready?"}],
            max_tokens=10, temperature=0,
            x402_settlement_mode=og.x402SettlementMode.SETTLE_BATCH
        ))
        content = (r.chat_output or {}).get("content","")
        if content:
            print(f"✓  ({content.strip()[:20]})")
            ACTIVE_MODEL = model
            ACTIVE_MODEL_NAME = name
            break
        else:
            print(f"✗  empty response")
    except Exception as e:
        print(f"✗  {str(e)[:80]}")

if not ACTIVE_MODEL:
    ACTIVE_MODEL = og.TEE_LLM.CLAUDE_3_5_HAIKU
    ACTIVE_MODEL_NAME = "CLAUDE_3_5_HAIKU (fallback)"

print(f"\n  Active model : {ACTIVE_MODEL_NAME}")
print(f"{'='*55}")
print(f"  Open: http://localhost:5000")
print(f"{'='*55}\n")

SYSTEM_PROMPT = """You are an Akinator-style oracle AI. The player is thinking of a {category}.
Ask clever yes/no questions to deduce what they're thinking of, then guess.
RULES:
- Ask ONE question at a time: Yes/No/Maybe/Probably Yes/Probably No/I don't know
- After 15-20 questions (or confidence > 80%), make your final guess
- Guessing: respond ONLY with JSON (nothing else): {{"type":"guess","name":"X","description":"Y","confidence":85}}
- Asking: respond ONLY with JSON (nothing else): {{"type":"question","text":"Q?","confidence":30}}"""

@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"]  = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return r

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/status")
def status():
    return jsonify({"wallet": WALLET, "balance": get_opg_balance(), "model": ACTIVE_MODEL_NAME})

@app.route("/api/ask", methods=["POST","OPTIONS"])
def ask():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data         = request.get_json(force=True) or {}
    category     = data.get("category","thing")
    history      = data.get("history",[])
    question_num = data.get("question_num",1)
    clean = [
        {"role":m["role"],"content":str(m["content"])}
        for m in history
        if isinstance(m,dict) and m.get("role") in ("user","assistant") and m.get("content")
    ]
    messages = [{"role":"system","content":SYSTEM_PROMPT.format(category=category)}] + clean
    print(f"[OG] → {ACTIVE_MODEL_NAME} q#{question_num}")
    try:
        result = run_in_thread(lambda: client.llm.chat(
            model=ACTIVE_MODEL, messages=messages,
            max_tokens=300, temperature=0.3,
            x402_settlement_mode=og.x402SettlementMode.SETTLE_BATCH
        ))
        raw   = (result.chat_output or {}).get("content","") or ""
        phash = getattr(result,"payment_hash",None)
        print(f"[OG] ← {raw[:80]} | tx:{phash}")
        try:
            s = raw.strip()
            if "```" in s:
                s = s.split("```")[1].lstrip("json").strip()
            parsed = json.loads(s)
        except Exception:
            parsed = {"type":"question","text":raw or "Is it a living thing?","confidence":10}
        return jsonify({"success":True,"response":parsed,
                        "payment_hash":phash,"question_num":question_num})
    except Exception as e:
        msg = str(e)
        print(f"[OG] ERROR: {msg}")
        return jsonify({"success":False,"error":msg}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
