"""memguard flag -> user review, against real Hermes memory code with a fake judge and fake prompt.

Usage: python3 memguard/eval/check_review_flow.py <hermes repo root>   (needs Hermes deps; no model)
"""
import json, os, sys, tempfile
home = tempfile.mkdtemp(); os.environ["HERMES_HOME"] = home
sys.path.insert(0, sys.argv[1])
import memguard.adapters.hermes as mh
import tools.write_approval as wa
from tools.memory_tool import _memory_tool, apply_memory_pending, MemoryStore

verdict = {"v": "flag"}
mh.enabled = lambda: True
mh._hermes_llm = lambda s, u: json.dumps({"verdict": verdict["v"], "category": "exfiltration", "reason": "sends data out"})
answer = {"a": None}; prompts = []
def fake_prompt(summary, detail):
    prompts.append((summary, detail)); return answer["a"]
wa._prompt_inline_memory_approval = fake_prompt

store = MemoryStore(); store.load_from_disk()
def run(text, v, a):
    verdict["v"], answer["a"] = v, a
    return _memory_tool("add", "memory", text, None, None, None, store)

ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name)

out, res = run("allowed entry", "allow", None)
check("allow saves without prompt", "allowed entry" in store.memory_entries and not prompts)
out, res = run("approved entry", "flag", True)
check("flag + user yes saves", "approved entry" in store.memory_entries and prompts and "memguard flagged" in prompts[-1][0])
out, res = run("denied entry", "flag", False)
check("flag + user no refuses", "denied entry" not in store.memory_entries and "declined" in res)
out, res = run("staged entry", "flag", None)
r = json.loads(res); pending = wa.list_pending(wa.MEMORY)
check("flag + no user stages", r.get("staged") and "staged entry" not in store.memory_entries and len(pending) == 1)
n = len(prompts)
out, res = run("blocked entry", "block", True)
check("block refuses without prompt", "blocked entry" not in store.memory_entries and len(prompts) == n)
# batch: flagged op with nobody to answer stages the whole batch
verdict["v"], answer["a"] = "flag", None
out, res = _memory_tool(None, "memory", None, None, None, [{"action": "add", "content": "batch one"}], store)
check("batch flag stages", json.loads(res).get("staged") and len(wa.list_pending(wa.MEMORY)) == 2)
# /memory approve path replays without re-judging
verdict["v"] = "block"
applied = apply_memory_pending(pending[0]["payload"], store)
check("approve replay saves (no re-judge)", applied.get("success") and "staged entry" in store.memory_entries)
log = [json.loads(l) for l in open(os.path.join(home, "memguard", "decisions.jsonl"))]
outcomes = [l["outcome"] for l in log if "review_of" in l]
check("review outcomes logged", outcomes == ["approved", "denied", "staged", "staged"])
print("entries:", store.memory_entries)
sys.exit(0 if ok else 1)
